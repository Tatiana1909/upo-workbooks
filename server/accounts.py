"""Personal identities, invitations and audit trail, additive to legacy learning records."""
import base64
import csv
import hashlib
import hmac
import io
import json
import secrets
import sqlite3
import time
from contextlib import closing
from fastapi import Depends, Header, HTTPException, Request

PERMISSIONS = {'view', 'review', 'edit', 'publish'}


def hash_password(value):
    if not isinstance(value, str) or not 10 <= len(value) <= 200:
        raise HTTPException(422, 'Пароль должен содержать от 10 до 200 символов')
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(value.encode(), salt=salt, n=16384, r=8, p=1)
    return base64.b64encode(salt + digest).decode()


def verify_password(value, stored):
    try:
        if not isinstance(value,str) or len(value)>200:return False
        raw = base64.b64decode(stored)
        return hmac.compare_digest(raw[16:], hashlib.scrypt(value.encode(), salt=raw[:16], n=16384, r=8, p=1))
    except (ValueError, TypeError):
        return False


class Accounts:
    def __init__(self, app, db, now, encode, decode, rate, admin_login, admin_password):
        self.db, self.now, self.encode, self.decode, self.rate = db, now, encode, decode, rate
        self.app = app
        self.bootstrap_login, self.bootstrap_password = admin_login, admin_password
        app.add_event_handler('startup', self.init)
        self.routes()

    def init(self):
        with closing(self.db()) as c:
            c.executescript('''
            CREATE TABLE IF NOT EXISTS identity_accounts(
              id TEXT PRIMARY KEY, login TEXT NOT NULL UNIQUE COLLATE NOCASE,
              full_name TEXT NOT NULL, normalized_name TEXT NOT NULL, unit TEXT NOT NULL,
              position TEXT NOT NULL, personnel_number TEXT,
              password_hash TEXT NOT NULL, roles TEXT NOT NULL, realms TEXT NOT NULL,
              permissions TEXT NOT NULL, units TEXT NOT NULL, courses TEXT NOT NULL,
              status TEXT NOT NULL, must_change INTEGER NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
              source TEXT NOT NULL, created_by TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE UNIQUE INDEX IF NOT EXISTS identity_personnel ON identity_accounts(personnel_number)
              WHERE personnel_number IS NOT NULL;
            CREATE TABLE IF NOT EXISTS identity_units(name TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS identity_invites(
              id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE, label TEXT NOT NULL,
              unit TEXT, courses TEXT NOT NULL, group_name TEXT NOT NULL, expires_at REAL NOT NULL,
              max_uses INTEGER NOT NULL, uses INTEGER NOT NULL DEFAULT 0, created_by TEXT NOT NULL,
              active INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE IF NOT EXISTS identity_audit(
              id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, actor TEXT,
              subject TEXT, action TEXT NOT NULL, details TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS identity_sessions(
              id TEXT PRIMARY KEY, account_id TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1);
            ''')
            # Bootstrap exactly once. Password changes cannot be undone by a restart.
            if not c.execute("SELECT 1 FROM identity_accounts WHERE roles LIKE '%admin%'").fetchone():
                self.create(c, dict(last_name='Администратор', first_name='системы', no_patronymic=True,
                    unit='Администрирование', position='Администратор', no_personnel_number=True,
                    login=self.bootstrap_login, password=self.bootstrap_password,
                    roles=['admin'], realms=['upo','fire'], permissions=list(PERMISSIONS)),
                    'bootstrap', None, allow_short_bootstrap=True)
            c.commit()

    def audit(self, c, actor, subject, action, details=None):
        c.execute('INSERT INTO identity_audit(at,actor,subject,action,details) VALUES(?,?,?,?,?)',
                  (self.now(), actor, subject, action, json.dumps(details or {}, ensure_ascii=False)))

    def public(self, r):
        d = dict(r)
        d.pop('password_hash', None)
        for k in ('roles','realms','permissions','units','courses'):
            d[k] = json.loads(d[k])
        d['must_change'] = bool(d['must_change'])
        return d

    def check(self, payload, allow_change=False):
        if not payload.get('account_id'):
            return None
        with closing(self.db()) as c:
            r = c.execute('SELECT * FROM identity_accounts WHERE id=?', (payload['account_id'],)).fetchone()
            session = c.execute('SELECT active FROM identity_sessions WHERE id=? AND account_id=?',
                                (payload.get('session_id'), payload['account_id'])).fetchone()
        if not r or r['status'] != 'active' or payload.get('revision') != r['revision'] or not session or not session['active']:
            raise HTTPException(401, 'Сеанс завершён. Войдите повторно')
        if r['must_change'] and not allow_change:
            raise HTTPException(403, 'Сначала смените временный пароль')
        return self.public(r)

    def identity(self, authorization: str | None = Header(None)):
        if not authorization or not authorization.startswith('Bearer '):
            raise HTTPException(401, 'Требуется персональный вход')
        p = self.decode(authorization[7:])
        a = self.check(p, allow_change=True)
        if not a:
            raise HTTPException(401, 'Требуется персональный вход')
        return a

    def manager(self, a):
        if a['must_change']:
            raise HTTPException(403, 'Сначала смените временный пароль')
        if not set(a['roles']) & {'admin','teacher'}:
            raise HTTPException(403, 'Недостаточно прав')

    def visible(self, actor, uid, c=None):
        if not actor or 'admin' in actor['roles']:
            return True
        if c is None:
            with closing(self.db()) as conn:
                return self.visible(actor, uid, conn)
        r = c.execute('SELECT * FROM identity_accounts WHERE id=?', (uid,)).fetchone()
        if not r:
            return False  # Historical identities require explicit administrative handling.
        d = self.public(r)
        return d['roles'] == ['participant'] and (r['created_by'] == actor['id'] or r['unit'] in actor['units'])

    def realm(self, cid):
        return 'fire' if cid == 'fire-safety-v4' or cid.startswith('fire-') or cid.startswith('fb-') or cid.startswith('pb-') else 'upo'

    def course_allowed(self, a, cid):
        if 'admin' in a['roles']:
            return True
        if 'teacher' in a['roles']:
            return self.realm(cid) in a['realms'] and (not a['courses'] or cid in a['courses'])
        return cid in a['courses']

    def authorize(self, p, role, realm):
        a = self.check(p)
        if not a:
            return None
        if role == 'user':
            if 'participant' not in a['roles']:
                raise HTTPException(403, 'Требуется аккаунт участника')
        elif not ('admin' in a['roles'] or ('teacher' in a['roles'] and realm in a['realms'])):
            raise HTTPException(403, 'Нет доступа к этому направлению')
        return {**p, 'uid': a['id'], 'sub': a['id'], 'name': a['full_name'], 'role': role, 'account': a}

    def create(self, c, data, source, actor, allow_short_bootstrap=False):
        if actor and 'admin' not in actor['roles'] and data.get('roles',['participant'])!=['participant']:
            raise HTTPException(403,'Недостаточно прав для назначения роли')
        def required(key):
            v = data.get(key)
            if not isinstance(v, str) or not v.strip() or len(v) > 160:
                raise HTTPException(422, 'Заполните поле: ' + key)
            return ' '.join(v.split())
        last, first = required('last_name'), required('first_name')
        patronymic = '' if data.get('no_patronymic') is True else required('patronymic')
        full = ' '.join(x for x in (last, first, patronymic) if x)
        normalized = full.casefold()
        unit=required('unit')
        position=required('position') if source=='self_registration' else str(data.get('position') or '').strip()
        if len(position)>160:raise HTTPException(422,'Слишком длинное название должности')
        number = data.get('personnel_number')
        if number is not None and not isinstance(number, str):
            raise HTTPException(422, 'Табельный номер должен быть текстом')
        number = (number or '').strip() or None
        if not number and data.get('no_personnel_number') is not True:
            raise HTTPException(422, 'Укажите табельный номер или отметьте его отсутствие')
        if number and len(number) > 80:
            raise HTTPException(422, 'Слишком длинный табельный номер')
        if number and c.execute('SELECT 1 FROM identity_accounts WHERE personnel_number=?', (number,)).fetchone():
            raise HTTPException(409, 'Возможно, аккаунт уже существует. Обратитесь к ответственному за доступ')
        duplicate = c.execute('SELECT 1 FROM identity_accounts WHERE normalized_name=? AND unit=?', (normalized,unit)).fetchone()
        if duplicate and not (actor and data.get('confirm_namesake') is True):
            raise HTTPException(409, 'Возможно, аккаунт уже существует. Совпадение должен проверить преподаватель или администратор')
        roles = data.get('roles', ['participant'])
        realms = data.get('realms', [])
        permissions = data.get('permissions', ['view'])
        units, courses = data.get('units', []), data.get('courses', [])
        if not isinstance(roles,list) or not roles or set(roles)-{'participant','teacher','admin'}:
            raise HTTPException(422, 'Неверные роли')
        if not isinstance(realms,list) or set(realms)-{'upo','fire'} or not isinstance(permissions,list) or set(permissions)-PERMISSIONS:
            raise HTTPException(422, 'Неверные права')
        for values in (units,courses):
            if not isinstance(values,list) or len(values)>200 or any(not isinstance(v,str) or not v or len(v)>200 for v in values):
                raise HTTPException(422, 'Неверные назначения')
        if actor and 'admin' not in actor['roles']:
            if roles != ['participant'] or any(not self.course_allowed(actor,x) for x in courses):
                raise HTTPException(403, 'Недостаточно прав для назначения')
            if unit not in actor['units']:
                raise HTTPException(403, 'Подразделение не входит в вашу область ответственности')
        uid = secrets.token_hex(16)
        login = data.get('login') or 'u' + secrets.token_hex(6)
        if not isinstance(login,str) or not 3<=len(login)<=80 or not all(ch.isascii() and (ch.isalnum() or ch in '._-') for ch in login):
            raise HTTPException(422, 'Логин: 3–80 латинских букв, цифр, точек, дефисов или подчёркиваний')
        password = data.get('password')
        temporary = password is None
        if temporary:
            password = secrets.token_urlsafe(12)
        if allow_short_bootstrap and len(password)<10:
            salt=secrets.token_bytes(16)
            hashed=base64.b64encode(salt+hashlib.scrypt(password.encode(),salt=salt,n=16384,r=8,p=1)).decode()
        else:
            hashed=hash_password(password)
        timestamp = self.now()
        c.execute('INSERT OR IGNORE INTO identity_units VALUES(?)',(unit,))
        try:
            c.execute('''INSERT INTO identity_accounts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (uid,login,full,normalized,unit,position,number,hashed,json.dumps(roles),json.dumps(realms),
                 json.dumps(permissions),json.dumps(units),json.dumps(courses),'active',int(temporary or source=='bootstrap'),1,
                 source,actor['id'] if actor else None,timestamp,timestamp))
        except sqlite3.IntegrityError:
            raise HTTPException(409,'Логин или табельный номер уже используется')
        if 'participant' in roles:
            # Never reuse an old name-based identity, including for full namesakes.
            c.execute('INSERT INTO users VALUES(?,?,?,?,?)',(uid,full,'@account:'+uid,timestamp,timestamp))
        result=self.public(c.execute('SELECT * FROM identity_accounts WHERE id=?',(uid,)).fetchone())
        self.audit(c,actor['id'] if actor else None,uid,'account_created',result)
        if temporary:
            result['temporary_password']=password
        return result

    def session(self, a, c):
        sid=secrets.token_hex(24)
        c.execute('INSERT INTO identity_sessions(id,account_id) VALUES(?,?)',(sid,a['id']))
        # Signed token extends existing signature format, but permissions are always read from DB.
        raw_token=self.encode(a['id'],a['full_name'],'account', **({'ttl':12*3600} if set(json.loads(a['roles'])) & {'admin','teacher'} else {}))
        p=self.decode(raw_token)
        p.update(account_id=a['id'],revision=a['revision'],session_id=sid)
        raw=base64.urlsafe_b64encode(json.dumps(p,ensure_ascii=False).encode()).rstrip(b'=')
        # encode_extra is injected by app so the secret remains in one place.
        return {'token':self.sign(raw),'full_name':a['full_name'],'login':a['login'], 'account':self.public(a)}

    def routes(self):
        app=self.app
        identity=self.identity
        def management(a=Depends(identity)):
            self.manager(a)
            return a
        def admin(a=Depends(management)):
            if 'admin' not in a['roles']:raise HTTPException(403,'Требуются права администратора')
            return a

        @app.post('/api/v1/accounts/session')
        def login(data:dict, request:Request):
            queue=self.rate(request.client.host if request.client else 'unknown','personal-login')
            queue.append(time.time())
            with closing(self.db()) as c:
                r=c.execute('SELECT * FROM identity_accounts WHERE login=? COLLATE NOCASE',(str(data.get('login','')),)).fetchone()
                if not r or not verify_password(str(data.get('password','')),r['password_hash']) or r['status']!='active':
                    raise HTTPException(401,'Неверный логин или пароль')
                queue.clear()
                result=self.session(r,c)
                self.audit(c,r['id'],r['id'],'login')
                c.commit()
                return result

        @app.get('/api/v1/accounts/me')
        def me(a=Depends(identity)):return a

        @app.post('/api/v1/accounts/logout')
        def logout(authorization:str|None=Header(None),a=Depends(identity)):
            p=self.decode(authorization[7:])
            with closing(self.db()) as c:
                c.execute('UPDATE identity_sessions SET active=0 WHERE id=?',(p['session_id'],));c.commit()
            return {'saved':True}

        @app.post('/api/v1/accounts/password')
        def password(data:dict,a=Depends(identity)):
            hashed=hash_password(data.get('new_password'))
            if data.get('new_password')!=data.get('password_repeat'):raise HTTPException(422,'Пароли не совпадают')
            with closing(self.db()) as c:
                c.execute('BEGIN IMMEDIATE')
                r=c.execute('SELECT * FROM identity_accounts WHERE id=?',(a['id'],)).fetchone()
                if not verify_password(str(data.get('current_password','')),r['password_hash']):raise HTTPException(401,'Неверный текущий пароль')
                if verify_password(data['new_password'],r['password_hash']):raise HTTPException(422,'Установите другой пароль')
                c.execute('UPDATE identity_accounts SET password_hash=?,must_change=0,revision=revision+1,updated_at=? WHERE id=?',(hashed,self.now(),a['id']))
                c.execute('UPDATE identity_sessions SET active=0 WHERE account_id=?',(a['id'],))
                self.audit(c,a['id'],a['id'],'password_changed');c.commit()
            return {'saved':True,'login_required':True}

        @app.get('/api/v1/accounts/courses/list')
        def course_list(a=Depends(management)):
            result=[]
            with closing(self.db()) as c:
                for r in c.execute("""SELECT e.course_id,e.title,e.version,
                    e.version=(SELECT MAX(n.version) FROM builder_editions n WHERE n.draft_id=e.draft_id AND n.archived=0 AND n.deleted_at IS NULL) AS is_latest
                    FROM builder_editions e WHERE e.archived=0 AND e.deleted_at IS NULL ORDER BY e.title,e.version DESC"""):
                    if self.course_allowed(a,r['course_id']):result.append({'id':r['course_id'],'title':r['title'],'version':r['version'],'is_latest':bool(r['is_latest']),'realm':'upo'})
                for r in c.execute('SELECT e.id,e.content,e.version,e.version=(SELECT MAX(n.version) FROM fb_editions n WHERE n.draft_id=e.draft_id) AS is_latest FROM fb_editions e ORDER BY e.version DESC'):
                    if self.course_allowed(a,r['id']):result.append({'id':r['id'],'title':json.loads(r['content'])['title'],'version':r['version'],'is_latest':bool(r['is_latest']),'realm':'fire'})
            return result

        @app.get('/api/v1/accounts/units')
        def units(a=Depends(management)):
            with closing(self.db()) as c:return [r[0] for r in c.execute('SELECT name FROM identity_units ORDER BY name') if 'admin' in a['roles'] or r[0] in a['units']]

        @app.post('/api/v1/accounts/units')
        def add_unit(data:dict,a=Depends(admin)):
            name=str(data.get('name','')).strip()
            if not 1<=len(name)<=160:raise HTTPException(422,'Укажите подразделение')
            with closing(self.db()) as c:
                c.execute('INSERT OR IGNORE INTO identity_units VALUES(?)',(name,));self.audit(c,a['id'],None,'unit_created',{'name':name});c.commit()
            return {'saved':True}

        @app.get('/api/v1/accounts/invitation')
        def invitation(token:str,request:Request):
            q=self.rate(request.client.host if request.client else 'unknown','invitation');q.append(time.time())
            with closing(self.db()) as c:
                r=self.invite(c,token)
                q.clear()
                return {'label':r['label'],'unit':r['unit'],'group_name':r['group_name'],
                        'units':[r['unit']] if r['unit'] else [x[0] for x in c.execute('SELECT name FROM identity_units ORDER BY name')]}

        @app.post('/api/v1/accounts/register')
        def register(data:dict,request:Request):
            q=self.rate(request.client.host if request.client else 'unknown','registration');q.append(time.time())
            if data.get('password')!=data.get('password_repeat'):raise HTTPException(422,'Пароли не совпадают')
            hash_password(data.get('password'))
            with closing(self.db()) as c:
                c.execute('BEGIN IMMEDIATE')
                invite=self.invite(c,str(data.get('invitation','')))
                unit=data.get('unit')
                if (invite['unit'] and unit!=invite['unit']) or not c.execute('SELECT 1 FROM identity_units WHERE name=?',(unit,)).fetchone():
                    raise HTTPException(422,'Выберите подразделение из справочника')
                safe={k:data.get(k) for k in ('last_name','first_name','patronymic','no_patronymic','unit','position','personnel_number','no_personnel_number','password')}
                safe.update(courses=json.loads(invite['courses']))
                result=self.create(c,safe,'self_registration',None)
                c.execute('UPDATE identity_invites SET uses=uses+1 WHERE id=?',(invite['id'],))
                # Created-by makes invitation registrants visible to their responsible teacher.
                c.execute('UPDATE identity_accounts SET created_by=? WHERE id=?',(invite['created_by'],result['id']))
                self.audit(c,None,result['id'],'invitation_used',{'invitation_id':invite['id'],'group':invite['group_name']})
                row=c.execute('SELECT * FROM identity_accounts WHERE id=?',(result['id'],)).fetchone()
                session=self.session(row,c);c.commit();q.clear();return session

        @app.get('/api/v1/accounts')
        def listing(a=Depends(management)):
            with closing(self.db()) as c:
                return [self.public(r) for r in c.execute('SELECT * FROM identity_accounts ORDER BY full_name') if self.visible(a,r['id'],c)]

        @app.post('/api/v1/accounts')
        def create(data:dict,a=Depends(management)):
            with closing(self.db()) as c:
                c.execute('BEGIN IMMEDIATE');r=self.create(c,data,'administrator' if 'admin' in a['roles'] else 'teacher',a);c.commit();return r

        @app.put('/api/v1/accounts/{uid}')
        def update(uid:str,data:dict,a=Depends(management)):
            with closing(self.db()) as c:
                c.execute('BEGIN IMMEDIATE')
                r=c.execute('SELECT * FROM identity_accounts WHERE id=?',(uid,)).fetchone()
                if not r or not self.visible(a,uid,c):raise HTTPException(403,'Нет доступа к участнику')
                if data.get('revision')!=r['revision']:raise HTTPException(409,'Карточка изменена. Обновите страницу')
                allowed={'full_name','unit','position','personnel_number','status','courses'}
                if 'admin' in a['roles']:allowed|={'roles','realms','permissions','units'}
                changes={k:v for k,v in data.items() if k in allowed}
                if not changes:raise HTTPException(422,'Нет изменений')
                for k in ('full_name','unit','position'):
                    if k in changes and (not isinstance(changes[k],str) or (k!='position' and not changes[k].strip()) or len(changes[k])>160):raise HTTPException(422,'Заполните '+k)
                if 'status' in changes and changes['status'] not in ('active','blocked'):raise HTTPException(422,'Неверный статус')
                for k in ('roles','realms','permissions','units','courses'):
                    if k in changes:
                        v=changes[k]
                        valid={'roles':{'participant','teacher','admin'},'realms':{'upo','fire'},'permissions':PERMISSIONS}.get(k)
                        if not isinstance(v,list) or len(v)>200 or any(not isinstance(x,str) or not x or len(x)>200 for x in v) or (valid is not None and (set(v)-valid or (k=='roles' and not v))):raise HTTPException(422,'Неверные права или назначения')
                if 'admin' not in a['roles']:
                    if 'unit' in changes and changes['unit'] not in a['units']:raise HTTPException(403,'Нет доступа к подразделению')
                    if 'courses' in changes and any(not self.course_allowed(a,x) for x in changes['courses']):raise HTTPException(403,'Нет доступа к курсу')
                old=self.public(r)
                if 'admin' in old['roles'] and ('admin' not in changes.get('roles',old['roles']) or changes.get('status',r['status'])!='active'):
                    count=c.execute("SELECT COUNT(*) FROM identity_accounts WHERE status='active' AND roles LIKE '%admin%'").fetchone()[0]
                    if count<=1:raise HTTPException(409,'Нельзя отключить последнего администратора')
                if 'personnel_number' in changes:
                    n=changes['personnel_number']
                    if n is not None and (not isinstance(n,str) or len(n)>80):raise HTTPException(422,'Табельный номер должен быть текстом')
                    changes['personnel_number']=(n or '').strip() or None
                if 'full_name' in changes:changes['normalized_name']=' '.join(changes['full_name'].split()).casefold()
                assignments=[];values=[]
                for k,v in changes.items():
                    assignments.append(k+'=?');values.append(json.dumps(v) if isinstance(v,list) else v)
                try:c.execute('UPDATE identity_accounts SET '+','.join(assignments)+',revision=revision+1,updated_at=? WHERE id=?',(*values,self.now(),uid))
                except sqlite3.IntegrityError:raise HTTPException(409,'Табельный номер уже используется')
                c.execute('UPDATE identity_sessions SET active=0 WHERE account_id=?',(uid,))
                if 'full_name' in changes:c.execute('UPDATE users SET full_name=?,updated_at=? WHERE id=?',(changes['full_name'],self.now(),uid))
                if 'participant' in changes.get('roles',old['roles']):
                    c.execute('INSERT OR IGNORE INTO users VALUES(?,?,?,?,?)',(uid,changes.get('full_name',r['full_name']),'@account:'+uid,self.now(),self.now()))
                self.audit(c,a['id'],uid,'account_updated',{'before':old,'changes':changes});c.commit()
                return self.public(c.execute('SELECT * FROM identity_accounts WHERE id=?',(uid,)).fetchone())

        @app.post('/api/v1/accounts/{uid}/reset-password')
        def reset(uid:str,a=Depends(management)):
            if uid==a['id']:raise HTTPException(409,'Для собственного аккаунта используйте смену пароля в профиле')
            temporary=secrets.token_urlsafe(12)
            with closing(self.db()) as c:
                c.execute('BEGIN IMMEDIATE')
                if not self.visible(a,uid,c):raise HTTPException(403,'Нет доступа к участнику')
                if not c.execute('SELECT 1 FROM identity_accounts WHERE id=?',(uid,)).fetchone():raise HTTPException(404,'Аккаунт не найден')
                c.execute('UPDATE identity_accounts SET password_hash=?,must_change=1,revision=revision+1,updated_at=? WHERE id=?',(hash_password(temporary),self.now(),uid))
                c.execute('UPDATE identity_sessions SET active=0 WHERE account_id=?',(uid,))
                self.audit(c,a['id'],uid,'password_reset');c.commit()
            return {'temporary_password':temporary}

        @app.get('/api/v1/accounts/audit/log')
        def audit_log(a=Depends(management)):
            with closing(self.db()) as c:
                return [dict(r,details=json.loads(r['details'])) for r in c.execute('SELECT * FROM identity_audit ORDER BY id DESC LIMIT 2000') if 'admin' in a['roles'] or (r['subject'] and self.visible(a,r['subject'],c))]

        @app.get('/api/v1/accounts/invitations/list')
        def invitations(a=Depends(management)):
            with closing(self.db()) as c:
                return [dict(r) for r in c.execute('SELECT id,label,unit,group_name,expires_at,max_uses,uses,active,created_by FROM identity_invites') if 'admin' in a['roles'] or r['created_by']==a['id']]

        @app.post('/api/v1/accounts/invitations/create')
        def new_invitation(data:dict,a=Depends(management)):
            courses=data.get('courses',[]);unit=data.get('unit') or None
            if not isinstance(courses,list) or not courses or any(not isinstance(x,str) or len(x)>200 or not self.course_allowed(a,x) for x in courses):raise HTTPException(422,'Выберите доступные курсы')
            if 'admin' not in a['roles'] and unit not in a['units']:raise HTTPException(403,'Выберите доступное подразделение')
            try:days=int(data.get('days',7));uses=int(data.get('max_uses',100))
            except (TypeError,ValueError):raise HTTPException(422,'Неверный срок или число регистраций')
            if not 1<=days<=90 or not 1<=uses<=10000:raise HTTPException(422,'Срок: 1–90 дней; регистраций: 1–10000')
            token=secrets.token_urlsafe(24);iid=secrets.token_hex(16)
            with closing(self.db()) as c:
                if unit and not c.execute('SELECT 1 FROM identity_units WHERE name=?',(unit,)).fetchone():raise HTTPException(422,'Нет такого подразделения')
                c.execute('INSERT INTO identity_invites(id,token_hash,label,unit,courses,group_name,expires_at,max_uses,created_by) VALUES(?,?,?,?,?,?,?,?,?)',
                    (iid,hashlib.sha256(token.encode()).hexdigest(),str(data.get('label','Приглашение'))[:160],unit,json.dumps(courses),str(data.get('group_name',''))[:160],time.time()+days*86400,uses,a['id']))
                self.audit(c,a['id'],None,'invitation_created',{'id':iid,'unit':unit,'courses':courses});c.commit()
            return {'id':iid,'token':token}

        @app.post('/api/v1/accounts/invitations/{iid}/revoke')
        def revoke(iid:str,a=Depends(management)):
            with closing(self.db()) as c:
                r=c.execute('SELECT * FROM identity_invites WHERE id=?',(iid,)).fetchone()
                if not r or ('admin' not in a['roles'] and r['created_by']!=a['id']):raise HTTPException(403,'Нет доступа')
                c.execute('UPDATE identity_invites SET active=0 WHERE id=?',(iid,));self.audit(c,a['id'],None,'invitation_revoked',{'id':iid});c.commit()
            return {'saved':True}

        @app.post('/api/v1/accounts/import/preview')
        async def import_preview(request:Request,a=Depends(management)):
            data=await request.body()
            if len(data)>5_000_000:raise HTTPException(413,'Файл: не более 5 МБ')
            if request.headers.get('x-file-format')=='xlsx':
                import zipfile
                from openpyxl import load_workbook
                try:
                    z=zipfile.ZipFile(io.BytesIO(data))
                    if sum(x.file_size for x in z.infolist())>25_000_000:raise ValueError()
                    wb=load_workbook(io.BytesIO(data),read_only=True,data_only=True)
                    from itertools import islice
                    rows=list(islice(wb.active.iter_rows(values_only=True),1002));wb.close()
                except Exception:raise HTTPException(422,'Не удалось прочитать XLSX')
            else:
                try:rows=list(csv.reader(io.StringIO(data.decode('utf-8-sig')),delimiter=';'))
                except Exception:raise HTTPException(422,'Используйте CSV UTF-8 с разделителем ;')
            if not rows or len(rows)>1001:raise HTTPException(422,'Файл должен содержать заголовок и до 1000 участников')
            names={'Фамилия':'last_name','Имя':'first_name','Отчество':'patronymic','Подразделение':'unit','Должность':'position','Табельный номер':'personnel_number'}
            headers=[names.get(str(x).strip(),str(x).strip()) for x in rows[0]]
            results=[];seen_names=set();seen_numbers=set()
            with closing(self.db()) as c:
                for i,row in enumerate(rows[1:],2):
                    d={k:str(v).strip() if v is not None else '' for k,v in zip(headers,row) if k in set(names.values())}
                    d.update(no_patronymic=not d.get('patronymic'),no_personnel_number=not d.get('personnel_number'))
                    name_key=(' '.join(str(d.get(x,'')) for x in ('last_name','first_name','patronymic')).strip().casefold(),d.get('unit'))
                    duplicate=name_key in seen_names or (d.get('personnel_number') and d['personnel_number'] in seen_numbers)
                    seen_names.add(name_key)
                    if d.get('personnel_number'):seen_numbers.add(d['personnel_number'])
                    if duplicate:
                        results.append({'row':i,'data':d,'valid':False,'error':'Возможный дубль внутри файла'});continue
                    c.execute('SAVEPOINT preview')
                    try:self.create(c,d,'excel',a);results.append({'row':i,'data':d,'valid':True})
                    except HTTPException as e:results.append({'row':i,'data':d,'valid':False,'error':e.detail})
                    finally:c.execute('ROLLBACK TO preview');c.execute('RELEASE preview')
            return results

        @app.post('/api/v1/accounts/import/commit')
        def import_commit(data:dict,a=Depends(management)):
            rows=data.get('rows',[]);courses=data.get('courses',[])
            if not isinstance(rows,list) or not 1<=len(rows)<=1000:raise HTTPException(422,'Выберите от 1 до 1000 строк')
            if not isinstance(courses,list) or any(not isinstance(x,str) or not self.course_allowed(a,x) for x in courses):raise HTTPException(403,'Нет доступа к курсу')
            result=[]
            with closing(self.db()) as c:
                c.execute('BEGIN IMMEDIATE')
                for row in rows:
                    if not isinstance(row,dict):raise HTTPException(422,'Неверная строка')
                    safe={k:v for k,v in row.items() if k in {'last_name','first_name','patronymic','no_patronymic','unit','position','personnel_number','no_personnel_number'}}
                    safe['courses']=courses
                    result.append(self.create(c,safe,'excel',a))
                c.commit()
            return result

    def invite(self,c,token):
        r=c.execute('SELECT * FROM identity_invites WHERE token_hash=?',(hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        if not r or not r['active'] or r['expires_at']<time.time() or r['uses']>=r['max_uses']:
            raise HTTPException(403,'Приглашение недействительно или срок его действия истёк')
        return r
