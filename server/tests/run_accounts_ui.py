import os,sys,tempfile
from pathlib import Path
root=Path(__file__).resolve().parents[2]
stage=Path(tempfile.mkdtemp(prefix='ifcm-account-stage-'))
for k,v in {'WORKBOOK_ACCESS_CODE':'local-demo-code','WORKBOOK_TOKEN_SECRET':'local-demo-secret-not-production','WORKBOOK_ADMIN_KEY':'local-demo-key','WORKBOOK_ADMIN_LOGIN':'demo-admin','WORKBOOK_ADMIN_PASSWORD':'demo-password-123','WORKBOOK_DB_PATH':str(stage/'db.sqlite3'),'WORKBOOK_STATIC_DIR':str(root),'WORKBOOK_FIRE_ENVELOPE':str(stage/'unused.enc'),'WORKBOOK_PERSONAL_ACCOUNTS':'1'}.items():os.environ[k]=v
sys.path.insert(0,str(root/'server'))
import app
from fastapi.testclient import TestClient
with TestClient(app.app) as c:
 h={'Authorization':'Bearer '+c.post('/api/v1/accounts/session',json={'login':'demo-admin','password':'demo-password-123'}).json()['token']}
 c.post('/api/v1/accounts/password',headers=h,json={'current_password':'demo-password-123','new_password':'demo-changed-123','password_repeat':'demo-changed-123'})
 token=c.post('/api/v1/accounts/session',json={'login':'demo-admin','password':'demo-changed-123'}).json()['token']
 h={'Authorization':'Bearer '+token}
 c.post('/api/v1/accounts/units',headers=h,json={'name':'Игарка'})
 d=c.post('/api/v1/builder/drafts',headers=h,json={'content':{'title':'Учебная тетрадь для проверки','sections':[{'title':'Раздел','blocks':[{'id':'q','type':'single','text':'Вопрос','options':['Да','Нет'],'correct':[0]}]}]}}).json()
 cid=c.post('/api/v1/builder/drafts/'+d['id']+'/publish',headers=h,json={'revision':d['revision']}).json()['course_id']
 app.app.state.demo_course=cid
import uvicorn
uvicorn.run(app.app,host='127.0.0.1',port=8124,log_level='warning')
