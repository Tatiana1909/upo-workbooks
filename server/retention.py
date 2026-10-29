"""Six calendar months for attempt content; scores and attempt identity survive."""
import argparse
import asyncio
import calendar
import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import os


def parse_date(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('A timezone is required')
    return result.astimezone(timezone.utc)


def expires_at(value):
    start = parse_date(value)
    month = start.year * 12 + start.month - 1 + 6
    year, month = divmod(month, 12)
    month += 1
    return start.replace(year=year, month=month,
                         day=min(start.day, calendar.monthrange(year, month)[1]))


def assert_open(state):
    from fastapi import HTTPException
    if state.get('retentionExpired'):
        raise HTTPException(409, 'Срок хранения ответов истёк. Начните новую попытку; итоговые результаты сохранены.')


def compact(state, timestamp, summary):
    # Do not keep copies of answers in first, exam, history, notes or game state.
    value = {k: state[k] for k in ('profile', 'submitted', 'submittedAt', 'examThreshold') if k in state}
    value.update({k: {} for k in ('answers', 'first', 'exam', 'notes', 'responses', 'checks', 'evidence', 'read', 'done')})
    value.update(retentionExpired=True, contentExpiredAt=timestamp, retentionSummary=summary)
    return json.dumps(value, ensure_ascii=False)


def safe_evidence_path(root, folder, iid, ext):
    if folder not in ('builder-evidence', 'fire-builder-evidence') or not re.fullmatch(r'[a-f0-9]{32}', iid) or ext not in ('png', 'jpg', 'webp'):
        raise ValueError('Invalid evidence path')
    directory = root / folder
    if directory.is_symlink():
        raise ValueError('Evidence directory must not be a symlink')
    return directory / (iid + '.' + ext)


def sweep(db_path, data_dir, *, apply=False, at=None):
    """Dry run by default; a persistent queue makes file deletion retryable."""
    at = at or datetime.now(timezone.utc)
    root = Path(data_dir)
    if not Path(db_path).is_file():
        raise ValueError('Database does not exist')
    counts = dict(attempts=0, screenshots=0, invalid_dates=0, files_pending=0)
    with closing(sqlite3.connect(db_path)) as con:
        con.row_factory = sqlite3.Row
        if apply:
            con.execute('PRAGMA secure_delete=ON')
            con.execute('BEGIN IMMEDIATE')
            con.execute('CREATE TABLE IF NOT EXISTS retention_files(folder TEXT,id TEXT,extension TEXT,PRIMARY KEY(folder,id))')
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        layouts = [('course_attempts', 'user_id', 'course_id', 'attempt_no', 'state_json', 'builder_evidence', 'builder-evidence'),
                   ('fb_attempts', 'uid', 'cid', 'attempt', 'state', 'fb_evidence', 'fire-builder-evidence')]
        for table, uid, cid, attempt, field, evidence, folder in layouts:
            if table not in tables:
                continue
            for row in con.execute('SELECT * FROM ' + table).fetchall():
                state = json.loads(row[field])
                if state.get('retentionExpired'):
                    continue
                completed = row['completed_at'] if table == 'course_attempts' else None
                # A submitted exam defines completion even if training reached 100% earlier.
                origin = state.get('submittedAt') or completed or row['updated_at']
                try:
                    expired = at >= expires_at(origin)
                except (ValueError, TypeError, AttributeError):
                    counts['invalid_dates'] += 1
                    continue
                if not expired:
                    continue
                key = (row[uid], row[cid], row[attempt])
                files = con.execute(f'SELECT id,extension FROM {evidence} WHERE {uid}=? AND {cid}=? AND {attempt}=?', key).fetchall() if evidence in tables else []
                for f in files:
                    safe_evidence_path(root, folder, f['id'], f['extension'])
                counts['attempts'] += 1
                counts['screenshots'] += len(files)
                if not apply:
                    continue
                summary_field = 'summary_json' if table == 'course_attempts' else 'summary'
                packed = compact(state, at.isoformat(), json.loads(row[summary_field]))
                con.execute(f'UPDATE {table} SET {field}=? WHERE {uid}=? AND {cid}=? AND {attempt}=?', (packed, *key))
                if table == 'course_attempts' and 'progress' in tables:
                    con.execute('UPDATE progress SET state_json=? WHERE user_id=? AND course_id=? AND attempt_no=?', (packed, *key))
                for f in files:
                    con.execute('INSERT OR IGNORE INTO retention_files VALUES(?,?,?)', (folder, f['id'], f['extension']))
                if evidence in tables:
                    con.execute(f'DELETE FROM {evidence} WHERE {uid}=? AND {cid}=? AND {attempt}=?', key)
            # Current progress can contain denormalized copies; scrubbed in the same transaction.
        if apply:
            con.commit()
            for f in con.execute('SELECT * FROM retention_files').fetchall():
                try:
                    path = safe_evidence_path(root, f['folder'], f['id'], f['extension'])
                    if path.is_symlink():
                        raise ValueError('Evidence file must not be a symlink')
                    path.unlink(missing_ok=True)
                    con.execute('DELETE FROM retention_files WHERE folder=? AND id=?', (f['folder'], f['id']))
                    con.commit()
                except (OSError, ValueError):
                    counts['files_pending'] += 1
    return counts


def register(app, db_path):
    """Reapply on recovery before serving traffic and every six hours thereafter."""
    async def startup():
        if os.environ.get('WORKBOOK_RETENTION_ENABLED', '1') != '1':
            return
        app.state.retention_status = await asyncio.to_thread(sweep, db_path, Path(db_path).parent, apply=True)
        async def periodic():
            while True:
                await asyncio.sleep(6 * 3600)
                try:
                    app.state.retention_status = await asyncio.to_thread(sweep, db_path, Path(db_path).parent, apply=True)
                except Exception as exc:
                    # No credentials, names or attempt contents in operational logs.
                    app.state.retention_status = {'error': type(exc).__name__}
        app.state.retention_task = asyncio.create_task(periodic())

    async def shutdown():
        task = getattr(app.state, 'retention_task', None)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    app.add_event_handler('startup', startup)
    app.add_event_handler('shutdown', shutdown)


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--database', required=True)
    p.add_argument('--data-dir', required=True)
    p.add_argument('--apply', action='store_true')
    args = p.parse_args()
    print(json.dumps(sweep(args.database, args.data_dir, apply=args.apply)))
