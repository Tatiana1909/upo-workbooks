import io
import json
from contextlib import closing
import pytest
from test_builder import service, publish


def headers(r):
    assert r.status_code==200,r.text
    return {'Authorization':'Bearer '+r.json()['token']}


def admin(c):
    h=headers(c.post('/api/v1/accounts/session',json={'login':'test-author','password':'test-password'}))
    r=c.post('/api/v1/accounts/password',headers=h,json={'current_password':'test-password','new_password':'new-test-password','password_repeat':'new-test-password'})
    assert r.status_code==200,r.text
    return headers(c.post('/api/v1/accounts/session',json={'login':'test-author','password':'new-test-password'}))


def person(**extra):
    return dict(last_name='Иванова',first_name='Анна',patronymic='Ивановна',unit='Игарка',position='Кладовщик',no_personnel_number=True,**extra)


def create(c,h,**extra):
    r=c.post('/api/v1/accounts',headers=h,json=person(**extra));assert r.status_code==200,r.text
    return r.json()


def test_bootstrap_password_restart_and_no_secrets(service):
    c,m,_=service
    h=admin(c)
    m.accounts.init()
    assert c.post('/api/v1/accounts/session',json={'login':'test-author','password':'test-password'}).status_code==401
    rows=c.get('/api/v1/accounts',headers=h).json()
    assert len(rows)==1 and 'password_hash' not in rows[0]
    log=c.get('/api/v1/accounts/audit/log',headers=h).text
    assert 'new-test-password' not in log and 'password_hash' not in log


def test_self_registration_invite_and_assignment(service):
    c,m,legacy=service;_,cid=publish(c,legacy);h=admin(c)
    c.post('/api/v1/accounts/units',headers=h,json={'name':'Игарка'})
    inv=c.post('/api/v1/accounts/invitations/create',headers=h,json={'label':'Группа обучения','unit':'Игарка','courses':[cid],'max_uses':1}).json()
    d={**person(), 'invitation':inv['token'],'password':'my-password-123','password_repeat':'my-password-123', 'roles':['admin'],'login':'injected'}
    r=c.post('/api/v1/accounts/register',json=d);sh=headers(r)
    a=r.json()['account'];assert a['roles']==['participant'] and a['login']!='injected' and not a['must_change']
    assert c.post('/api/v1/accounts/register',json={**d,'last_name':'Петрова'}).status_code==403
    assert c.get('/api/v1/progress/'+cid,headers=sh).status_code==200
    assert c.get('/api/v1/progress/accounting',headers=sh).status_code==403
    assert c.get('/api/v1/accounts',headers=sh).status_code==403
    assert c.get('/api/v1/admin/dashboard',headers=sh).status_code==403
    p=c.put('/api/v1/progress/'+cid,headers=sh,json={'state':{'profile':{'unit':'Подмена'}},'summary':{}})
    assert p.status_code==200,p.text
    assert c.get('/api/v1/progress/'+cid,headers=sh).json()['state']['profile']['unit']=='Игарка'
    assert 'my-password-123' not in c.get('/api/v1/accounts/audit/log',headers=h).text


def test_reset_revokes_sessions_and_requires_change(service):
    c,m,_=service;h=admin(c);a=create(c,h,password='initial-password',courses=['accounting'])
    sh=headers(c.post('/api/v1/accounts/session',json={'login':a['login'],'password':'initial-password'}))
    assert c.get('/api/v1/progress/accounting',headers=sh).status_code==200
    reset=c.post('/api/v1/accounts/'+a['id']+'/reset-password',headers=h).json()['temporary_password']
    assert c.get('/api/v1/progress/accounting',headers=sh).status_code==401
    fresh=headers(c.post('/api/v1/accounts/session',json={'login':a['login'],'password':reset}))
    assert c.get('/api/v1/progress/accounting',headers=fresh).status_code==403
    assert c.post('/api/v1/accounts/password',headers=fresh,json={'current_password':reset,'new_password':'changed-password','password_repeat':'changed-password'}).status_code==200
    assert c.get('/api/v1/accounts/me',headers=fresh).status_code==401
    assert reset not in c.get('/api/v1/accounts/audit/log',headers=h).text


