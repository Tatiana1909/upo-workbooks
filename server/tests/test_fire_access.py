import base64
import json
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from test_builder import service


def provision(m, tmp_path, password='pb-test-password'):
    key = serialization.load_pem_public_key(m.fire_access.public_key().encode())
    raw = json.dumps({'code': 'pb-test-code', 'login': 'pb-test-admin', 'password': password}).encode()
    envelope = tmp_path / 'access.enc'
    envelope.write_text(base64.b64encode(key.encrypt(raw, padding.OAEP(
        mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(),
        label=b'ifcm-fire-access-v1'))).decode())
    m.fire_access.install(envelope)
    return envelope


def bearer(response):
    assert response.status_code == 200, response.text
    return {'Authorization': 'Bearer ' + response.json()['token']}


def test_separate_access_and_preserved_progress(service, tmp_path):
    c, m, warehouse_admin = service
    old = bearer(c.post('/api/v1/session', json={
        'code': 'test-code', 'full_name': 'Иванова Анна', 'course_id': m.fire.CID}))
    assert c.put('/api/v1/progress/' + m.fire.CID, headers=old,
                 json={'state': {'notes': {'m1': 'Сохранённая заметка'}}, 'summary': {}}).status_code == 200
    provision(m, tmp_path)
    participant = {'code': 'pb-test-code', 'full_name': 'Иванова Анна'}
    student = bearer(c.post('/api/v1/fire/session', json=participant))
    teacher = bearer(c.post('/api/v1/fire/admin/session', json={
        'login': 'pb-test-admin', 'password': 'pb-test-password'}))
    assert c.get('/api/v1/progress/' + m.fire.CID, headers=student).json()['state']['notes']['m1'] == 'Сохранённая заметка'
    assert c.get('/api/v1/fire/results', headers=teacher).status_code == 200
    assert c.put('/api/v1/fire/settings', headers=teacher, json={'address': 'Игарка'}).status_code == 200
    # Old shared sessions and credentials no longer grant fire access.
    assert c.get('/api/v1/progress/' + m.fire.CID, headers=old).status_code == 403
    assert c.get('/api/v1/fire/results', headers=warehouse_admin).status_code == 403
    assert c.post('/api/v1/fire/session', json={**participant, 'code': 'test-code'}).status_code == 401
    assert c.post('/api/v1/session', json={**participant, 'code': 'test-code', 'course_id': m.fire.CID}).status_code == 401
    assert c.post('/api/v1/fire/admin/session', json={'login': 'test-author', 'password': 'test-password'}).status_code == 401
    # Fire credentials cannot be used through shared auth endpoints or other course APIs.
    assert c.post('/api/v1/session', json=participant).status_code == 401
    assert c.post('/api/v1/admin/session', json={'login': 'pb-test-admin', 'password': 'pb-test-password'}).status_code == 401
    for path in ('/api/v1/admin/dashboard', '/api/v1/admin/results.csv', '/api/v1/builder/drafts'):
        assert c.get(path, headers=teacher).status_code == 403
    assert c.get('/api/v1/progress/accounting', headers=student).status_code == 403
    assert c.post('/api/v1/progress/warehouse/restart', headers=student).status_code == 403
    assert c.get('/api/v1/fire/results', headers=student).status_code == 403
    # Warehouse login and results stay available with their original credentials.
    assert c.post('/api/v1/session', json={**participant, 'code': 'test-code', 'course_id': 'accounting'}).status_code == 200
    assert c.get('/api/v1/admin/dashboard', headers=warehouse_admin).status_code == 200


def test_encrypted_provisioning_and_rotation(service, tmp_path):
    c, m, admin = service
    envelope = provision(m, tmp_path)
    config = m.fire_access.config_path.read_text()
    assert 'pb-test-password' not in config and 'pb-test-code' not in config
    assert m.fire_access.key_path.stat().st_mode & 0o777 == 0o600
    assert m.fire_access.config_path.stat().st_mode & 0o777 == 0o600
    revision = m.fire_access.config['revision']
    m.fire_access.install(envelope)
    assert m.fire_access.config['revision'] == revision
    teacher = bearer(c.post('/api/v1/fire/admin/session', json={
        'login': 'pb-test-admin', 'password': 'pb-test-password'}))
    public = c.get('/api/v1/fire/access-public-key').json()['public_key']
    assert public == m.fire_access.public_key() and 'PRIVATE' not in public
    provision(m, tmp_path, 'pb-new-test-password')
    assert c.get('/api/v1/fire/results', headers=teacher).status_code == 401
    assert c.post('/api/v1/fire/admin/session', json={'login': 'pb-test-admin', 'password': 'pb-test-password'}).status_code == 401
    teacher = bearer(c.post('/api/v1/fire/admin/session', json={
        'login': 'pb-test-admin', 'password': 'pb-new-test-password'}))
    assert c.get('/api/v1/fire/results', headers=teacher).status_code == 200
