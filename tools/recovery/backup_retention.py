"""Prune verified scheduled recovery points after a newer verified point exists."""
import json
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path


def prune(root, *, at=None, apply=False):
    root = Path(root)
    if root.is_symlink():
        raise ValueError('Backup root must not be a symlink')
    at = at or datetime.now(timezone.utc)
    points = []
    for p in root.iterdir():
        if p.is_symlink() or not p.is_dir() or not re.fullmatch(r'\d{8}T\d{12}Z', p.name):
            continue
        try:
            verified = json.loads((p / 'verified.json').read_text())
            if verified.get('database_and_files_verified') is not True or verified.get('isolated_api_restore') is not True:
                continue
            stamp = datetime.strptime(p.name, '%Y%m%dT%H%M%S%fZ').replace(tzinfo=timezone.utc)
            if any(x.is_symlink() for x in p.rglob('*')):
                continue
            points.append((stamp, p))
        except (ValueError, OSError):
            continue
    points.sort()
    if not points:
        return {'eligible': 0, 'removed': 0}
    latest = points[-1][1]
    old = [p for stamp, p in points if p != latest and stamp <= at - timedelta(days=14)]
    if apply:
        for p in old:
            shutil.rmtree(p)
    return {'eligible': len(old), 'removed': len(old) if apply else 0}
