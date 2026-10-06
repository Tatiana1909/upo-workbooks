"""Isolated fire workbook: authoritative scoring and shared teacher records."""
import json
from contextlib import closing
from pathlib import Path
from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field

CID = 'fire-safety-v4'
DATA = None
DEFAULTS = dict(object='', address='', assembly='', responsible='', dispatcher='', passPercent=80)

def course_data(static_dir):
    global DATA
    if DATA is None:
        DATA = json.loads((Path(static_dir) / 'v4/fire/data.json').read_text())
    return DATA

def tasks():
    return [q for m in DATA['modules'] for q in m['quiz'] + m['games']]

def is_correct(q, v):
    if q['type'] == 'choice':
        return type(v) is int and v == q['answer']
    if not isinstance(v, list) or any(type(i) is not int for i in v):
        return False
    return v == q['answer'] if q['type'] == 'order' else sorted(v) == sorted(q['answer'])

def validate_state(s):
    for key in ('answers', 'first', 'notes', 'read', 'exam', 'profile'):
        s.setdefault(key, {})
        if not isinstance(s[key], dict):
            raise HTTPException(422, 'Неверный формат данных: ' + key)
    bank = {q['id']: q for q in tasks() + DATA['final']}
    for key in ('answers', 'first', 'exam'):
        for qid, v in s[key].items():
            q = bank.get(qid)
            if not q:
                raise HTTPException(422, 'Неизвестное задание')
            if q['type'] == 'choice':
                if type(v) is not int or v < 0 or v >= len(q['options']):
                    raise HTTPException(422, 'Неверный вариант ответа')
            elif not isinstance(v, list) or any(type(i) is not int or i < 0 or i >= len(q['options']) for i in v) or len(v) != len(set(v)):
                raise HTTPException(422, 'Неверный набор вариантов')
    if any(not isinstance(v, str) or len(v) > 4000 for v in s['notes'].values()):
        raise HTTPException(422, 'Неверные записи участника')
    if not isinstance(s['profile'].get('unit', ''), str) or len(s['profile'].get('unit', '')) > 160:
        raise HTTPException(422, 'Неверное подразделение')
    history = s.setdefault('history', [])
    if not isinstance(history, list) or len(history) > 500 or any(not isinstance(x,dict) or not isinstance(x.get('id'),str) or not isinstance(x.get('at'),str) for x in history):
        raise HTTPException(422, 'Неверная история')
    return s

def prepare_state(s, previous):
    validate_state(s)
    previous = previous or {}
    # Only the server submit endpoint can freeze an exam and its threshold.
    for k in ('submitted', 'submittedAt', 'examThreshold'):
        if k in previous:
            s[k] = previous[k]
        else:
            s.pop(k, None)
    if previous.get('submitted'):
        s['exam'] = previous['exam']
    # First submitted training answer cannot be overwritten by a later save.
    s['first'] = {qid: v for qid, v in s['first'].items() if qid in s['answers']}
    for qid, v in s['answers'].items():
        s['first'].setdefault(qid, v)
    s['first'].update(previous.get('first', {}))
    return s

def score(s, threshold=80):
    validate_state(s)
    ts = tasks()
    answered = sum(q['id'] in s['answers'] for q in ts)
    good = sum(is_correct(q, s['answers'].get(q['id'])) for q in ts)
    read = sum(s['read'].get(m['id']) is True for m in DATA['modules'])
    submitted = s.get('submitted') is True
    exam_good = sum(is_correct(q, s['exam'].get(q['id'])) for q in DATA['final'])
    critical = sum(q.get('critical', False) and not is_correct(q, s['exam'].get(q['id'])) for q in DATA['final'])
    exam_percent = round(exam_good / len(DATA['final']) * 100)
    threshold = s.get('examThreshold', threshold)
    return dict(percent=round((read+answered+(len(DATA['final']) if submitted else 0))/(len(DATA['modules'])+len(ts)+len(DATA['final']))*100), correct=good+(exam_good if submitted else 0), totalQ=len(ts)+len(DATA['final']), answered=answered+(len(s['exam']) if submitted else 0), checked=read, totalChecks=len(DATA['modules']), points=10*(good+(exam_good if submitted else 0)), examCorrect=exam_good, examPercent=exam_percent, criticalErrors=critical, passed=submitted and exam_percent>=threshold and critical==0, firstCorrect=sum(is_correct(q,s['first'].get(q['id'])) for q in ts), practiceCorrect=good, practiceTotal=len(ts), unit=s['profile'].get('unit',''), submitted=submitted)

class Settings(BaseModel):
    object: str = Field(default='', max_length=300)
    address: str = Field(default='', max_length=300)
    assembly: str = Field(default='', max_length=300)
    responsible: str = Field(default='', max_length=300)
    dispatcher: str = Field(default='', max_length=300)
    passPercent: int = Field(default=80, ge=60, le=100)

