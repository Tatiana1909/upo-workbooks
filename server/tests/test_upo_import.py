import json,sqlite3,sys,hashlib,tarfile
from pathlib import Path
from test_builder import service,login
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backup_bundle import create,verify

def test_complete_transfer_and_isolation(service,tmp_path):
    c,m,h=service
    # A previous real-shaped attempt with a deliberately arbitrary stored summary stays byte-for-byte unchanged.
    old=login(c,'accounting');c.put('/api/v1/progress/accounting',headers=old,json={'state':{'notes':{'0':'Старые записи'},'answers':{'0-0':0}},'summary':{}})
    with m.db() as con:before=[tuple(r) for r in con.execute('SELECT * FROM course_attempts')]
    for source,count,quiz in [('accounting',5,15),('warehouse',7,21)]:
        assert c.post(f'/api/v1/builder/templates/{source}/import').status_code==401
        d=c.post(f'/api/v1/builder/templates/{source}/import',headers=h).json()
        assert c.post(f'/api/v1/builder/templates/{source}/import',headers=h).json()['id']==d['id']
        draft=next(x for x in c.get('/api/v1/builder/drafts',headers=h).json() if x['id']==d['id'])
        body=draft['content'];assert len(body['sections'])==count
        blocks=[b for s in body['sections'] for b in s['blocks']]
        assert sum(b['type'] in ('single','multiple','boolean') for b in blocks)==quiz
        assert sum(b['type'] in {'sequence','sort','match','errors','scenario','documents','hotspots'} for b in blocks)==7
        assert any(b['type']=='completion' for b in blocks)
        assert all(b.get('points')==0 for b in blocks if b['type']=='practice')
        assert not any(b.get('legacyQuiz') or b.get('legacyPractice') for b in blocks)
        first=c.post(f"/api/v1/builder/drafts/{d['id']}/publish",headers=h,json={'revision':1});assert first.status_code==200,first.text
        cid=first.json()['course_id'];p=c.get('/api/v1/workbooks/'+cid).json()['content']
        assert 'correct' not in next(b for s in p['sections'] for b in s['blocks'] if b['type']=='single')
        link=next(b for b in blocks if b['type']=='link');original=link['url'];link['url']='https://example.org/new-instruction'
        assert c.put('/api/v1/builder/drafts/'+d['id'],headers=h,json={'revision':1,'content':body}).status_code==200
        second=c.post(f"/api/v1/builder/drafts/{d['id']}/publish",headers=h,json={'revision':2}).json()['course_id'];assert second!=cid
        public=c.get('/api/v1/workbooks/'+cid).json()['content'];assert next(b for s in public['sections'] for b in s['blocks'] if b['id']==link['id'])['url']==original
        user=login(c,cid);q=next(b for b in blocks if b['type']=='single')
        checked=c.post(f"/api/v1/workbooks/{cid}/questions/{q['id']}/check",headers=user,json={'values':q['correct']});assert checked.json()['correct']
        state={'answers':{q['id']:q['correct']},'checks':{},'evidence':{}}
        assert c.put('/api/v1/progress/'+cid,headers=user,json={'state':state,'summary':{'points':999}}).json()['summary']['points']==10
        assert c.get('/api/v1/progress/'+second,headers=login(c,second)).json()['state']['answers']=={}
    with m.db() as con:after=[tuple(r) for r in con.execute("SELECT * FROM course_attempts WHERE course_id='accounting'")]
    assert after==before
    assert c.get('/v4/sklad-1c-upo.html').status_code==200
    assert c.get('/v4/rabotniki-sklada.html').status_code==200
    out=tmp_path.parent/('protected-'+tmp_path.name)/'backup.tgz';result=create(m.DB_PATH.parent,out,m.STATIC_DIR)
    assert result['verified'] and verify(out)
    with tarfile.open(out) as tar:assert 'data/workbooks.sqlite3' in tar.getnames()

def test_fire_teacher_cannot_import_warehouse(service):
    c,m,h=service
    # Genuine signed restricted role, but no wider builder access.
    m.fire_access.config={'revision':'test-revision'}
    token=m.encode_token('fire-admin','Fire teacher','admin',60,scope=m.fire.CID)
    fire={'Authorization':'Bearer '+token}
    assert c.get('/api/v1/builder/templates',headers=fire).status_code==403
    assert c.post('/api/v1/builder/templates/accounting/import',headers=fire).status_code==403
    assert c.get('/api/v1/builder/drafts',headers=fire).status_code==403
