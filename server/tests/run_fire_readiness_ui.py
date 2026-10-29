"""Isolated regression fixture: six reading marks, two unvisited smoke scenarios."""
import os, sys, tempfile, json
from pathlib import Path
root=Path(__file__).resolve().parents[2]
stage=Path(tempfile.mkdtemp(prefix='ifcm-fire-readiness-'))
for k,v in {'WORKBOOK_PERSONAL_ACCOUNTS':'0','WORKBOOK_ACCESS_CODE':'local-demo','WORKBOOK_TOKEN_SECRET':'readiness-test-only','WORKBOOK_ADMIN_KEY':'test-only','WORKBOOK_ADMIN_LOGIN':'test-author','WORKBOOK_ADMIN_PASSWORD':'test-password','WORKBOOK_DB_PATH':str(stage/'db.sqlite3'),'WORKBOOK_STATIC_DIR':str(root),'WORKBOOK_FIRE_ENVELOPE':str(stage/'unused.enc')}.items():os.environ[k]=v
sys.path[:0]=[str(root/'server'),str(root/'server/tests')]
import app
from fastapi.testclient import TestClient
from test_fire_access import provision, bearer
from test_fire_builder import full
with TestClient(app.app) as c:
 provision(app,stage)
 a=bearer(c.post('/api/v1/fire/admin/session',json={'login':'pb-test-admin','password':'pb-test-password'}))
 d=c.post('/api/v1/fire/builder/import',headers=a,json={}).json()
 e=c.post('/api/v1/fire/builder/drafts/'+d['id']+'/publish',headers=a,json={'revision':d['revision']}).json()
 session=c.post('/api/v1/fire/session',json={'full_name':'Учебный Тест Допуска','code':'pb-test-code'}).json()
 s=full(d['content'])
 del s['answers']['smoke2'];del s['answers']['smoke3']
 r=c.put('/api/v1/fire/builder/editions/'+e['id']+'/progress',headers={'Authorization':'Bearer '+session['token']},json={'state':s})
 assert r.status_code==200,r.text
 assert r.json()['summary']['percent']==68
 assert c.post('/api/v1/fire/builder/editions/'+e['id']+'/submit',headers={'Authorization':'Bearer '+session['token']}).status_code==409
 Path('/tmp/fire-readiness-fixture.json').write_text(json.dumps({'edition':e['id'],'session':session}))
import uvicorn
uvicorn.run(app.app,host='127.0.0.1',port=8125,log_level='warning')
