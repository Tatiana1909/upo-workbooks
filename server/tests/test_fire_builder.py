import base64
import copy
import hashlib
import json
import sqlite3
import tarfile
from test_builder import service
from test_fire_access import provision,bearer
import pytest

P='/api/v1/fire/builder'
@pytest.fixture
def setup(service,tmp_path):
    c,m,warehouse=service;provision(m,tmp_path)
    admin=bearer(c.post('/api/v1/fire/admin/session',json={'login':'pb-test-admin','password':'pb-test-password'}))
    user=bearer(c.post('/api/v1/fire/session',json={'full_name':'Проверка Пользователь','code':'pb-test-code'}))
    return c,m,admin,user,warehouse

def imported(c,a):
    r=c.post(P+'/import',headers=a,json={});assert r.status_code==200,r.text;return r.json()
def publish(c,a,d):
    r=c.post(P+'/drafts/'+d['id']+'/publish',headers=a,json={'revision':d['revision']});assert r.status_code==200,r.text;return r.json()['id']
def save(c,u,cid,s):return c.put(P+'/editions/'+cid+'/progress',headers=u,json={'state':s,'summary':{'points':99999,'passed':True}})
def full(c):
    qs=[q for m in c['modules'] for q in m['quiz']+m['games']]
    return dict(read={m['id']:True for m in c['modules']},answers={q['id']:q['answer'] for q in qs},first={},notes={},exam={q['id']:q['answer'] for q in c['final']},profile={'unit':'Тест','location':{'object':'Объект А'}},history=[])

def test_content_complete_legacy_untouched_and_backup(setup):
    c,m,a,u,w=setup
    old=c.get('/api/v1/progress/'+m.fire.CID,headers=u).json()
    d=imported(c,a);content=d['content'];source=m.fire.DATA
    assert len(content['modules'])==6 and len(content['final'])==15
    assert sum(len(x['quiz']) for x in content['modules'])==18
    assert sum(len(x['games']) for x in content['modules'])==14
    for mod,src in zip(content['modules'],source['modules']):
        for key in ('id','title','lead','theory','quiz','games','prompt','minutes','source'):assert mod[key]==src[key]
    assert content['final']==source['final']
    assert sum(len(m['images']) for m in content['modules'])==6
    cid=publish(c,a,d)
    assert c.get('/api/v1/progress/'+m.fire.CID,headers=u).json()==old
    assert c.get('/v4/fire/index.html').status_code==200
    assert c.get(P+'/editions/'+cid+'/progress',headers=u).json()['state'] is None
    backups=list((m.DB_PATH.parent/'full-backups').glob('*.tgz'));assert backups
    with tarfile.open(backups[0]) as t:
        assert 'data/workbooks.sqlite3' in t.getnames()
        assert 'server/environment.json' in t.getnames()
        assert 'static/v4/fire/assets/instruction.docx' in t.getnames()
        with t.extractfile('data/workbooks.sqlite3') as f:raw=f.read()
    dest=m.DB_PATH.parent/'restored.sqlite3';dest.write_bytes(raw)
    with sqlite3.connect(dest) as con:assert con.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    assert hashlib.sha256(backups[0].read_bytes()).hexdigest() in backups[0].with_suffix('.sha256').read_text()

