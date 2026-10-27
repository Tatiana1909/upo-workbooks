"""Local server-only scheduled recovery points. No pruning or offsite transfers."""
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from restore_check import check

APP=Path('/opt/upo-workbooks')
ROOT=APP/'backups/scheduled'
STAGE='preparation'


def command(args):
    r=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    if r.returncode:raise RuntimeError('Backup command failed')
    return r.stdout.decode()


def main():
    global STAGE
    os.umask(0o077)
    ROOT.mkdir(mode=0o700,parents=True,exist_ok=True)
    with open(Path(__file__).with_name('backup.lock'),'a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return {'backup_already_running':True}
        stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        directory=ROOT/(stamp+'.partial');directory.mkdir(mode=0o700)
        STAGE='runtime-inspection'
        runtime=json.loads(command(['docker','inspect','workbook_api']))[0]
        container=runtime['Id'];image=runtime['Image']
        STAGE='capacity-check'
        sizing="import os,json; total=0\nfor root in ['/data','/app']:\n for path,dirs,files in os.walk(root):\n  dirs[:]=[d for d in dirs if d not in ('backups','.git','__pycache__')]\n  for name in files:\n   p=os.path.join(path,name)\n   if not os.path.islink(p):total+=os.path.getsize(p)\nprint(json.dumps(total))"
        runtime_size=json.loads(command(['docker','exec',container,'python','-c',sizing]))
        host_size=0
        for path,dirs,files in os.walk(APP):
            dirs[:]=[d for d in dirs if d not in ('backups','.git','__pycache__')]
            for name in files:
                p=Path(path)/name
                if not p.is_symlink():host_size+=p.stat().st_size
        # Allow uncompressed archives, SQLite snapshot, extracted clone and a reserve.
        required=4*runtime_size+2*host_size+128*1024**2
        if shutil.disk_usage(ROOT).free<required:
            raise RuntimeError('Insufficient space for a verified recovery point')
        STAGE='application-archive'
        script=Path(__file__).with_name('backup_bundle.py')
        command(['docker','cp',str(script),container+':/tmp/ifcm-scheduled-backup.py'])
        try:
            created=json.loads(command(['docker','exec',container,'python','/tmp/ifcm-scheduled-backup.py',
               '--data-dir','/data','--output','/tmp/ifcm-scheduled-data.tgz','--source-dir','/app']))
            if not created.get('verified'):raise RuntimeError('Backup verification failed')
            command(['docker','cp',container+':/tmp/ifcm-scheduled-data.tgz',str(directory/'data.tgz')])
        finally:
            command(['docker','exec',container,'rm','-f','/tmp/ifcm-scheduled-backup.py','/tmp/ifcm-scheduled-data.tgz'])
        STAGE='server-settings'
        command(['tar','--exclude=backups','--exclude=.git','--exclude=__pycache__','-czf',str(directory/'server-settings.tgz'),'-C',str(APP),'.'])
        (directory/'metadata.json').write_text(json.dumps({'created_at':stamp,'image_id':image,'scope':'local_server','https_enabled':(APP/'https/origin').is_file()}))
        (directory/'probe.py').write_bytes(Path(__file__).with_name('probe.py').read_bytes())
        sums={}
        for name in ('data.tgz','server-settings.tgz','metadata.json','probe.py'):
            path=directory/name;path.chmod(0o600);h=hashlib.sha256()
            with path.open('rb') as f:
                for part in iter(lambda:f.read(1024*1024),b''):h.update(part)
            sums[name]=h.hexdigest()
        (directory/'checksums.json').write_text(json.dumps(sums))
        STAGE='isolated-restore'
        result=check(directory)
        (directory/'verified.json').write_text(json.dumps(result))
        directory.rename(ROOT/stamp)
        return {'local_backup_verified':True,'isolated_restore_verified':True,'offsite_copy':False}


if __name__=='__main__':
    try:print(json.dumps(main()))
    except Exception as error:
        print(json.dumps({'local_backup_verified':False,'stage':STAGE,'error_type':type(error).__name__,'action':'Check the private backup service on the server'}))
        raise SystemExit(1)
