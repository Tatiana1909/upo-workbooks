"""Offline recovery for an administrator, run by the server operator only."""
import argparse
import os
import secrets
import sqlite3
from datetime import datetime, timezone
from accounts import hash_password


def main():
    parser=argparse.ArgumentParser(description='Сброс пароля администратора через локальный доступ к серверу')
    parser.add_argument('--login',required=True)
    parser.add_argument('--db',default=os.environ.get('WORKBOOK_DB_PATH','/data/workbooks.sqlite3'))
    args=parser.parse_args()
    temporary=secrets.token_urlsafe(14)
    now=datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(args.db) as c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute('SELECT id,roles FROM identity_accounts WHERE login=? COLLATE NOCASE',(args.login,)).fetchone()
        if not row or 'admin' not in __import__('json').loads(row[1]):
            raise SystemExit('Аккаунт администратора не найден')
        c.execute("UPDATE identity_accounts SET password_hash=?,must_change=1,status='active',revision=revision+1,updated_at=? WHERE id=?",(hash_password(temporary),now,row[0]))
        c.execute('UPDATE identity_sessions SET active=0 WHERE account_id=?',(row[0],))
        c.execute("INSERT INTO identity_audit(at,actor,subject,action,details) VALUES(?,NULL,?,'offline_admin_recovery','{}')",(now,row[0]))
    print('Временный пароль показан однократно: '+temporary)
    print('При входе администратор должен установить собственный пароль.')

if __name__=='__main__':main()
