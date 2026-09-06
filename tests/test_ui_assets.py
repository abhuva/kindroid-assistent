from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


def test_reusable_browser_api_client_behaviors() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is not installed")
    repository = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [node, str(repository / "tests" / "ui_api_client_test.cjs")],
        cwd=repository,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_dashboard_dom_behaviors_and_accessibility() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is not installed; run npm ci to enable UI verification")
    repository = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [node, "--test", "--test-timeout=15000", "tests/ui_dashboard_test.cjs"],
        cwd=repository, capture_output=True, text=True, encoding="utf-8",
        timeout=20, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
