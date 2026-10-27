import io
import json
import os
import sqlite3
import sys
import tarfile
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backup_bundle import create, verify, restore


@pytest.fixture
def sample(tmp_path):
    data=tmp_path/'data';data.mkdir()
    with sqlite3.connect(data/'workbooks.sqlite3') as c:
        c.execute('CREATE TABLE answers(id INTEGER PRIMARY KEY,value TEXT)')
        c.execute('INSERT INTO answers VALUES(1,?)',('Сохранённый ответ',))
    (data/'images').mkdir();(data/'images/a.png').write_bytes(b'fixture-image')
    source=tmp_path/'source';source.mkdir();(source/'app.py').write_text('print("fixture")')
    return data,source


def test_complete_restore_and_private_modes(sample,tmp_path,monkeypatch):
    data,source=sample;monkeypatch.setenv('WORKBOOK_TOKEN_SECRET','fixture-only-secret')
    bundle=tmp_path/'backup.tgz';create(data,bundle,source)
    assert verify(bundle)
    target=restore(bundle,tmp_path/'restored')
    assert (target/'data/images/a.png').read_bytes()==b'fixture-image'
    assert (target/'source/app.py').read_text()=='print("fixture")'
    assert json.loads((target/'settings/environment.json').read_text())['WORKBOOK_TOKEN_SECRET']=='fixture-only-secret'
    with sqlite3.connect(target/'data/workbooks.sqlite3') as c:assert c.execute('SELECT value FROM answers').fetchone()[0]=='Сохранённый ответ'
    assert bundle.stat().st_mode&0o777==0o600
    assert target.stat().st_mode&0o777==0o700
    assert (target/'settings/environment.json').stat().st_mode&0o777==0o600
    with pytest.raises(FileExistsError):restore(bundle,target)
    with pytest.raises(ValueError):create(data,bundle,source)


def rewrite(bundle,callback):
    with tarfile.open(bundle,'r:gz') as t:entries=[(m,t.extractfile(m).read()) for m in t.getmembers()]
    with tarfile.open(bundle,'w:gz') as t:
        for m,raw in callback(entries):
            if m.isfile():m.size=len(raw);t.addfile(m,io.BytesIO(raw))
            else:t.addfile(m)


def test_corruption_detected_before_destination_created(sample,tmp_path):
    data,source=sample;bundle=tmp_path/'backup.tgz';create(data,bundle,source)
    rewrite(bundle,lambda rows:[(m,b'changed' if m.name=='data/images/a.png' else raw) for m,raw in rows])
    with pytest.raises(ValueError):restore(bundle,tmp_path/'restored')
    assert not (tmp_path/'restored').exists()


@pytest.mark.parametrize('kind',['traversal','absolute','symlink','duplicate','extra'])
def test_unsafe_archives_rejected(sample,tmp_path,kind):
    data,source=sample;bundle=tmp_path/'backup.tgz';create(data,bundle,source)
    def modify(rows):
        if kind=='duplicate':return rows+[rows[0]]
        name={'traversal':'data/../../outside','absolute':'/tmp/should-not-write','symlink':'data/link','extra':'data/extra'}[kind]
        m=tarfile.TarInfo(name)
        if kind=='symlink':m.type=tarfile.SYMTYPE;m.linkname='/etc/passwd'
        return rows+[(m,b'x')]
    rewrite(bundle,modify)
    with pytest.raises(ValueError):verify(bundle)


def test_missing_database_and_source_symlink_are_not_silently_backed_up(tmp_path,sample):
    with pytest.raises(ValueError):create(tmp_path/'missing',tmp_path/'backup.tgz')
    assert not (tmp_path/'missing').exists()
    data,source=sample;(data/'link').symlink_to(source/'app.py')
    with pytest.raises(ValueError):create(data,tmp_path/'backup.tgz',source)
    assert not (tmp_path/'backup.tgz').exists()
    assert not list(tmp_path.glob('.bundle-*'))


def test_configured_database_name_is_used(sample,tmp_path,monkeypatch):
    data,source=sample
    actual=data/'custom.sqlite3';(data/'workbooks.sqlite3').rename(actual)
    monkeypatch.setenv('WORKBOOK_DB_PATH',str(actual))
    bundle=tmp_path/'backup.tgz';create(data,bundle,source)
    target=restore(bundle,tmp_path/'restored')
    with sqlite3.connect(target/'data/workbooks.sqlite3') as c:
        assert c.execute('SELECT value FROM answers').fetchone()[0]=='Сохранённый ответ'
    assert not (target/'data/custom.sqlite3').exists()