def test_namesakes_and_optional_number_preserve_legacy(service):
    c,m,_=service
    old=headers(c.post('/api/v1/session',json={'code':'test-code','full_name':'Иванова Анна Ивановна','course_id':'accounting'}))
    c.put('/api/v1/progress/accounting',headers=old,json={'state':{'notes':{'x':'Старые данные'}},'summary':{}})
    old_uid=m.decode_token(old['Authorization'][7:])['sub']
    h=admin(c);a=create(c,h,password='initial-password')
    assert c.post('/api/v1/accounts',headers=h,json=person()).status_code==409
    b=create(c,h,confirm_namesake=True)
    assert len({a['id'],b['id'],old_uid})==3
    assert c.get('/api/v1/progress/accounting',headers=old).json()['state']['notes']['x']=='Старые данные'
    r=c.put('/api/v1/accounts/'+a['id'],headers=h,json={'revision':a['revision'],'personnel_number':'000123'})
    assert r.status_code==200 and r.json()['personnel_number']=='000123'
    assert c.put('/api/v1/accounts/'+b['id'],headers=h,json={'revision':b['revision'],'personnel_number':'000123'}).status_code==409
    assert c.put('/api/v1/accounts/'+a['id'],headers=h,json={'revision':a['revision'],'position':'Новая'}).status_code==409


def test_teacher_scopes_accounts_and_learning_data(service):
    c,m,legacy=service;_,cid=publish(c,legacy);h=admin(c)
    teacher=create(c,h,password='teacher-password',roles=['teacher'],realms=['upo'],permissions=['view','review'],units=['Игарка'])
    th=headers(c.post('/api/v1/accounts/session',json={'login':teacher['login'],'password':'teacher-password'}))
    p=c.post('/api/v1/accounts',headers=th,json= {**person(),'last_name':'Сидорова','courses':[cid],'password':'participant-password'})
    assert p.status_code==200,p.text
    participant=p.json();other=c.post('/api/v1/accounts',headers=h,json={**person(),'unit':'Ачинск','last_name':'Петрова','password':'participant-password','courses':[cid]}).json()
    sh=headers(c.post('/api/v1/accounts/session',json={'login':participant['login'],'password':'participant-password'}))
    oh=headers(c.post('/api/v1/accounts/session',json={'login':other['login'],'password':'participant-password'}))
    for auth in (sh,oh):assert c.put('/api/v1/progress/'+cid,headers=auth,json={'state':{},'summary':{}}).status_code==200
    listed=c.get('/api/v1/accounts',headers=th).json();assert {x['id'] for x in listed}=={participant['id']}
    assert c.get('/api/v1/admin/users/'+other['id'],headers=th).status_code==403
    assert c.post('/api/v1/accounts/'+other['id']+'/reset-password',headers=th).status_code==403
    assert c.post('/api/v1/accounts/'+teacher['id']+'/reset-password',headers=th).status_code==409
    assert c.get('/api/v1/fire/results',headers=th).status_code==403
    assert c.post('/api/v1/builder/drafts',headers=th,json={'content':{}}).status_code==403
    dashboard=c.get('/api/v1/admin/dashboard',headers=th)
    assert dashboard.status_code==200,dashboard.text
    assert {x['user_id'] for x in dashboard.json()['records']}=={participant['id']}
    exported=c.get('/api/v1/admin/results.csv',headers=th)
    assert exported.status_code==200 and participant['id'] in exported.text and other['id'] not in exported.text
    assert c.post('/api/v1/accounts',headers=th,json={**person(),'roles':['admin']}).status_code==403


def test_logout_block_last_admin_and_invite_revoke(service):
    c,m,_=service;h=admin(c);me=c.get('/api/v1/accounts/me',headers=h).json()
    assert c.put('/api/v1/accounts/'+me['id'],headers=h,json={'revision':me['revision'],'status':'blocked'}).status_code==409
    inv=c.post('/api/v1/accounts/invitations/create',headers=h,json={'courses':['accounting']}).json()
    assert c.post('/api/v1/accounts/invitations/'+inv['id']+'/revoke',headers=h).status_code==200
    assert c.get('/api/v1/accounts/invitation',params={'token':inv['token']}).status_code==403
    assert c.post('/api/v1/accounts/logout',headers=h).status_code==200
    assert c.get('/api/v1/accounts/me',headers=h).status_code==401


