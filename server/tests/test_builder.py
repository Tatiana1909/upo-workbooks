import base64
import importlib
import json
import sys
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

@pytest.fixture
def service(tmp_path,monkeypatch):
    monkeypatch.setenv('WORKBOOK_FIRE_ENVELOPE', str(tmp_path/'unused-envelope.enc'))
    for k,v in {'WORKBOOK_ACCESS_CODE':'test-code','WORKBOOK_TOKEN_SECRET':'local-test-secret','WORKBOOK_ADMIN_KEY':'test-key','WORKBOOK_ADMIN_LOGIN':'test-author','WORKBOOK_ADMIN_PASSWORD':'test-password','WORKBOOK_DB_PATH':str(tmp_path/'db.sqlite3'),'WORKBOOK_STATIC_DIR':str(Path(__file__).resolve().parents[2])}.items():monkeypatch.setenv(k,v)
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]));sys.modules.pop('app',None)
    m=importlib.import_module('app')
    with TestClient(m.app) as c:
        admin=c.post('/api/v1/admin/session',json={'login':'test-author','password':'test-password'}).json()['token']
        yield c,m,{'Authorization':'Bearer '+admin}

def content():return {'title':'Тестовая тетрадь','reviewMode':'auto','sections':[{'title':'День 1','blocks':[{'id':'q','type':'single','text':'Вопрос','options':['Да','Нет'],'correct':[0]},{'id':'p','type':'practice','text':'Сделайте скриншот','evidenceRequired':True,'maxImages':2,'reviewMode':'manual'},{'id':'o','type':'open','text':'Объясните','reviewMode':'manual'}]}]}

def publish(c,h,body=None):
    d=c.post('/api/v1/builder/drafts',headers=h,json={'content':body or content()}).json()
    r=c.post('/api/v1/builder/drafts/'+d['id']+'/publish',headers=h,json={'revision':d['revision']})
    assert r.status_code==200,r.text
    return d,r.json()['course_id']

def login(c,cid,name='Иванов Иван'):
    r=c.post('/api/v1/session',json={'code':'test-code','full_name':name,'course_id':cid});assert r.status_code==200,r.text
    return {'Authorization':'Bearer '+r.json()['token']}

def test_versions_and_permissions(service):
    c,m,h=service
    assert c.get('/api/v1/builder/drafts').status_code==401
    d,cid=publish(c,h)
    public=c.get('/api/v1/workbooks/'+cid).json();assert 'correct' not in public['content']['sections'][0]['blocks'][0]
    modified=content();modified['title']='Новая версия';modified['sections'][0]['blocks'][0]['correct']=[1]
    assert c.put('/api/v1/builder/drafts/'+d['id'],headers=h,json={'revision':1,'content':modified}).status_code==200
    assert c.put('/api/v1/builder/drafts/'+d['id'],headers=h,json={'revision':1,'content':modified}).status_code==409
    second=c.post('/api/v1/builder/drafts/'+d['id']+'/publish',headers=h,json={'revision':2}).json()['course_id']
    assert second!=cid;assert c.get('/api/v1/workbooks/'+cid).json()['content']['title']=='Тестовая тетрадь'
    c.post('/api/v1/builder/editions/'+cid+'/archive',headers=h)
    assert cid not in [r['course_id'] for r in c.get('/api/v1/workbooks').json()]
    assert c.get('/api/v1/workbooks/'+cid).status_code==200

