import os,sys,json
from pathlib import Path
root=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(root/'server'),str(root/'server/tests')]
base=Path(os.environ.get('PB_UI_DATA','/tmp/pb-ui-data'));base.mkdir(exist_ok=True)
os.environ.update(WORKBOOK_FIRE_ENVELOPE='/tmp/pb-ui-no-envelope',WORKBOOK_ACCESS_CODE='test-code',WORKBOOK_TOKEN_SECRET='ui-test-secret',WORKBOOK_ADMIN_KEY='ui-key',WORKBOOK_ADMIN_LOGIN='test-author',WORKBOOK_ADMIN_PASSWORD='test-password',WORKBOOK_DB_PATH=str(base/'db.sqlite3'),WORKBOOK_STATIC_DIR=str(root))
import app
from test_fire_access import provision
from fastapi.testclient import TestClient
with TestClient(app.app) as client:
 provision(app,base)
 h={'Authorization':'Bearer '+client.post('/api/v1/fire/admin/session',json={'login':'pb-test-admin','password':'pb-test-password'}).json()['token']}
 d=client.post('/api/v1/fire/builder/import',headers=h,json={}).json()
 e=client.post('/api/v1/fire/builder/drafts/'+d['id']+'/publish',headers=h,json={'revision':d['revision']}).json()
 print(json.dumps(e))
import uvicorn
uvicorn.run(app.app,host='127.0.0.1',port=8123)
