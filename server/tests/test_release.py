import sqlite3,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from test_builder import service
from verify_legacy import verify

def test_existing_rows_verified_after_new_drafts_and_versions(service,tmp_path):
    c,m,h=service
    before=tmp_path/'before.sqlite3'
    with m.db() as con,sqlite3.connect(before) as dst:con.backup(dst)
    for source in ('accounting','warehouse'):
        draft=c.post('/api/v1/builder/templates/'+source+'/import',headers=h,json={}).json()
        assert c.post('/api/v1/builder/drafts/'+draft['id']+'/publish',headers=h,json={'revision':draft['revision']}).status_code==200
    assert verify(before,m.DB_PATH)['legacy_rows_unchanged']
    catalog=c.get('/api/v1/workbooks').json()
    assert {e['source_course'] for e in catalog}=={'accounting','warehouse'}
    for e in catalog:assert e['is_latest'] and e['version']==1
