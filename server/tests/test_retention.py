import importlib.util
import json
import sqlite3
import sys
from datetime import datetime,timezone,timedelta
from pathlib import Path
from test_builder import service,publish,login
from test_fire_builder import imported,publish as publish_fire,full,save
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from retention import expires_at,sweep

UTC=timezone.utc
AT=datetime(2026,10,29,12,tzinfo=UTC)

def test_calendar_months_leap_and_timezone():
    assert expires_at('2024-08-31T12:30:00+00:00').isoformat()=='2025-02-28T12:30:00+00:00'
    assert expires_at('2023-08-31T12:30:00+00:00').isoformat()=='2024-02-29T12:30:00+00:00'
    assert expires_at('2026-04-29T15:00:00+03:00')==AT


def test_expiry_dry_run_files_scores_boundary_and_restart(service):
    c,m,a=service;_,cid=publish(c,a);u=login(c,cid)
    uid=m.decode_token(u['Authorization'][7:])['sub']
    image=c.post(f'/api/v1/workbooks/{cid}/evidence/p',headers=u,json={'data':'iVBORw0KGgo='}).json()['id']
    state={'answers':{'o':'Private response'},'notes':{'0':'Private note'},'evidence':{'p':[image]},'history':[{'answers':'private'}]}
    assert c.put('/api/v1/progress/'+cid,headers=u,json={'state':state,'summary':{}}).status_code==200
    with m.db() as con:
        con.execute('UPDATE course_attempts SET updated_at=?,completed_at=? WHERE course_id=?',('2026-10-28T12:00:00+00:00','2026-04-29T12:00:00+00:00',cid))
        con.execute('UPDATE progress SET updated_at=?,completed_at=? WHERE course_id=?',('2026-10-28T12:00:00+00:00','2026-04-29T12:00:00+00:00',cid))
        before=con.execute('SELECT summary_json FROM progress WHERE course_id=?',(cid,)).fetchone()[0]
    p=m.DB_PATH.parent/'builder-evidence'/(image+'.png');assert p.exists()
    assert sweep(m.DB_PATH,m.DB_PATH.parent,at=AT-timedelta(microseconds=1))['attempts']==0
    assert sweep(m.DB_PATH,m.DB_PATH.parent,at=AT)['attempts']==1
    assert p.exists() and 'Private response' in c.get('/api/v1/progress/'+cid,headers=u).text
    got=sweep(m.DB_PATH,m.DB_PATH.parent,apply=True,at=AT)
    assert got['attempts']==1 and not p.exists() and got['files_pending']==0
    r=c.get('/api/v1/progress/'+cid,headers=u).json()
    assert r['state']['retentionExpired'] and r['state']['answers']=={}
    assert 'Private' not in json.dumps(r['state'])
    with m.db() as con:assert con.execute('SELECT summary_json FROM progress WHERE course_id=?',(cid,)).fetchone()[0]==before
    assert c.put('/api/v1/progress/'+cid,headers=u,json={'state':state,'summary':{}}).status_code==409
    assert c.post(f'/api/v1/workbooks/{cid}/evidence/p',headers=u,json={'data':'iVBORw0KGgo='}).status_code==409
    assert c.post('/api/v1/progress/'+cid+'/restart',headers=u).status_code==200
    assert c.put('/api/v1/progress/'+cid,headers=u,json={'state':{'notes':{'0':'New attempt'}},'summary':{}}).status_code==200
    assert sweep(m.DB_PATH,m.DB_PATH.parent,apply=True,at=AT)['attempts']==0
    with m.db() as con:assert con.execute('SELECT COUNT(*) FROM course_attempts WHERE course_id=?',(cid,)).fetchone()[0]==2


def test_unfinished_last_save_and_invalid_dates(service):
    c,m,a=service;_,cid=publish(c,a);u=login(c,cid)
    with m.db() as con:
        con.execute('UPDATE course_attempts SET updated_at=?,completed_at=NULL WHERE course_id=?',('2026-04-29T12:00:00+00:00',cid))
    assert sweep(m.DB_PATH,m.DB_PATH.parent,at=AT)['attempts']==1
    with m.db() as con:con.execute('UPDATE course_attempts SET updated_at=? WHERE course_id=?',('invalid',cid))
    r=sweep(m.DB_PATH,m.DB_PATH.parent,apply=True,at=AT)
    assert r['invalid_dates']==1 and r['attempts']==0


