#!/usr/bin/env bash
set -euo pipefail
umask 077
exec 9>/run/lock/ifcm-workbooks-cert-renew.lock
flock -n 9 || exit 0
TLS=/opt/upo-workbooks/https
IMAGE=$(cat "$TLS/certbot-image")
docker run --rm --network host \
  -v "$TLS/letsencrypt:/etc/letsencrypt" -v "$TLS/acme:/var/www/acme" \
  -v "$TLS/certbot-logs:/var/log/letsencrypt" "$IMAGE" \
  renew --quiet --cert-name ifcm-workbooks-ip
docker exec workbook_https nginx -t
docker exec workbook_https nginx -s reload
