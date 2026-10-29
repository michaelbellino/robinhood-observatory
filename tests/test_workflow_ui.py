"""Run browser-independent DOM/history regressions in normal Python CI."""
import shutil
import subprocess

import pytest


@pytest.mark.parametrize("suite", ["manual_handoff_ui.mjs", "owner_login_ui.mjs"])
def test_workflow_ui(suite):
    if not shutil.which("node"):
        pytest.skip("Node is required for UI regression checks")
    subprocess.run(["node", f"tests/{suite}"], check=True, capture_output=True, text=True, timeout=30)