def test_evidence_review_attempts(service):
    c,m,h=service;_,cid=publish(c,h);student=login(c,cid)
    state={'answers':{'q':[0],'o':'Объяснение'},'checks':{'p':True},'notes':{},'done':{},'evidence':{}}
    r=c.put('/api/v1/progress/'+cid,headers=student,json={'state':state,'summary':{'points':9999}});assert r.status_code==200,r.text
    assert r.json()['summary']['points']==10;assert r.json()['summary']['percent']==67
    image=c.post(f'/api/v1/workbooks/{cid}/evidence/p',headers=student,json={'data':base64.b64encode(b'\x89PNG\r\n\x1a\nlocal-fixture').decode()});assert image.status_code==200,image.text
    iid=image.json()['id'];state['evidence']['p']=[iid]
    assert c.get('/api/v1/builder/evidence/'+iid).status_code==401
    other=login(c,cid,'Петров Пётр');assert c.get('/api/v1/builder/evidence/'+iid,headers=other).status_code==403
    assert c.get('/api/v1/builder/evidence/'+iid,headers=h).status_code==200
    r=c.put('/api/v1/progress/'+cid,headers=student,json={'state':state,'summary':{}});assert r.status_code==200,r.text
    assert r.json()['summary']['percent']==100
    uid=m.decode_token(student['Authorization'][7:])['sub'];p=c.get(f'/api/v1/builder/reviews/{uid}/{cid}/1',headers=h).json()
    review={'points':25,'checked':True,'comment':'Хорошо выполнено','updated_at':p['updated_at']}
    r=c.put(f'/api/v1/builder/reviews/{uid}/{cid}/1/p',headers=h,json=review);assert r.status_code==200,r.text
    assert r.json()['summary']['points']==35;assert r.json()['summary']['pendingReview']==1
    assert c.get(f'/api/v1/workbooks/{cid}/feedback',headers=student).json()['p']['comment']=='Хорошо выполнено'
    # Unchanged student save preserves manual points.
    r=c.put('/api/v1/progress/'+cid,headers=student,json={'state':state,'summary':{}});assert r.json()['summary']['points']==35
    assert c.put(f'/api/v1/builder/reviews/{uid}/{cid}/1/p',headers=h,json=review).status_code==409
    # Changed evidence is visibly marked for recheck; old feedback is retained.
    state['checks']['p']=False
    changed=c.put('/api/v1/progress/'+cid,headers=student,json={'state':state,'summary':{}})
    assert changed.json()['summary']['needsRecheck']==1
    feedback=c.get(f'/api/v1/workbooks/{cid}/feedback',headers=student).json()['p']
    assert feedback['needs_recheck'] is True and feedback['points']==25
    assert feedback['comment']=='Хорошо выполнено'
    state['checks']['p']=True
    c.put('/api/v1/progress/'+cid,headers=student,json={'state':state,'summary':{}})
    fresh=c.get(f'/api/v1/builder/reviews/{uid}/{cid}/1',headers=h).json()
    review['updated_at']=fresh['updated_at']
    c.put(f'/api/v1/builder/reviews/{uid}/{cid}/1/p',headers=h,json=review)
    assert c.get('/api/v1/progress/'+cid,headers=student).json()['summary']['needsRecheck']==0
    # Restart preserves first attempt and blocks reusing images in a different attempt.
    c.post('/api/v1/progress/'+cid+'/restart',headers=student)
    assert c.put('/api/v1/progress/'+cid,headers=student,json={'state':state,'summary':{}}).status_code==422
    detail=c.get('/api/v1/admin/users/'+uid,headers=h).json();assert len(detail['courses'][0]['attempts'])==2
    assert detail['courses'][0]['attempts'][0]['summary']['points']==35

def test_invalid_and_existing_courses(service):
    c,m,h=service;b=content();b['sections'][0]['blocks'].append({'id':'x','type':'link','text':'Инструкция','url':'javascript:alert(1)'})
    assert c.post('/api/v1/builder/drafts',headers=h,json={'content':b}).status_code==422
    student=login(c,'accounting');r=c.put('/api/v1/progress/accounting',headers=student,json={'state':{'answers':{}},'summary':{'percent':50,'points':20}})
    assert r.status_code==200;assert r.json()['summary']['points']==0  # built-in scoring now ignores client-provided points
    assert c.get('/v3/index.html').status_code==200

