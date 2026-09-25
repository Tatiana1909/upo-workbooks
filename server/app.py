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

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field


ACCESS_CODE = os.environ.get("WORKBOOK_ACCESS_CODE", "")
TOKEN_SECRET = os.environ.get("WORKBOOK_TOKEN_SECRET", "")
ADMIN_KEY = os.environ.get("WORKBOOK_ADMIN_KEY", "")
DB_PATH = Path(os.environ.get("WORKBOOK_DB_PATH", "/data/workbooks.sqlite3"))
STATIC_DIR = Path(os.environ.get("WORKBOOK_STATIC_DIR", "/app/static"))
TOKEN_TTL = int(os.environ.get("WORKBOOK_TOKEN_TTL_HOURS", "168")) * 3600
ALLOWED_ORIGINS = [x.strip() for x in os.environ.get(
    "WORKBOOK_ALLOWED_ORIGINS", "https://tatiana1909.github.io"
).split(",") if x.strip()]
COURSES = {"accounting", "warehouse"}

if not ACCESS_CODE or not TOKEN_SECRET or not ADMIN_KEY:
    raise RuntimeError("Set WORKBOOK_ACCESS_CODE, WORKBOOK_TOKEN_SECRET and WORKBOOK_ADMIN_KEY")

app = FastAPI(title="IFCM Workbooks API", docs_url=None, redoc_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "PUT", "POST"],
    allow_headers=["Authorization", "Content-Type", "X-Admin-Key"],
)
attempts: dict[str, deque] = defaultdict(deque)


class LoginInput(BaseModel):
    code: str = Field(min_length=1, max_length=200)
    full_name: str = Field(min_length=5, max_length=160)


class ProgressInput(BaseModel):
    state: dict
    summary: dict


def db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
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
          PRIMARY KEY (user_id, course_id),
          FOREIGN KEY (user_id) REFERENCES users(id)
        );
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


def encode_token(user_id: str, full_name: str):
    payload = {"uid": user_id, "name": full_name, "exp": int(time.time()) + TOKEN_TTL}
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


def current_user(authorization: str | None = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Требуется вход")
    return decode_token(authorization[7:])


def check_rate_limit(ip: str):
    current = time.time()
    queue = attempts[ip]
    while queue and queue[0] < current - 600:
        queue.popleft()
    if len(queue) >= 10:
        raise HTTPException(429, "Слишком много попыток. Повторите через 10 минут")
    return queue


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/api/v1/session")
def login(data: LoginInput, request: Request):
    queue = check_rate_limit(request.client.host if request.client else "unknown")
    if not hmac.compare_digest(data.code.encode("utf-8"), ACCESS_CODE.encode("utf-8")):
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
        con.commit()
    return {"token": encode_token(user_id, full_name), "full_name": full_name}


def validate_course(course_id: str):
    if course_id not in COURSES:
        raise HTTPException(404, "Курс не найден")


@app.get("/api/v1/progress/{course_id}")
def get_progress(course_id: str, user=Depends(current_user)):
    validate_course(course_id)
    with closing(db()) as con:
        row = con.execute(
            "SELECT state_json, summary_json, updated_at FROM progress WHERE user_id = ? AND course_id = ?",
            (user["uid"], course_id),
        ).fetchone()
    if not row:
        return {"state": None, "summary": None, "updated_at": None}
    return {"state": json.loads(row["state_json"]), "summary": json.loads(row["summary_json"]), "updated_at": row["updated_at"]}


@app.put("/api/v1/progress/{course_id}")
def put_progress(course_id: str, data: ProgressInput, user=Depends(current_user)):
    validate_course(course_id)
    if len(json.dumps(data.state, ensure_ascii=False)) > 250_000:
        raise HTTPException(413, "Слишком большой объём данных")
    timestamp = now_iso()
    with closing(db()) as con:
        con.execute("""
          INSERT INTO progress(user_id, course_id, state_json, summary_json, updated_at)
          VALUES(?,?,?,?,?)
          ON CONFLICT(user_id, course_id) DO UPDATE SET
            state_json=excluded.state_json,
            summary_json=excluded.summary_json,
            updated_at=excluded.updated_at
        """, (
            user["uid"], course_id,
            json.dumps(data.state, ensure_ascii=False),
            json.dumps(data.summary, ensure_ascii=False),
            timestamp,
        ))
        con.commit()
    return {"saved": True, "updated_at": timestamp}


def require_admin(x_admin_key: str | None = Header(None)):
    if not x_admin_key or not hmac.compare_digest(x_admin_key.encode("utf-8"), ADMIN_KEY.encode("utf-8")):
        raise HTTPException(403, "Недостаточно прав")


@app.get("/api/v1/admin/results.csv", dependencies=[Depends(require_admin)])
def export_results():
    with closing(db()) as con:
        rows = con.execute("""
          SELECT u.full_name, p.course_id, p.summary_json, p.updated_at
          FROM progress p JOIN users u ON u.id = p.user_id
          ORDER BY p.updated_at DESC
        """).fetchall()
    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow(["ФИО", "Курс", "Прогресс, %", "Верных ответов", "Всего вопросов", "Обновлено"])
    for row in rows:
        summary = json.loads(row["summary_json"])
        writer.writerow([
            row["full_name"], row["course_id"], summary.get("percent", 0),
            summary.get("correct", 0), summary.get("totalQ", 0), row["updated_at"],
        ])
    payload = "\ufeff" + output.getvalue()
    return StreamingResponse(
        iter([payload.encode("utf-8")]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=workbook-results.csv"},
    )


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="workbooks")
