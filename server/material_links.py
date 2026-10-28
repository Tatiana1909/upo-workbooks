"""A content-only link release; published snapshots and student records are immutable."""
import argparse
import copy
import json
import re
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

RELEASE = 'material-links-20261028'
LINKS = json.loads(Path(__file__).with_name(RELEASE + '.json').read_text())
assert all(re.fullmatch(r'https://disk\.360\.yandex\.ru/i/[A-Za-z0-9_-]+', m['url'])
           for source in ('accounting', 'warehouse') for m in LINKS[source].values())


def replace_links(content, source):
    """Copy content and change URL fields only. Preserve teacher edits and block IDs."""
    result = copy.deepcopy(content)
    changed = 0
    if source in ('accounting', 'warehouse'):
        for section in result.get('sections', []):
            for block in section.get('blocks', []):
                material = LINKS[source].get(block.get('id'))
                if not material or block.get('type') != 'link':
                    continue
                if block.get('materialType') != material['materialType']:
                    raise ValueError('Material type changed: ' + block['id'])
                if block.get('url') != material['url'] or block.get('download'):
                    block['url'] = material['url']
                    # The old download route points at the old folder, not this document.
                    block.pop('download', None)
                    changed += 1
    else:
        def walk(value):
            nonlocal changed
            if isinstance(value, dict):
                if (value.get('url') in ('assets/instruction.docx', '/v4/fire/assets/instruction.docx')
                    or value.get('title') == 'Открыть и изучить инструкцию по ПБ'):
                    if value.get('url') != LINKS['fire']['url']:
                        value['url'] = LINKS['fire']['url']
                        changed += 1
                for child in value.values():
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)
        walk(result)
    return result, changed


def release(db_path, apply=False, *, release_id=RELEASE, transform=replace_links,
            sources=('accounting', 'warehouse', 'fire'), change_kind='links'):
    """Atomic/idempotent publication from current editions, never from unsaved draft edits."""
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        con.execute('BEGIN IMMEDIATE')
        con.execute('CREATE TABLE IF NOT EXISTS content_link_releases(id TEXT PRIMARY KEY, report TEXT NOT NULL)')
        done = con.execute('SELECT report FROM content_link_releases WHERE id=?', (release_id,)).fetchone()
        if done:
            return {**json.loads(done['report']), 'already_applied': True}
        # Verify every pre-existing result, published snapshot, review and file record
        # inside the same transaction. New rows are allowed; old rows cannot change.
        protected = {}
        for (table,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
            if table not in ('builder_drafts', 'fb_drafts', 'identity_accounts', 'identity_invites', 'content_link_releases'):
                quoted = '"' + table.replace('"', '""') + '"'
                protected[quoted] = [tuple(r) for r in con.execute('SELECT rowid,* FROM ' + quoted)]
        timestamp = datetime.now(timezone.utc).isoformat()
        report = {'release': release_id, 'applied': apply, 'editions': []}
        transitions = []
        for source in sources:
            fire = source == 'fire'
            draft_table = 'fb_drafts' if fire else 'builder_drafts'
            edition_table = 'fb_editions' if fire else 'builder_editions'
            id_key = 'id' if fire else 'course_id'
            if fire:
                did = 'fire-original'
            else:
                mapping = con.execute('SELECT draft_id FROM builder_imports WHERE source_course=?', (source,)).fetchone()
                if not mapping:
                    raise ValueError('Imported workbook not found: ' + source)
                did = mapping['draft_id']
            draft = con.execute(f'SELECT * FROM {draft_table} WHERE id=?', (did,)).fetchone()
            if not draft or (not fire and draft['deleted_at']):
                raise ValueError('Draft missing or deleted: ' + source)
            old = con.execute(f'SELECT * FROM {edition_table} WHERE draft_id=? ORDER BY version DESC LIMIT 1', (did,)).fetchone()
            if not old or (not fire and (old['deleted_at'] or old['archived'])):
                raise ValueError('Latest published edition is missing or hidden: ' + source)
            # Preserve unpublished teaching changes in the draft, but do not publish them.
            draft_content, draft_count = transform(json.loads(draft['content']), source)
            published, count = transform(json.loads(old['content']), source)
            if draft_count:
                con.execute(f'UPDATE {draft_table} SET content=?,revision=revision+1,updated_at=? WHERE id=?',
                            (json.dumps(draft_content, ensure_ascii=False), timestamp, did))
            cid, version = old[id_key], old['version']
            if count:
                version = con.execute(f'SELECT MAX(version)+1 FROM {edition_table} WHERE draft_id=?', (did,)).fetchone()[0]
                cid = 'pb-' + secrets.token_hex(12) if fire else f'cb-{did}-v{version}'
                packed = json.dumps(published, ensure_ascii=False)
                if fire:
                    con.execute('INSERT INTO fb_editions VALUES(?,?,?,?,?)', (cid, did, version, packed, timestamp))
                else:
                    con.execute('INSERT INTO builder_editions(course_id,draft_id,version,title,content,published_at) VALUES(?,?,?,?,?,?)',
                                (cid, did, version, published['title'], packed, timestamp))
                transitions.append((old[id_key], cid))
            report['editions'].append(dict(source=source, course_id=cid, previous_id=old[id_key], version=version,
                **{change_kind+'_changed':count, 'draft_'+change_kind+'_changed':draft_count},
                url=('/v4/fire/editable.html?edition=' if fire else '/v4/builder/workbook.html?course=') + cid))
        # Give the new edition to the same explicitly assigned audience. Keep old access.
        # No other course, realm, role, password or session is changed.
        assignments = 0
        for table in ('identity_accounts', 'identity_invites'):
            if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                continue
            for row in con.execute(f'SELECT id,courses FROM {table}').fetchall():
                courses = json.loads(row['courses'])
                added = [new for old, new in transitions if old in courses and new not in courses]
                if added:
                    con.execute(f'UPDATE {table} SET courses=? WHERE id=?', (json.dumps(courses + added), row['id']))
                    con.execute('INSERT INTO identity_audit(at,actor,subject,action,details) VALUES(?,?,?,?,?)',
                                (timestamp, None, row['id'], 'material_link_edition_assigned' if change_kind=='links' else 'content_edition_assigned', json.dumps({'release': release_id, 'table': table, 'added': added})))
                    assignments += 1
        verified = 0
        for table, old_rows in protected.items():
            for old_row in old_rows:
                current = con.execute('SELECT * FROM ' + table + ' WHERE rowid=?', (old_row[0],)).fetchone()
                assert current is not None and tuple(current) == old_row[1:], 'Existing row changed: ' + table
                verified += 1
        report['existing_results_and_editions_unchanged'] = True
        report['protected_rows_verified'] = verified
        report['assignments_updated'] = assignments
        con.execute('INSERT INTO content_link_releases VALUES(?,?)', (release_id, json.dumps(report, ensure_ascii=False)))
        assert con.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        if apply:
            con.commit()
        else:
            con.rollback()
        return report
    finally:
        con.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    import app
    print(json.dumps(release(app.DB_PATH, args.apply), ensure_ascii=False))
