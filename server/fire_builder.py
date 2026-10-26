"""Scoped constructor for structured fire workbooks. Legacy data is never migrated."""
import base64
import copy
import csv
import io
import json
import re
import secrets
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit
from fastapi import Depends, HTTPException, Header
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
import fire

PREFIX = '/api/v1/fire/builder'
class Draft(BaseModel):
    revision: int = 0
    content: dict
class Revision(BaseModel):
    revision: int

def require(ok, message):
    if not ok: raise HTTPException(422, message)

def safe_url(value):
    if not isinstance(value, str): return False
    if re.fullmatch(r'/v4/fire/assets/[a-zA-Z0-9_.-]+', value): return True
    if re.fullmatch(r'/api/v1/fire/builder/images/[a-f0-9]{32}', value): return True
    u = urlsplit(value)
    return u.scheme in ('https', 'http') and bool(u.hostname) and not u.username and not u.password

def validate(c):
    require(len(json.dumps(c)) < 700000, 'Тетрадь слишком большая')
    def text(v, label, limit=20000, empty=True):
        require(isinstance(v,str) and len(v)<=limit and (empty or bool(v.strip())), label)
    text(c.get('title'), 'Укажите название',200,False)
    require(type(c.get('passPercent')) is int and 60<=c['passPercent']<=100,'Порог: 60–100%')
    require(isinstance(c.get('modules'),list) and 1<=len(c['modules'])<=100,'Добавьте разделы')
    require(isinstance(c.get('final'),list) and 1<=len(c['final'])<=100,'Добавьте итоговые вопросы')
    ids=set()
    def identity(o):
        v=o.get('id');require(isinstance(v,str) and re.fullmatch(r'[A-Za-z0-9_-]{1,80}',v) and v not in ids,'Идентификаторы должны быть уникальны');ids.add(v)
    def material(m):
        text(m.get('title'),'Название материала',200,False)
        require(m.get('kind') in ('instruction','sop','video','link','image'),'Укажите тип материала')
        require(safe_url(m.get('url')),'Нужна безопасная ссылка http(s) или локальный материал')
        text(m.get('note',''),'Описание материала')
    def question(q,exam=False):
        identity(q);require(q.get('type') in ('choice','multi','order'),'Неверный тип вопроса')
        require(not exam or q['type']=='choice','В итоговом тесте используется один ответ')
        text(q.get('text'),'Текст задания',20000,False);text(q.get('explain',''),'Разбор ответа')
        opts=q.get('options');require(isinstance(opts,list) and 2<=len(opts)<=30 and all(isinstance(x,str) and 0<len(x)<=2000 for x in opts),'Заполните варианты (2–30)')
        a=q.get('answer')
        if q['type']=='choice':require(type(a) is int and 0<=a<len(opts),'Выберите правильный ответ')
        else:
            require(isinstance(a,list) and len(a)>0 and all(type(x) is int and 0<=x<len(opts) for x in a) and len(a)==len(set(a)),'Укажите правильные варианты')
            if q['type']=='order':require(sorted(a)==list(range(len(opts))),'В порядке должны участвовать все варианты')
        require(type(q.get('critical',False)) is bool,'Неверная отметка критического вопроса')
    for m in c['modules']:
        identity(m);text(m.get('title'),'Название раздела',200,False)
        for k in ('lead','source','prompt','gamesTitle'):text(m.get(k,''),'Текст раздела')
        require(type(m.get('minutes')) is int and 0<=m['minutes']<=1000,'Длительность раздела')
        require(isinstance(m.get('theory'),list) and len(m['theory'])<=200,'Неверные учебные блоки')
        for pair in m['theory']:
            require(isinstance(pair,list) and len(pair)==2,'Учебный блок: заголовок и текст')
            for v in pair:text(v,'Текст учебного блока')
        for k in ('quiz','games'):
            require(isinstance(m.get(k),list) and len(m[k])<=200,'Неверный список заданий')
            for q in m[k]:question(q)
        for img in m.get('images',[]):
            require(safe_url(img.get('url')),'Ссылка изображения');text(img.get('title',''),'Подпись изображения')
        for mt in m.get('materials',[]):material(mt)
        require(m.get('scenarioMode','list') in ('list','smoke'),'Неверный сценарий')
        if m.get('scenarioMode')=='smoke':
            require(len(m['games'])>=4,'Сценарий дыма: не менее четырёх заданий')
            require(len(m.get('scenarioLabels',[]))==3 and len(m.get('mapLabels',[]))==3,'Сценарий: три обстановки и три участка')
            for v in m['scenarioLabels']+m['mapLabels']:text(v,'Подпись сценария',200,False)
        for b in m.get('blocks',[]):
            identity(b);require(b.get('type') in ('text','practice','video','image','link','open'),'Неверный дополнительный блок')
            text(b.get('text',''),'Текст блока');require(b.get('reviewMode','auto') in ('auto','manual'),'Режим проверки')
            if b['type'] in ('video','image','link'):require(safe_url(b.get('url')),'Ссылка блока')
            require(type(b.get('maxImages',3)) is int and 1<=b.get('maxImages',3)<=10,'Скриншоты: 1–10')
            require(type(b.get('evidenceRequired',False)) is bool,'Требование скриншота')
    for q in c['final']:question(q,True)
    for mt in c.get('materials',[]):material(mt)
    for mt in c.get('sources',[]):material(mt)
    require(isinstance(c.get('settings',{}),dict),'Настройки объекта')
    for k,v in c.get('settings',{}).items():require(k in fire.DEFAULTS and isinstance(v,str) and len(v)<=300,'Настройки объекта')
    for k in ('intro','priorityTitle','priorityText','locationIntro','locationHint','locationNotice','disclaimer','methodText'):
        text(c.get(k,''),'Вводный текст')
    return c

