from test_builder import service,publish,content


def test_dashboard_groups_editions_and_keeps_old_links(service):
    c,m,h=service
    draft,first=publish(c,h)
    second=c.post('/api/v1/builder/drafts/'+draft['id']+'/publish',headers=h,json={'revision':draft['revision']}).json()['course_id']
    for source in ('accounting','warehouse'):
        imported=c.post('/api/v1/builder/templates/'+source+'/import',headers=h).json()
        for _ in range(3):
            response=c.post('/api/v1/builder/drafts/'+imported['id']+'/publish',headers=h,json={'revision':imported['revision']})
            assert response.status_code==200,response.text
    response=c.get('/api/v1/admin/dashboard',headers=h)
    assert response.headers['cache-control']=='no-store'
    courses=response.json()['courses']
    assert len(courses)==4 # Three original workbooks and one independent custom workbook.
    assert sum(r['group_id']=='source:accounting' for r in courses)==1
    assert sum(r['group_id']=='source:warehouse' for r in courses)==1
    custom=next(r for r in courses if r['group_id']=='draft:'+draft['id'])
    assert custom['course_title']==content()['title']
    assert all(' · версия ' not in r['course_title'] for r in courses)
    assert c.get('/api/v1/workbooks/'+first).status_code==200
    assert c.get('/api/v1/workbooks/'+second).status_code==200
    for path in ('/v4/admin.html','/v4/admin.js','/v4/admin-summary.js'):
        assert c.get(path).headers['cache-control']=='no-store'


def test_summary_latest_per_user_with_all_attempts_and_reviews(service):
    _,m,_=service
    def course(cid,version):
        return dict(course_id=cid,group_id='source:accounting',group_title='Складской учёт',
                    edition_version=version,course_direction='1С:УПО')
    def row(cid,uid,date,progress,attempts=1,pending=0):
        return dict(course_id=cid,user_id=uid,updated_at=date,progress_percent=progress,
                    success_percent=progress,status='completed' if progress==100 else 'active',
                    attempts_count=attempts,pending_review=pending,needs_recheck=0)
    courses=[course('accounting',0),course('edition-1',1),course('edition-2',2)]
    rows=[row('accounting','a','2026-01-01',100,2,1),row('edition-2','a','2026-01-03',20),
          row('edition-1','a','2026-01-02',50),row('edition-1','b','2026-01-02',100)]
    import copy
    before=copy.deepcopy((courses,rows))
    grouped,records=m.dashboard_families(courses,rows)
    assert (courses,rows)==before
    assert len(grouped)==1 and len(records)==2
    assert grouped[0]['users']==2 and grouped[0]['average_progress']==60
    assert grouped[0]['completed']==1
    a=next(r for r in records if r['user_id']=='a')
    assert a['progress_percent']==20 and a['attempts_count']==4 and a['pending_review']==1
