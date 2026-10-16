import os
from sqlalchemy import select
from observatory.db import Database, Secret, State, OAuthFlow
from observatory.security import Vault

db = Database(os.environ["DATABASE_URL"])
vault = Vault(os.environ["ENCRYPTION_KEYS"])
with db.session() as s:
    state = s.scalar(select(State).where(State.id == 1).with_for_update())
    if not state.paused:
        raise SystemExit("Pause monitoring before encryption-key rotation")
    for row in s.scalars(select(Secret)):
        row.cipher = vault.fernet.rotate(row.cipher.encode()).decode()
    for row in s.scalars(select(OAuthFlow)):
        row.payload_cipher = vault.fernet.rotate(row.payload_cipher.encode()).decode()
    if state.account_cipher:
        state.account_cipher = vault.fernet.rotate(state.account_cipher.encode()).decode()
print("Encrypted records rotated; retain old keys for older backups")
