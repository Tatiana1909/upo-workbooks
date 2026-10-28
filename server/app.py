import base64
import csv
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import sqlite3
import time
from collections import defaultdict, deque
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError as UrlHTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request as UrlRequest, urlopen

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from fire_access import FireAccess


ACCESS_CODE = os.environ.get("WORKBOOK_ACCESS_CODE", "")
TOKEN_SECRET = os.environ.get("WORKBOOK_TOKEN_SECRET", "")
ADMIN_KEY = os.environ.get("WORKBOOK_ADMIN_KEY", "")
ADMIN_LOGIN = os.environ.get("WORKBOOK_ADMIN_LOGIN", "")
ADMIN_PASSWORD = os.environ.get("WORKBOOK_ADMIN_PASSWORD", "")
DB_PATH = Path(os.environ.get("WORKBOOK_DB_PATH", "/data/workbooks.sqlite3"))
STATIC_DIR = Path(os.environ.get("WORKBOOK_STATIC_DIR", "/app/static"))
fire_access = FireAccess(DB_PATH.parent)
fire_access.install(os.environ.get('WORKBOOK_FIRE_ENVELOPE', str(DB_PATH.parent / 'fire-access.enc')))
TOKEN_TTL = int(os.environ.get("WORKBOOK_TOKEN_TTL_HOURS", "168")) * 3600
ADMIN_TOKEN_TTL = int(os.environ.get("WORKBOOK_ADMIN_TOKEN_TTL_HOURS", "12")) * 3600
ALLOWED_ORIGINS = [x.strip() for x in os.environ.get(
    "WORKBOOK_ALLOWED_ORIGINS", "https://tatiana1909.github.io"
).split(",") if x.strip()]
COURSE_TITLES = {
    "accounting": "Складской и производственный учёт в 1С УПО",
    "warehouse": "Работники склада",
    "fire-safety-v4": "Пожарная безопасность",
}
MATERIAL_FOLDERS = {
    "day1": {
        "public_key": "https://disk.360.yandex.ru/d/Lf4_V6RcVCNufA",
        "files": {
            "1. Знакомство с 1С_УПО и логика_учета.docx",
            "2. Основные отличия 1С_УР и 1С_УПО.docx",
            "3. Пользовательские настройки в системе 1С УПО.docx",
            "4. Ввод и проверка начальных остатков в 1С УПО.docx",
        },
    },
    "day2": {
        "public_key": "https://disk.360.yandex.ru/d/lmuhIx6UDFhnlA",
        "files": {
            "1.1_Принятие к учету ТМЦ по приходной накладной, через ТСД.docx",
            "1.2_Приходная накладная на основании заказа поставщику.docx",
            "1.3_Формирование приходной накладной из Диадок.docx",
            "1.4_Приемка ТМЦ на склад без заказа поставщику.docx",
            "1.5_Поступление ТМЦ с расхождениями.docx",
            "1.6_Корректировка поступления. Формирование УКД, КСФ.docx",
            "1.7_Сторнирование документов товародвижения.docx",
            "1.8_Формирование реестра документов по ячейкам склада в 1С УПО.docx",
            "2.1_Перемещение_запасов_на_основании_заказа.docx",
            "2.2_Перемещение_запасов.docx",
            "2.3_Перемещение_номенклатуры_склада_и_производства_на_основании_заказа.docx",
            "2.4_Перемещение_номенклатуры_склада_и_производства.docx",
            "2.5_Перемещение ТМЦ между 1С_УР и 1С_УПО.docx",
            "2.6_Создание перемещений по ячейке со склада Красноярск.docx",
            "3_Авансовый отчет.docx",
        },
    },
    "day3": {
        "public_key": "https://disk.360.yandex.ru/d/KDizdoiGARFN8w",
        "files": {
            "1. Инструкция Загрузка продаж из кассовых систем IFCM.docx",
            "2. Альбом отчетов для СУ.docx",
            "3. Настройка отчетов на примере Движение товаров.docx",
        },
    },
    "day4": {
        "public_key": "https://disk.360.yandex.ru/d/rd3Rh5VSFdZEeg",
        "files": {"Инвентаризация.docx"},
    },
    "day5": {
        "public_key": "https://disk.360.yandex.ru/d/6uP7AKpQdPY9bQ",
        "files": {
            "1. Списание запасов.docx",
            "2. Групповое перепроведение.docx",
            "3. Контрольный лист закрытия месяца.docx",
        },
    },
    "sop": {
        "public_key": "https://disk.360.yandex.ru/d/xhsiCZwaVArKQw",
        "files": {f"{number:02d}" for number in range(1, 25)},
        "prefix_only": True,
    },
    "warehouse": {
        "public_key": "https://disk.360.yandex.ru/d/Qt8tOUOwVb6c6A",
        "files": {
            "01 Общие правила работы с ТСД.docx",
            "02 Памятка по работе с ТСД.docx",
            "03 Приемка от поставщика с ТСД.docx",
            "04 Приемка перемещения на склад с ТСД.docx",
            "04 Тест_УПО_ТСД_исправленный.docx",
            "05 Заказ на перемещение.docx",
            "06 Сборка и перемещение по заказу с ТСД.docx",
            "07 Заказ на перемещение в номенклатуру производства.docx",
            "08 Перемещение запасов без заказа.docx",
            "09 Инвентаризация запасов с ТСД.docx",
            "10 Сбор информации с ТСД.docx",
            "11 Печать штрихкодов для склада.docx",
            "12 Списание запасов с ТСД.docx",
            "13 Контроль складских операций и отчеты.docx",
            "СОП Складская логистика.pptx",
        },
    },
}

