"""Conservative source scan. Not a substitute for repository secret scanning."""

from pathlib import Path
import re
import sys

root = Path(__file__).resolve().parents[1]
patterns = [
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    r"sk-(?:proj-)?[A-Za-z0-9_-]{30,}",
    r"gh[pousr]_[A-Za-z0-9]{30,}",
    r"postgres(?:ql)?://[^\s:]+:[^\s${}]+@[^\s]+",
]
excluded = {
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "private",
    "backups",
    "dist",
    "node_modules",
}
failures = []
for file in root.rglob("*"):
    if (
        not file.is_file()
        or excluded.intersection(file.relative_to(root).parts)
        or file.suffix in {".png", ".zip", ".pyc", ".db"}
    ):
        continue
    data = file.read_text(errors="ignore")
    if any(re.search(pattern, data) for pattern in patterns):
        # Fixture-only PostgreSQL password is not a credential, restricted to CI tests.
        if file.name != "verify.yml":
            failures.append(str(file.relative_to(root)))
print("Source scan: " + ("PASS" if not failures else "FAILED in " + ", ".join(failures)))
sys.exit(bool(failures))
