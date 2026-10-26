"""Seed demo editions only on an isolated staging DB."""
import json
import urllib.request
import app

def call(path,data=None):
    token=app.encode_token('fire-admin',app.fire_access.config['login'],'admin',60,scope=app.fire.CID)
    req=urllib.request.Request('http://127.0.0.1:8080'+path,data=None if data is None else json.dumps(data).encode(),headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
    return json.load(urllib.request.urlopen(req))

if __name__=='__main__':
    d=call('/api/v1/fire/builder/import',{})
    editions=call('/api/v1/fire/builder/editions')
    while len(editions)<2:
        call('/api/v1/fire/builder/drafts/'+d['id']+'/publish',{'revision':d['revision']})
        editions=call('/api/v1/fire/builder/editions')
    print(json.dumps({'draft':d['id'],'editions':editions},ensure_ascii=False))