def test_import_preview_transaction_duplicates_and_no_privilege_injection(service):
    c,m,_=service;h=admin(c)
    text='Фамилия;Имя;Отчество;Подразделение;Должность;Табельный номер\nСидорова;Анна;Ивановна;Игарка;Кладовщик;0012\n'
    r=c.post('/api/v1/accounts/import/preview',headers=h,content=text.encode())
    assert r.status_code==200 and r.json()[0]['valid'],r.text
    assert len(c.get('/api/v1/accounts',headers=h).json())==1
    rows=[{**r.json()[0]['data'],'roles':['admin']}]*2
    assert c.post('/api/v1/accounts/import/commit',headers=h,json={'rows':rows}).status_code==409
    assert len(c.get('/api/v1/accounts',headers=h).json())==1
    r=c.post('/api/v1/accounts/import/commit',headers=h,json={'rows':rows[:1]})
    assert r.status_code==200 and r.json()[0]['roles']==['participant']
    assert r.json()[0]['personnel_number']=='0012' and r.json()[0]['must_change']


def test_transition_disables_shared_staff_and_new_course_logins(service,monkeypatch):
    c,m,legacy=service;_,cid=publish(c,legacy);h=admin(c)
    monkeypatch.setenv('WORKBOOK_PERSONAL_ACCOUNTS','1')
    assert c.get('/api/v1/admin/dashboard',headers=legacy).status_code==401
    assert c.post('/api/v1/admin/session',json={'login':'test-author','password':'test-password'}).status_code==401
    assert c.post('/api/v1/session',json={'code':'test-code','full_name':'Иванов Иван','course_id':cid}).status_code==401
    assert c.get('/api/v1/admin/dashboard',headers=h).status_code==200
    assert c.post('/api/v1/session',json={'code':'test-code','full_name':'Иванов Иван','course_id':'accounting'}).status_code==200


def test_fire_teacher_registry_and_course_separation(service):
    c,m,_=service;h=admin(c)
    teacher=create(c,h,roles=['teacher'],realms=['fire'],units=['Игарка'],permissions=['view','review'],password='teacher-password')
    th=headers(c.post('/api/v1/accounts/session',json={'login':teacher['login'],'password':'teacher-password'}))
    assert c.get('/api/v1/accounts',headers=th).status_code==200
    assert c.get('/api/v1/accounts/courses/list',headers=th).status_code==200
    assert c.get('/api/v1/fire/results',headers=th).status_code==200
    assert c.get('/api/v1/admin/dashboard',headers=th).status_code==403
    assert m.accounts.realm('pb-example-v1')=='fire'
    # Staff and participants cannot inject profile data with an invalid type.
    a=c.post('/api/v1/accounts',headers=h,json={**person(),'last_name':'Смирнова','password':'participant-password','courses':['accounting']}).json()
    sh=headers(c.post('/api/v1/accounts/session',json={'login':a['login'],'password':'participant-password'}))
    assert c.put('/api/v1/progress/accounting',headers=sh,json={'state':{'profile':'bad'},'summary':{}}).status_code==422


def test_xlsx_and_preview_duplicates(service):
    from openpyxl import Workbook
    c,m,_=service;h=admin(c)
    wb=Workbook();ws=wb.active
    ws.append(['Фамилия','Имя','Отчество','Подразделение','Должность','Табельный номер'])
    for _ in range(2):ws.append(['Тестова','Анна',None,'Игарка','Кладовщик','00045'])
    stream=io.BytesIO();wb.save(stream)
    r=c.post('/api/v1/accounts/import/preview',headers={**h,'X-File-Format':'xlsx'},content=stream.getvalue())
    assert r.status_code==200,r.text
    assert r.json()[0]['valid'] and r.json()[0]['data']['personnel_number']=='00045'
    assert not r.json()[1]['valid']
