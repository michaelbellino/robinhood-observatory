import hashlib
import hmac
import json
from contextvars import ContextVar
from cryptography.fernet import Fernet, MultiFernet
from fastapi import HTTPException
from .db import Secret, now

principal = ContextVar("principal", default=None)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class Vault:
    def __init__(self, keys):
        self.fernet = MultiFernet([Fernet(k.encode()) for k in keys.split(",") if k])

    def encrypt(self, obj):
        return self.fernet.encrypt(json.dumps(obj).encode()).decode()

    def decrypt(self, ciphertext):
        return json.loads(self.fernet.decrypt(ciphertext.encode()))

    def put(self, session, name, obj):
        row = session.get(Secret, name)
        if row is None:
            row = Secret(name=name, cipher=self.encrypt(obj))
            session.add(row)
        else:
            row.cipher = self.encrypt(obj)
            row.updated_at = now()

    def get(self, session, name):
        row = session.get(Secret, name)
        return self.decrypt(row.cipher) if row else None


def bearer_principal(header, settings):
    if not header or not header.startswith("Bearer "):
        return None
    candidate = digest(header[7:])
    for level, expected in [("control", settings.control_token_hash), ("read", settings.read_token_hash)]:
        if expected and len(expected) == 64 and hmac.compare_digest(candidate, expected):
            return {"sub": settings.owner_subject, "scope": level, "method": "bearer"}
    return None


def require_control():
    actor = principal.get()
    if not actor or actor["scope"] != "control":
        raise HTTPException(403, "Monitor-control authorization required")
    return actor