def test_video_blocks(service):
    c,m,h=service;b=content();b['sections'][0]['blocks'] += [
        {'id':'video-link','type':'video','text':'Видео на диске','url':'https://disk.yandex.ru/i/example','videoMode':'link'},
        {'id':'video-inline','type':'video','text':'Видео в тетради','url':'https://example.org/video.mp4','videoMode':'inline'}]
    _,cid=publish(c,h,b)
    blocks=c.get('/api/v1/workbooks/'+cid).json()['content']['sections'][0]['blocks']
    assert blocks[-1]['videoMode']=='inline'
    b['sections'][0]['blocks'][-1]['url']='javascript:alert(1)'
    assert c.post('/api/v1/builder/drafts',headers=h,json={'content':b}).status_code==422

def test_catalog_directions_and_safe_metadata(service):
    c,m,h=service
    _,default_id=publish(c,h)
    body=content();body['courseDirection']='Складская логистика';body['description']='Учебные материалы'
    _,other_id=publish(c,h,body)
    rows={r['course_id']:r for r in c.get('/api/v1/workbooks').json()}
    assert rows[default_id]['course_direction']=='1С:УПО'
    assert rows[other_id]['course_direction']=='Складская логистика'
    assert rows[other_id]['description']=='Учебные материалы'
    assert 'content' not in rows[other_id]
    body['courseDirection']=42
    assert c.post('/api/v1/builder/drafts',headers=h,json={'content':body}).status_code==422

    c.post('/api/v1/builder/editions/'+other_id+'/archive',headers=h)
    courses={r['course_id']:r for r in c.get('/api/v1/admin/dashboard',headers=h).json()['courses']}
    assert courses[other_id]['course_direction']=='Складская логистика'

def test_trash_restore_and_history(service):
    c,m,h=service;d,cid=publish(c,h);student=login(c,cid)
    assert c.put('/api/v1/progress/'+cid,headers=student,json={'state':{'answers':{'q':[0]}},'summary':{}}).status_code==200
    uid=m.decode_token(student['Authorization'][7:])['sub']
    assert c.delete('/api/v1/builder/drafts/'+d['id']+'?revision=1').status_code==401
    assert c.get('/api/v1/builder/trash',headers=student).status_code==403
    assert c.delete('/api/v1/builder/drafts/'+d['id']+'?revision=0',headers=h).status_code==409
    assert c.delete('/api/v1/builder/drafts/'+d['id']+'?revision=1',headers=h).status_code==200
    assert c.get('/api/v1/builder/drafts',headers=h).json()==[]
    assert c.get('/api/v1/workbooks/'+cid).status_code==200
    assert c.put('/api/v1/builder/drafts/'+d['id'],headers=h,json={'content':content(),'revision':2}).status_code==409
    assert c.post('/api/v1/builder/drafts/'+d['id']+'/publish',headers=h,json={'revision':2}).status_code==409
    assert c.delete('/api/v1/builder/editions/'+cid,headers=h).status_code==200
    assert c.get('/api/v1/workbooks/'+cid).status_code==410
    assert c.get('/api/v1/workbooks').json()==[]
    trash=c.get('/api/v1/builder/trash',headers=h).json()
    assert trash['drafts'][0]['id']==d['id'] and trash['editions'][0]['course_id']==cid
    review=c.get(f'/api/v1/builder/reviews/{uid}/{cid}/1',headers=h)
    assert review.status_code==200 and review.json()['summary']['points']==10
    assert c.post('/api/v1/builder/editions/'+cid+'/restore',headers=h).status_code==200
    assert c.get('/api/v1/workbooks/'+cid).status_code==200
    assert c.get('/api/v1/admin/users/'+uid,headers=h).json()['courses'][0]['attempts'][0]['summary']['points']==10
    assert c.post('/api/v1/builder/drafts/'+d['id']+'/restore',headers=h).status_code==200
    restored=c.get('/api/v1/builder/drafts',headers=h).json()[0]
    assert restored['revision']==3
    # Restoring an archived edition keeps it hidden from the student catalog.
    c.post('/api/v1/builder/editions/'+cid+'/archive',headers=h)
    c.delete('/api/v1/builder/editions/'+cid,headers=h)
    c.post('/api/v1/builder/editions/'+cid+'/restore',headers=h)
    assert c.get('/api/v1/workbooks').json()==[]
    assert c.get('/api/v1/builder/editions',headers=h).json()[0]['archived']==1
