"""Consistent SQLite + immutable uploaded files + settings/source recovery bundle.
Run inside the live workbook container, or on a stopped/copy test database.
The archive contains secrets and must stay on protected server storage.
"""
import argparse,hashlib,json,os,sqlite3,tarfile,tempfile
from pathlib import Path
from datetime import datetime,timezone

def create(data_dir,output,source_dir=None):
    data_dir=Path(data_dir).resolve();output=Path(output).resolve();output.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    if output.is_relative_to(data_dir):raise ValueError('Backup output must be outside the data volume')
    with tempfile.TemporaryDirectory() as tmp:
        snapshot=Path(tmp)/'workbooks.sqlite3'
        with sqlite3.connect(data_dir/'workbooks.sqlite3') as src,sqlite3.connect(snapshot) as dst:src.backup(dst)
        with sqlite3.connect(snapshot) as check:
            assert check.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
            tables={r[0]:check.execute('SELECT COUNT(*) FROM "'+r[0]+'"').fetchone()[0] for r in check.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        hashes={}
        with tarfile.open(output,'w:gz') as archive:
            def add(path,name):
                if path.is_file():hashes[name]=hashlib.sha256(path.read_bytes()).hexdigest();archive.add(path,arcname=name,recursive=False)
            add(snapshot,'data/workbooks.sqlite3')
            for path in sorted(data_dir.rglob('*')):
                rel=path.relative_to(data_dir)
                if rel.parts[0] in ('backups','builder-backups','full-backups') or path.name in ('workbooks.sqlite3','workbooks.sqlite3-wal','workbooks.sqlite3-shm'):continue
                add(path,'data/'+str(rel))
            if source_dir:
                source_dir=Path(source_dir)
                for path in sorted(source_dir.rglob('*')):
                    if any(p in ('.git','__pycache__','backups') for p in path.relative_to(source_dir).parts):continue
                    add(path,'source/'+str(path.relative_to(source_dir)))
            meta={'created_at':datetime.now(timezone.utc).isoformat(),'tables':tables,'sha256':hashes}
            manifest=Path(tmp)/'manifest.json';manifest.write_text(json.dumps(meta,indent=2));archive.add(manifest,arcname='manifest.json')
        os.chmod(output,0o600)
        verify(output)
        return {'verified':True,'files':len(hashes),'tables':len(tables),'path':str(output)}

def verify(bundle):
    with tarfile.open(bundle,'r:gz') as archive:
        manifest=json.load(archive.extractfile('manifest.json'))
        for name,digest in manifest['sha256'].items():
            assert hashlib.sha256(archive.extractfile(name).read()).hexdigest()==digest,name
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'restore.sqlite3';path.write_bytes(archive.extractfile('data/workbooks.sqlite3').read())
            with sqlite3.connect(path) as con:
                assert con.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
                for name,count in manifest['tables'].items():assert con.execute('SELECT COUNT(*) FROM "'+name+'"').fetchone()[0]==count
    return True

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--data-dir',required=True);p.add_argument('--output',required=True);p.add_argument('--source-dir');a=p.parse_args();print(json.dumps(create(a.data_dir,a.output,a.source_dir)))
