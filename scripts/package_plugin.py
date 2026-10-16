"""Reproducibly package the public skill-only ChatGPT plugin, never private state."""

import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "plugins" / "stock-observatory"
FILES = (
    "plugin.json",
    "skills/observe-brokerage/SKILL.md",
    "skills/observe-brokerage/agents/openai.yaml",
)


def main():
    manifest = json.loads((SOURCE / "plugin.json").read_text())
    assert manifest["name"] == SOURCE.name
    assert manifest["$schema"] == "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
    assert len(manifest["extensions"]["com.openai"]["interface"]["shortDescription"]) <= 30
    output = ROOT / "dist" / f"stock-observatory-{manifest['version']}.zip"
    output.parent.mkdir(exist_ok=True)
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for name in (*FILES, "LICENSE"):
            source = ROOT / name if name == "LICENSE" else SOURCE / name
            assert source.is_file() and not source.is_symlink()
            data = source.read_bytes()
            assert b"TODO" not in data
            entry = ZipInfo(f"stock-observatory/{name}", (2026, 10, 16, 0, 0, 0))
            entry.create_system = 3
            entry.external_attr = 0o100644 << 16
            entry.compress_type = ZIP_DEFLATED
            archive.writestr(entry, data)
    with ZipFile(output) as archive:
        assert archive.testzip() is None
        assert len(archive.namelist()) == len(FILES) + 1
    print(json.dumps({"path": str(output), "sha256": hashlib.sha256(output.read_bytes()).hexdigest()}))


if __name__ == "__main__":
    main()
