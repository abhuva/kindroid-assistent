import os
from pathlib import Path
import subprocess
import shutil
import tempfile
import unittest


@unittest.skipUnless(os.name == "nt", "Windows PowerShell launcher")
class VoiceLauncherTests(unittest.TestCase):
    def test_missing_portable_bundle_stops_before_launch(self):
        launcher = Path(__file__).resolve().parents[1] / "scripts" / "voice-chat.ps1"
        with tempfile.TemporaryDirectory() as empty_state:
            scripts = Path(empty_state) / "scripts"
            scripts.mkdir()
            copy = scripts / launcher.name
            shutil.copyfile(launcher, copy)
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
                 "Bypass", "-File", str(copy)],
                cwd=empty_state, capture_output=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"repository-local voice bundle", result.stderr)