class Review(BaseModel):
    user_id: str = Field(min_length=1, max_length=160)
    attempt: int = Field(ge=1)
    comment: str = Field(default='', max_length=4000)
    practical: bool = False

def register_fire(app, db, current_user, current_admin, now_iso, static_dir):
    course_data(static_dir)
    @app.on_event('startup')
    def initialize():
        with closing(db()) as con:
            con.executescript('''CREATE TABLE IF NOT EXISTS fire_settings (id INTEGER PRIMARY KEY, content TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS fire_reviews(user_id TEXT NOT NULL, attempt INTEGER NOT NULL, content TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(user_id,attempt));''')
            con.commit()
    def settings():
        with closing(db()) as con:
            r = con.execute('SELECT content FROM fire_settings WHERE id=1').fetchone()
        return {**DEFAULTS, **(json.loads(r['content']) if r else {})}
    @app.get('/api/v1/fire/settings')
    def get_settings():
        return settings()
    @app.put('/api/v1/fire/settings')
    def put_settings(data: Settings, admin=Depends(current_admin)):
        with closing(db()) as con:
            con.execute('INSERT INTO fire_settings(id,content) VALUES(1,?) ON CONFLICT(id) DO UPDATE SET content=excluded.content',(data.model_dump_json(),))
            con.commit()
        return data.model_dump()
    @app.post('/api/v1/fire/submit')
    def submit(user=Depends(current_user)):
        with closing(db()) as con:
            con.execute('BEGIN IMMEDIATE')
            row = con.execute('SELECT * FROM progress WHERE user_id=? AND course_id=?',(user['uid'],CID)).fetchone()
            if not row:
                raise HTTPException(409, 'Сначала сохраните результаты')
            s = json.loads(row['state_json'])
            validate_state(s)
            if not s.get('submitted'):
                if not all(s['read'].get(m['id']) is True for m in DATA['modules']) or not all(q['id'] in s['answers'] for q in tasks()):
                    raise HTTPException(409, 'Завершите все тренировочные задания и разделы')
                if not all(q['id'] in s['exam'] for q in DATA['final']):
                    raise HTTPException(422, 'Ответьте на все вопросы итогового теста')
                s.update(submitted=True,submittedAt=now_iso(),examThreshold=settings()['passPercent'])
                summary = score(s)
                timestamp = now_iso()
                sj, smj = json.dumps(s,ensure_ascii=False),json.dumps(summary,ensure_ascii=False)
                con.execute('UPDATE progress SET state_json=?,summary_json=?,updated_at=?,completed_at=? WHERE user_id=? AND course_id=?',(sj,smj,timestamp,timestamp,user['uid'],CID))
                con.execute('UPDATE course_attempts SET state_json=?,summary_json=?,updated_at=?,completed_at=? WHERE user_id=? AND course_id=? AND attempt_no=?',(sj,smj,timestamp,timestamp,user['uid'],CID,row['attempt_no']))
                con.commit()
            return dict(state=s,summary=score(s),attempt=row['attempt_no'])
    @app.get('/api/v1/fire/results')
    def results(admin=Depends(current_admin)):
        with closing(db()) as con:
            rows = con.execute('''SELECT a.*,u.full_name,r.content AS review FROM course_attempts a JOIN users u ON u.id=a.user_id LEFT JOIN fire_reviews r ON r.user_id=a.user_id AND r.attempt=a.attempt_no WHERE a.course_id=? ORDER BY u.full_name,a.attempt_no''',(CID,)).fetchall()
        return {'rows':[dict(user_id=r['user_id'],full_name=r['full_name'],attempt=r['attempt_no'],state=json.loads(r['state_json']),summary=json.loads(r['summary_json']),updated_at=r['updated_at'],started_at=r['started_at'],completed_at=r['completed_at'],review=json.loads(r['review']) if r['review'] else {}) for r in rows]}
    @app.put('/api/v1/fire/review')
    def review(data: Review,admin=Depends(current_admin)):
        with closing(db()) as con:
            exists=con.execute('SELECT 1 FROM course_attempts WHERE user_id=? AND course_id=? AND attempt_no=?',(data.user_id,CID,data.attempt)).fetchone()
            if not exists:
                raise HTTPException(404,'Попытка не найдена')
            con.execute('INSERT INTO fire_reviews(user_id,attempt,content,updated_at) VALUES(?,?,?,?) ON CONFLICT(user_id,attempt) DO UPDATE SET content=excluded.content,updated_at=excluded.updated_at',(data.user_id,data.attempt,json.dumps(dict(comment=data.comment,practical=data.practical),ensure_ascii=False),now_iso()))
            con.commit()
        return {'saved':True}
    return settings
