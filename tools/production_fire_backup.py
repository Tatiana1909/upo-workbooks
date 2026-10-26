"""Run inside the existing container before changing production sources."""
import sqlite3,tarfile,tempfile,json,os,hashlib
from pathlib import Path
from datetime import datetime,timezone
root=Path('/data');backups=root/'full-backups';backups.mkdir(exist_ok=True,mode=0o700)
stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ');out=backups/(stamp+'-before-fire-production.tgz')
with tempfile.TemporaryDirectory() as td:
    p=Path(td)/'workbooks.sqlite3'
    with sqlite3.connect('/data/workbooks.sqlite3') as src,sqlite3.connect(p) as dst:src.backup(dst)
    with sqlite3.connect(p) as con:
        assert con.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        tables=[r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        hashes={table:hashlib.sha256(repr(con.execute('SELECT * FROM "'+table+'" ORDER BY rowid').fetchall()).encode()).hexdigest() for table in tables}
    with tarfile.open(out,'w:gz') as t:
        t.add(p,arcname='data/workbooks.sqlite3')
        for f in root.iterdir():
            if f.name not in {'full-backups','backups','builder-backups','workbooks.sqlite3','workbooks.sqlite3-wal','workbooks.sqlite3-shm'}:t.add(f,arcname='data/'+f.name)
        t.add('/app',arcname='app')
        cfg=Path(td)/'environment.json';cfg.write_text(json.dumps({k:v for k,v in os.environ.items() if k.startswith('WORKBOOK_')}));t.add(cfg,arcname='environment.json')
    # Prove the archived SQLite bytes can be restored independently.
    restored=Path(td)/'restored.sqlite3'
    with tarfile.open(out) as t:restored.write_bytes(t.extractfile('data/workbooks.sqlite3').read())
    with sqlite3.connect(restored) as con:assert con.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
out.chmod(0o600)
manifest=backups/(stamp+'-before-fire-production.json')
manifest.write_text(json.dumps({'archive':str(out),'sha256':hashlib.sha256(out.read_bytes()).hexdigest(),'tables':hashes}));manifest.chmod(0o600)
(root/'fire-production-restore-manifest').write_text(str(manifest))
print('Full production backup and restore integrity: OK; SHA256:',hashlib.sha256(out.read_bytes()).hexdigest())
