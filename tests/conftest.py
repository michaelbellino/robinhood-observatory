"""Synthetic fixtures only. No user financial records or credentials."""

import secrets
from datetime import timedelta
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from observatory.db import Database, State, now
from observatory.settings import Settings
from observatory.security import Vault, digest
from observatory.api import create_app


@pytest.fixture
def settings(tmp_path):
    return Settings(
        database_url=f"sqlite:///{tmp_path}/test.db",
        environment="test",
        encryption_keys=Fernet.generate_key().decode(),
        session_secret=secrets.token_urlsafe(40),
        owner_subject="synthetic-owner",
        read_token_hash=digest("fixture-read-only"),
        control_token_hash=digest("fixture-control-only"),
    )


@pytest.fixture
def db(settings):
    result = Database(settings.database_url)
    result.initialize_test()
    return result


@pytest.fixture
def vault(settings):
    return Vault(settings.encryption_keys)


@pytest.fixture
def configured(db, vault):
    with db.session() as s:
        row = s.get(State, 1)
        row.account_cipher = vault.encrypt("SYNTHETIC-ACCOUNT")
        row.account_label = "Synthetic test account"
        row.account_verified, row.paused = True, False
        vault.put(
            s,
            "robinhood_tokens",
            {
                "access_token": "synthetic-test-token",
                "token_type": "Bearer",
                "expires_at": (now() + timedelta(hours=1)).isoformat(),
            },
        )
    return db


@pytest.fixture
def client(db, settings):
    with TestClient(create_app(settings, db), base_url="http://127.0.0.1:8000") as c:
        yield c


@pytest.fixture
def read_headers():
    return {"Authorization": "Bearer fixture-read-only"}


@pytest.fixture
def control_headers():
    return {"Authorization": "Bearer fixture-control-only"}
