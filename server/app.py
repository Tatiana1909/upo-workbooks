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
ADMIN_LOGIN = os.environ.get("WORKBOOK_ADMIN_LOGIN", "")
ADMIN_PASSWORD = os.environ.get("WORKBOOK_ADMIN_PASSWORD", "")
DB_PATH = Path(os.environ.get("WORKBOOK_DB_PATH", "/data/workbooks.sqlite3"))
STATIC_DIR = Path(os.environ.get("WORKBOOK_STATIC_DIR", "/app/static"))
TOKEN_TTL = int(os.environ.get("WORKBOOK_TOKEN_TTL_HOURS", "168")) * 3600
ADMIN_TOKEN_TTL = int(os.environ.get("WORKBOOK_ADMIN_TOKEN_TTL_HOURS", "12")) * 3600
ALLOWED_ORIGINS = [x.strip() for x in os.environ.get(
    "WORKBOOK_ALLOWED_ORIGINS", "https://tatiana1909.github.io"
).split(",") if x.strip()]
COURSE_TITLES = {
    "accounting": "Складской и производственный учёт в 1С УПО",
    "warehouse": "Работники склада",
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
    if course_id not in COURSE_TITLES:
        raise HTTPException(404, "Курс не найден")


def encode_token(subject: str, name: str, role: str, ttl: int = TOKEN_TTL):
    payload = {"sub": subject, "name": name, "role": role, "exp": int(time.time()) + ttl}
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
    if payload.get("role") != "user":
        raise HTTPException(403, "Недостаточно прав")
    payload["uid"] = payload["sub"]
    return payload


def current_admin(authorization: str | None = Header(None)):
    payload = bearer_payload(authorization)
    if payload.get("role") != "admin":
        raise HTTPException(403, "Недостаточно прав")
    return payload


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


@app.post("/api/v1/session")
def login(data: LoginInput, request: Request):
    queue = check_rate_limit(request.client.host if request.client else "unknown", "student")
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
        if data.course_id:
            ensure_course_progress(con, user_id, data.course_id, timestamp)
        con.commit()
    return {"token": encode_token(user_id, full_name, "user"), "full_name": full_name}


@app.post("/api/v1/admin/session")
def admin_login(data: AdminLoginInput, request: Request):
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


@app.get("/api/v1/progress/{course_id}")
def get_progress(course_id: str, user=Depends(current_user)):
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
def put_progress(course_id: str, data: ProgressInput, user=Depends(current_user)):
    validate_course(course_id)
    state_json = json.dumps(data.state, ensure_ascii=False)
    if len(state_json) > 250_000:
        raise HTTPException(413, "Слишком большой объём данных")
    summary = normalized_summary(data.summary)
    summary_json = json.dumps(summary, ensure_ascii=False)
    timestamp = now_iso()
    with closing(db()) as con:
        row = con.execute(
            "SELECT attempt_no, started_at, completed_at FROM progress WHERE user_id = ? AND course_id = ?",
            (user["uid"], course_id),
        ).fetchone()
        attempt_no = row["attempt_no"] if row else 1
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
    return {"saved": True, "updated_at": timestamp, "attempt_no": attempt_no, "completed_at": completed_at}


@app.post("/api/v1/progress/{course_id}/restart")
def restart_progress(course_id: str, user=Depends(current_user)):
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
        "course_title": COURSE_TITLES.get(row["course_id"], row["course_id"]),
        "attempt_no": row["attempt_no"],
        "state": parse_json(row["state_json"], empty_state()),
        "summary": normalized_summary(parse_json(row["summary_json"], empty_summary())),
        "started_at": row["started_at"],
        "updated_at": row["updated_at"],
        "completed_at": row["completed_at"],
    }


@app.get("/api/v1/admin/dashboard")
def admin_dashboard(admin=Depends(current_admin)):
    del admin
    with closing(db()) as con:
        users = con.execute("SELECT * FROM users ORDER BY full_name COLLATE NOCASE").fetchall()
        progress_rows = con.execute("""
          SELECT p.*, u.full_name
          FROM progress p JOIN users u ON u.id = p.user_id
          ORDER BY p.updated_at DESC
        """).fetchall()
        attempt_rows = con.execute("SELECT * FROM course_attempts ORDER BY attempt_no").fetchall()

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
            "course_title": COURSE_TITLES.get(row["course_id"], row["course_id"]),
            "status": status,
            "progress_percent": percent,
            "success_percent": summary["success_percent"],
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
    for course_id, title in COURSE_TITLES.items():
        rows = by_course.get(course_id, [])
        course_stats.append({
            "course_id": course_id,
            "course_title": title,
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
    del admin
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
                "course_title": COURSE_TITLES.get(course_id, course_id),
                "attempts": attempts,
            }
            for course_id, attempts in courses.items()
        ],
    }


def authorize_admin_export(authorization: str | None, x_admin_key: str | None):
    if x_admin_key and hmac.compare_digest(x_admin_key.encode("utf-8"), ADMIN_KEY.encode("utf-8")):
        return
    payload = bearer_payload(authorization)
    if payload.get("role") != "admin":
        raise HTTPException(403, "Недостаточно прав")


@app.get("/api/v1/admin/results.csv")
def export_results(
    authorization: str | None = Header(None),
    x_admin_key: str | None = Header(None),
):
    authorize_admin_export(authorization, x_admin_key)
    with closing(db()) as con:
        rows = con.execute("""
          SELECT u.full_name, p.course_id, p.summary_json, p.attempt_no, p.updated_at
          FROM progress p JOIN users u ON u.id = p.user_id
          ORDER BY p.updated_at DESC
        """).fetchall()
    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow(["ФИО", "Курс", "Попытка", "Прогресс, %", "Успех, %", "Баллы", "Верных ответов", "Всего вопросов", "Обновлено"])
    for row in rows:
        summary = normalized_summary(parse_json(row["summary_json"], empty_summary()))
        writer.writerow([
            row["full_name"], COURSE_TITLES.get(row["course_id"], row["course_id"]), row["attempt_no"],
            summary["percent"], summary["success_percent"], summary["points"],
            summary["correct"], summary["totalQ"], row["updated_at"],
        ])
    payload = "\ufeff" + output.getvalue()
    return StreamingResponse(
        iter([payload.encode("utf-8")]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=workbook-results.csv"},
    )


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="workbooks")
