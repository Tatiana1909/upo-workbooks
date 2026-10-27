#!/usr/bin/env bash
# HTTPS for the existing workbook service. No credentials are printed or exported.
set -euo pipefail
umask 077
IP=194.48.140.70
APP=/opt/upo-workbooks
TLS="$APP/https"
SOURCE_DIR=$(cd "$(dirname "$0")" && pwd)
CERTBOT_IMAGE=certbot/certbot:v5.8.0
NGINX_IMAGE=nginx:stable-alpine
GATEWAY=workbook_https
BACKEND_PORT=18081
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
BACKUP="$APP/backups/before-https-$STAMP"
install -d -m 700 "$BACKUP" "$TLS" "$TLS/nginx" "$TLS/letsencrypt" "$TLS/staging" "$TLS/certbot-logs"
install -d -m 755 "$TLS/acme" "$TLS/acme/.well-known" "$TLS/acme/.well-known/acme-challenge"

# Fail closed rather than replacing any existing listener or unrelated service.
python3 - <<'PY'
import subprocess,sys
rows=subprocess.check_output(['ss','-ltnp'],text=True).splitlines()[1:]
for port in (80,443,18081):
    if any(r.split()[3].endswith(':'+str(port)) for r in rows):
        raise SystemExit('HTTPS setup stopped: a required port is already occupied; existing services were not modified.')
PY
if docker inspect "$GATEWAY" >/dev/null 2>&1; then
  echo 'HTTPS gateway already exists; use the verification/renewal procedure instead of replacing it.' >&2
  exit 1
fi
# Recovery archives remain root-only on the original server.
# Use the same verified backup utility as the existing production deployment.
docker cp "$APP/server/backup_bundle.py" workbook_api:/tmp/ifcm-https-backup.py
docker exec workbook_api python /tmp/ifcm-https-backup.py --data-dir /data --output /tmp/ifcm-https-data.tgz --source-dir /app
docker cp workbook_api:/tmp/ifcm-https-data.tgz "$BACKUP/data.tgz"
docker exec workbook_api rm -f /tmp/ifcm-https-backup.py /tmp/ifcm-https-data.tgz
tar --exclude=backups --exclude=__pycache__ -czf "$BACKUP/source-and-settings.tgz" -C "$APP" .
docker inspect workbook_api > "$BACKUP/container-settings.json"
tar -tzf "$BACKUP/source-and-settings.tgz" >/dev/null
(cd "$BACKUP" && sha256sum data.tgz source-and-settings.tgz container-settings.json > SHA256SUMS && sha256sum -c SHA256SUMS >/dev/null)
cp "$APP/server/compose.yaml" "$BACKUP/compose.yaml"
cp "$APP/server/.env" "$BACKUP/app.env"
DATA_VOLUME=$(docker inspect workbook_api --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Name}}{{end}}{{end}}')
test -n "$DATA_VOLUME"
PHASE=preparation
BACKEND_CHANGED=0
SUCCESS=0
rollback() {
  code=$?
  if [ "$SUCCESS" != 1 ]; then
    systemctl disable --now ifcm-workbooks-cert-renew.timer >/dev/null 2>&1 || true
    if [ "$BACKEND_CHANGED" = 1 ]; then
      docker rm -f "$GATEWAY" >/dev/null 2>&1 || true
      cp "$BACKUP/compose.yaml" "$APP/server/compose.yaml"
      cp "$BACKUP/app.env" "$APP/server/.env"
      (cd "$APP/server" && docker compose -p upo_workbooks up -d --no-build --no-deps workbook-api) >/dev/null 2>&1 || true
      echo 'HTTPS activation failed; previous application listener restored. Recovery archives remain on the server.' >&2
    else
      docker rm -f "$GATEWAY" >/dev/null 2>&1 || true
      echo "HTTPS setup stopped during $PHASE before application switching; the original service is unchanged." >&2
    fi
  fi
  exit "$code"
}
trap rollback EXIT

