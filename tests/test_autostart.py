from __future__ import annotations

import os
import runpy
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest


@pytest.mark.parametrize("result", [0, 1, 3])
def test_background_launcher_uses_repo_streams_and_propagates_exit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, result: int
) -> None:
    repository = Path(__file__).resolve().parents[1]
    namespace = runpy.run_path(str(repository / "scripts" / "dashboard-autostart.pyw"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RELAY_WEB_OPEN_BROWSER", "true")
    for name in ("stdin", "stdout", "stderr"):
        monkeypatch.setattr(sys, name, None)
    fake_cli = ModuleType("kindroid_relay.cli")

    def fake_main(args: list[str]) -> int:
        assert args == ["serve"]
        assert Path.cwd() == repository
        assert os.environ["RELAY_WEB_OPEN_BROWSER"] == "false"
        assert sys.stdin.read() == ""
        assert sys.stdout.fileno() >= 0
        assert sys.stderr.fileno() >= 0
        return result

    fake_cli.main = fake_main
    monkeypatch.setitem(sys.modules, "kindroid_relay.cli", fake_cli)
    assert namespace["main"]() == result
    assert sys.stdin is sys.stdout is sys.stderr is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Task Scheduler only")
def test_task_definition_and_ownership_without_registering_tasks() -> None:
    powershell = shutil.which("powershell.exe")
    assert powershell is not None
    repository = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-File", str(repository / "tests" / "autostart_test.ps1")],
        capture_output=True, text=True, errors="replace", timeout=20, check=False,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stdout + result.stderr
