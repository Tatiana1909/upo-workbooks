"""Runs INSIDE the isolated restored container. Never prints personal data."""
import hashlib
import json
import secrets
import sys
from pathlib import Path
from urllib.request import Request, build_opener, ProxyHandler
sys.path.insert(0, '/app')
import app

opener = build_opener(ProxyHandler({}))

def call(path, body=None, token=None):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    request = Request('http://127.0.0.1:8080' + path, headers=headers,
                      data=json.dumps(body).encode() if body is not None else None)
    with opener.open(request, timeout=15) as response:
        data = response.read()
        return json.loads(data) if 'application/json' in response.headers.get('Content-Type', '') else data


def main():
    if call('/health').get('status') != 'ok':
        raise RuntimeError('Restored API health check failed')
    password = secrets.token_urlsafe(24)
    with app.db() as c:
        users = c.execute('SELECT COUNT(*) FROM users').fetchone()[0]
        progress = c.execute('SELECT COUNT(*) FROM progress').fetchone()[0]
        test = app.accounts.create(c, {'last_name': 'Проверка', 'first_name': 'восстановления', 'no_patronymic': True,
               'unit': 'Техническая проверка', 'no_personnel_number': True, 'password': password,
               'roles': ['admin'], 'realms': ['upo', 'fire'], 'permissions': ['view']}, 'restore_probe', None)
        c.commit()
    token = call('/api/v1/accounts/session', {'login': test['login'], 'password': password})['token']
    dashboard = call('/api/v1/admin/dashboard', token=token)
    if dashboard['kpi']['users_total'] != users or len(dashboard['records']) != progress:
        raise RuntimeError('Restored results do not match the database')
    if not call('/v4/index.html'):
        raise RuntimeError('Restored learning page is missing')
    # Verify real file-serving permissions/bytes, where evidence already exists.
    with app.db() as c:
        for table, folder, endpoint in [('builder_evidence', 'builder-evidence', '/api/v1/builder/evidence/'),
                                        ('fb_evidence', 'fire-builder-evidence', '/api/v1/fire/builder/evidence/')]:
            r = c.execute('SELECT id,extension FROM '+table+' LIMIT 1').fetchone()
            if r:
                expected = (Path('/data') / folder / (r['id'] + '.' + r['extension'])).read_bytes()
                actual = call(endpoint + r['id'], token=token)
                if hashlib.sha256(actual).digest() != hashlib.sha256(expected).digest():
                    raise RuntimeError('Restored evidence is unreadable')
    print(json.dumps({'isolated_api_restore': True, 'results_readable': True, 'evidence_checked': True}))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print(json.dumps({'isolated_api_restore': False}))
        raise SystemExit(1)
