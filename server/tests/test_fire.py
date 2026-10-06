import copy
import json
from test_builder import service

def user(c,name='Иванова Анна Сергеевна'):
    r=c.post('/api/v1/session',json={'code':'test-code','full_name':name,'course_id':'fire-safety-v4'})
    assert r.status_code==200,r.text
    return {'Authorization':'Bearer '+r.json()['token']}

def full_state(m):
    d=m.fire.DATA
    qs=[q for x in d['modules'] for q in x['quiz']+x['games']]
    return dict(read={x['id']:True for x in d['modules']},answers={q['id']:q['answer'] for q in qs},first={q['id']:q['answer'] for q in qs},notes={},history=[],exam={q['id']:q['answer'] for q in d['final']},profile={'unit':'Игарка'})

def save(c,h,s):return c.put('/api/v1/progress/fire-safety-v4',headers=h,json={'state':s,'summary':{'percent':100,'points':999999}})

def test_grade_freeze_restart_and_permissions(service):
    c,m,admin=service; h=user(c);s=full_state(m)
    assert c.get('/api/v1/fire/results',headers=h).status_code==403
    assert c.put('/api/v1/fire/settings',headers=h,json={'address':'x'}).status_code==403
    assert c.post('/api/v1/fire/submit',headers=h,json={}).status_code==409
    r=save(c,h,s);assert r.status_code==200,r.text
    assert r.json()['summary']['percent']==72
    assert r.json()['summary']['points']==320
    # Client cannot mark a test submitted or set its own threshold.
    s.update(submitted=True,examThreshold=1)
    r=save(c,h,s);assert not r.json()['summary']['submitted']
    r=c.post('/api/v1/fire/submit',headers=h,json={});assert r.status_code==200,r.text
    assert r.json()['summary']['percent']==100 and r.json()['summary']['passed']
    frozen=r.json()['state'];changed=copy.deepcopy(frozen);changed['exam']['x1']=1
    r=save(c,h,changed);assert r.json()['summary']['examCorrect']==15
    assert c.put('/api/v1/fire/settings',headers=admin,json={'passPercent':100}).status_code==200
    row=c.get('/api/v1/fire/results',headers=admin).json()['rows'][0]
    assert row['state']['examThreshold']==80
    r=c.put('/api/v1/fire/review',headers=admin,json={'user_id':row['user_id'],'attempt':1,'comment':'Повторить очно','practical':True});assert r.status_code==200
    assert c.get('/api/v1/fire/results',headers=admin).json()['rows'][0]['review']['practical']
    assert c.post('/api/v1/progress/fire-safety-v4/restart',headers=h).json()['attempt_no']==2
    assert len(c.get('/api/v1/fire/results',headers=admin).json()['rows'])==2
    # Other learner has no access to first learner's progress.
    h2=user(c,'Петров Иван Иванович');r=c.get('/api/v1/progress/fire-safety-v4',headers=h2)
    assert r.json()['summary']['points']==0

def test_critical_errors_validation_and_score(service):
    c,m,a=service;h=user(c);s=full_state(m);s['exam']['x1']=1
    assert save(c,h,s).status_code==200
    r=c.post('/api/v1/fire/submit',headers=h,json={});assert r.status_code==200
    assert r.json()['summary']['examPercent']==93
    assert r.json()['summary']['criticalErrors']==1 and not r.json()['summary']['passed']
    bad=full_state(m);bad['answers']['p1']=999
    assert save(c,h,bad).status_code==422
    bad=full_state(m);bad['answers']['hazards']=[0,0]
    assert save(c,h,bad).status_code==422
    assert c.get('/api/v1/admin/dashboard',headers=a).status_code==200
    dirs={r['course_id']:r['course_direction'] for r in c.get('/api/v1/admin/dashboard',headers=a).json()['courses']}
    assert dirs['fire-safety-v4']=='Безопасность'
