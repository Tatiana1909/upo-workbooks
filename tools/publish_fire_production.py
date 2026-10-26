"""Idempotent production content publication; never imports staging results."""
import json,hashlib,urllib.request,urllib.error,sqlite3,tarfile,tempfile
from pathlib import Path
import app
P='/api/v1/fire/builder'
assert app.fire_access.config,'Fire access must be configured'
token=app.encode_token('fire-admin',app.fire_access.config['login'],'admin',120,scope=app.fire.CID)
headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'}
def call(path,data=None):
    req=urllib.request.Request('http://127.0.0.1:8080'+path,data=None if data is None else json.dumps(data).encode(),headers=headers)
    return json.load(urllib.request.urlopen(req))
# Deployment/import only adds scoped constructor tables. Existing records must stay unchanged.
manifest_path=Path('/data/fire-production-restore-manifest')
if manifest_path.exists():
    manifest=json.loads(Path(manifest_path.read_text().strip()).read_text())
    # Canonical SQLite tuples; compare previous rows and allow newly published editions.
    with tempfile.TemporaryDirectory() as temp:
        restored=Path(temp)/'before.sqlite3'
        with tarfile.open(manifest['archive']) as archive:restored.write_bytes(archive.extractfile('data/workbooks.sqlite3').read())
        with sqlite3.connect(restored) as old,sqlite3.connect(app.DB_PATH) as current:
            assert current.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
            for (table,) in old.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
                columns=[r[1] for r in old.execute('PRAGMA table_info("'+table+'")')]
                quoted=','.join('"'+c+'"' for c in columns)
                for row in old.execute('SELECT rowid,'+quoted+' FROM "'+table+'"'):
                    actual=current.execute('SELECT '+quoted+' FROM "'+table+'" WHERE rowid=?',(row[0],)).fetchone()
                    assert actual==row[1:],'Previous row changed during deployment: '+table
    print('Existing production tables unchanged after deployment: OK')
draft=call(P+'/import',{})
editions=call(P+'/editions')
if not editions:
    result=call(P+'/drafts/'+draft['id']+'/publish',{'revision':draft['revision']})
else:
    result={'id':editions[0]['id'],'version':editions[0]['version'],'url':'/v4/fire/editable.html?edition='+editions[0]['id']}
# Verify scope and private answer keys without creating any learner account or progress.
assert isinstance(call(P+'/drafts'),list)
try:
    call('/api/v1/admin/dashboard');raise AssertionError('Fire teacher gained warehouse access')
except urllib.error.HTTPError as e:assert e.code==403
public=json.load(urllib.request.urlopen('http://127.0.0.1:8080'+P+'/editions/'+result['id']))
assert all('answer' not in q for q in public['content']['final'])
assert any(x['id']==result['id'] for x in call(P+'/catalog'))
print('Production fire edition:',json.dumps(result,ensure_ascii=False))
print('Fire scope and private exam keys: OK')
