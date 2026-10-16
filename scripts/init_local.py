"""Generate ignored local development configuration; never print secrets or enroll an owner."""

import os
from pathlib import Path
import secrets
from cryptography.fernet import Fernet


def main():
    path = Path(".env")
    if path.exists():
        raise SystemExit("Refusing to overwrite .env; preserve existing credentials")
    values = dict(line.split("=", 1) for line in Path(".env.example").read_text().splitlines() if "=" in line)
    values.update(
        APP_ENV="development",
        PUBLIC_URL="http://127.0.0.1:8000",
        POSTGRES_PASSWORD=secrets.token_hex(32),
        SESSION_SECRET=secrets.token_urlsafe(48),
        ENCRYPTION_KEYS=Fernet.generate_key().decode(),
        BACKUP_ENCRYPTION_KEY=Fernet.generate_key().decode(),
    )
    with open(path, "x") as f:
        os.chmod(path, 0o600)
        f.write("".join(f"{key}={value}\n" for key, value in values.items()))
    print("Created .env with local secrets. Configure explicit owner identity and OAuth before starting.")


if __name__ == "__main__":
    main()
