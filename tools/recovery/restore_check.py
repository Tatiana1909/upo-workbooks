"""Verify and boot a PRIVATE backup in a container without network/published ports."""
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import tarfile
from pathlib import Path
from backup_bundle import restore
STAGE="archive-verification"


def run(args):
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        raise RuntimeError('Isolated recovery command failed')
    return result.stdout.decode()


def check(directory):
    global STAGE
    directory = Path(directory)
    sums = json.loads((directory / 'checksums.json').read_text())
    if set(sums)!={'data.tgz','server-settings.tgz','metadata.json','probe.py'}:raise ValueError('Incomplete recovery manifest')
    for name, digest in sums.items():
        if name not in ('data.tgz', 'server-settings.tgz', 'metadata.json','probe.py'):
            raise ValueError('Unexpected recovery manifest entry')
        h = hashlib.sha256()
        with (directory / name).open('rb') as f:
            for part in iter(lambda: f.read(1024 * 1024), b''):h.update(part)
        if h.hexdigest() != digest:
            raise ValueError('Recovery point checksum mismatch')
    metadata = json.loads((directory / 'metadata.json').read_text())
    with tarfile.open(directory/'server-settings.tgz','r:gz') as archive:
        names={m.name.removeprefix('./') for m in archive.getmembers()}
        if 'server/.env' not in names:raise ValueError('Server configuration is missing')
        if metadata.get('https_enabled'):
            if not {'https/nginx/workbooks.conf','https/origin','https/certbot-image'}.issubset(names):
                raise ValueError('HTTPS configuration is missing')
            if not any(n.startswith('https/letsencrypt/archive/') and '/privkey' in n for n in names):
                raise ValueError('TLS recovery files are missing')
    image = metadata['image_id']
    if not re.fullmatch(r'sha256:[a-f0-9]{64}', image):raise ValueError('Invalid recovery image identifier')
    # Restore the exact archived image when deployment cleanup removed its cache.
    exists = subprocess.run(['docker','image','inspect',image],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode == 0
    if not exists or metadata.get('runtime_image_archived'):
        STAGE='restore-runtime-image'
        with tarfile.open(directory/'server-settings.tgz','r:gz') as archive:
            member=archive.getmember('runtime-image.tar')
            if not member.isfile():raise ValueError('Invalid runtime image archive')
            with archive.extractfile(member) as stream:
                loaded=subprocess.run(['docker','image','load'],stdin=stream,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
                if loaded.returncode:raise RuntimeError('Archived runtime image could not be loaded')
        run(['docker','image','inspect',image])
    with tempfile.TemporaryDirectory(prefix='isolated-restore-', dir=directory.parent) as tmp:
        STAGE='extract-to-new-directory'
        root = restore(directory / 'data.tgz', Path(tmp) / 'restored')
        with sqlite3.connect(root/'data/workbooks.sqlite3') as db:
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table,folder in [('builder_images','builder-images'),('builder_evidence','builder-evidence'),('fb_images','fire-builder-images'),('fb_evidence','fire-builder-evidence')]:
                if table not in tables:continue
                for iid,extension in db.execute('SELECT id,extension FROM '+table):
                    if not (root/'data'/folder/(iid+'.'+extension)).is_file():
                        raise ValueError('Referenced learning image or evidence is missing')
        STAGE='restored-configuration'
        environment = json.loads((root / 'settings/environment.json').read_text())
        environment.update(WORKBOOK_DB_PATH='/data/workbooks.sqlite3', WORKBOOK_STATIC_DIR='/app/static')
        envfile = Path(tmp) / 'restored.env'
        with envfile.open('x') as f:
            for key, value in environment.items():
                if not key.startswith('WORKBOOK_') or '\n' in key or '\n' in value or '\r' in value:
                    raise ValueError('Unsupported restored configuration')
                f.write(key + '=' + value + '\n')
        envfile.chmod(0o600)
        name = 'ifcm-restore-' + os.urandom(6).hex()
        try:
            STAGE='isolated-container'
            run(['docker', 'run', '-d', '--name', name, '--network', 'none', '--env-file', str(envfile),
                 '-v', str(root/'data')+':/data', '-v', str(root/'source')+':/app:ro', image])
            config = json.loads(run(['docker', 'inspect', name]))[0]['HostConfig']
            if config['NetworkMode'] != 'none' or config.get('PortBindings'):
                raise RuntimeError('Restore container is not isolated')
            STAGE='isolated-startup'
            ready = False
            for _ in range(30):
                try:
                    run(['docker','exec',name,'python','-c',
                         'import urllib.request; urllib.request.build_opener(urllib.request.ProxyHandler({})).open("http://127.0.0.1:8080/health",timeout=2).read()'])
                    ready=True;break
                except RuntimeError:time.sleep(1)
            if not ready:raise RuntimeError('Restored service did not start')
            run(['docker','cp',str(directory/'probe.py'),name+':/tmp/restore_probe.py'])
            STAGE='isolated-api-probe'
            result=json.loads(run(['docker','exec',name,'python','/tmp/restore_probe.py']))
            if not result.get('isolated_api_restore'):raise RuntimeError('Recovery probe failed')
        finally:
            subprocess.run(['docker','rm','-f',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    return {'database_and_files_verified':True,'isolated_api_restore':True}


if __name__ == '__main__':
    try:
        print(json.dumps(check(sys.argv[1])))
    except Exception:
        print(json.dumps({'isolated_api_restore':False,'stage':STAGE}))
        raise SystemExit(1)
