#!/usr/bin/env bash
# Invoked only by the review job on the feature branch. Does not deploy production.
set -euo pipefail
umask 077
stamp=$(date -u +%Y%m%dT%H%M%SZ)
snapshot="/opt/upo-workbooks/backups/editable-review-$stamp"
install -d -m 700 "$snapshot"
# Backup the current live data, private settings and deployed sources before staging.
docker cp /tmp/upo-review-source/server/backup_bundle.py workbook_api:/tmp/ifcm_backup_bundle.py
docker exec workbook_api python /tmp/ifcm_backup_bundle.py --data-dir /data --output /tmp/ifcm-full-data.tgz --source-dir /app
docker cp workbook_api:/tmp/ifcm-full-data.tgz "$snapshot/data-and-image.tgz"
docker inspect workbook_api > "$snapshot/container-settings.json"
tar --exclude=backups --exclude=__pycache__ -czf "$snapshot/deployed-source-and-settings.tgz" -C /opt/upo-workbooks .
tar -tzf "$snapshot/deployed-source-and-settings.tgz" > /dev/null
(cd "$snapshot" && sha256sum data-and-image.tgz deployed-source-and-settings.tgz container-settings.json > SHA256SUMS && sha256sum -c SHA256SUMS)
chmod 600 "$snapshot"/*
docker exec workbook_api rm -f /tmp/ifcm-full-data.tgz /tmp/ifcm_backup_bundle.py
# No production volume or production token secret is mounted in the review container.
review=/opt/upo-workbooks-review
install -d -m 700 "$review"
cp -a /tmp/upo-review-source/. "$review/"
python3 - "$review/server/review.env" <<'PY'
import secrets,sys
from pathlib import Path
lines=Path('/opt/upo-workbooks/server/.env').read_text().splitlines()
keep=('WORKBOOK_ACCESS_CODE=','WORKBOOK_ADMIN_LOGIN=','WORKBOOK_ADMIN_PASSWORD=')
selected=[line for line in lines if line.startswith(keep)]
assert len(selected)==3
selected += ['WORKBOOK_TOKEN_SECRET='+secrets.token_hex(32),'WORKBOOK_ADMIN_KEY='+secrets.token_hex(32),'WORKBOOK_DB_PATH=/data/workbooks.sqlite3','WORKBOOK_ALLOWED_ORIGINS=http://194.48.140.70:8082','WORKBOOK_FIRE_ENVELOPE=/data/not-configured.enc']
p=Path(sys.argv[1]);p.write_text('\n'.join(selected)+'\n');p.chmod(0o600)
PY
cd "$review"
docker compose -p upo_review -f server/review-compose.yaml up -d --build
for attempt in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8082/health >/dev/null; then break; fi
  if [ "$attempt" = 30 ]; then exit 1; fi
  sleep 2
done
docker exec -i workbook_review python <<'PY'
import app,json,urllib.request
headers={'Authorization':'Bearer '+app.encode_token('admin',app.ADMIN_LOGIN,'admin',60),'Content-Type':'application/json'}
def call(path,data=None):
 req=urllib.request.Request('http://127.0.0.1:8080/api/v1/'+path,headers=headers,data=json.dumps(data).encode() if data is not None else None)
 return json.load(urllib.request.urlopen(req))
for source in ('accounting','warehouse'):
 draft=call('builder/templates/'+source+'/import',{})
 editions=[v for v in call('builder/editions') if v['draft_id']==draft['id']]
 if not editions:call('builder/drafts/'+draft['id']+'/publish',{'revision':draft['revision']})
 print(json.dumps({'source':source,'editor':'http://194.48.140.70:8082/v4/builder/index.html?draft='+draft['id'],'preview':'http://194.48.140.70:8082/v4/builder/workbook.html?preview=1&draft='+draft['id']}))
PY
curl -fsS http://127.0.0.1:8081/health
printf '\nVerified recovery bundle: %s\n' "$snapshot"