def seed(static):
    c=json.loads((Path(static)/'v4/fire/data.json').read_text())
    c.pop('id',None);c['engine']='fire';c['settings']={k:'' for k in fire.DEFAULTS if k!='passPercent'}
    c.update(intro='Начните с изучения инструкции по пожарной безопасности, затем отработайте безопасные действия в тренажёрах и проверьте знания в итоговом тесте.',priorityTitle='Главный приоритет — безопасность людей.',priorityText='Оповестите, вызовите пожарную охрану, эвакуируйтесь. Тушение начального очага допустимо только без угрозы жизни и здоровью.',locationIntro='Заполните сведения о вашем объекте и порядке действий при пожаре.',locationHint='Если не знаете эти сведения, уточните их у ответственного за пожарную безопасность на объекте или у вашего руководителя.',locationNotice='План эвакуации изучите на своём объекте. Условные задачи в тетради его не заменяют.',disclaimer='Тетрадь — учебное сопровождение инструкции. Результат теста не заменяет противопожарный инструктаж, очную практику и установленное оформление допуска.')
    c['materials']=[dict(kind='instruction',title='Открыть и изучить инструкцию по ПБ',url='https://disk.360.yandex.ru/i/Y-da-9hfeoi0LQ',note='Перед выполнением заданий откройте и изучите инструкцию по пожарной безопасности. При необходимости возвращайтесь к ней во время обучения.')]
    for mt in c['sources']:
        mt['kind']='instruction' if mt['url'].startswith('assets/') else 'link'
        if mt['url'].startswith('assets/'):mt['url']='/v4/fire/'+mt['url']
    titles=['Мини-игра «Инспектор смены»','Тренажёр вызова помощи','Ситуации с дымом','Тренажёр выбора огнетушителя','Соберите порядок действий','Мини-игра «Закрой смену»']
    images={'prevent':[('image2.jpeg','Курение запрещено'),('image3.jpeg','Курение и открытый огонь запрещены'),('image1.jpeg','Место для курения')],'ext':[('image4.png','Плакат из инструкции: порошковый огнетушитель'),('image5.png','Плакат из инструкции: углекислотный огнетушитель')],'use':[('image6.png','Плакат из инструкции: пожарный кран')]}
    for m,title in zip(c['modules'],titles):
        m.update(gamesTitle=title,images=[dict(url='/v4/fire/assets/'+f,title=t) for f,t in images.get(m['id'],[])],materials=[],blocks=[],scenarioMode='smoke' if m['id']=='smoke' else 'list')
        if m['id']=='smoke':m.update(scenarioLabels=['Признаки горения','Задымлённый маршрут','Выходы недоступны'],mapLabels=['А · короткий коридор. Дым','Б · лестница к выходу. Путь свободен','В · лифт. Для эвакуации не используется'])
    return validate(c)

