import copy
import json
import sqlite3
from pathlib import Path
import pytest
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from test_builder import service
from material_links import LINKS, replace_links, release
from fire_builder import seed


def rows(con, table):
    return [tuple(r) for r in con.execute('SELECT * FROM ' + table)]


def prepare(m):
    raw = json.loads(Path(m.__file__).with_name('upo-templates.json').read_text())
    fire = seed(m.STATIC_DIR)
    fire['sources'][0]['url'] = '/v4/fire/assets/instruction.docx'
    with m.db() as con:
        for source, content in raw.items():
            did = source + '-draft'
            packed = json.dumps(content, ensure_ascii=False)
            con.execute('INSERT INTO builder_imports VALUES(?,?,?,?)', (source, did, 'old-source', 'before'))
            con.execute('INSERT INTO builder_drafts(id,content,revision,updated_at) VALUES(?,?,1,?)', (did, packed, 'before'))
            con.execute('INSERT INTO builder_editions(course_id,draft_id,version,title,content,published_at) VALUES(?,?,1,?,?,?)',
                        (source + '-old', did, content['title'], packed, 'before'))
        con.execute('INSERT INTO fb_drafts VALUES(?,?,1,?)', ('fire-original', json.dumps(fire), 'before'))
        con.execute('INSERT INTO fb_editions VALUES(?,?,?,?,?)', ('pb-old', 'fire-original', 1, json.dumps(fire), 'before'))
        con.commit()
    return raw, fire


def test_atomic_release_preserves_results_unpublished_edits_and_access(service):
    c, m, h = service
    raw, fire = prepare(m)
    # Actual legacy results and comments must survive byte-for-byte.
    user = c.post('/api/v1/session', json={'code':'test-code','full_name':'Проверка Сохранности','course_id':'accounting-old'}).json()
    auth = {'Authorization':'Bearer ' + user['token']}
    assert c.put('/api/v1/progress/accounting-old', headers=auth, json={'state':{'answers':{},'notes':{'test':'Запись'},'evidence':{}},'summary':{}}).status_code == 200
    with m.db() as con:
        unpublished = copy.deepcopy(raw['accounting'])
        unpublished['title'] = 'Неопубликованное изменение преподавателя'
        con.execute('UPDATE builder_drafts SET content=? WHERE id=?', (json.dumps(unpublished), 'accounting-draft'))
        creator = m.accounts.create(con,dict(last_name='Тест',first_name='Участник',no_patronymic=True,unit='Склад',no_personnel_number=True,
                roles=['participant'],realms=['upo'],courses=['accounting-old'],login='link-participant',password='Strong-test-password'), 'test', None)
        con.commit()
        before = {t:rows(con,t) for t in ('progress','course_attempts','users','builder_reviews','builder_evidence','fb_attempts','builder_editions','fb_editions')}
        account_before = dict(con.execute('SELECT * FROM identity_accounts WHERE id=?',(creator['id'],)).fetchone())
    plan = release(m.DB_PATH)
    assert not plan['applied']
    with m.db() as con:
        for table, expected in before.items(): assert rows(con,table) == expected
        assert con.execute("SELECT title FROM builder_editions WHERE course_id='accounting-draft-v2'").fetchone() is None
    report = release(m.DB_PATH,True)
    assert [x['links_changed'] for x in report['editions']] == [50,14,1]
    with m.db() as con:
        for table, expected in before.items():
            current = rows(con,table)
            assert current[:len(expected)] == expected
        for entry in report['editions']:
            if entry['source'] == 'fire':
                new = json.loads(con.execute('SELECT content FROM fb_editions WHERE id=?',(entry['course_id'],)).fetchone()[0])
                assert new['sources'][0]['url'] == LINKS['fire']['url']
                assert new['final'] == fire['final']
                assert new['modules'] == fire['modules']
                assert c.get('/api/v1/fire/builder/editions/'+entry['course_id']).status_code == 200
            else:
                source = entry['source']
                new = json.loads(con.execute('SELECT content FROM builder_editions WHERE course_id=?',(entry['course_id'],)).fetchone()[0])
                assert new == replace_links(raw[source],source)[0]
                assert c.get('/api/v1/progress/'+entry['course_id'],headers=auth).json()['state'] is None
        draft = json.loads(con.execute("SELECT content FROM builder_drafts WHERE id='accounting-draft'").fetchone()[0])
        assert draft == replace_links(unpublished,'accounting')[0]
        account_after = dict(con.execute('SELECT * FROM identity_accounts WHERE id=?',(creator['id'],)).fetchone())
        assert json.loads(account_after.pop('courses')) == ['accounting-old','cb-accounting-draft-v2']
        account_before.pop('courses')
        assert account_after == account_before
        assert len(rows(con,'identity_audit')) >= 1
    repeated = release(m.DB_PATH,True)
    assert repeated['already_applied']
    assert [x['course_id'] for x in repeated['editions']] == [x['course_id'] for x in report['editions']]
    with m.db() as con:
        assert len(rows(con,'builder_editions')) == 4
        assert len(rows(con,'fb_editions')) == 2


def test_missing_workbook_rolls_back_all_changes(service):
    c,m,h=service
    prepare(m)
    with m.db() as con:
        con.execute('DELETE FROM fb_drafts');con.commit()
        before = rows(con,'builder_drafts')
    with pytest.raises(ValueError,match='Draft missing'):
        release(m.DB_PATH,True)
    with m.db() as con:
        assert rows(con,'builder_drafts') == before
        assert len(rows(con,'builder_editions')) == 2


def test_all_supplied_links_are_mapped_without_changing_teaching_content():
    raw=json.loads(Path(__file__).resolve().parents[1].joinpath('upo-templates.json').read_text())
    assert sum(len(LINKS[source]) for source in raw) + 1 == 65
    for source,c in raw.items():
        updated,count=replace_links(c,source)
        assert count == len(LINKS[source])
        for before_s,after_s in zip(c['sections'],updated['sections']):
            for before,after in zip(before_s['blocks'],after_s['blocks']):
                if before['id'] in LINKS[source]:
                    assert after['url'] == LINKS[source][before['id']]['url']
                    assert 'download' not in after
                    before={k:v for k,v in before.items() if k not in ('url','download')}
                    after={k:v for k,v in after.items() if k not in ('url','download')}
                assert before == after
        assert replace_links(updated,source)[1] == 0
