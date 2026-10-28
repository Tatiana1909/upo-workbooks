from test_builder import service,publish,content


def test_dashboard_group_metadata_keeps_editions_and_results(service):
    c,m,h=service
    draft,first=publish(c,h)
    second=c.post('/api/v1/builder/drafts/'+draft['id']+'/publish',headers=h,json={'revision':draft['revision']}).json()['course_id']
    imported=c.post('/api/v1/builder/templates/accounting/import',headers=h).json()
    cid=c.post('/api/v1/builder/drafts/'+imported['id']+'/publish',headers=h,json={'revision':imported['revision']}).json()['course_id']
    data=c.get('/api/v1/admin/dashboard',headers=h).json()
    courses={r['course_id']:r for r in data['courses']}
    assert courses[first]['group_id']==courses[second]['group_id']=='draft:'+draft['id']
    assert courses[first]['group_title']==content()['title']
    assert courses[first]['edition_version']==1 and courses[second]['edition_version']==2
    assert courses[cid]['group_id']==courses['accounting']['group_id']=='source:accounting'
    assert c.get('/api/v1/workbooks/'+first).status_code==200
    assert c.get('/api/v1/workbooks/'+second).status_code==200
