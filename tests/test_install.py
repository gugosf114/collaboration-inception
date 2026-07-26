import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "install.py"
SPEC = importlib.util.spec_from_file_location("inception_install", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class InstallTests(unittest.TestCase):
    def test_installs_working_launchers_from_the_downloaded_repo(self):
        with tempfile.TemporaryDirectory() as directory:
            bin_dir = Path(directory) / "bin"
            backups = MODULE.install(bin_dir, skip_preflight=True)
            completed = subprocess.run(
                [str(bin_dir / "inception"), "--help"],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(backups, [])
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("supervised live Claude-Codex switchboard", completed.stdout)
            for name in ("inception", "po", "searchchats", "search-chats"):
                self.assertTrue((bin_dir / name).is_file())

    def test_preserves_an_existing_unrelated_launcher(self):
        with tempfile.TemporaryDirectory() as directory:
            bin_dir = Path(directory)
            existing = bin_dir / "inception"
            existing.write_text("someone else's command\n", encoding="utf-8")

            backups = MODULE.install(bin_dir, skip_preflight=True)

            self.assertEqual(len(backups), 1)
            self.assertEqual(
                backups[0].read_text(encoding="utf-8"),
                "someone else's command\n",
            )

    def test_top_level_installer_parses(self):
        subprocess.run(["sh", "-n", str(ROOT / "install.sh")], check=True)

    def test_preflight_reports_missing_image_support_before_runtime(self):
        def fake_command(command):
            if command[0] == "magick":
                return False, "not found"
            return True, "ok"

        with (
            patch.dict(MODULE.os.environ, {"PREFIX": ""}, clear=True),
            patch.object(MODULE, "command_works", side_effect=fake_command),
        ):
            problems = MODULE.preflight()

        self.assertEqual(len(problems), 1)
        self.assertIn("ImageMagick", problems[0])
        self.assertIn("screenshot pointing", problems[0])


if __name__ == "__main__":
    unittest.main()
