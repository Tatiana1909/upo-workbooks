import base64
import json
import re
import secrets
import sqlite3
import games
from pathlib import Path
from contextlib import closing
from urllib.parse import urlsplit
from fastapi import Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

class DraftInput(BaseModel):
    revision: int = 0
    content: dict
class PublishInput(BaseModel):
    revision: int
class ImageInput(BaseModel):
    data: str = Field(max_length=7_000_000)

TYPES = {'text','image','link','video','single','multiple','boolean','open','practice','notes'} | games.TYPES

def register(app, db, admin, student, now, data_dir, static_dir=None):
    builtin_path=Path(static_dir or ".")/"v4/learning-content.json"
    builtins=json.loads(builtin_path.read_text()) if builtin_path.exists() else {}
    def init():
        with closing(db()) as con:
            con.executescript('''
            CREATE TABLE IF NOT EXISTS builder_drafts(id TEXT PRIMARY KEY,content TEXT NOT NULL,revision INTEGER NOT NULL,updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS builder_editions(course_id TEXT PRIMARY KEY,draft_id TEXT NOT NULL,version INTEGER NOT NULL,title TEXT NOT NULL,content TEXT NOT NULL,published_at TEXT NOT NULL,archived INTEGER NOT NULL DEFAULT 0,UNIQUE(draft_id,version));
            CREATE TABLE IF NOT EXISTS builder_reviews(user_id TEXT NOT NULL,course_id TEXT NOT NULL,attempt_no INTEGER NOT NULL,block_id TEXT NOT NULL,review TEXT NOT NULL,updated_at TEXT NOT NULL,PRIMARY KEY(user_id,course_id,attempt_no,block_id));
            CREATE TABLE IF NOT EXISTS builder_evidence(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,course_id TEXT NOT NULL,attempt_no INTEGER NOT NULL,block_id TEXT NOT NULL,extension TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS builder_images(id TEXT PRIMARY KEY,extension TEXT NOT NULL);
            ''')
            for table in ('builder_drafts','builder_editions'):
                columns={r['name'] for r in con.execute(f'PRAGMA table_info({table})')}
                if 'deleted_at' not in columns:con.execute(f'ALTER TABLE {table} ADD COLUMN deleted_at TEXT')
            con.commit()
    app.add_event_handler('startup',init)
    def edition(cid):
        if cid in builtins:return {'course_id':cid,'version':1,'content':json.dumps(builtins[cid],ensure_ascii=False),'title':builtins[cid]['title'],'builtin':True}
        with closing(db()) as con:
            r=con.execute('SELECT * FROM builder_editions WHERE course_id=?',(cid,)).fetchone()
        return dict(r) if r else None
    def titles():
        with closing(db()) as con:
            return {r['course_id']:r['title']+' · версия '+str(r['version']) for r in con.execute('SELECT * FROM builder_editions')}
    def validate(c,publish=False):
        if len(json.dumps(c))>500_000: raise HTTPException(413,'Тетрадь слишком большая')
        if not isinstance(c.get('title'),str) or not c['title'].strip() or len(c['title'])>200: raise HTTPException(422,'Укажите название (до 200 символов)')
        direction=c.get('courseDirection','1С:УПО')
        if not isinstance(direction,str) or not direction.strip() or len(direction)>120:raise HTTPException(422,'Укажите направление курса (до 120 символов)')
        c['courseDirection']=direction.strip()
        if c.get('reviewMode','auto') not in ('auto','manual'):raise HTTPException(422,'Неверный режим проверки')
        sections=c.get('sections',[])
        if not isinstance(sections,list) or len(sections)>100 or (publish and not sections): raise HTTPException(422,'Добавьте разделы (до 100)')
        ids=set()
        for s in sections:
            if not isinstance(s,dict) or not isinstance(s.get('title'),str) or (publish and not s['title'].strip()): raise HTTPException(422,'Укажите название раздела')
            blocks=s.get('blocks',[])
            if not isinstance(blocks,list) or len(blocks)>200 or (publish and not blocks): raise HTTPException(422,'Добавьте блоки каждого раздела (до 200)')
            for b in blocks:
                if not isinstance(b,dict) or b.get('type') not in TYPES: raise HTTPException(422,'Неизвестный тип блока')
                bid=b.get('id','')
                if not isinstance(bid,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}',bid) or bid in ids: raise HTTPException(422,'Идентификаторы блоков должны быть уникальны')
                ids.add(bid)
                if b.get('reviewMode','inherit') not in ('inherit','auto','manual'):raise HTTPException(422,'Неверный режим задания')
                if b['type']=='practice' and (type(b.get('maxImages',3)) is not int or not 1<=b.get('maxImages',3)<=10 or type(b.get('evidenceRequired',False)) is not bool): raise HTTPException(422,'Количество скриншотов: от 1 до 10')
                for k in ('text','title','url','download','image','explanation'):
                    if k in b and (not isinstance(b[k],str) or len(b[k])>20000): raise HTTPException(422,'Неверное или слишком длинное поле')
                for k in ('url','download'):
                    v=b.get(k,'')
                    if v and (urlsplit(v).scheme not in ('http','https') or not urlsplit(v).hostname or urlsplit(v).username): raise HTTPException(422,'Ссылка должна начинаться с http:// или https://')
                if b.get('image') and not re.fullmatch(r'/api/v1/builder/images/[a-f0-9]{32}',b['image']): raise HTTPException(422,'Загрузите картинку через конструктор')
                if publish and b['type'] in ('image','hotspots') and not b.get('image'): raise HTTPException(422,'Загрузите картинку')
                if b['type']=='video' and b.get('videoMode','link') not in ('link','inline'):raise HTTPException(422,'Неверный режим видео')
                if publish and b['type'] in ('link','video') and not b.get('url'): raise HTTPException(422,'Укажите ссылку инструкции')
                if publish and b['type']!='image' and not b.get('text','').strip(): raise HTTPException(422,'Заполните текст каждого блока')
                if b['type'] in games.TYPES:games.validate(b,publish)
                if b['type'] in ('single','multiple','boolean'):
                    opts=b.get('options',[]); correct=b.get('correct',[])
                    if not isinstance(opts,list) or not 2<=len(opts)<=20 or any(not isinstance(o,str) or len(o)>2000 for o in opts): raise HTTPException(422,'Добавьте от 2 до 20 вариантов')
                    if not isinstance(correct,list) or any(type(i) is not int or not 0<=i<len(opts) for i in correct) or len(set(correct))!=len(correct): raise HTTPException(422,'Неверные правильные ответы')
                    if publish and (not correct or any(not o.strip() for o in opts) or (b['type']!='multiple' and len(correct)!=1)): raise HTTPException(422,'Заполните варианты и отметьте правильные ответы')
        return c
    @app.get('/api/v1/builder/drafts')
    def drafts(user=Depends(admin)):
        with closing(db()) as con:
            return [{'id':r['id'],'revision':r['revision'],'updated_at':r['updated_at'],'content':json.loads(r['content'])} for r in con.execute('SELECT * FROM builder_drafts WHERE deleted_at IS NULL ORDER BY updated_at DESC')]
    @app.post('/api/v1/builder/drafts')
    def create(data:DraftInput,user=Depends(admin)):
        validate(data.content); did=secrets.token_hex(12)
        with closing(db()) as con:
            con.execute('INSERT INTO builder_drafts(id,content,revision,updated_at) VALUES(?,?,1,?)',(did,json.dumps(data.content,ensure_ascii=False),now()));con.commit()
        return {'id':did,'revision':1}
    @app.put('/api/v1/builder/drafts/{did}')
    def save(did:str,data:DraftInput,user=Depends(admin)):
        validate(data.content)
        with closing(db()) as con:
            r=con.execute('UPDATE builder_drafts SET content=?,revision=revision+1,updated_at=? WHERE id=? AND revision=? AND deleted_at IS NULL',(json.dumps(data.content,ensure_ascii=False),now(),did,data.revision))
            if r.rowcount!=1: raise HTTPException(409,'Черновик изменён в другом окне. Откройте актуальный вариант из списка')
            con.commit()
        return {'id':did,'revision':data.revision+1}
    @app.post('/api/v1/builder/drafts/{did}/publish')
    def publish(did:str,data:PublishInput,user=Depends(admin)):
        with closing(db()) as con:
            backup_dir=data_dir/'builder-backups';backup_dir.mkdir(parents=True,exist_ok=True)
            with sqlite3.connect(backup_dir/(secrets.token_hex(12)+'.sqlite3')) as backup:
                con.backup(backup)
            con.execute('BEGIN IMMEDIATE');r=con.execute('SELECT * FROM builder_drafts WHERE id=? AND deleted_at IS NULL',(did,)).fetchone()
            if not r or r['revision']!=data.revision: raise HTTPException(409,'Сначала сохраните актуальный черновик')
            c=validate(json.loads(r['content']),True)
            v=con.execute('SELECT COALESCE(MAX(version),0)+1 FROM builder_editions WHERE draft_id=?',(did,)).fetchone()[0];cid=f'cb-{did}-v{v}'
            con.execute('INSERT INTO builder_editions(course_id,draft_id,version,title,content,published_at) VALUES(?,?,?,?,?,?)',(cid,did,v,c['title'],r['content'],now()));con.commit()
        return {'course_id':cid,'version':v,'url':'/v4/builder/workbook.html?course='+cid}
    @app.get('/api/v1/workbooks')
    def catalog():
        with closing(db()) as con:
            rows=con.execute('SELECT course_id,title,version,published_at,content FROM builder_editions WHERE archived=0 AND deleted_at IS NULL ORDER BY published_at DESC').fetchall()
        result=[]
        for row in rows:
            item=dict(row);content=json.loads(item.pop('content'))
            item['course_direction']=content.get('courseDirection') or '1С:УПО'
            item['description']=content.get('description','')
            result.append(item)
        return result
    @app.get('/api/v1/builder/editions')
    def admin_editions(user=Depends(admin)):
        with closing(db()) as con:
            return [dict(r) for r in con.execute('SELECT course_id,title,version,archived FROM builder_editions WHERE deleted_at IS NULL ORDER BY published_at DESC')]
    @app.delete('/api/v1/builder/drafts/{did}')
    def delete_draft(did:str,revision:int,user=Depends(admin)):
        with closing(db()) as con:
            r=con.execute('UPDATE builder_drafts SET deleted_at=?,revision=revision+1 WHERE id=? AND revision=? AND deleted_at IS NULL',(now(),did,revision))
            if not r.rowcount:raise HTTPException(409,'Черновик изменён или уже удалён. Обновите список')
            con.commit()
        return {'deleted':True}
    @app.delete('/api/v1/builder/editions/{cid}')
    def delete_edition(cid:str,user=Depends(admin)):
        with closing(db()) as con:
            r=con.execute('UPDATE builder_editions SET deleted_at=? WHERE course_id=? AND deleted_at IS NULL',(now(),cid))
            if not r.rowcount:raise HTTPException(404,'Версия не найдена или уже удалена')
            con.commit()
        return {'deleted':True}
    @app.get('/api/v1/builder/trash')
    def trash(user=Depends(admin)):
        with closing(db()) as con:
            drafts=[{'id':r['id'],'title':json.loads(r['content'])['title'],'deleted_at':r['deleted_at']} for r in con.execute('SELECT * FROM builder_drafts WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC')]
            editions=[dict(r) for r in con.execute('SELECT course_id,title,version,deleted_at FROM builder_editions WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC')]
        return {'drafts':drafts,'editions':editions}
    @app.post('/api/v1/builder/drafts/{did}/restore')
    def restore_draft(did:str,user=Depends(admin)):
        with closing(db()) as con:
            r=con.execute('UPDATE builder_drafts SET deleted_at=NULL,revision=revision+1,updated_at=? WHERE id=? AND deleted_at IS NOT NULL',(now(),did))
            if not r.rowcount:raise HTTPException(404,'Черновик не найден в корзине')
            con.commit()
        return {'restored':True}
    @app.post('/api/v1/builder/editions/{cid}/restore')
    def restore_edition(cid:str,user=Depends(admin)):
        with closing(db()) as con:
            r=con.execute('UPDATE builder_editions SET deleted_at=NULL WHERE course_id=? AND deleted_at IS NOT NULL',(cid,))
            if not r.rowcount:raise HTTPException(404,'Версия не найдена в корзине')
            con.commit()
        return {'restored':True}
    @app.post('/api/v1/builder/editions/{cid}/archive')
    def archive(cid:str,user=Depends(admin)):
        with closing(db()) as con:
            r=con.execute('UPDATE builder_editions SET archived=1 WHERE course_id=?',(cid,))
            if not r.rowcount: raise HTTPException(404,'Версия не найдена')
            con.commit()
        return {'archived':True}
    @app.get('/api/v1/workbooks/{cid}')
    def workbook(cid:str):
        r=edition(cid)
        if not r: raise HTTPException(404,'Тетрадь не найдена')
        if r.get('deleted_at'):raise HTTPException(410,'Тетрадь удалена преподавателем')
        c=json.loads(r['content'])
        for s in c['sections']:
            for b in s['blocks']:
                b.pop('correct',None);b.pop('explanation',None)
                for step in b.get('steps',[]):step.pop('correct',None)
        return {'course_id':cid,'version':r['version'],'content':c}
    @app.post('/api/v1/builder/images')
    def image(data:ImageInput,user=Depends(admin)):
        try: raw=base64.b64decode(data.data,validate=True)
        except ValueError: raise HTTPException(422,'Неверный файл')
        if not raw or len(raw)>5_000_000: raise HTTPException(413,'Картинка: не более 5 МБ')
        ext=None
        if raw.startswith(b'\x89PNG\r\n\x1a\n'): ext='png'
        elif raw.startswith(b'\xff\xd8\xff'): ext='jpg'
        elif raw.startswith((b'GIF87a',b'GIF89a')): ext='gif'
        elif raw.startswith(b'RIFF') and raw[8:12]==b'WEBP': ext='webp'
        if not ext: raise HTTPException(422,'Поддерживаются PNG, JPG, GIF и WebP')
        iid=secrets.token_hex(16);folder=data_dir/'builder-images';folder.mkdir(parents=True,exist_ok=True);(folder/(iid+'.'+ext)).write_bytes(raw)
        with closing(db()) as con: con.execute('INSERT INTO builder_images VALUES(?,?)',(iid,ext));con.commit()
        return {'url':'/api/v1/builder/images/'+iid}
    @app.get('/api/v1/builder/images/{iid}')
    def get_image(iid:str):
        with closing(db()) as con: r=con.execute('SELECT extension FROM builder_images WHERE id=?',(iid,)).fetchone()
        if not r or not re.fullmatch(r'[a-f0-9]{32}',iid): raise HTTPException(404,'Картинка не найдена')
        return FileResponse(data_dir/'builder-images'/(iid+'.'+r['extension']),headers={'X-Content-Type-Options':'nosniff'})
    def normalize_state(cid,state):
        if cid not in builtins:return state
        state=json.loads(json.dumps(state));state.setdefault('answers',{});state.setdefault('checks',{})
        if not isinstance(state['answers'],dict) or not isinstance(state['checks'],dict):raise HTTPException(422,'Неверный формат ответов')
        for sec in builtins[cid]['sections']:
            for b in sec['blocks']:
                if b.get('legacyQuiz') and type(state['answers'].get(b['id'])) is int:state['answers'][b['id']]=[state['answers'][b['id']]]
                if b.get('legacyPractice'):state['checks'][b['id']]=state['checks'].get(b['legacyKey'],False)
        return state

    @app.post('/api/v1/workbooks/{cid}/games/{bid}/check')
    def check_game(cid:str,bid:str,data:dict,user=Depends(student)):
        r=edition(cid)
        if not r or r.get('deleted_at'):raise HTTPException(404,'Тетрадь не найдена')
        c=json.loads(r['content']);b=next((b for sec in c['sections'] for b in sec['blocks'] if b['id']==bid and b['type'] in games.TYPES),None)
        if not b:raise HTTPException(404,'Игра не найдена')
        valid,right=games.evaluate(b,data)
        if not valid:raise HTTPException(422,'Завершите решение игры')
        manual=b.get('reviewMode','inherit')=='manual' or (b.get('reviewMode','inherit')=='inherit' and c.get('reviewMode')=='manual')
        return {'correct':right,'points':b.get('points',10) if right and not manual else 0,'manual':manual,'explanation':b.get('explanation','')}

    def score(cid,state):
        r=edition(cid)
        if not r:return None
        c=json.loads(r['content']);state=normalize_state(cid,state);answers=state.get('answers',{});checks=state.get('checks',{})
        if not isinstance(answers,dict) or not isinstance(checks,dict): raise HTTPException(422,'Неверный формат ответов')
        qs=[b for s in c['sections'] for b in s['blocks'] if b['type'] in {'single','multiple','boolean','open'} | games.TYPES];tasks=[b for s in c['sections'] for b in s['blocks'] if b['type']=='practice'];answered=correct=0
        for b in qs:
            a=answers.get(b['id'])
            if b['type'] in games.TYPES:
                valid,right=games.evaluate(b,a);answered+=int(valid);correct+=int(right)
            elif b['type']=='open':answered+=int(isinstance(a,str) and bool(a.strip()))
            elif isinstance(a,list) and a and all(type(i) is int and 0<=i<len(b['options']) for i in a) and len(set(a))==len(a):
                answered+=1;correct+=int(sorted(set(a))==sorted(b['correct']))
        evidence=state.get('evidence',{})
        if not isinstance(evidence,dict):raise HTTPException(422,'Неверные скриншоты')
        checked=sum(checks.get(b['id']) is True and (not b.get('evidenceRequired') or bool(evidence.get(b['id']))) for b in tasks);total=len(qs)+len(tasks);completed=answered+checked
        if not total:
            total=len(c['sections']);done=state.get('done',{});completed=sum(done.get(str(i)) is True for i in range(total)) if isinstance(done,dict) else 0
        manual=[b for b in qs+tasks if b.get('reviewMode','inherit')=='manual' or (b.get('reviewMode','inherit')=='inherit' and c.get('reviewMode','auto')=='manual')]
        auto_points=0
        base_points={}
        for b in qs+tasks:
            if b in manual:base_points[b['id']]=0;continue
            if b['type'] in games.TYPES:earned=b.get('points',10) if games.evaluate(b,answers.get(b['id']))[1] else 0
            elif b['type']=='practice':earned=10 if checks.get(b['id']) is True and (not b.get('evidenceRequired') or bool(evidence.get(b['id']))) else 0
            elif b['type']=='open':earned=0
            else:
                a=answers.get(b['id']);earned=10 if isinstance(a,list) and all(type(i) is int for i in a) and len(set(a))==len(a) and sorted(a)==sorted(b['correct']) else 0
            if b.get('legacyPractice'):earned=0
            auto_points+=earned;base_points[b['id']]=earned
        return {'percent':round(completed/total*100) if total else 0,'basePoints':base_points,'manualBlocks':[b['id'] for b in manual],'pendingReview':len(manual),'correct':correct,'totalQ':sum(b['type']!='open' for b in qs),'answered':answered,'checked':checked,'totalChecks':len(tasks),'points':auto_points,'openAnswers':sum(b['type']=='open' for b in qs)}
    def apply_reviews(con, uid, cid, attempt, summary):
        rows=con.execute('SELECT review FROM builder_reviews WHERE user_id=? AND course_id=? AND attempt_no=?',(uid,cid,attempt)).fetchall()
        reviews=[json.loads(r['review']) for r in rows]
        summary=dict(summary)
        review_map={r['block_id']:r for r in reviews}
        summary['points']=max(0,summary['points']+sum(r['points']-summary.get('basePoints',{}).get(bid,0) for bid,r in review_map.items() if r['checked']))
        summary['pendingReview']=sum(not review_map.get(bid,{}).get('checked') for bid in summary.get('manualBlocks',[]))
        summary['reviewed']=sum(r['checked'] for r in reviews)
        summary['needsRecheck']=sum(bool(r.get('needs_recheck')) for r in reviews)
        return summary

    def authorized(authorization):
        # Reuse the existing signed tokens; evidence is never served anonymously.
        try: return student(authorization), False
        except HTTPException:
            return admin(authorization), True

    @app.post('/api/v1/workbooks/{cid}/evidence/{bid}')
    def evidence(cid:str,bid:str,data:ImageInput,user=Depends(student)):
        row=edition(cid)
        if not row: raise HTTPException(404,'Тетрадь не найдена')
        content=json.loads(row['content']);block=next((b for s in content['sections'] for b in s['blocks'] if b['id']==bid and b['type']=='practice'),None)
        if not block: raise HTTPException(404,'Задание не найдено')
        try: raw=base64.b64decode(data.data,validate=True)
        except ValueError: raise HTTPException(422,'Неверная картинка')
        if not raw or len(raw)>5_000_000: raise HTTPException(413,'Скриншот: не более 5 МБ')
        ext=None
        if raw.startswith(b'\x89PNG\r\n\x1a\n'):ext='png'
        elif raw.startswith(b'\xff\xd8\xff'):ext='jpg'
        elif raw.startswith(b'RIFF') and raw[8:12]==b'WEBP':ext='webp'
        if not ext: raise HTTPException(422,'Поддерживаются PNG, JPG и WebP')
        iid=secrets.token_hex(16)
        with closing(db()) as con:
            attempt=con.execute('SELECT attempt_no FROM progress WHERE user_id=? AND course_id=?',(user['uid'],cid)).fetchone()
            if not attempt:raise HTTPException(409,'Сначала войдите в тетрадь')
            folder=data_dir/'builder-evidence';folder.mkdir(parents=True,exist_ok=True);(folder/(iid+'.'+ext)).write_bytes(raw)
            con.execute('INSERT INTO builder_evidence VALUES(?,?,?,?,?,?)',(iid,user['uid'],cid,attempt['attempt_no'],bid,ext));con.commit()
        return {'id':iid}

    from fastapi import Header
    @app.get('/api/v1/builder/evidence/{iid}')
    def read_evidence(iid:str,authorization:str|None=Header(None)):
        user,is_admin=authorized(authorization)
        with closing(db()) as con:r=con.execute('SELECT * FROM builder_evidence WHERE id=?',(iid,)).fetchone()
        if not r or not re.fullmatch(r'[a-f0-9]{32}',iid):raise HTTPException(404,'Скриншот не найден')
        if not is_admin and r['user_id']!=user['uid']:raise HTTPException(403,'Нет доступа')
        return FileResponse(data_dir/'builder-evidence'/(iid+'.'+r['extension']),headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})

    @app.get('/api/v1/builder/reviews/{uid}/{cid}/{attempt}')
    def reviews(uid:str,cid:str,attempt:int,user=Depends(admin)):
        with closing(db()) as con:
            r=con.execute('SELECT * FROM course_attempts WHERE user_id=? AND course_id=? AND attempt_no=?',(uid,cid,attempt)).fetchone()
            if not r:raise HTTPException(404,'Попытка не найдена')
            reviews={x['block_id']:json.loads(x['review']) for x in con.execute('SELECT * FROM builder_reviews WHERE user_id=? AND course_id=? AND attempt_no=?',(uid,cid,attempt))}
        edition_row=edition(cid)
        if not edition_row:raise HTTPException(404,'Тетрадь создана вне конструктора')
        return {'content':json.loads(edition_row['content']),'state':normalize_state(cid,json.loads(r['state_json'])),'summary':json.loads(r['summary_json']),'reviews':reviews,'updated_at':r['updated_at']}

    @app.put('/api/v1/builder/reviews/{uid}/{cid}/{attempt}/{bid}')
    def review(uid:str,cid:str,attempt:int,bid:str,data:dict,user=Depends(admin)):
        points=data.get('points');checked=data.get('checked');comment=data.get('comment','')
        if type(points) is not int or not 0<=points<=100 or type(checked) is not bool or not isinstance(comment,str) or len(comment)>10000:raise HTTPException(422,'Баллы: 0–100; комментарий: до 10000 символов')
        row=edition(cid)
        if not row:raise HTTPException(404,'Тетрадь не найдена')
        c=json.loads(row['content']);block=next((b for s in c['sections'] for b in s['blocks'] if b['id']==bid and b['type'] in {'single','multiple','boolean','open','practice'} | games.TYPES),None)
        if not block:raise HTTPException(404,'Задание не найдено')
        with closing(db()) as con:
            con.execute('BEGIN IMMEDIATE')
            a=con.execute('SELECT * FROM course_attempts WHERE user_id=? AND course_id=? AND attempt_no=?',(uid,cid,attempt)).fetchone()
            if not a:raise HTTPException(404,'Попытка не найдена')
            if data.get('updated_at')!=a['updated_at']:raise HTTPException(409,'Студент изменил работу. Обновите её перед проверкой')
            state=json.loads(a['state_json']);answer=state.get('answers',{}).get(bid)
            base=10 if block['type'] in ('single','multiple','boolean') and isinstance(answer,list) and all(type(i) is int for i in answer) and sorted(set(answer))==sorted(block['correct']) else 0
            value={'block_id':bid,'needs_recheck':False,'checked':checked,'points':points,'base_points':base,'comment':comment,'reviewer':user.get('name'),'reviewed_at':now()}
            con.execute('INSERT INTO builder_reviews VALUES(?,?,?,?,?,?) ON CONFLICT(user_id,course_id,attempt_no,block_id) DO UPDATE SET review=excluded.review,updated_at=excluded.updated_at',(uid,cid,attempt,bid,json.dumps(value,ensure_ascii=False),now()))
            summary=apply_reviews(con,uid,cid,attempt,score(cid,state));packed=json.dumps(summary,ensure_ascii=False)
            con.execute('UPDATE course_attempts SET summary_json=? WHERE user_id=? AND course_id=? AND attempt_no=?',(packed,uid,cid,attempt))
            con.execute('UPDATE progress SET summary_json=? WHERE user_id=? AND course_id=? AND attempt_no=?',(packed,uid,cid,attempt));con.commit()
        return {'saved':True,'summary':summary}
    @app.get('/api/v1/workbooks/{cid}/feedback')
    def feedback(cid:str,user=Depends(student)):
        with closing(db()) as con:
            r=con.execute('SELECT attempt_no FROM progress WHERE user_id=? AND course_id=?',(user['uid'],cid)).fetchone()
            if not r:return {}
            return {x['block_id']:json.loads(x['review']) for x in con.execute('SELECT * FROM builder_reviews WHERE user_id=? AND course_id=? AND attempt_no=?',(user['uid'],cid,r['attempt_no']))}
    return edition,titles,score,apply_reviews
