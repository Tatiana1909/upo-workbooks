import copy
import json
import sys
from pathlib import Path
import pytest
from test_builder import service, login
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from accounting_wording import PATCHES, replace_questions, release


def original():
    return json.loads(Path(__file__).resolve().parents[1].joinpath('upo-templates.json').read_text())['accounting']


def test_only_five_existing_questions_are_updated():
    before=original();after,count=replace_questions(before,'accounting')
    assert count==5 and len(after['sections'])==len(before['sections'])
    for old_s,new_s in zip(before['sections'],after['sections']):
        assert len(old_s['blocks'])==len(new_s['blocks'])
        for old,new in zip(old_s['blocks'],new_s['blocks']):
            assert old['id']==new['id']
            assert new==({**old,**PATCHES[old['id']]['content']} if old['id'] in PATCHES else old)
    assert sum(b['type'] in ('single','multiple','boolean') for s in after['sections'] for b in s['blocks'])==15
    assert replace_questions(after,'accounting')[1]==0
    assert replace_questions(before,'warehouse')==(before,0)
    edited=copy.deepcopy(before)
    next(b for s in edited['sections'] for b in s['blocks'] if b['id']=='0-1')['text']='Изменение из другого окна'
    with pytest.raises(ValueError,match='independently edited'):replace_questions(edited,'accounting')


def test_versioned_release_draft_preview_grading_and_legacy_preservation(service):
    c,m,h=service;raw=original()
    d=c.post('/api/v1/builder/drafts',headers=h,json={'content':raw}).json()
    cid=c.post('/api/v1/builder/drafts/'+d['id']+'/publish',headers=h,json={'revision':d['revision']}).json()['course_id']
    with m.db() as con:
        con.execute('INSERT INTO builder_imports VALUES(?,?,?,?)',('accounting',d['id'],'original','before'));con.commit()
    u=login(c,cid)
    state={'answers':{'4-0':[0]},'notes':{'0':'Прежние записи'},'evidence':{},'checks':{}}
    assert c.put('/api/v1/progress/'+cid,headers=u,json={'state':state,'summary':{}}).status_code==200
    with m.db() as con:
        progress=[tuple(r) for r in con.execute('SELECT * FROM progress')]
        attempts=[tuple(r) for r in con.execute('SELECT * FROM course_attempts')]
        old=tuple(con.execute('SELECT * FROM builder_editions WHERE course_id=?',(cid,)).fetchone())
        draft=copy.deepcopy(raw);draft['description']='Неопубликованная правка преподавателя'
        con.execute('UPDATE builder_drafts SET content=? WHERE id=?',(json.dumps(draft),d['id']));con.commit()
    assert not release(m.DB_PATH)['applied']
    with m.db() as con:assert con.execute('SELECT COUNT(*) FROM builder_editions').fetchone()[0]==1
    result=release(m.DB_PATH,True)
    entry=result['editions'][0];new=entry['course_id']
    assert entry['questions_changed']==5 and entry['version']==2 and new!=cid
    assert result['existing_results_and_editions_unchanged']
    with m.db() as con:
        assert tuple(con.execute('SELECT * FROM builder_editions WHERE course_id=?',(cid,)).fetchone())==old
        assert [tuple(r) for r in con.execute('SELECT * FROM progress')]==progress
        assert [tuple(r) for r in con.execute('SELECT * FROM course_attempts')]==attempts
        published=json.loads(con.execute('SELECT content FROM builder_editions WHERE course_id=?',(new,)).fetchone()[0])
        assert published==replace_questions(raw,'accounting')[0]
    preview=c.get('/api/v1/builder/drafts/'+d['id']+'/preview',headers=h).json()['content']
    assert preview==replace_questions(draft,'accounting')[0]
    user=login(c,new)
    assert c.get('/api/v1/progress/'+new,headers=user).json()['state']['answers']=={}
    url='/api/v1/workbooks/'+new+'/questions/4-0/check'
    assert c.post(url,headers=user,json={'values':[0,1,2]}).json()['correct']
    assert not c.post(url,headers=user,json={'values':[0]}).json()['correct']
    assert not c.post(url,headers=user,json={'values':[0,1,2,3]}).json()['correct']
    assert c.post('/api/v1/workbooks/'+cid+'/questions/4-0/check',headers=u,json={'values':[0]}).json()['correct']
    assert c.get('/api/v1/workbooks/'+cid).status_code==200
    assert release(m.DB_PATH,True)['already_applied']
    with m.db() as con:assert con.execute('SELECT COUNT(*) FROM builder_editions').fetchone()[0]==2
