"""Private, checksummed SQLite/files/config bundles and non-destructive restoration.
Archives contain credentials and personal data. Keep them on protected storage.
"""
import argparse
import hashlib
import json
import os
import sqlite3
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


def digest_stream(stream):
    digest = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
        digest.update(chunk)
    return digest.hexdigest()


def quoted(name):
    return '"' + name.replace('"', '""') + '"'


def database_counts(path):
    with sqlite3.connect(path) as con:
        if con.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Database integrity check failed')
        return {r[0]: con.execute('SELECT COUNT(*) FROM ' + quoted(r[0])).fetchone()[0]
                for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def safe_member(name):
    path = PurePosixPath(name)
    if path.is_absolute() or any(p in ('..', '') for p in path.parts) or '\\' in name:
        raise ValueError('Unsafe archive path')
    if name != 'manifest.json' and (not path.parts or path.parts[0] not in ('data', 'source', 'settings')):
        raise ValueError('Unexpected archive section')
    return path


def create(data_dir, output, source_dir=None):
    data_dir, output = Path(data_dir).resolve(), Path(output).resolve()
    database = data_dir / 'workbooks.sqlite3'
    configured = os.environ.get('WORKBOOK_DB_PATH')
    if configured and Path(configured).resolve().parent == data_dir:
        database = Path(configured).resolve()
    if not database.is_file():
        raise ValueError('Source database does not exist')
    if output.is_relative_to(data_dir) or (source_dir and output.is_relative_to(Path(source_dir).resolve())):
        raise ValueError('Backup output must be outside source directories')
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if output.exists():
        raise ValueError('Backup already exists; refusing to overwrite')
    descriptor, partial_name = tempfile.mkstemp(prefix='.bundle-', dir=output.parent)
    os.close(descriptor)
    partial = Path(partial_name)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = Path(tmp) / 'workbooks.sqlite3'
            with sqlite3.connect(database) as src, sqlite3.connect(snapshot) as dst:
                src.backup(dst)
            tables = database_counts(snapshot)
            hashes = {}
            with tarfile.open(partial, 'w:gz') as archive:
                def add(path, name):
                    if path.is_symlink():
                        raise ValueError('Symlinks are not supported in application bundles')
                    if path.is_file():
                        safe_member(name)
                        with path.open('rb') as f:
                            hashes[name] = digest_stream(f)
                        archive.add(path, arcname=name, recursive=False)
                add(snapshot, 'data/workbooks.sqlite3')
                for path in sorted(data_dir.rglob('*')):
                    rel = path.relative_to(data_dir)
                    if rel.parts[0] in ('backups', 'builder-backups', 'full-backups') or path.name in (database.name, database.name+'-wal', database.name+'-shm'):
                        continue
                    add(path, 'data/' + rel.as_posix())
                if source_dir:
                    source_dir = Path(source_dir).resolve()
                    for path in sorted(source_dir.rglob('*')):
                        if any(p in ('.git', '__pycache__', 'backups') for p in path.relative_to(source_dir).parts):
                            continue
                        add(path, 'source/' + path.relative_to(source_dir).as_posix())
                    environment = Path(tmp) / 'environment.json'
                    environment.write_text(json.dumps({k: v for k, v in os.environ.items() if k.startswith('WORKBOOK_')}))
                    add(environment, 'settings/environment.json')
                manifest = Path(tmp) / 'manifest.json'
                manifest.write_text(json.dumps({'created_at': datetime.now(timezone.utc).isoformat(), 'tables': tables, 'sha256': hashes}, indent=2))
                archive.add(manifest, arcname='manifest.json')
        verify(partial)
        # Another writer must never silently replace a recovery point.
        os.link(partial, output)
        output.chmod(0o600)
        return {'verified': True, 'files': len(hashes), 'tables': len(tables), 'path': str(output)}
    finally:
        partial.unlink(missing_ok=True)


def inspect_bundle(archive):
    members = archive.getmembers()
    names = [m.name for m in members]
    if len(names) != len(set(names)) or 'manifest.json' not in names:
        raise ValueError('Duplicate archive entries or missing manifest')
    for member in members:
        safe_member(member.name)
        if not member.isfile():
            raise ValueError('Archive entries must be regular files')
    manifest = json.load(archive.extractfile('manifest.json'))
    hashes = manifest.get('sha256')
    if not isinstance(hashes, dict) or set(names) != set(hashes) | {'manifest.json'} or 'data/workbooks.sqlite3' not in hashes:
        raise ValueError('Manifest does not cover the archive')
    for name, digest in hashes.items():
        if digest_stream(archive.extractfile(name)) != digest:
            raise ValueError('Archive checksum mismatch')
    return manifest


def verify(bundle):
    with tarfile.open(bundle, 'r:gz') as archive:
        manifest = inspect_bundle(archive)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'restore.sqlite3'
            with path.open('wb') as out:
                import shutil
                shutil.copyfileobj(archive.extractfile('data/workbooks.sqlite3'), out)
            if database_counts(path) != manifest['tables']:
                raise ValueError('Restored database row counts do not match')
    return True


def restore(bundle, destination):
    """Extract only verified regular files into a NEW directory, never live data."""
    destination = Path(destination)
    verify(bundle)
    # Existing paths (including symlinks or empty dirs) are refused deliberately.
    destination.mkdir(mode=0o700, parents=False, exist_ok=False)
    try:
        with tarfile.open(bundle, 'r:gz') as archive:
            manifest = inspect_bundle(archive)
            for name in manifest['sha256']:
                target = destination.joinpath(*safe_member(name).parts)
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                with target.open('xb') as out:
                    import shutil
                    shutil.copyfileobj(archive.extractfile(name), out)
                target.chmod(0o600)
        return destination
    except Exception:
        import shutil
        shutil.rmtree(destination)
        raise


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--data-dir'); p.add_argument('--output'); p.add_argument('--source-dir')
    p.add_argument('--verify'); p.add_argument('--restore'); p.add_argument('--destination')
    a = p.parse_args()
    if a.verify:
        verify(a.verify); print(json.dumps({'verified': True}))
    elif a.restore and a.destination:
        restore(a.restore, a.destination); print(json.dumps({'restored_to_new_directory': True}))
    elif a.data_dir and a.output:
        print(json.dumps(create(a.data_dir, a.output, a.source_dir)))
    else:
        p.error('Provide create arguments, --verify, or --restore with --destination')
