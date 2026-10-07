import base64
import copy
import json
from test_builder import service, publish, login

def blocks(m,cid='accounting'):
    return [b for s in json.loads(m.builder_edition(cid)['content'])['sections'] for b in s['blocks']]

def correct_answer(b):
    if b['type'] != 'scenario':return {'values':b['correct'], 'submitted':True}
    values=[];si=0
    while si != -1:
        s=b['steps'][si];values.append(s['correct']);si=s['options'][s['correct']]['next']
    return {'values':values,'submitted':True}

def test_all_formats_authoritative_and_manual(service):
    c,m,h=service
    from games import TYPES
    games=[copy.deepcopy(b) for b in blocks(m) if b['type'] in TYPES]
    assert set(b['type'] for b in games)==TYPES
    for b in games:b.pop('image',None) if b['type']!='hotspots' else None
    image=c.post('/api/v1/builder/images',headers=h,json={'data':base64.b64encode(b'\x89PNG\r\n\x1a\n'+b'x'*20).decode()}).json()['url']
    next(b for b in games if b['type']=='hotspots')['image']=image
    body={'title':'Все игровые форматы','sections':[{'title':'Игры','blocks':games}]}
    _,cid=publish(c,h,body);student=login(c,cid)
    public=c.get('/api/v1/workbooks/'+cid).json()['content']['sections'][0]['blocks']
    assert all('correct' not in b for b in public)
    assert all('correct' not in step for b in public for step in b.get('steps',[]))
    answers={}
    for b in games:
        answer=correct_answer(b);answers[b['id']]=answer
        r=c.post(f"/api/v1/workbooks/{cid}/games/{b['id']}/check",headers=student,json=answer)
        assert r.status_code==200,r.text
        assert r.json()['points']==10
    r=c.put('/api/v1/progress/'+cid,headers=student,json={'state':{'answers':answers},'summary':{'points':99999}})
    assert r.json()['summary']['points']==70
    answers[games[0]['id']]['submitted']=False
    r=c.put('/api/v1/progress/'+cid,headers=student,json={'state':{'answers':answers},'summary':{'points':99999}})
    assert r.json()['summary']['points']==60
    body['reviewMode']='manual';_,manual_cid=publish(c,h,body);student=login(c,manual_cid)
    for b in games:b['reviewMode']='inherit'
    # Publish a separate manual edition; existing automatic editions retain their configuration.
    body['sections'][0]['blocks']=games
    _,manual_cid=publish(c,h,body);student=login(c,manual_cid)
    r=c.put('/api/v1/progress/'+manual_cid,headers=student,json={'state':{'answers':{b['id']:correct_answer(b) for b in games}},'summary':{}})
    assert r.json()['summary']['points']==0
    assert r.json()['summary']['pendingReview']==7

def test_invalid_keys_and_scenario_paths(service):
    c,m,h=service
    for typ in ('sequence','sort','match','errors','hotspots'):
        b=copy.deepcopy(next(b for b in blocks(m) if b['type']==typ));b['correct']=[True]
        assert c.post('/api/v1/builder/drafts',headers=h,json={'content':{'title':'Неверная игра','sections':[{'title':'Тема','blocks':[b]}]}}).status_code==422
    b=copy.deepcopy(next(b for b in blocks(m) if b['type']=='scenario'))
    b['steps'][0]['options'][0]['next']=0
    assert c.post('/api/v1/builder/drafts',headers=h,json={'content':{'title':'Цикл','sections':[{'title':'Тема','blocks':[b]}]}}).status_code==422
    student=login(c,'accounting');b=next(b for b in blocks(m) if b['type']=='scenario')
    for answer,status in [({'values':[0],'submitted':True},422),({'values':[0,0,0],'submitted':True},422),({'values':[True,0],'submitted':True},422),({'values':[1,0],'submitted':True},200)]:
        r=c.post('/api/v1/workbooks/accounting/games/'+b['id']+'/check',headers=student,json=answer)
        assert r.status_code==status,r.text
        if status==200:assert r.json()['correct'] is False

def test_builtin_evidence_history_review_and_legacy_keys(service):
    c,m,h=service;student=login(c,'accounting');other=login(c,'accounting','Другой Студент')
    state={'checks':{'0-0':True},'answers':{'0-0':0},'evidence':{}}
    r=c.put('/api/v1/progress/accounting',headers=student,json={'state':state,'summary':{}})
    assert r.status_code==200
    image=c.post('/api/v1/workbooks/accounting/evidence/practice-0-0',headers=student,json={'data':base64.b64encode(b'\x89PNG\r\n\x1a\n'+b'x'*20).decode()}).json()['id']
    assert c.get('/api/v1/builder/evidence/'+image,headers=other).status_code==403
    state['evidence']={'practice-0-0':[image]}
    c.put('/api/v1/progress/accounting',headers=student,json={'state':state,'summary':{}})
    record=next(r for r in c.get('/api/v1/admin/dashboard',headers=h).json()['records'] if r['full_name']=='Иванов Иван')
    url=f"/api/v1/builder/reviews/{record['user_id']}/accounting/1"
    work=c.get(url,headers=h).json()
    assert work['state']['checks']['practice-0-0'] is True
    assert work['state']['answers']['0-0']==[0]
    r=c.put(url+'/practice-0-0',headers=h,json={'checked':True,'points':15,'comment':'Проверено','updated_at':work['updated_at']})
    assert r.status_code==200,r.text
    state['checks']['0-0']=False
    c.put('/api/v1/progress/accounting',headers=student,json={'state':state,'summary':{}})
    assert c.get(url,headers=h).json()['reviews']['practice-0-0']['needs_recheck'] is True
    c.post('/api/v1/progress/accounting/restart',headers=student)
    r=c.put('/api/v1/progress/accounting',headers=student,json={'state':{'evidence':{'practice-0-0':[image]}},'summary':{}})
    assert r.status_code==422
    assert c.get(url,headers=h).json()['state']['evidence']['practice-0-0']==[image]
