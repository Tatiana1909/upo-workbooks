"""Gaps in the twenty product acceptance scenarios; synthetic data only."""
import base64
import csv
import io
from test_builder import service, publish, login, content
from test_accounts import admin, create, headers
from test_fire import full_state, save, user
from test_fire_access import provision


def test_s02_personal_relogin_preserves_answers_checks_and_notes(service, monkeypatch):
    c,m,legacy=service
    _,cid=publish(c,legacy)
    a=admin(c)
    monkeypatch.setenv('WORKBOOK_PERSONAL_ACCOUNTS','1')
    p=create(c,a,password='acceptance-password',courses=[cid])
    def enter():return headers(c.post('/api/v1/accounts/session',json={'login':p['login'],'password':'acceptance-password'}))
    h=enter()
    state={'answers':{'q':[0],'o':'Объяснение'},'checks':{'p':True},'notes':{'section':'Заметка приёмки'}}
    assert c.put('/api/v1/progress/'+cid,headers=h,json={'state':state,'summary':{}}).status_code==200
    before=c.get('/api/v1/progress/'+cid,headers=h).json()
    assert c.post('/api/v1/accounts/logout',headers=h).status_code==200
    assert c.get('/api/v1/progress/'+cid,headers=h).status_code==401
    after=c.get('/api/v1/progress/'+cid,headers=enter()).json()
    for key in ('answers','checks','notes'):assert before['state'][key]==after['state'][key]==state[key]


def test_s04_s05_personal_realms_and_legacy_code_rejection(service,monkeypatch,tmp_path):
    c,m,legacy=service
    a=admin(c)
    provision(m,tmp_path)
    monkeypatch.setenv('WORKBOOK_PERSONAL_ACCOUNTS','1')
    fire=create(c,a,password='fire-password',roles=['teacher'],realms=['fire'],units=['Игарка'],permissions=['view','review'])
    h=headers(c.post('/api/v1/accounts/session',json={'login':fire['login'],'password':'fire-password'}))
    assert c.get('/api/v1/fire/results',headers=h).status_code==200
    for route in ('admin/dashboard','builder/drafts','admin/results.csv'):
        assert c.get('/api/v1/'+route,headers=h).status_code==403
    assert c.post('/api/v1/fire/session',json={'code':'test-code','full_name':'Тест Приёмки'}).status_code==401
    assert c.get('/api/v1/admin/dashboard',headers=a).status_code==200


def test_s06_s09_fire_incomplete_exam_settings_and_comment(service):
    c,m,a=service
    h=user(c)
    assert c.put('/api/v1/fire/settings',headers=a,json={'address':'Учебный объект','passPercent':80}).status_code==200
    assert c.get('/api/v1/fire/settings',headers=h).json()['address']=='Учебный объект'
    state=full_state(m);state['exam'].pop('x15')
    assert save(c,h,state).status_code==200
    assert c.post('/api/v1/fire/submit',headers=h,json={}).status_code==422
    uid=m.decode_token(h['Authorization'][7:])['sub']
    assert c.put('/api/v1/fire/review',headers=a,json={'user_id':uid,'attempt':1,'comment':'Повторить практику','practical':True}).status_code==200
    row=c.get('/api/v1/fire/results',headers=a).json()['rows'][0]
    assert row['review']['comment']=='Повторить практику' and row['review']['practical']


def test_s11_materials_copy_and_author_preview(service):
    c,m,a=service
    body=content();body['sections'][0]['blocks'].append({'id':'instruction','type':'link','text':'Инструкция','url':'https://example.org/instruction'})
    draft=c.post('/api/v1/builder/drafts',headers=a,json={'content':body}).json()
    got=c.get('/api/v1/builder/drafts/'+draft['id']+'/preview',headers=a)
    assert got.status_code==200
    assert got.json()['content']['title']==body['title']
    assert got.json()['content']['sections']==body['sections']
    assert got.json()['content']['courseDirection']=='1С:УПО'
    copied=c.post('/api/v1/builder/drafts',headers=a,json={'content':got.json()['content']})
    assert copied.status_code==200 and copied.json()['id']!=draft['id']


def test_s14_open_answer_manual_score_and_s18_upo_csv(service):
    c,m,a=service
    _,cid=publish(c,a)
    h=login(c,cid);uid=m.decode_token(h['Authorization'][7:])['sub']
    assert c.put('/api/v1/progress/'+cid,headers=h,json={'state':{'answers':{'o':'Ответ для проверки'}},'summary':{'points':999}}).json()['summary']['points']==0
    row=c.get(f'/api/v1/builder/reviews/{uid}/{cid}/1',headers=a).json()
    assert c.put(f'/api/v1/builder/reviews/{uid}/{cid}/1/o',headers=a,json={'checked':True,'points':40,'comment':'Ответ принят','updated_at':row['updated_at']}).status_code==200
    assert c.get('/api/v1/progress/'+cid,headers=h).json()['summary']['points']==40
    assert c.get('/api/v1/workbooks/'+cid+'/feedback',headers=h).json()['o']['comment']=='Ответ принят'
    fh=user(c);save(c,fh,full_state(m))
    for endpoint in ('/api/v1/admin/results.csv',):
        r=c.get(endpoint,headers=a)
        assert r.status_code==200 and 'csv' in r.headers.get('content-type',''),endpoint
        rows=list(csv.reader(io.StringIO(r.content.decode('utf-8-sig')),delimiter=';'))
        assert len(rows)>=2 and len(rows[0])>3
    exported=c.get('/api/v1/admin/results.csv',headers=a).content.decode('utf-8-sig')
    records=list(csv.DictReader(io.StringIO(exported),delimiter=';'))
    own=[r for r in records if r['ID участника']==uid]
    assert len(own)==1
    assert own[0]['Курс']=='Тестовая тетрадь · версия 1'
    assert own[0]['Баллы']=='40'