def register(app, db, admin, student, now, static, data_dir, backup):
    def init():
        with closing(db()) as con:
            con.executescript('''CREATE TABLE IF NOT EXISTS fb_drafts(id TEXT PRIMARY KEY,content TEXT NOT NULL,revision INTEGER NOT NULL,updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS fb_editions(id TEXT PRIMARY KEY,draft_id TEXT NOT NULL,version INTEGER NOT NULL,content TEXT NOT NULL,published_at TEXT NOT NULL,UNIQUE(draft_id,version));
            CREATE TABLE IF NOT EXISTS fb_attempts(uid TEXT NOT NULL,cid TEXT NOT NULL,attempt INTEGER NOT NULL,state TEXT NOT NULL,summary TEXT NOT NULL,updated_at TEXT NOT NULL,review TEXT NOT NULL DEFAULT '{}',PRIMARY KEY(uid,cid,attempt));
            CREATE TABLE IF NOT EXISTS fb_images(id TEXT PRIMARY KEY,extension TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS fb_evidence(id TEXT PRIMARY KEY,uid TEXT NOT NULL,cid TEXT NOT NULL,attempt INTEGER NOT NULL,bid TEXT NOT NULL,extension TEXT NOT NULL);''');con.commit()
    app.add_event_handler('startup',init)
    def draft(con,did):
        r=con.execute('SELECT * FROM fb_drafts WHERE id=?',(did,)).fetchone()
        if not r:raise HTTPException(404,'Черновик ПБ не найден')
        return dict(r)
    def edition(con,cid):
        r=con.execute('SELECT * FROM fb_editions WHERE id=?',(cid,)).fetchone()
        if not r:raise HTTPException(404,'Версия ПБ не найдена')
        return dict(r)
    def packed(r):return {**r,'content':json.loads(r['content'])}
    @app.get(PREFIX+'/drafts')
    def drafts(u=Depends(admin)):
        with closing(db()) as con:return [packed(dict(r)) for r in con.execute('SELECT * FROM fb_drafts ORDER BY updated_at DESC')]
    @app.post(PREFIX+'/import')
    def import_legacy(u=Depends(admin)):
        # Import only authored content, never participant records. Repeat calls reuse the draft.
        with closing(db()) as con:
            r=con.execute("SELECT * FROM fb_drafts WHERE id='fire-original'").fetchone()
            if r:return packed(dict(r))
            content=seed(static)
            setting=con.execute('SELECT content FROM fire_settings WHERE id=1').fetchone()
            if setting:
                cfg=json.loads(setting['content']);content['passPercent']=cfg.get('passPercent',80);content['settings']={k:cfg.get(k,'') for k in content['settings']}
            con.execute('INSERT INTO fb_drafts VALUES(?,?,1,?)',('fire-original',json.dumps(content,ensure_ascii=False),now()));con.commit()
            return packed(draft(con,'fire-original'))
    @app.get(PREFIX+'/drafts/{did}')
    def get_draft(did:str,u=Depends(admin)):
        with closing(db()) as con:return packed(draft(con,did))
    @app.put(PREFIX+'/drafts/{did}')
    def save(did:str,d:Draft,u=Depends(admin)):
        validate(d.content)
        with closing(db()) as con:
            r=con.execute('UPDATE fb_drafts SET content=?,revision=revision+1,updated_at=? WHERE id=? AND revision=?',(json.dumps(d.content,ensure_ascii=False),now(),did,d.revision))
            if not r.rowcount:raise HTTPException(409,'Конфликт ревизии: откройте актуальный черновик, изменения другого окна сохранены')
            con.commit();return packed(draft(con,did))
    @app.post(PREFIX+'/drafts/{did}/publish')
    def publish(did:str,d:Revision,u=Depends(admin)):
        backup('fire-publication')
        with closing(db()) as con:
            con.execute('BEGIN IMMEDIATE');r=draft(con,did)
            if r['revision']!=d.revision:raise HTTPException(409,'Сначала сохраните актуальный черновик')
            validate(json.loads(r['content']))
            v=con.execute('SELECT COALESCE(MAX(version),0)+1 FROM fb_editions WHERE draft_id=?',(did,)).fetchone()[0]
            cid='pb-'+secrets.token_hex(12)
            con.execute('INSERT INTO fb_editions VALUES(?,?,?,?,?)',(cid,did,v,r['content'],now()));con.commit()
            return dict(id=cid,version=v,url='/v4/fire/editable.html?edition='+cid)
    @app.get(PREFIX+'/editions')
    def editions(u=Depends(admin)):
        with closing(db()) as con:return [dict(id=r['id'],draft_id=r['draft_id'],version=r['version'],title=json.loads(r['content'])['title'],published_at=r['published_at']) for r in con.execute('SELECT * FROM fb_editions ORDER BY published_at DESC')]
    @app.get(PREFIX+'/editions/{cid}')
    def get_edition(cid:str):
        with closing(db()) as con:r=packed(edition(con,cid))
        for q in fire.tasks(r['content'])+r['content']['final']:
            q.pop('answer',None);q.pop('explain',None)
        return r
    @app.get(PREFIX+'/editions/{cid}/author')
    def get_author(cid:str,u=Depends(admin)):
        with closing(db()) as con:return packed(edition(con,cid))
    @app.get(PREFIX+'/catalog')
    def catalog():
        with closing(db()) as con:
            rows=con.execute('SELECT e.* FROM fb_editions e WHERE version=(SELECT MAX(version) FROM fb_editions WHERE draft_id=e.draft_id)').fetchall()
            return [dict(id=r['id'],version=r['version'],title=json.loads(r['content'])['title'],url='/v4/fire/editable.html?edition='+r['id']) for r in rows]
    def latest(con,uid,cid):return con.execute('SELECT * FROM fb_attempts WHERE uid=? AND cid=? ORDER BY attempt DESC LIMIT 1',(uid,cid)).fetchone()
    def feedback(c,s):
        def one(q,v):return dict(value=v,correct=fire.is_correct(q,v),explain=q.get('explain',''))
        return dict(current={q['id']:one(q,s['answers'][q['id']]) for q in fire.tasks(c) if q['id'] in s.get('answers',{})},first={q['id']:one(q,s['first'][q['id']]) for q in fire.tasks(c) if q['id'] in s.get('first',{})},exam={q['id']:one(q,s['exam'][q['id']]) for q in c['final'] if s.get('submitted') and q['id'] in s.get('exam',{})})
    def parse(r,c):
        if not r:return dict(state=None,summary=None,attempt_no=1)
        return dict(state=json.loads(r['state']),summary=json.loads(r['summary']),attempt_no=r['attempt'],updated_at=r['updated_at'],review=json.loads(r['review']),feedback=feedback(c,json.loads(r['state'])))
    def supplements(con,cid,uid,attempt,c,s):
        evidence=s.setdefault('evidence',{});checks=s.setdefault('checks',{});responses=s.setdefault('responses',{})
        require(all(isinstance(x,dict) for x in (evidence,checks,responses)),'Неверная практика')
        blocks={b['id']:b for m in c['modules'] for b in m.get('blocks',[])}
        for bid,imgs in evidence.items():
            b=blocks.get(bid);require(b and b['type']=='practice' and isinstance(imgs,list) and len(imgs)<=b.get('maxImages',3),'Скриншоты задания')
            for iid in imgs:
                require(isinstance(iid,str),'Скриншот задания')
                require(con.execute('SELECT 1 FROM fb_evidence WHERE id=? AND uid=? AND cid=? AND attempt=? AND bid=?',(iid,uid,cid,attempt,bid)).fetchone() is not None,'Чужой скриншот')
        for bid,v in responses.items():require(bid in blocks and isinstance(v,str) and len(v)<=4000,'Ответ практического задания')
        return all((bool(responses.get(b['id'],'').strip()) if b['type']=='open' else checks.get(b['id']) is True and (not b.get('evidenceRequired') or bool(evidence.get(b['id'])))) for b in blocks.values() if b['type'] in ('practice','open'))
    def feedback_score(sm,review):
        sm['needsRecheck']=bool(review.get('needs_recheck'));sm['reviewed']=bool(review.get('checked'))
        sm['teacherPoints']=review.get('points') if review.get('checked') else None
        return sm
    @app.get(PREFIX+'/editions/{cid}/progress')
    def progress(cid:str,u=Depends(student)):
        with closing(db()) as con:c=json.loads(edition(con,cid)['content']);return parse(latest(con,u['uid'],cid),c)
    @app.put(PREFIX+'/editions/{cid}/progress')
    def put_progress(cid:str,payload:dict,u=Depends(student)):
        require(isinstance(payload.get('state'),dict) and len(json.dumps(payload))<=250000,'Неверный формат ответа')
        with closing(db()) as con:
            con.execute('BEGIN IMMEDIATE');c=json.loads(edition(con,cid)['content']);r=latest(con,u['uid'],cid);a=r['attempt'] if r else 1
            old=json.loads(r['state']) if r else {};incoming=copy.deepcopy(payload['state']);incoming['first']={};s=fire.prepare_state(incoming,old,c)
            supplements(con,cid,u['uid'],a,c,s)
            review=json.loads(r['review']) if r else {}
            if review and any(s.get(k)!=old.get(k) for k in ('answers','notes','responses','checks','evidence')):review.update(checked=False,needs_recheck=True,changed_at=now())
            sm=feedback_score(fire.score(s,c['passPercent'],c),review);timestamp=now()
            con.execute('INSERT INTO fb_attempts VALUES(?,?,?,?,?,?,?) ON CONFLICT(uid,cid,attempt) DO UPDATE SET state=excluded.state,summary=excluded.summary,updated_at=excluded.updated_at,review=excluded.review',(u['uid'],cid,a,json.dumps(s,ensure_ascii=False),json.dumps(sm),timestamp,json.dumps(review,ensure_ascii=False)));con.commit()
            return dict(saved=True,summary=sm,attempt_no=a,updated_at=timestamp,feedback=feedback(c,s))
    @app.post(PREFIX+'/editions/{cid}/submit')
    def submit(cid:str,u=Depends(student)):
        with closing(db()) as con:
            con.execute('BEGIN IMMEDIATE');e=edition(con,cid);c=json.loads(e['content']);r=latest(con,u['uid'],cid)
            if not r:raise HTTPException(409,'Сначала сохраните результаты')
            s=json.loads(r['state']);fire.validate_state(s,c)
            if not s.get('submitted'):
                if not all(s['read'].get(m['id']) is True for m in c['modules']) or not all(q['id'] in s['answers'] for q in fire.tasks(c)) or not supplements(con,cid,u['uid'],r['attempt'],c,s):raise HTTPException(409,'Завершите разделы, тренировку и практику')
                if not all(q['id'] in s['exam'] for q in c['final']):raise HTTPException(422,'Ответьте на все вопросы')
                s.update(submitted=True,submittedAt=now(),examThreshold=c['passPercent'])
            sm=feedback_score(fire.score(s,c['passPercent'],c),json.loads(r['review']))
            con.execute('UPDATE fb_attempts SET state=?,summary=?,updated_at=? WHERE uid=? AND cid=? AND attempt=?',(json.dumps(s,ensure_ascii=False),json.dumps(sm),now(),u['uid'],cid,r['attempt']));con.commit()
            return dict(state=s,summary=sm,attempt=r['attempt'],feedback=feedback(c,s))
    @app.post(PREFIX+'/editions/{cid}/restart')
    def restart(cid:str,u=Depends(student)):
        with closing(db()) as con:
            con.execute('BEGIN IMMEDIATE');c=json.loads(edition(con,cid)['content']);r=latest(con,u['uid'],cid);a=r['attempt']+1 if r else 1
            s={'profile':json.loads(r['state']).get('profile',{}) if r else {}};fire.validate_state(s,c)
            con.execute('INSERT INTO fb_attempts(uid,cid,attempt,state,summary,updated_at) VALUES(?,?,?,?,?,?)',(u['uid'],cid,a,json.dumps(s),json.dumps(fire.score(s,c['passPercent'],c)),now()));con.commit()
            return {'attempt_no':a}
    @app.get(PREFIX+'/editions/{cid}/results')
    def results(cid:str,u=Depends(admin)):
        with closing(db()) as con:
            e=edition(con,cid)
            rows=con.execute('SELECT a.*,u.full_name FROM fb_attempts a JOIN users u ON u.id=a.uid WHERE cid=? ORDER BY u.full_name,attempt',(cid,)).fetchall()
            return {'rows':[dict(user_id=r['uid'],full_name=r['full_name'],attempt=r['attempt'],state=json.loads(r['state']),summary=json.loads(r['summary']),review=json.loads(r['review']),updated_at=r['updated_at'],course_id=cid,version=e['version']) for r in rows]}
    @app.put(PREFIX+'/editions/{cid}/review')
    def review(cid:str,p:dict,u=Depends(admin)):
        require(isinstance(p.get('comment',''),str) and len(p.get('comment',''))<=4000,'Комментарий: до 4000 знаков')
        require(type(p.get('practical',False)) is bool and type(p.get('checked',True)) is bool,'Неверная отметка')
        require(p.get('points') is None or type(p['points']) is int and 0<=p['points']<=100,'Оценка: 0–100')
        with closing(db()) as con:
            con.execute('BEGIN IMMEDIATE');edition(con,cid)
            r=con.execute('SELECT * FROM fb_attempts WHERE uid=? AND cid=? AND attempt=?',(p.get('user_id'),cid,p.get('attempt'))).fetchone()
            if not r:raise HTTPException(404,'Попытка не найдена')
            if p.get('updated_at')!=r['updated_at']:raise HTTPException(409,'Участник изменил работу: обновите данные')
            value={k:p.get(k) for k in ('comment','practical','points')};value.update(checked=p.get('checked',True),needs_recheck=False,reviewer=u.get('name'),reviewed_at=now())
            sm=feedback_score(json.loads(r['summary']),value)
            con.execute('UPDATE fb_attempts SET review=?,summary=? WHERE uid=? AND cid=? AND attempt=?',(json.dumps(value,ensure_ascii=False),json.dumps(sm),r['uid'],cid,r['attempt']));con.commit()
            return dict(saved=True,summary=sm)
    def image_bytes(p):
        try:raw=base64.b64decode(p['data'],validate=True)
        except Exception:raise HTTPException(422,'Неверное изображение')
        require(0<len(raw)<=5000000,'Изображение: до 5 МБ')
        ext='png' if raw.startswith(b'\x89PNG\r\n\x1a\n') else 'jpg' if raw.startswith(b'\xff\xd8\xff') else 'webp' if raw.startswith(b'RIFF') and raw[8:12]==b'WEBP' else None
        require(ext is not None,'Поддерживаются PNG, JPEG, WebP');return raw,ext
    @app.post(PREFIX+'/images')
    def upload(p:dict,u=Depends(admin)):
        raw,ext=image_bytes(p);iid=secrets.token_hex(16);folder=Path(data_dir)/'fire-builder-images';folder.mkdir(exist_ok=True)
        (folder/(iid+'.'+ext)).write_bytes(raw)
        with closing(db()) as con:con.execute('INSERT INTO fb_images VALUES(?,?)',(iid,ext));con.commit()
        return {'url':PREFIX+'/images/'+iid}
    @app.get(PREFIX+'/images/{iid}')
    def image(iid:str):
        with closing(db()) as con:r=con.execute('SELECT extension FROM fb_images WHERE id=?',(iid,)).fetchone()
        if not r:raise HTTPException(404,'Изображение не найдено')
        return FileResponse(Path(data_dir)/'fire-builder-images'/(iid+'.'+r['extension']),headers={'X-Content-Type-Options':'nosniff'})
    @app.post(PREFIX+'/editions/{cid}/evidence/{bid}')
    def evidence(cid:str,bid:str,p:dict,u=Depends(student)):
        raw,ext=image_bytes(p)
        with closing(db()) as con:
            c=json.loads(edition(con,cid)['content']);b=next((b for m in c['modules'] for b in m.get('blocks',[]) if b['id']==bid and b['type']=='practice'),None)
            if not b:raise HTTPException(404,'Практика не найдена')
            r=latest(con,u['uid'],cid)
            if not r:raise HTTPException(409,'Сначала сохраните работу')
            iid=secrets.token_hex(16);folder=Path(data_dir)/'fire-builder-evidence';folder.mkdir(exist_ok=True)
            (folder/(iid+'.'+ext)).write_bytes(raw)
            con.execute('INSERT INTO fb_evidence VALUES(?,?,?,?,?,?)',(iid,u['uid'],cid,r['attempt'],bid,ext));con.commit()
            return {'id':iid}
    @app.get(PREFIX+'/evidence/{iid}')
    def read_evidence(iid:str,authorization:str|None=Header(None)):
        try:u=student(authorization);is_admin=False
        except HTTPException:u=admin(authorization);is_admin=True
        with closing(db()) as con:r=con.execute('SELECT * FROM fb_evidence WHERE id=?',(iid,)).fetchone()
        if not r:raise HTTPException(404,'Скриншот не найден')
        if not is_admin and r['uid']!=u['uid']:raise HTTPException(403,'Нет доступа')
        return FileResponse(Path(data_dir)/'fire-builder-evidence'/(iid+'.'+r['extension']),headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})
    @app.get(PREFIX+'/editions/{cid}/results.csv')
    def export(cid:str,u=Depends(admin)):
        rows=results(cid,u)['rows'];out=io.StringIO();w=csv.writer(out,delimiter=';');w.writerow(['Версия','ID версии','ФИО','Попытка','Прохождение %','Тест %','Критические ошибки','Порог','Комментарий','Повторная проверка'])
        def safe(v):
            s=str(v);return "'"+s if s.startswith(('=','+','-','@','\t','\r')) else s
        for r in rows:w.writerow([safe(v) for v in (r['version'],cid,r['full_name'],r['attempt'],r['summary']['percent'],r['summary']['examPercent'],r['summary']['criticalErrors'],r['state'].get('examThreshold',''),r['review'].get('comment',''),r['review'].get('needs_recheck',False))])
        return StreamingResponse(iter(['\ufeff'+out.getvalue()]),media_type='text/csv',headers={'Content-Disposition':'attachment; filename=fire-results.csv'})
