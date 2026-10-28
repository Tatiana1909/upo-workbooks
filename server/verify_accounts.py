"""Read-only checks after deploying personal identities. Prints no credentials."""
import json
import sqlite3
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import app


def verify():
    with sqlite3.connect(app.DB_PATH) as c:
        assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert c.execute("SELECT COUNT(*) FROM identity_accounts WHERE status='active' AND roles LIKE '%admin%'").fetchone()[0]>=1
    for path in ('/health','/v4/accounts.html','/v4/accounts.js','/v4/accounts.css','/v4/session-exit.js'):
        with urlopen('http://127.0.0.1:8080'+path,timeout=10) as r:assert r.status==200
    for path in ('/api/v1/accounts','/api/v1/accounts/audit/log'):
        try:
            urlopen('http://127.0.0.1:8080'+path,timeout=10)
            raise AssertionError('Anonymous account management must be denied')
        except HTTPError as e:assert e.code in (401,403)
    if __import__('os').environ.get('WORKBOOK_PERSONAL_ACCOUNTS','1')=='1':
        token=app.encode_token('admin',app.ADMIN_LOGIN,'admin',60)
        try:
            urlopen(Request('http://127.0.0.1:8080/api/v1/admin/dashboard',headers={'Authorization':'Bearer '+token}),timeout=10)
            raise AssertionError('Legacy administrator session must be denied')
        except HTTPError as e:assert e.code in (401,403)
    assert app.fire_access.config, 'Separate fire staff credentials must be configured'
    token=app.encode_token('fire-admin',app.fire_access.config['login'],'admin',60,scope=app.fire.CID)
    with urlopen(Request('http://127.0.0.1:8080/api/v1/fire/builder/drafts',headers={'Authorization':'Bearer '+token}),timeout=10) as r:
        assert r.status==200
    try:
        urlopen(Request('http://127.0.0.1:8080/api/v1/admin/dashboard',headers={'Authorization':'Bearer '+token}),timeout=10)
        raise AssertionError('Fire staff must not access UPO administration')
    except HTTPError as e:assert e.code in (401,403)
    print(json.dumps({'personal_accounts_ready':True,'fire_staff_separate_ready':True,'database_integrity':'ok','anonymous_management_denied':True}))

if __name__=='__main__':verify()