PHASE=container-images
docker pull "$NGINX_IMAGE" >/dev/null
docker pull "$CERTBOT_IMAGE" >/dev/null
# Keep the exact image used for renewal, avoiding mutable tags during scheduled runs.
CERTBOT_DIGEST=$(docker image inspect "$CERTBOT_IMAGE" --format '{{index .RepoDigests 0}}')
NGINX_DIGEST=$(docker image inspect "$NGINX_IMAGE" --format '{{index .RepoDigests 0}}')
printf '%s\n' "$CERTBOT_DIGEST" > "$TLS/certbot-image"
cat > "$TLS/nginx/workbooks.conf" <<EOF
server {
    listen 80;
    server_name $IP;
    location ^~ /.well-known/acme-challenge/ { root /var/www/acme; default_type text/plain; }
    location / { return 404; }
}
EOF
chmod 644 "$TLS/nginx/workbooks.conf"
if command -v ufw >/dev/null 2>&1 && ufw status | grep -q '^Status: active'; then
  ufw allow 80/tcp comment 'IFCM certificate validation' >/dev/null
  ufw allow 443/tcp comment 'IFCM workbooks HTTPS' >/dev/null
fi
docker run -d --name "$GATEWAY" --restart unless-stopped --network host \
  -v "$TLS/nginx:/etc/nginx/conf.d:ro" \
  -v "$TLS/acme:/var/www/acme:ro" \
  -v "$TLS/letsencrypt:/etc/letsencrypt:ro" \
  "$NGINX_DIGEST" >/dev/null