def test_fire_completion_uses_submission_not_last_save(service,tmp_path):
    from test_fire_access import provision,bearer
    c,m,_=service;provision(m,tmp_path)
    a=bearer(c.post('/api/v1/fire/admin/session',json={'login':'pb-test-admin','password':'pb-test-password'}))
    u=bearer(c.post('/api/v1/fire/session',json={'full_name':'Retention Fire','code':'pb-test-code'}))
    d=imported(c,a);cid=publish_fire(c,a,d);s=full(d['content']);s.update(submitted=True,submittedAt='2026-04-29T12:00:00+00:00')
    save(c,u,cid,full(d['content']))
    with m.db() as con:
        con.execute('UPDATE fb_attempts SET state=?,updated_at=? WHERE cid=?',(json.dumps(s),'2026-10-29T00:00:00+00:00',cid))
        before=con.execute('SELECT summary FROM fb_attempts WHERE cid=?',(cid,)).fetchone()[0]
    assert sweep(m.DB_PATH,m.DB_PATH.parent,apply=True,at=AT)['attempts']==1
    r=c.get('/api/v1/fire/builder/editions/'+cid+'/progress',headers=u).json()
    assert r['state']['retentionExpired'] and not r['feedback']['current']
    assert save(c,u,cid,s).status_code==409
    assert c.post('/api/v1/fire/builder/editions/'+cid+'/submit',headers=u).status_code==409
    with m.db() as con:assert con.execute('SELECT summary FROM fb_attempts WHERE cid=?',(cid,)).fetchone()[0]==before


def test_backup_prune_keeps_last_good_partial_unknown_and_symlink(tmp_path):
    spec=importlib.util.spec_from_file_location('backup_retention',Path(__file__).resolve().parents[2]/'tools/recovery/backup_retention.py')
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    def point(days):
        p=tmp_path/(AT-timedelta(days=days)).strftime('%Y%m%dT%H%M%S%fZ');p.mkdir()
        (p/'verified.json').write_text(json.dumps({'database_and_files_verified':True,'isolated_api_restore':True}));return p
    old=point(15);edge=point(14);recent=point(0)
    partial=tmp_path/'failed.partial';partial.mkdir();unknown=tmp_path/'unrelated';unknown.mkdir()
    link=tmp_path/'link';link.symlink_to(unknown,target_is_directory=True)
    assert mod.prune(tmp_path,at=AT)['eligible']==2 and old.exists()
    assert mod.prune(tmp_path,at=AT,apply=True)['removed']==2
    assert not old.exists() and not edge.exists() and recent.exists() and partial.exists() and unknown.exists() and link.is_symlink()
    assert mod.prune(tmp_path,at=AT+timedelta(days=100),apply=True)['removed']==0


def test_restored_backup_is_scrubbed_before_app_serves(service,tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from retention import register
    c,m,a=service;_,cid=publish(c,a);u=login(c,cid)
    c.put('/api/v1/progress/'+cid,headers=u,json={'state':{'notes':{'0':'Expired confidential response'}},'summary':{}})
    with m.db() as con:
        con.execute('UPDATE course_attempts SET completed_at=? WHERE course_id=?',('2025-01-01T00:00:00+00:00',cid))
        con.execute('UPDATE progress SET completed_at=? WHERE course_id=?',('2025-01-01T00:00:00+00:00',cid))
    restored=tmp_path/'restored';restored.mkdir();path=restored/'workbooks.sqlite3'
    with m.db() as src,sqlite3.connect(path) as dst:src.backup(dst)
    app=FastAPI();register(app,path)
    @app.get('/check')
    def check():
        with sqlite3.connect(path) as con:return json.loads(con.execute('SELECT state_json FROM progress WHERE course_id=?',(cid,)).fetchone()[0])
    with TestClient(app) as client:
        got=client.get('/check').json()
        assert got['retentionExpired'] and 'Expired confidential response' not in json.dumps(got)
    assert 'Expired confidential response' in c.get('/api/v1/progress/'+cid,headers=u).text