def test_versions_link_snapshot_conflict_and_scopes(setup):
    c,m,a,u,w=setup;d=imported(c,a);cid1=publish(c,a,d)
    original=d['content']['materials'][0]['url'];d['content']['materials'][0]['url']='https://example.org/new-instruction'
    r=c.put(P+'/drafts/'+d['id'],headers=a,json=d);assert r.status_code==200,r.text;new=r.json()
    assert c.put(P+'/drafts/'+d['id'],headers=a,json=d).status_code==409
    assert c.post(P+'/drafts/'+d['id']+'/publish',headers=a,json={'revision':d['revision']}).status_code==409
    cid2=publish(c,a,new);assert cid1!=cid2
    assert c.get(P+'/editions/'+cid1).json()['content']['materials'][0]['url']==original
    assert c.get(P+'/editions/'+cid2).json()['content']['materials'][0]['url'].endswith('new-instruction')
    assert c.get(P+'/catalog').json()[0]['id']==cid2
    assert len(c.get(P+'/editions',headers=a).json())==2
    for denied in (u,w):
        for route in ('drafts','editions'):
            assert c.get(P+'/'+route,headers=denied).status_code==403
        assert c.post(P+'/import',headers=denied,json={}).status_code==403
        assert c.put(P+'/drafts/'+d['id'],headers=denied,json=new).status_code==403
        assert c.post(P+'/drafts/'+d['id']+'/publish',headers=denied,json={'revision':new['revision']}).status_code==403
        assert c.post(P+'/images',headers=denied,json={'data':'x'}).status_code==403
    for path in ('/api/v1/admin/dashboard','/api/v1/builder/drafts','/api/v1/admin/results.csv'):
        assert c.get(path,headers=a).status_code==403
    assert c.get(P+'/editions/'+cid1+'/progress',headers=w).status_code==403
    assert c.get(P+'/editions/'+cid1+'/results',headers=u).status_code==403
    assert c.get(P+'/editions/'+cid1+'/results.csv',headers=w).status_code==403
    assert c.put(P+'/editions/'+cid1,headers=a,json=new).status_code==405

def test_authoritative_score_first_answer_exam_lock_and_attempts(setup):
    c,m,a,u,w=setup;d=imported(c,a);cid=publish(c,a,d);content=d['content'];s=full(content)
    assert c.post(P+'/editions/'+cid+'/submit',headers=u).status_code==409
    partial=copy.deepcopy(s);partial['read']={};save(c,u,cid,partial)
    assert c.post(P+'/editions/'+cid+'/submit',headers=u).status_code==409
    s['answers']['p1']=0;s['first']={'p1':1};s.update(submitted=True,examThreshold=1)
    r=save(c,u,cid,s);assert r.status_code==200,r.text;assert r.json()['summary']['points']==310
    s['answers']['p1']=1;save(c,u,cid,s)
    got=c.get(P+'/editions/'+cid+'/progress',headers=u).json()['state'];assert got['first']['p1']==1 # initial complete partial save answered correctly
    assert not got.get('submitted') and 'examThreshold' not in got
    s['exam'].pop('x15');save(c,u,cid,s)
    assert c.post(P+'/editions/'+cid+'/submit',headers=u).status_code==422
    s=full(content);s['exam']['x1']=1;save(c,u,cid,s)
    r=c.post(P+'/editions/'+cid+'/submit',headers=u);assert r.status_code==200,r.text
    assert r.json()['summary']['examPercent']==93 and not r.json()['summary']['passed']
    assert r.json()['summary']['criticalErrors']==1
    s=full(content);s['examThreshold']=60;save(c,u,cid,s)
    frozen=c.get(P+'/editions/'+cid+'/progress',headers=u).json()['state']
    assert frozen['exam']['x1']==1 and frozen['examThreshold']==80
    assert c.post(P+'/editions/'+cid+'/restart',headers=u).json()['attempt_no']==2
    assert c.get(P+'/editions/'+cid+'/progress',headers=u).json()['state']['profile']['location']['object']=='Объект А'
    assert len(c.get(P+'/editions/'+cid+'/results',headers=a).json()['rows'])==2
    other=bearer(c.post('/api/v1/fire/session',json={'full_name':'Другой Участник','code':'pb-test-code'}))
    assert c.get(P+'/editions/'+cid+'/progress',headers=other).json()['state'] is None
    second=publish(c,a,d);assert c.get(P+'/editions/'+second+'/progress',headers=u).json()['state'] is None