PHASE=acme-route
TOKEN="ifcm-https-check-$STAMP"
printf '%s' "$TOKEN" > "$TLS/acme/.well-known/acme-challenge/$TOKEN"
chmod 644 "$TLS/acme/.well-known/acme-challenge/$TOKEN"
for attempt in $(seq 1 20); do
  if [ "$(curl --silent --fail --max-time 5 "http://127.0.0.1/.well-known/acme-challenge/$TOKEN" -H "Host: $IP" || true)" = "$TOKEN" ]; then break; fi
  sleep 1
done
test "$(curl --silent --fail --max-time 10 "http://$IP/.well-known/acme-challenge/$TOKEN")" = "$TOKEN"
certbot_run() {
  root=$1; shift
  docker run --rm --network host \
    -v "$root:/etc/letsencrypt" -v "$TLS/acme:/var/www/acme" \
    -v "$TLS/certbot-logs:/var/log/letsencrypt" "$CERTBOT_DIGEST" "$@"
}
# Validate the route against staging first; production is requested only on success.
PHASE=staging-certificate
certbot_run "$TLS/staging" certonly --staging --non-interactive --agree-tos --register-unsafely-without-email \
  --preferred-profile shortlived --webroot --webroot-path /var/www/acme --ip-address "$IP" \
  --cert-name ifcm-workbooks-ip --quiet
PHASE=trusted-certificate
certbot_run "$TLS/letsencrypt" certonly --non-interactive --agree-tos --register-unsafely-without-email \
  --preferred-profile shortlived --webroot --webroot-path /var/www/acme --ip-address "$IP" \
  --cert-name ifcm-workbooks-ip --quiet
CERT="$TLS/letsencrypt/live/ifcm-workbooks-ip"
openssl x509 -in "$CERT/fullchain.pem" -noout -checkip "$IP" >/dev/null
openssl x509 -in "$CERT/fullchain.pem" -noout -checkend 172800 >/dev/null
cat > "$TLS/nginx/workbooks.conf" <<EOF
server {
    listen 80;
    server_name $IP;
    location ^~ /.well-known/acme-challenge/ { root /var/www/acme; default_type text/plain; }
    location / { return 308 https://$IP\$request_uri; }
}
server {
    listen 8081;
    server_name $IP;
    return 308 https://$IP\$request_uri;
}
server {
    listen 443 ssl;
    server_name $IP;
    ssl_certificate /etc/letsencrypt/live/ifcm-workbooks-ip/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/ifcm-workbooks-ip/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_session_cache shared:IFCM_TLS:10m;
    ssl_session_timeout 1d;
    ssl_session_tickets off;
    client_max_body_size 10m;
    add_header X-Content-Type-Options nosniff always;
    add_header Referrer-Policy strict-origin-when-cross-origin always;
    location = / { return 302 /v4/index.html; }
    location / {
        proxy_pass http://127.0.0.1:$BACKEND_PORT;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-For \$remote_addr;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_read_timeout 120s;
    }
}
EOF
chmod 644 "$TLS/nginx/workbooks.conf"
docker exec "$GATEWAY" nginx -t
# Activate the prepared backend binding; no image rebuild or data migration is needed.
install -m 644 "$SOURCE_DIR/compose.yaml" "$APP/server/compose.yaml"
sed -i '/^WORKBOOK_BIND_ADDRESS=/d;/^WORKBOOK_BIND_PORT=/d;/^WORKBOOK_ALLOWED_ORIGINS=/d' "$APP/server/.env"
printf 'WORKBOOK_BIND_ADDRESS=127.0.0.1\nWORKBOOK_BIND_PORT=%s\nWORKBOOK_ALLOWED_ORIGINS=https://%s\n' "$BACKEND_PORT" "$IP" >> "$APP/server/.env"
PHASE=activation
BACKEND_CHANGED=1
(cd "$APP/server" && docker compose -p upo_workbooks up -d --no-build --no-deps workbook-api) >/dev/null
for attempt in $(seq 1 30); do
  if curl --fail --silent --max-time 5 "http://127.0.0.1:$BACKEND_PORT/health" >/dev/null; then break; fi
  sleep 1
done
curl --fail --silent --max-time 10 "http://127.0.0.1:$BACKEND_PORT/health" >/dev/null
test "$(docker inspect workbook_api --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Name}}{{end}}{{end}}')" = "$DATA_VOLUME"
docker exec "$GATEWAY" nginx -s reload
for attempt in $(seq 1 20); do
  if curl --fail --silent --max-time 10 "https://$IP/health" >/dev/null; then break; fi
  sleep 1
done
curl --fail --silent --max-time 15 "https://$IP/health" >/dev/null
curl --fail --silent --max-time 15 "https://$IP/v4/accounts.html" >/dev/null
test "$(curl --silent --output /dev/null --write-out '%{http_code}' "http://$IP:8081/v4/accounts.html")" = 308
docker exec workbook_api python -c 'import sqlite3,os; c=sqlite3.connect(os.environ["WORKBOOK_DB_PATH"]); assert c.execute("PRAGMA integrity_check").fetchone()[0]=="ok"; print("Database integrity and persistent volume verified")'
PHASE=automatic-renewal
# Renewal is independent of GitHub and runs even when no deployments take place.
install -m 700 "$SOURCE_DIR/renew.sh" /usr/local/sbin/ifcm-workbooks-cert-renew
install -m 644 "$SOURCE_DIR/renew.service" /etc/systemd/system/ifcm-workbooks-cert-renew.service
install -m 644 "$SOURCE_DIR/renew.timer" /etc/systemd/system/ifcm-workbooks-cert-renew.timer
systemctl daemon-reload
systemctl enable --now ifcm-workbooks-cert-renew.timer >/dev/null
systemctl is-active --quiet ifcm-workbooks-cert-renew.timer
systemctl start ifcm-workbooks-cert-renew.service
test "$(systemctl show --property Result --value ifcm-workbooks-cert-renew.service)" = success
certbot_run "$TLS/letsencrypt" renew --dry-run --cert-name ifcm-workbooks-ip --quiet
printf 'https://%s\n' "$IP" > "$TLS/origin"
printf '%s\n' "$BACKUP" > "$TLS/last-backup"
rm -f "$TLS/acme/.well-known/acme-challenge/$TOKEN"
SUCCESS=1
printf 'HTTPS enabled: https://%s/v4/index.html\n' "$IP"
openssl x509 -in "$CERT/fullchain.pem" -noout -issuer -enddate
printf 'Automatic renewal timer is active; renewal dry-run passed.\n'
