#!/usr/bin/env bash
set -euo pipefail
stage=/opt/upo-workbooks-pb-staging
install -d -m 700 "$stage" /opt/upo-workbooks/backups
# Online SQLite snapshot plus all current static assets, server code, uploads and access settings.
# No credentials or participant records are uploaded to GitHub.
docker exec -i workbook_api python <<'PY'
import sqlite3,tarfile,tempfile,json,os,hashlib
from pathlib import Path
from datetime import datetime,timezone
root=Path('/data');backups=root/'full-backups';backups.mkdir(exist_ok=True,mode=0o700)
stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ');out=backups/(stamp+'-before-fire-staging.tgz')
with tempfile.TemporaryDirectory() as td:
 p=Path(td)/'workbooks.sqlite3'
 with sqlite3.connect('/data/workbooks.sqlite3') as src,sqlite3.connect(p) as dst:src.backup(dst)
 with sqlite3.connect(p) as con:assert con.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
 with tarfile.open(out,'w:gz') as t:
  t.add(p,arcname='data/workbooks.sqlite3')
  for f in root.iterdir():
   if f.name not in {'full-backups','backups','builder-backups','workbooks.sqlite3','workbooks.sqlite3-wal','workbooks.sqlite3-shm'}:t.add(f,arcname='data/'+f.name)
  t.add('/app',arcname='app')
  cfg=Path(td)/'environment.json';cfg.write_text(json.dumps({k:v for k,v in os.environ.items() if k.startswith('WORKBOOK_')}));t.add(cfg,arcname='environment.json')
out.chmod(0o600);Path('/data/fire-staging-backup-path').write_text(str(out))
print('Production backup complete; SQLite integrity: OK; archive SHA256:',hashlib.sha256(out.read_bytes()).hexdigest())
PY
backup_path="$(docker exec workbook_api cat /data/fire-staging-backup-path)"
docker cp "workbook_api:$backup_path" "$stage/production-restore-point.tgz"
chmod 600 "$stage/production-restore-point.tgz"
if [ ! -d "$stage/data" ]; then
  mkdir -m 700 "$stage/data"
  python3 - "$stage" <<'PY'
import sys,tarfile,json,os,secrets,sqlite3,hashlib
from pathlib import Path
stage=Path(sys.argv[1])
with tarfile.open(stage/'production-restore-point.tgz') as t:
 members=[m for m in t.getmembers() if m.name.startswith('data/')]
 t.extractall(stage,members=members,filter='data')
 cfg=json.load(t.extractfile('environment.json'))
cfg.update(WORKBOOK_TOKEN_SECRET=secrets.token_hex(32),WORKBOOK_ADMIN_KEY=secrets.token_hex(32),WORKBOOK_DB_PATH='/data/workbooks.sqlite3',WORKBOOK_STATIC_DIR='/app/static',WORKBOOK_ALLOWED_ORIGINS='http://194.48.140.70:8083')
(stage/'.env').write_text('\n'.join(k+'='+v for k,v in cfg.items())+'\n');(stage/'.env').chmod(0o600)
with sqlite3.connect(stage/'data/workbooks.sqlite3') as c:
 assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
 tables=[r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")]
 hashes={table:hashlib.sha256(repr(c.execute('SELECT * FROM "'+table+'" ORDER BY rowid').fetchall()).encode()).hexdigest() for table in tables if not table.startswith('fb_')}
(stage/'legacy-hashes.json').write_text(json.dumps(hashes))
print('Restore tested in separate staging database; integrity: OK')
PY
fi
mkdir -p "$stage/source"
tar -xzf /tmp/upo-workbooks-pb-staging.tgz -C "$stage/source"
docker build -t ifcm-pb-staging:latest -f "$stage/source/server/Dockerfile" "$stage/source"
# Only the isolated staging container is replaced.
docker rm -f workbook_pb_staging 2>/dev/null || true
docker run -d --name workbook_pb_staging --restart unless-stopped --env-file "$stage/.env" -v "$stage/data:/data" -p 8083:8080 ifcm-pb-staging:latest
for attempt in $(seq 1 30); do
 if curl -fsS http://127.0.0.1:8083/health >/dev/null; then break; fi
 if [ "$attempt" -eq 30 ]; then docker logs --tail 50 workbook_pb_staging; exit 1; fi
 sleep 2
done
docker cp "$stage/source/server/dev_fire_stage.py" workbook_pb_staging:/app/dev_fire_stage.py
docker exec workbook_pb_staging python /app/dev_fire_stage.py
python3 - "$stage" <<'PY'
import sys,json,sqlite3,hashlib
from pathlib import Path
stage=Path(sys.argv[1]);expected=json.loads((stage/'legacy-hashes.json').read_text())
with sqlite3.connect(stage/'data/workbooks.sqlite3') as c:
 for table,digest in expected.items():
  actual=hashlib.sha256(repr(c.execute('SELECT * FROM "'+table+'" ORDER BY rowid').fetchall()).encode()).hexdigest()
  assert actual==digest,'Legacy table changed: '+table
print('All pre-existing result, attempt, review, evidence and settings tables unchanged after staging import/publications')
PY
rm -f /tmp/upo-workbooks-pb-staging.tgz
