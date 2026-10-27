FROM python:3.12-slim
WORKDIR /app
COPY server/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server/verify_accounts.py server/account_admin.py server/accounts.py server/app.py server/builder.py server/fire.py server/fire_access.py server/games.py server/fire_builder.py server/full_backup.py server/backup_bundle.py server/release_bootstrap.py server/verify_legacy.py server/material_links.py ./
COPY server/upo-templates.json server/material-links-20261028.json ./
COPY courses.js /app/static/courses.js
COPY assets /app/static/assets
COPY v2 /app/static/v2
COPY v3 /app/static/v3
COPY v4 /app/static/v4
COPY builder /app/static/builder
RUN mkdir -p /data
EXPOSE 8080
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "*"]
