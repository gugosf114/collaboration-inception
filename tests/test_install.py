import importlib.util
import os
import shutil
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
    @unittest.skipIf(os.name == "nt", "Unix launcher behavior")
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
            self.assertIn("supervised live multi-model cockpit", completed.stdout)
            for name in ("inception", "po", "searchchats", "search-chats"):
                self.assertTrue((bin_dir / name).is_file())

    @unittest.skipIf(os.name == "nt", "Unix launcher behavior")
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

    @unittest.skipUnless(shutil.which("sh"), "POSIX shell is unavailable")
    def test_top_level_installer_parses(self):
        subprocess.run(["sh", "-n", str(ROOT / "install.sh")], check=True)

    @unittest.skipUnless(
        shutil.which("powershell") or shutil.which("powershell.exe"),
        "Windows PowerShell is unavailable",
    )
    def test_power_shell_installer_parses(self):
        shell = shutil.which("powershell") or shutil.which("powershell.exe")
        assert shell is not None
        command = (
            "$errors=$null; "
            "[System.Management.Automation.Language.Parser]::ParseFile("
            f"'{ROOT / 'install.ps1'}', [ref]$null, [ref]$errors) | Out-Null; "
            "if ($errors.Count) { $errors | ForEach-Object { Write-Error $_ }; exit 1 }"
        )
        subprocess.run(
            [shell, "-NoProfile", "-Command", command],
            check=True,
        )

    @unittest.skipUnless(
        os.name == "nt"
        and (shutil.which("powershell") or shutil.which("powershell.exe")),
        "native Windows installer behavior",
    )
    def test_power_shell_installer_builds_a_working_launcher(self):
        shell = shutil.which("powershell") or shutil.which("powershell.exe")
        assert shell is not None
        with tempfile.TemporaryDirectory() as directory:
            bin_dir = Path(directory) / "bin"
            installed = subprocess.run(
                [
                    shell,
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(ROOT / "install.ps1"),
                    "-SkipPreflight",
                    "-SkipPathUpdate",
                    "-BinDir",
                    str(bin_dir),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(installed.returncode, 0, installed.stderr)
            launched = subprocess.run(
                [str(bin_dir / "inception.cmd"), "cockpit", "--help"],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(launched.returncode, 0, launched.stderr)
            self.assertIn("comma-separated models", launched.stdout)

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
