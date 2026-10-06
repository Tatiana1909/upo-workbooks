"""Server-only fire credentials, provisioned with the server's public key.

Only a trusted deployment can install the encrypted envelope. There is no
public credential update endpoint. Plaintext and password verifiers never
leave the persistent server data directory.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


class FireAccess:
    def __init__(self, data_dir):
        self.directory = Path(data_dir)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.key_path = self.directory / 'fire-access-private.pem'
        self.config_path = self.directory / 'fire-access.json'
        if not self.key_path.exists():
            key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
            self._write(self.key_path, key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption()))
        self.key = serialization.load_pem_private_key(self.key_path.read_bytes(), password=None)
        self.config = json.loads(self.config_path.read_text()) if self.config_path.exists() else None

    @staticmethod
    def _write(path, value):
        temp = path.with_suffix(path.suffix + '.tmp')
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'wb') as output:
            output.write(value)
        os.replace(temp, path)

    def public_key(self):
        return self.key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()

    @staticmethod
    def _hash(value, salt):
        return hashlib.scrypt(value.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()

    @classmethod
    def verifier(cls, value):
        salt = secrets.token_hex(16)
        return {'salt': salt, 'hash': cls._hash(value, salt)}

    def matches(self, field, value):
        if not self.config:
            return False
        verifier = self.config[field]
        return hmac.compare_digest(self._hash(value, verifier['salt']), verifier['hash'])

    def install(self, envelope_path):
        path = Path(envelope_path)
        if not path.exists():
            return
        ciphertext = base64.b64decode(path.read_text().strip(), validate=True)
        revision = hashlib.sha256(ciphertext).hexdigest()
        if self.config and self.config['revision'] == revision:
            return
        raw = self.key.decrypt(ciphertext, padding.OAEP(
            mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(),
            label=b'ifcm-fire-access-v1'))
        data = json.loads(raw)
        for field in ('code', 'login', 'password'):
            if not isinstance(data.get(field), str) or not 1 <= len(data[field]) <= 200:
                raise ValueError('Invalid fire access configuration')
        self.config = {
            'revision': revision, 'login': data['login'],
            'code': self.verifier(data['code']), 'password': self.verifier(data['password']),
        }
        self._write(self.config_path, json.dumps(self.config).encode())
