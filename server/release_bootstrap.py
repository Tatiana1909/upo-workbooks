"""Create production editions from authored content only. Never migrate results."""
import json,urllib.request,sqlite3,hashlib
from pathlib import Path
import app
BASE_URL="http://127.0.0.1:8080"

def call(path,data=None,fire=False):
    if fire:
        assert app.fire_access.config,'Separate fire access must be configured'
        token=app.encode_token('fire-admin',app.fire_access.config['login'],'admin',120,scope=app.fire.CID)
    else:token=app.encode_token('admin',app.ADMIN_LOGIN,'admin',120)
    request=urllib.request.Request(BASE_URL+'/api/v1/'+path,headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'},data=json.dumps(data).encode() if data is not None else None)
    return json.load(urllib.request.urlopen(request,timeout=180))

def publish():
    publications={}
    for source in ('accounting','warehouse'):
        draft=call('builder/templates/'+source+'/import',{})
        assert not draft.get('deleted'),'Imported draft is in trash; do not restore it implicitly'
        editions=[e for e in call('builder/editions') if e['draft_id']==draft['id']]
        if not editions:call('builder/drafts/'+draft['id']+'/publish',{'revision':draft['revision']})
        editions=[e for e in call('builder/editions') if e['draft_id']==draft['id'] and not e['archived']]
        assert editions
        edition=max(editions,key=lambda e:e['version']);publications[source]={'course_id':edition['course_id'],'draft_id':draft['id'],'version':edition['version'],'url':'/v4/builder/workbook.html?course='+edition['course_id']}
    draft=call('fire/builder/import',{},True)
    editions=[e for e in call('fire/builder/editions',fire=True) if e['draft_id']==draft['id']]
    if not editions:call('fire/builder/drafts/'+draft['id']+'/publish',{'revision':draft['revision']},True)
    edition=max([e for e in call('fire/builder/editions',fire=True) if e['draft_id']==draft['id']],key=lambda e:e['version'])
    publications['fire']={'course_id':edition['id'],'draft_id':draft['id'],'version':edition['version'],'url':'/v4/fire/editable.html?edition='+edition['id']}
    (app.DB_PATH.parent/'release-publications.json').write_text(json.dumps(publications,ensure_ascii=False,indent=2))
    print(json.dumps(publications,ensure_ascii=False))

if __name__=='__main__':publish()