if not all((ACCESS_CODE, TOKEN_SECRET, ADMIN_KEY, ADMIN_LOGIN, ADMIN_PASSWORD)):
    raise RuntimeError(
        "Set WORKBOOK_ACCESS_CODE, WORKBOOK_TOKEN_SECRET, WORKBOOK_ADMIN_KEY, "
        "WORKBOOK_ADMIN_LOGIN and WORKBOOK_ADMIN_PASSWORD"
    )

app = FastAPI(title="IFCM Workbooks API", docs_url=None, redoc_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "PUT", "POST"],
    allow_headers=["Authorization", "Content-Type", "X-Admin-Key"],
)
attempts_by_ip: dict[str, deque] = defaultdict(deque)


class LoginInput(BaseModel):
    code: str = Field(min_length=1, max_length=200)
    full_name: str = Field(min_length=5, max_length=160)
    course_id: str | None = None


class AdminLoginInput(BaseModel):
    login: str = Field(min_length=1, max_length=160)
    password: str = Field(min_length=1, max_length=200)


class ProgressInput(BaseModel):
    state: dict
    summary: dict


def db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db():
    with closing(db()) as con:
        con.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS users (
          id TEXT PRIMARY KEY,
          full_name TEXT NOT NULL,
          normalized_name TEXT NOT NULL UNIQUE,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS progress (
          user_id TEXT NOT NULL,
          course_id TEXT NOT NULL,
          state_json TEXT NOT NULL,
          summary_json TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          attempt_no INTEGER NOT NULL DEFAULT 1,
          started_at TEXT,
          completed_at TEXT,
          PRIMARY KEY (user_id, course_id),
          FOREIGN KEY (user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS course_attempts (
          user_id TEXT NOT NULL,
          course_id TEXT NOT NULL,
          attempt_no INTEGER NOT NULL,
          state_json TEXT NOT NULL,
          summary_json TEXT NOT NULL,
          started_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          completed_at TEXT,
          PRIMARY KEY (user_id, course_id, attempt_no),
          FOREIGN KEY (user_id) REFERENCES users(id)
        );
        """)
        columns = {row["name"] for row in con.execute("PRAGMA table_info(progress)")}
        if "attempt_no" not in columns:
            con.execute("ALTER TABLE progress ADD COLUMN attempt_no INTEGER NOT NULL DEFAULT 1")
        if "started_at" not in columns:
            con.execute("ALTER TABLE progress ADD COLUMN started_at TEXT")
        if "completed_at" not in columns:
            con.execute("ALTER TABLE progress ADD COLUMN completed_at TEXT")
        con.execute("UPDATE progress SET started_at = updated_at WHERE started_at IS NULL")
        con.execute("""
          INSERT OR IGNORE INTO course_attempts(
            user_id, course_id, attempt_no, state_json, summary_json,
            started_at, updated_at, completed_at
          )
          SELECT user_id, course_id, COALESCE(attempt_no, 1), state_json, summary_json,
                 COALESCE(started_at, updated_at), updated_at, completed_at
          FROM progress
        """)
        con.commit()


@app.on_event("startup")
def startup():
    init_db()


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def normalize_name(value: str):
    return re.sub(r"\s+", " ", value.strip()).casefold()


def clean_name(value: str):
    value = re.sub(r"\s+", " ", value.strip())
    if len(value.split()) < 2 or not re.fullmatch(r"[A-Za-zА-Яа-яЁё\-\s]+", value):
        raise HTTPException(422, "Укажите фамилию и имя без цифр и специальных символов")
    return value


def validate_course(course_id: str):
    if course_id not in COURSE_TITLES and not builder_edition(course_id):
        raise HTTPException(404, "Курс не найден")


def encode_token(subject: str, name: str, role: str, ttl: int = TOKEN_TTL, scope: str | None = None):
    payload = {"sub": subject, "name": name, "role": role, "exp": int(time.time()) + ttl}
    if scope:
        payload.update(scope=scope, credential_revision=fire_access.config['revision'])
    raw = base64.urlsafe_b64encode(json.dumps(payload, ensure_ascii=False).encode()).rstrip(b"=")
    signature = hmac.new(TOKEN_SECRET.encode(), raw, hashlib.sha256).digest()
    return (raw + b"." + base64.urlsafe_b64encode(signature).rstrip(b"=")).decode()


def decode_token(token: str):
    try:
        raw, signature = token.encode().split(b".", 1)
        expected = hmac.new(TOKEN_SECRET.encode(), raw, hashlib.sha256).digest()
        actual = base64.urlsafe_b64decode(signature + b"=" * (-len(signature) % 4))
        if not hmac.compare_digest(expected, actual):
            raise ValueError
        payload = json.loads(base64.urlsafe_b64decode(raw + b"=" * (-len(raw) % 4)))
        if int(payload["exp"]) < int(time.time()):
            raise ValueError
        return payload
    except Exception:
        raise HTTPException(401, "Сеанс истёк. Войдите повторно")


def bearer_payload(authorization: str | None):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Требуется вход")
    return decode_token(authorization[7:])


def current_user(authorization: str | None = Header(None)):
    payload = bearer_payload(authorization)
    personal = accounts.authorize(payload, 'user', 'upo')
    if personal: return personal
    if payload.get("role") != "user" or payload.get('scope'):
        raise HTTPException(403, "Недостаточно прав")
    payload["uid"] = payload["sub"]
    return payload


def current_admin(authorization: str | None = Header(None)):
    payload = bearer_payload(authorization)
    personal = accounts.authorize(payload, 'admin', 'upo')
    if personal: return personal
    if os.environ.get('WORKBOOK_PERSONAL_ACCOUNTS','1')=='1':raise HTTPException(401,'Войдите в персональный аккаунт')
    if payload.get("role") != "admin" or payload.get('scope'):
        raise HTTPException(403, "Недостаточно прав")
    return payload


def fire_identity(authorization, role):
    payload = bearer_payload(authorization)
    personal = accounts.authorize(payload, role, 'fire')
    if personal: return personal
    if role=='admin' and os.environ.get('WORKBOOK_PERSONAL_ACCOUNTS','1')=='1':raise HTTPException(401,'Войдите в персональный аккаунт')
    if payload.get('role') != role:
        raise HTTPException(403, 'Недостаточно прав')
    if fire_access.config:
        if payload.get('scope') != fire.CID:
            raise HTTPException(403, 'Войдите с данными доступа пожарной безопасности')
        if payload.get('credential_revision') != fire_access.config['revision']:
            raise HTTPException(401, 'Данные доступа изменены. Войдите повторно')
    elif payload.get('scope'):
        raise HTTPException(403, 'Недостаточно прав')
    payload['uid'] = payload['sub']
    return payload


def fire_current_user(authorization: str | None = Header(None)):
    return fire_identity(authorization, 'user')


def fire_current_admin(authorization: str | None = Header(None)):
    return fire_identity(authorization, 'admin')


def progress_user(course_id: str, authorization: str | None = Header(None)):
    return fire_current_user(authorization) if course_id == fire.CID else current_user(authorization)


def check_rate_limit(ip: str, scope: str):
    current = time.time()
    queue = attempts_by_ip[f"{scope}:{ip}"]
    while queue and queue[0] < current - 600:
        queue.popleft()
    if len(queue) >= 10:
        raise HTTPException(429, "Слишком много попыток. Повторите через 10 минут")
    return queue


def empty_state():
    return {"checks": {}, "answers": {}, "notes": {}, "done": {}, "profile": {"name": "", "unit": ""}}


def empty_summary():
    return {"percent": 0, "correct": 0, "totalQ": 0, "answered": 0, "checked": 0, "totalChecks": 0, "points": 0}


def number(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def normalized_summary(summary: dict):
    correct = max(0, number(summary.get("correct")))
    total_q = max(0, number(summary.get("totalQ")))
    points = max(0, number(summary.get("points"), correct * 10))
    return {
        **summary,
        "percent": max(0, min(100, number(summary.get("percent")))),
        "correct": correct,
        "totalQ": total_q,
        "answered": max(0, number(summary.get("answered"))),
        "checked": max(0, number(summary.get("checked"))),
        "totalChecks": max(0, number(summary.get("totalChecks"))),
        "points": points,
        "success_percent": round(correct / total_q * 100) if total_q else 0,
    }


def ensure_course_progress(con, user_id: str, course_id: str, timestamp: str):
    validate_course(course_id)
    row = con.execute(
        "SELECT 1 FROM progress WHERE user_id = ? AND course_id = ?", (user_id, course_id)
    ).fetchone()
    if row:
        return
    state_json = json.dumps(empty_state(), ensure_ascii=False)
    summary_json = json.dumps(empty_summary(), ensure_ascii=False)
    con.execute("""
      INSERT INTO progress(
        user_id, course_id, state_json, summary_json, updated_at,
        attempt_no, started_at, completed_at
      ) VALUES(?,?,?,?,?,1,?,NULL)
    """, (user_id, course_id, state_json, summary_json, timestamp, timestamp))
    con.execute("""
      INSERT INTO course_attempts(
        user_id, course_id, attempt_no, state_json, summary_json,
        started_at, updated_at, completed_at
      ) VALUES(?,?,1,?,?,?,?,NULL)
    """, (user_id, course_id, state_json, summary_json, timestamp, timestamp))


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/v1/materials/{folder_key}/download")
def download_material(folder_key: str, filename: str):
    folder = MATERIAL_FOLDERS.get(folder_key)
    if not folder:
        raise HTTPException(404, "Папка материалов не найдена")
    if folder.get("prefix_only"):
        allowed = any(filename.startswith(f"{prefix} ") for prefix in folder["files"])
    else:
        allowed = filename in folder["files"]
    if not allowed or "/" in filename or "\\" in filename:
        raise HTTPException(404, "Файл не найден")
    query = urlencode({"public_key": folder["public_key"], "path": f"/{filename}"})
    request = UrlRequest(
        f"https://cloud-api.yandex.net/v1/disk/public/resources/download?{query}",
        headers={"User-Agent": "IFCM-Workbooks/1.0"},
    )
    try:
        with urlopen(request, timeout=12) as response:
            payload = json.loads(response.read().decode("utf-8"))
        href = payload.get("href")
        if not href:
            raise ValueError("download URL is missing")
    except (UrlHTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError):
        raise HTTPException(502, "Яндекс Диск временно не вернул ссылку на скачивание")
    return RedirectResponse(href, status_code=302)


@app.post("/api/v1/session")
def login(data: LoginInput, request: Request):
    if os.environ.get('WORKBOOK_PERSONAL_ACCOUNTS','1') == '1' and (request.url.path.endswith('/builder/session') or (data.course_id and data.course_id not in COURSE_TITLES)):
        raise HTTPException(401, 'Для этой тетради используйте персональный вход')
    separate_fire = data.course_id == fire.CID and fire_access.config is not None
    queue = check_rate_limit(request.client.host if request.client else "unknown", "fire-student" if separate_fire else "student")
    valid = fire_access.matches('code', data.code) if separate_fire else hmac.compare_digest(data.code.encode("utf-8"), ACCESS_CODE.encode("utf-8"))
    if not valid:
        queue.append(time.time())
        raise HTTPException(401, "Неверный код доступа")
    queue.clear()
    full_name = clean_name(data.full_name)
    normalized = normalize_name(full_name)
    timestamp = now_iso()
    with closing(db()) as con:
        row = con.execute("SELECT id FROM users WHERE normalized_name = ?", (normalized,)).fetchone()
        if row:
            user_id = row["id"]
            con.execute("UPDATE users SET full_name = ?, updated_at = ? WHERE id = ?", (full_name, timestamp, user_id))
        else:
            user_id = secrets.token_urlsafe(16)
            con.execute(
                "INSERT INTO users(id, full_name, normalized_name, created_at, updated_at) VALUES(?,?,?,?,?)",
                (user_id, full_name, normalized, timestamp, timestamp),
            )
        if data.course_id and request.url.path != '/api/v1/fire/builder/session':
            ensure_course_progress(con, user_id, data.course_id, timestamp)
        con.commit()
    return {"token": encode_token(user_id, full_name, "user", scope=fire.CID if separate_fire else None), "full_name": full_name}


@app.post('/api/v1/fire/session')
def fire_login(data: LoginInput, request: Request):
    if os.environ.get('WORKBOOK_PERSONAL_ACCOUNTS','1')=='1' and request.url.path.endswith('/builder/session'):
        raise HTTPException(401,'Для этой тетради используйте персональный вход')
    return login(data.model_copy(update={'course_id': fire.CID}), request)


@app.post("/api/v1/admin/session")
def admin_login(data: AdminLoginInput, request: Request):
    if os.environ.get('WORKBOOK_PERSONAL_ACCOUNTS','1') == '1':
        raise HTTPException(401, 'Используйте персональный вход /v4/accounts.html')
    queue = check_rate_limit(request.client.host if request.client else "unknown", "admin")
    login_ok = hmac.compare_digest(data.login.encode("utf-8"), ADMIN_LOGIN.encode("utf-8"))
    password_ok = hmac.compare_digest(data.password.encode("utf-8"), ADMIN_PASSWORD.encode("utf-8"))
    if not (login_ok and password_ok):
        queue.append(time.time())
        raise HTTPException(401, "Неверный логин или пароль")
    queue.clear()
    return {
        "token": encode_token("admin", ADMIN_LOGIN, "admin", ADMIN_TOKEN_TTL),
        "login": ADMIN_LOGIN,
    }


@app.post('/api/v1/fire/admin/session')
def fire_admin_login(data: AdminLoginInput, request: Request):
    if os.environ.get('WORKBOOK_PERSONAL_ACCOUNTS','1') == '1':
        raise HTTPException(401, 'Используйте персональный вход /v4/accounts.html')
    if not fire_access.config:
        return admin_login(data, request)
    queue = check_rate_limit(request.client.host if request.client else 'unknown', 'fire-admin')
    login_ok = hmac.compare_digest(data.login.encode(), fire_access.config['login'].encode())
    password_ok = fire_access.matches('password', data.password)
    if not (login_ok and password_ok):
        queue.append(time.time())
        raise HTTPException(401, 'Неверный логин или пароль')
    queue.clear()
    return {'token': encode_token('fire-admin', data.login, 'admin', ADMIN_TOKEN_TTL, scope=fire.CID), 'login': data.login}


@app.get("/api/v1/progress/{course_id}")
def get_progress(course_id: str, user=Depends(progress_user)):
    validate_course(course_id)
    with closing(db()) as con:
        row = con.execute(
            """SELECT state_json, summary_json, updated_at, attempt_no, started_at, completed_at
               FROM progress WHERE user_id = ? AND course_id = ?""",
            (user["uid"], course_id),
        ).fetchone()
    if not row:
        return {"state": None, "summary": None, "updated_at": None, "attempt_no": 1}
    return {
        "state": json.loads(row["state_json"]),
        "summary": normalized_summary(json.loads(row["summary_json"])),
        "updated_at": row["updated_at"],
        "attempt_no": row["attempt_no"],
        "started_at": row["started_at"],
        "completed_at": row["completed_at"],
    }


@app.put("/api/v1/progress/{course_id}")
def put_progress(course_id: str, data: ProgressInput, user=Depends(progress_user)):
    validate_course(course_id)
    state_json = json.dumps(data.state, ensure_ascii=False)
    if len(state_json) > 250_000:
        raise HTTPException(413, "Слишком большой объём данных")
    if course_id == fire.CID:
        with closing(db()) as con:
            previous = con.execute("SELECT state_json FROM progress WHERE user_id=? AND course_id=?", (user["uid"], course_id)).fetchone()
        data.state = fire.prepare_state(data.state, json.loads(previous["state_json"]) if previous else None)
        state_json = json.dumps(data.state, ensure_ascii=False)
        summary = normalized_summary(fire.score(data.state, fire_settings()["passPercent"]))
    else:
        summary = normalized_summary(builder_score(course_id, data.state) or data.summary)
    summary_json = json.dumps(summary, ensure_ascii=False)
    timestamp = now_iso()
    with closing(db()) as con:
        row = con.execute(
            "SELECT attempt_no, started_at, completed_at FROM progress WHERE user_id = ? AND course_id = ?",
            (user["uid"], course_id),
        ).fetchone()
        attempt_no = row["attempt_no"] if row else 1
        if builder_edition(course_id):
            if any(not isinstance(data.state.get(k, {}), dict) for k in ("answers", "checks", "evidence", "notes", "done")):
                raise HTTPException(422, "Неверный формат данных тетради")
            evidence = data.state.get("evidence", {})
            content = json.loads(builder_edition(course_id)["content"])
            limits = {b["id"]: b.get("maxImages", 3) for section in content["sections"] for b in section["blocks"] if b["type"] == "practice"}
            for bid, ids in evidence.items():
                if bid not in limits or not isinstance(ids, list) or len(ids) > limits[bid] or any(not isinstance(i, str) for i in ids):
                    raise HTTPException(422, "Неверные скриншоты задания")
                for iid in ids:
                    owned = con.execute("SELECT 1 FROM builder_evidence WHERE id=? AND user_id=? AND course_id=? AND attempt_no=? AND block_id=?", (iid, user["uid"], course_id, attempt_no, bid)).fetchone()
                    if not owned:
                        raise HTTPException(422, "Скриншот не относится к этой попытке")
            # Changing a checked answer requires another teacher review.
            if row:
                previous = con.execute("SELECT state_json FROM progress WHERE user_id=? AND course_id=?", (user["uid"], course_id)).fetchone()
                old_state = json.loads(previous["state_json"])
                reviews = con.execute("SELECT block_id, review FROM builder_reviews WHERE user_id=? AND course_id=? AND attempt_no=?", (user["uid"], course_id, attempt_no)).fetchall()
                for review in reviews:
                    bid = review["block_id"]
                    block=next((b for sec in content['sections'] for b in sec['blocks'] if b['id']==bid),{})
                    changed=any(old_state.get(k, {}).get(bid) != data.state.get(k, {}).get(bid) for k in ("answers", "checks", "evidence"))
                    if block.get('legacyKey'):changed=changed or old_state.get('checks',{}).get(block['legacyKey'])!=data.state.get('checks',{}).get(block['legacyKey'])
                    if changed:
                        prior_review = json.loads(review["review"])
                        prior_review.update({"checked": False, "needs_recheck": True, "changed_at": timestamp})
                        con.execute("UPDATE builder_reviews SET review=?, updated_at=? WHERE user_id=? AND course_id=? AND attempt_no=? AND block_id=?", (json.dumps(prior_review, ensure_ascii=False), timestamp, user["uid"], course_id, attempt_no, bid))
            summary = normalized_summary(builder_apply_reviews(con, user["uid"], course_id, attempt_no, summary))
            summary_json = json.dumps(summary, ensure_ascii=False)
        started_at = row["started_at"] if row and row["started_at"] else timestamp
        completed_at = row["completed_at"] if row else None
        if summary["percent"] >= 100 and not completed_at:
            completed_at = timestamp
        con.execute("""
          INSERT INTO progress(
            user_id, course_id, state_json, summary_json, updated_at,
            attempt_no, started_at, completed_at
          ) VALUES(?,?,?,?,?,?,?,?)
          ON CONFLICT(user_id, course_id) DO UPDATE SET
            state_json=excluded.state_json,
            summary_json=excluded.summary_json,
            updated_at=excluded.updated_at,
            attempt_no=excluded.attempt_no,
            started_at=excluded.started_at,
            completed_at=excluded.completed_at
        """, (
            user["uid"], course_id, state_json, summary_json, timestamp,
            attempt_no, started_at, completed_at,
        ))
        con.execute("""
          INSERT INTO course_attempts(
            user_id, course_id, attempt_no, state_json, summary_json,
            started_at, updated_at, completed_at
          ) VALUES(?,?,?,?,?,?,?,?)
          ON CONFLICT(user_id, course_id, attempt_no) DO UPDATE SET
            state_json=excluded.state_json,
            summary_json=excluded.summary_json,
            updated_at=excluded.updated_at,
            completed_at=COALESCE(course_attempts.completed_at, excluded.completed_at)
        """, (
            user["uid"], course_id, attempt_no, state_json, summary_json,
            started_at, timestamp, completed_at,
        ))
        con.commit()
    return {"saved": True, "updated_at": timestamp, "attempt_no": attempt_no, "completed_at": completed_at, "summary": summary}


@app.post("/api/v1/progress/{course_id}/restart")
def restart_progress(course_id: str, user=Depends(progress_user)):
    validate_course(course_id)
    timestamp = now_iso()
    state = empty_state()
    summary = empty_summary()
    state_json = json.dumps(state, ensure_ascii=False)
    summary_json = json.dumps(summary, ensure_ascii=False)
    with closing(db()) as con:
        row = con.execute(
            "SELECT attempt_no FROM progress WHERE user_id = ? AND course_id = ?",
            (user["uid"], course_id),
        ).fetchone()
        attempt_no = (row["attempt_no"] + 1) if row else 1
        con.execute("""
          INSERT INTO progress(
            user_id, course_id, state_json, summary_json, updated_at,
            attempt_no, started_at, completed_at
          ) VALUES(?,?,?,?,?,?,?,NULL)
          ON CONFLICT(user_id, course_id) DO UPDATE SET
            state_json=excluded.state_json,
            summary_json=excluded.summary_json,
            updated_at=excluded.updated_at,
            attempt_no=excluded.attempt_no,
            started_at=excluded.started_at,
            completed_at=NULL
        """, (user["uid"], course_id, state_json, summary_json, timestamp, attempt_no, timestamp))
        con.execute("""
          INSERT INTO course_attempts(
            user_id, course_id, attempt_no, state_json, summary_json,
            started_at, updated_at, completed_at
          ) VALUES(?,?,?,?,?,?,?,NULL)
        """, (user["uid"], course_id, attempt_no, state_json, summary_json, timestamp, timestamp))
        con.commit()
    return {"restarted": True, "attempt_no": attempt_no, "state": state, "summary": summary}


def parse_json(value: str, fallback: dict):
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def attempt_payload(row):
    return {
        "course_id": row["course_id"],
        "course_title": course_titles().get(row["course_id"], row["course_id"]),
        "attempt_no": row["attempt_no"],
        "state": parse_json(row["state_json"], empty_state()),
        "summary": normalized_summary(parse_json(row["summary_json"], empty_summary())),
        "started_at": row["started_at"],
        "updated_at": row["updated_at"],
        "completed_at": row["completed_at"],
    }


@app.get("/api/v1/admin/dashboard")
def admin_dashboard(admin=Depends(current_admin)):
    actor = admin.get("account")
    with closing(db()) as con:
        users = con.execute("SELECT * FROM users ORDER BY full_name COLLATE NOCASE").fetchall()
        progress_rows = con.execute("""
          SELECT p.*, u.full_name
          FROM progress p JOIN users u ON u.id = p.user_id
          ORDER BY p.updated_at DESC
        """).fetchall()
        attempt_rows = con.execute("SELECT * FROM course_attempts ORDER BY attempt_no").fetchall()
        direction_rows = con.execute("SELECT course_id,draft_id,version,title,content FROM builder_editions").fetchall()
        imported_groups = {r["draft_id"]: r["source_course"] for r in con.execute("SELECT draft_id,source_course FROM builder_imports")}
    if actor and 'admin' not in actor['roles']:
        users = [r for r in users if accounts.visible(actor,r['id'])]
        progress_rows = [r for r in progress_rows if accounts.visible(actor,r['user_id']) and accounts.course_allowed(actor,r['course_id'])]
        attempt_rows = [r for r in attempt_rows if accounts.visible(actor,r['user_id']) and accounts.course_allowed(actor,r['course_id'])]
    course_directions = {row["course_id"]: parse_json(row["content"], {}).get("courseDirection") or "1С:УПО" for row in direction_rows}
    course_directions[fire.CID] = "Безопасность"
    course_groups = {cid: {"group_id": "source:" + cid, "group_title": title, "edition_version": 0} for cid, title in COURSE_TITLES.items()}
    for row in direction_rows:
        course_groups[row["course_id"]] = {
            "group_id": "source:" + imported_groups[row["draft_id"]] if row["draft_id"] in imported_groups else "draft:" + row["draft_id"],
            "group_title": row["title"], "edition_version": row["version"],
        }


    attempts_map = defaultdict(list)
    for row in attempt_rows:
        attempts_map[(row["user_id"], row["course_id"])].append(attempt_payload(row))

    records = []
    by_course = defaultdict(list)
    for row in progress_rows:
        summary = normalized_summary(parse_json(row["summary_json"], empty_summary()))
        history = attempts_map[(row["user_id"], row["course_id"])]
        first_summary = history[0]["summary"] if history else summary
        percent = summary["percent"]
        if percent >= 100:
            status = "completed"
        elif percent > 0 or summary["answered"] or summary["checked"]:
            status = "active"
        else:
            status = "not_started"
        record = {
            "user_id": row["user_id"],
            "full_name": row["full_name"],
            "course_id": row["course_id"],
            "course_title": course_titles().get(row["course_id"], row["course_id"]),
            "course_direction": course_directions.get(row["course_id"], "1С:УПО"),
            "status": status,
            "progress_percent": percent,
            "success_percent": summary["success_percent"],
            "needs_recheck": summary.get("needsRecheck", 0),
            "pending_review": summary.get("pendingReview", 0),
            "points": summary["points"],
            "correct": summary["correct"],
            "total_questions": summary["totalQ"],
            "attempt_no": row["attempt_no"],
            "attempts_count": len(history) or 1,
            "first_progress_percent": first_summary["percent"],
            "first_success_percent": first_summary["success_percent"],
            "progress_delta": percent - first_summary["percent"],
            "success_delta": summary["success_percent"] - first_summary["success_percent"],
            "started_at": row["started_at"],
            "updated_at": row["updated_at"],
            "completed_at": row["completed_at"],
        }
        records.append(record)
        by_course[row["course_id"]].append(record)

    course_stats = []
    for course_id, title in course_titles().items():
        if actor and not accounts.course_allowed(actor,course_id):continue
        rows = by_course.get(course_id, [])
        course_stats.append({
            "course_id": course_id,
            "course_title": title,
            "course_direction": course_directions.get(course_id, "1С:УПО"),
            **course_groups.get(course_id, {}),
            "users": len(rows),
            "completed": sum(1 for item in rows if item["status"] == "completed"),
            "active": sum(1 for item in rows if item["status"] == "active"),
            "average_progress": round(sum(item["progress_percent"] for item in rows) / len(rows)) if rows else 0,
            "average_success": round(sum(item["success_percent"] for item in rows) / len(rows)) if rows else 0,
        })

    unique_active_users = {item["user_id"] for item in records if item["status"] == "active"}
    unique_completed_users = {item["user_id"] for item in records if item["status"] == "completed"}
    return {
        "generated_at": now_iso(),
        "kpi": {
            "users_total": len(users),
            "users_active": len(unique_active_users),
            "users_completed": len(unique_completed_users),
            "courses_completed": sum(1 for item in records if item["status"] == "completed"),
            "average_progress": round(sum(item["progress_percent"] for item in records) / len(records)) if records else 0,
            "average_success": round(sum(item["success_percent"] for item in records) / len(records)) if records else 0,
            "attempts_total": len(attempt_rows),
        },
        "distribution": {
            "completed": sum(1 for item in records if item["status"] == "completed"),
            "active": sum(1 for item in records if item["status"] == "active"),
            "not_started": sum(1 for item in records if item["status"] == "not_started"),
        },
        "courses": course_stats,
        "records": records,
    }


@app.get("/api/v1/admin/users/{user_id}")
def admin_user_detail(user_id: str, admin=Depends(current_admin)):
    actor=admin.get('account')
    if actor and not accounts.visible(actor,user_id):raise HTTPException(403,'Нет доступа к участнику')
    with closing(db()) as con:
        user = con.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user:
            raise HTTPException(404, "Пользователь не найден")
        rows = con.execute("""
          SELECT * FROM course_attempts
          WHERE user_id = ?
          ORDER BY course_id, attempt_no
        """, (user_id,)).fetchall()
    courses = defaultdict(list)
    for row in rows:
        if actor and not accounts.course_allowed(actor,row['course_id']):continue
        courses[row["course_id"]].append(attempt_payload(row))
    return {
        "user": {
            "id": user["id"],
            "full_name": user["full_name"],
            "created_at": user["created_at"],
            "updated_at": user["updated_at"],
        },
        "courses": [
            {
                "course_id": course_id,
                "course_title": course_titles().get(course_id, course_id),
                "attempts": attempts,
            }
            for course_id, attempts in courses.items()
        ],
    }


def authorize_admin_export(authorization: str | None, x_admin_key: str | None):
    if os.environ.get('WORKBOOK_PERSONAL_ACCOUNTS','1') != '1' and x_admin_key and hmac.compare_digest(x_admin_key.encode("utf-8"), ADMIN_KEY.encode("utf-8")):
        return
    payload = bearer_payload(authorization)
    personal=accounts.authorize(payload,'admin','upo')
    if personal:
        return personal['account']
    if payload.get("role") != "admin" or payload.get('scope'):
        raise HTTPException(403, "Недостаточно прав")


@app.get("/api/v1/admin/results.csv")
def export_results(
    authorization: str | None = Header(None),
    x_admin_key: str | None = Header(None),
):
    actor = authorize_admin_export(authorization, x_admin_key)
    with closing(db()) as con:
        rows = con.execute("""
          SELECT u.id AS user_id, u.full_name, p.course_id, p.state_json, p.summary_json, p.attempt_no, p.updated_at
          FROM progress p JOIN users u ON u.id = p.user_id
          ORDER BY p.updated_at DESC
        """).fetchall()
    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow(["ID участника", "Табельный номер", "Подразделение", "Должность", "ФИО", "Курс", "Попытка", "Прогресс, %", "Успех, %", "Баллы", "Верных ответов", "Всего вопросов", "Обновлено"])
    for row in rows:
        if actor and (not accounts.visible(actor,row['user_id']) or not accounts.course_allowed(actor,row['course_id'])):continue
        profile=parse_json(row['state_json'],{}).get('profile',{})
        summary = normalized_summary(parse_json(row["summary_json"], empty_summary()))
        values=[row['user_id'],profile.get('personnel_number',''),profile.get('unit',''),profile.get('position',''),
            row["full_name"], course_titles().get(row["course_id"], row["course_id"]), row["attempt_no"],
            summary["percent"], summary["success_percent"], summary["points"],
            summary["correct"], summary["totalQ"], row["updated_at"],
        ]
        writer.writerow(["'"+str(v) if str(v).startswith(("=","+","-","@","\t","\r")) else v for v in values])
    payload = "\ufeff" + output.getvalue()
    return StreamingResponse(
        iter([payload.encode("utf-8")]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=workbook-results.csv"},
    )


from builder import register
builder_edition, builder_titles, builder_score, builder_apply_reviews = register(app, db, current_admin, current_user, now_iso, DB_PATH.parent, STATIC_DIR)

def course_titles():
    return {**COURSE_TITLES, **builder_titles()}

import fire
fire_settings = fire.register_fire(app, db, fire_current_user, fire_current_admin, now_iso, STATIC_DIR)

@app.get('/api/v1/fire/access-public-key')
def fire_access_public_key():
    return {'public_key': fire_access.public_key()}

@app.post('/api/v1/fire/builder/session')
def fire_editable_login(data: LoginInput, request: Request):
    return fire_login(data, request)

import fire_builder
from full_backup import create as full_backup
fire_builder.register(app, db, fire_current_admin, fire_current_user, now_iso, STATIC_DIR, DB_PATH.parent,
    lambda reason: full_backup(DB_PATH, STATIC_DIR, reason))

from accounts import Accounts
accounts = Accounts(app, db, now_iso, encode_token, decode_token, check_rate_limit, ADMIN_LOGIN, ADMIN_PASSWORD)
def sign_account_token(raw):
    signature = hmac.new(TOKEN_SECRET.encode(), raw, hashlib.sha256).digest()
    return (raw + b'.' + base64.urlsafe_b64encode(signature).rstrip(b'=')).decode()
accounts.sign = sign_account_token
app.state.accounts = accounts

# Uniform authorization of personal sessions including evidence and legacy adapters.
from fastapi.responses import JSONResponse
@app.middleware('http')
async def personal_access(request: Request, call_next):
    try:
        path=request.url.path
        is_accounts=path=='/api/v1/accounts' or path.startswith('/api/v1/accounts/')
        auth=request.headers.get('authorization','')
        if path.startswith('/api/v1/') and auth.startswith('Bearer '):
            payload=decode_token(auth[7:])
            if not payload.get('account_id') and os.environ.get('WORKBOOK_PERSONAL_ACCOUNTS','1')=='1':
                if '/builder/' in path or '/workbooks/' in path or path.startswith('/api/v1/admin/') or payload.get('role')=='admin':
                    raise HTTPException(401,'Войдите в персональный аккаунт')
                if '/progress/' in path and path.split('/progress/')[1].split('/')[0] not in COURSE_TITLES:
                    raise HTTPException(401,'Войдите в персональный аккаунт')
            if payload.get('account_id'):
                account=accounts.check(payload, allow_change=is_accounts)
                if not is_accounts:
                    realm='fire' if path.startswith('/api/v1/fire/') or '/fire-safety-v4' in path else 'upo'
                    staff='admin' in account['roles'] or 'teacher' in account['roles']
                    if staff and 'admin' not in account['roles']:
                        if account['courses'] and ('/builder/drafts' in path or '/builder/templates' in path or '/builder/import' in path or '/builder/editions' in path and not '/fire/' in path or path.endswith('/builder/trash')):
                            raise HTTPException(403,'Для работы с конструктором требуется доступ ко всему направлению')
                        if realm not in account['realms']:raise HTTPException(403,'Нет доступа к направлению')
                        permission='view'
                        if request.method!='GET':
                            permission='publish' if path.endswith('/publish') else ('edit' if '/builder/' in path and '/reviews/' not in path and not path.endswith('/review') else 'review')
                        if request.method=='GET' and any(x in path for x in ('/builder/drafts','/builder/templates','/builder/trash')):permission='edit'
                        if permission not in account['permissions']:raise HTTPException(403,'Нет права на это действие')
                        # Filtered dashboard and user detail are implemented in their handlers.
                    parts=path.split('/')
                    cid=None;uid=None
                    if '/progress/' in path:cid=parts[parts.index('progress')+1]
                    elif '/workbooks/' in path:cid=parts[parts.index('workbooks')+1]
                    elif '/fire/builder/editions/' in path:cid=parts[parts.index('editions')+1]
                    elif '/builder/reviews/' in path:
                        idx=parts.index('reviews');uid=parts[idx+1];cid=parts[idx+2]
                    elif '/fire/' in path and not '/builder/' in path:cid=fire.CID
                    if '/evidence/' in path and request.method=='GET':
                        with closing(db()) as con:
                            if '/fire/' in path:r=con.execute('SELECT uid AS user_id,cid AS course_id FROM fb_evidence WHERE id=?',(parts[-1],)).fetchone()
                            else:r=con.execute('SELECT user_id,course_id FROM builder_evidence WHERE id=?',(parts[-1],)).fetchone()
                        if r:uid=r['user_id'];cid=r['course_id']
                    if cid and not accounts.course_allowed(account,cid):raise HTTPException(403,'Курс не назначен')
                    if uid and staff and not accounts.visible(account,uid):raise HTTPException(403,'Нет доступа к участнику')
                    if request.method=='PUT' and path.endswith('/review') and staff:
                        body=await request.json();target=body.get('user_id') or body.get('uid')
                        if target and not accounts.visible(account,target):raise HTTPException(403,'Нет доступа к участнику')
                    if request.method=='PUT' and ('/progress/' in path or path.endswith('/progress')):
                        try:body=await request.json()
                        except ValueError:raise HTTPException(422,'Неверный JSON')
                        if not isinstance(body,dict) or not isinstance(body.get('state'),dict) or not isinstance(body['state'].get('profile',{}),dict):raise HTTPException(422,'Неверный формат состояния')
                        profile=body['state'].setdefault('profile',{})
                        profile.update(name=account['full_name'],unit=account['unit'],position=account['position'],participant_id=account['id'],personnel_number=account['personnel_number'])
                        request._body=json.dumps(body,ensure_ascii=False).encode()
        response=await call_next(request)
        if path.startswith('/api/v1/accounts/') or path=='/api/v1/accounts':response.headers['Cache-Control']='no-store'
        return response
    except HTTPException as exc:
        return JSONResponse({'detail':exc.detail},status_code=exc.status_code,headers={'Cache-Control':'no-store'})

app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="workbooks")