def test_evidence_review_recheck_and_validation(setup):
    c,m,a,u,w=setup;d=imported(c,a)
    d['content']['modules'][0]['blocks']=[{'id':'pract','type':'practice','text':'Отработайте очно','evidenceRequired':True,'maxImages':1,'reviewMode':'manual'},{'id':'written','type':'open','text':'Объяснение'}]
    d=c.put(P+'/drafts/'+d['id'],headers=a,json=d).json();cid=publish(c,a,d);s=full(d['content']);s['checks']={'pract':True};s['responses']={'written':'Проверка'}
    assert save(c,u,cid,s).status_code==200
    assert c.post(P+'/editions/'+cid+'/submit',headers=u).status_code==409
    raw=base64.b64encode(b'\x89PNG\r\n\x1a\nfixture').decode()
    r=c.post(P+'/editions/'+cid+'/evidence/pract',headers=u,json={'data':raw});assert r.status_code==200,r.text;iid=r.json()['id']
    other=bearer(c.post('/api/v1/fire/session',json={'full_name':'Чужой Участник','code':'pb-test-code'}))
    assert c.get(P+'/evidence/'+iid,headers=other).status_code==403
    assert c.get(P+'/evidence/'+iid,headers=w).status_code==403
    assert c.get(P+'/evidence/'+iid,headers=a).status_code==200
    s['evidence']={'pract':[iid]};assert save(c,u,cid,s).status_code==200
    assert save(c,other,cid,s).status_code==422
    row=c.get(P+'/editions/'+cid+'/results',headers=a).json()['rows'][0]
    review={**row,'comment':'Хорошо','points':90,'practical':True,'checked':True}
    assert c.put(P+'/editions/'+cid+'/review',headers=a,json=review).status_code==200
    s['responses']['written']='Новая запись';assert save(c,u,cid,s).status_code==200
    assert c.put(P+'/editions/'+cid+'/review',headers=a,json=review).status_code==409
    row=c.get(P+'/editions/'+cid+'/results',headers=a).json()['rows'][0]
    assert row['review']['needs_recheck'] and not row['review']['checked'] and row['review']['comment']=='Хорошо'
    assert row['summary']['teacherPoints'] is None
    assert c.post(P+'/editions/'+cid+'/submit',headers=u).status_code==200
    csv=c.get(P+'/editions/'+cid+'/results.csv',headers=a);assert csv.status_code==200 and cid in csv.text and 'Хорошо' in csv.text
    invalid=copy.deepcopy(d);invalid['content']['materials'][0]['url']='javascript:alert(1)'
    assert c.put(P+'/drafts/'+d['id'],headers=a,json=invalid).status_code==422
    invalid=copy.deepcopy(d);invalid['content']['modules'][0]['quiz'][0]['answer']=999
    assert c.put(P+'/drafts/'+d['id'],headers=a,json=invalid).status_code==422


def test_first_cannot_be_forged_and_keys_private(setup):
    c,m,a,u,w=setup;d=imported(c,a);cid=publish(c,a,d)
    content=c.get(P+'/editions/'+cid).json()['content']
    assert all('answer' not in q and 'explain' not in q for mod in content['modules'] for q in mod['quiz']+mod['games'])
    assert all('answer' not in q for q in content['final'])
    assert c.get(P+'/editions/'+cid+'/author',headers=u).status_code==403
    assert c.get(P+'/editions/'+cid+'/author',headers=a).json()['content']['final'][0]['answer']==0
    s=full(d['content']);s['answers']['p1']=0;s['first']={'p1':1}
    r=save(c,u,cid,s);assert r.status_code==200,r.text
    assert not r.json()['feedback']['first']['p1']['correct']
    s['answers']['p1']=1;save(c,u,cid,s)
    state=c.get(P+'/editions/'+cid+'/progress',headers=u).json()
    assert state['state']['first']['p1']==0 and state['feedback']['current']['p1']['correct']
    assert not state['feedback']['exam']
