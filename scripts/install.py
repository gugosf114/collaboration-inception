#!/usr/bin/env python3
"""Install the Inception cockpit launchers for one Unix or Termux user."""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Sequence


PROJECT = Path(__file__).resolve().parents[1]


class InstallError(RuntimeError):
    pass


def default_bin_dir() -> Path:
    prefix = os.environ.get("PREFIX")
    if prefix:
        return Path(prefix).expanduser() / "bin"
    return Path.home() / ".local" / "bin"


def command_works(command: Sequence[str]) -> tuple[bool, str]:
    try:
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    detail = (completed.stdout or completed.stderr).strip()
    return completed.returncode == 0, detail


def preflight() -> list[str]:
    problems: list[str] = []
    ok, detail = command_works(["codex", "--version"])
    if not ok:
        problems.append(f"Codex CLI is unavailable: {detail}")

    prefix = os.environ.get("PREFIX", "")
    if "com.termux" in prefix:
        ok, detail = command_works(
            ["proot-distro", "login", "debian", "--", "claude", "--version"]
        )
        if not ok:
            problems.append(f"Claude Code is unavailable inside Debian: {detail}")
    else:
        ok, detail = command_works(["claude", "--version"])
        if not ok:
            problems.append(f"Claude Code is unavailable: {detail}")
    return problems


def wrapper(command: Sequence[str]) -> str:
    rendered = " ".join(shlex.quote(part) for part in command)
    return f"#!/usr/bin/env sh\nexec {rendered} \"$@\"\n"


def install_wrapper(path: Path, content: str) -> Path | None:
    backup: Path | None = None
    if path.exists() or path.is_symlink():
        try:
            existing = path.read_text(encoding="utf-8")
        except OSError:
            existing = ""
        if existing == content:
            path.chmod(0o755)
            return None
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = path.with_name(f"{path.name}.backup-{stamp}")
        suffix = 1
        while backup.exists():
            backup = path.with_name(f"{path.name}.backup-{stamp}-{suffix}")
            suffix += 1
        path.replace(backup)
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)
    return backup


def install(bin_dir: Path, skip_preflight: bool = False) -> list[Path]:
    required = (
        PROJECT / "scripts" / "inception.py",
        PROJECT / "scripts" / "cockpit.py",
        PROJECT / "postoffice" / "po",
        PROJECT / "context" / "WORKING_COVENANT.md",
        PROJECT / "context" / "MICROHISTORY_V1.md",
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise InstallError(f"Downloaded repository is incomplete: {', '.join(missing)}")

    if not skip_preflight:
        problems = preflight()
        if problems:
            raise InstallError("\n".join(problems))

    python = shutil.which("python3") or sys.executable
    bash = shutil.which("bash")
    if not bash:
        raise InstallError("bash is required for the transcript post office")

    bin_dir.mkdir(parents=True, exist_ok=True)
    launchers = {
        "inception": wrapper([python, str(PROJECT / "scripts" / "inception.py")]),
        "po": wrapper([bash, str(PROJECT / "postoffice" / "po")]),
        "searchchats": wrapper([bash, str(PROJECT / "postoffice" / "po")]),
        "search-chats": wrapper([bash, str(PROJECT / "postoffice" / "po")]),
    }
    backups: list[Path] = []
    for name, content in launchers.items():
        backup = install_wrapper(bin_dir / name, content)
        if backup:
            backups.append(backup)
    return backups


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Install the Inception cockpit for the current user"
    )
    result.add_argument("--bin-dir", type=Path, default=default_bin_dir())
    result.add_argument("--skip-preflight", action="store_true", help=argparse.SUPPRESS)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        backups = install(args.bin_dir.expanduser().resolve(), args.skip_preflight)
    except InstallError as exc:
        print(f"Inception install stopped:\n{exc}", file=sys.stderr)
        return 1

    print("Inception is installed.")
    print(f"Launch: {args.bin_dir / 'inception'} cockpit")
    print("Inside the cockpit: /both Say hello and explain what you can do.")
    if backups:
        print("Previous launchers were preserved:")
        for path in backups:
            print(f"- {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
