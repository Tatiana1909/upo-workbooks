"""Complete local restore point; contains secrets, never expose through HTTP."""
import hashlib
import json
import os
import sqlite3
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

def create(db_path, static_dir, reason='manual'):
    db_path=Path(db_path);root=db_path.parent
    folder=root/'full-backups';folder.mkdir(parents=True,exist_ok=True,mode=0o700)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ');dest=folder/(stamp+'-'+reason+'.tgz')
    with tempfile.TemporaryDirectory(dir=folder) as td:
        snap=Path(td)/'workbooks.sqlite3'
        with sqlite3.connect(db_path) as src,sqlite3.connect(snap) as dst:src.backup(dst)
        with sqlite3.connect(snap) as con:
            if con.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise RuntimeError('Backup integrity check failed')
        with tarfile.open(dest,'w:gz') as out:
            out.add(snap,arcname='data/workbooks.sqlite3')
            for p in root.iterdir():
                if p.name not in {'full-backups','backups','builder-backups',db_path.name,db_path.name+'-wal',db_path.name+'-shm'}:out.add(p,arcname='data/'+p.name)
            out.add(static_dir,arcname='static')
            for p in Path(__file__).parent.iterdir():
                if p.suffix in {'.py','.yaml','.txt','.enc'} or p.name in {'.env','Dockerfile'}:out.add(p,arcname='server/'+p.name)
            cfg=Path(td)/'environment.json';cfg.write_text(json.dumps({k:v for k,v in os.environ.items() if k.startswith('WORKBOOK_')}));out.add(cfg,arcname='server/environment.json')
    dest.chmod(0o600)
    digest=hashlib.sha256(dest.read_bytes()).hexdigest()
    dest.with_suffix('.sha256').write_text(digest+'  '+dest.name+'\n')
    return dest
