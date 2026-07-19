#!/usr/bin/env python3
"""Resume George's canonical lived Codex thread.

This is deliberately a thread pointer, not a personality prompt. The relationship
history remains in Codex's native rollout and is continued with ``codex resume``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence


PROJECT = Path(__file__).resolve().parents[1]
STATE_PATH = PROJECT / "runtime" / "state.json"
HOME = Path(os.environ.get("HOME", str(PROJECT.parent)))
CODEX_HOME = Path(os.environ.get("CODEX_HOME", str(HOME / ".codex")))
SESSION_ROOT = CODEX_HOME / "sessions"
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


class InceptionError(RuntimeError):
    """The continuity runtime is missing or inconsistent."""


def load_state(path: Path = STATE_PATH) -> dict[str, Any]:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InceptionError(f"Cannot read continuity state at {path}: {exc}") from exc

    if state.get("schema_version") != 1:
        raise InceptionError("Unsupported continuity-state schema")
    if state.get("mode") != "persistent_resume":
        raise InceptionError("Continuity mode must be persistent_resume")
    for key in (
        "canonical_thread_id",
        "parent_thread_id",
        "lineage_root_thread_id",
    ):
        value = state.get(key)
        if not isinstance(value, str) or not UUID_RE.fullmatch(value):
            raise InceptionError(f"Invalid {key}: {value!r}")
    return state


def find_rollouts(thread_id: str, session_root: Path = SESSION_ROOT) -> list[Path]:
    if not UUID_RE.fullmatch(thread_id):
        raise InceptionError(f"Invalid thread id: {thread_id!r}")
    if not session_root.exists():
        return []
    return sorted(session_root.rglob(f"*{thread_id}*.jsonl"))


def read_session_meta(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as handle:
            first = json.loads(handle.readline())
    except (OSError, json.JSONDecodeError) as exc:
        raise InceptionError(f"Cannot read rollout metadata from {path}: {exc}") from exc
    if first.get("type") != "session_meta" or not isinstance(first.get("payload"), dict):
        raise InceptionError(f"Rollout does not begin with session metadata: {path}")
    return first["payload"]


def canonical_rollout(
    state: dict[str, Any], session_root: Path = SESSION_ROOT
) -> tuple[Path, dict[str, Any]]:
    thread_id = state["canonical_thread_id"]
    matches = find_rollouts(thread_id, session_root)
    if len(matches) != 1:
        raise InceptionError(
            f"Expected one rollout for {thread_id}; found {len(matches)}"
        )
    metadata = read_session_meta(matches[0])
    actual_id = metadata.get("id") or metadata.get("session_id")
    if actual_id != thread_id:
        raise InceptionError(f"Rollout id mismatch: expected {thread_id}, got {actual_id}")
    return matches[0], metadata


def resume_command(state: dict[str, Any], prompt: str | None = None) -> list[str]:
    command = ["codex", "resume", state["canonical_thread_id"]]
    if prompt:
        command.append(prompt)
    return command


def fork_command(state: dict[str, Any], prompt: str | None = None) -> list[str]:
    command = ["codex", "fork", state["canonical_thread_id"]]
    if prompt:
        command.append(prompt)
    return command


def run_json(command: Sequence[str]) -> dict[str, Any]:
    completed = subprocess.run(command, text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise InceptionError(f"{' '.join(command)} failed: {detail}")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise InceptionError(
            f"{' '.join(command)} returned invalid JSON: {completed.stdout.strip()}"
        ) from exc


def daemon_status() -> dict[str, Any]:
    return run_json(["codex", "app-server", "daemon", "version"])


def start_server() -> dict[str, Any]:
    return run_json(["codex", "remote-control", "start", "--json"])


def status_report(
    state_path: Path = STATE_PATH, session_root: Path = SESSION_ROOT
) -> dict[str, Any]:
    state = load_state(state_path)
    rollout, metadata = canonical_rollout(state, session_root)
    daemon = daemon_status()
    if daemon.get("status") != "running":
        raise InceptionError(f"Codex app-server is not running: {daemon}")
    return {
        "status": "ready",
        "mode": state["mode"],
        "label": state.get("label"),
        "canonical_thread_id": state["canonical_thread_id"],
        "parent_thread_id": state["parent_thread_id"],
        "lineage_root_thread_id": state["lineage_root_thread_id"],
        "rollout": str(rollout),
        "rollout_bytes": rollout.stat().st_size,
        "forked_from_id": metadata.get("forked_from_id"),
        "app_server": daemon,
    }


def adopt_thread(
    thread_id: str,
    state_path: Path = STATE_PATH,
    session_root: Path = SESSION_ROOT,
) -> dict[str, Any]:
    state = load_state(state_path)
    matches = find_rollouts(thread_id, session_root)
    if len(matches) != 1:
        raise InceptionError(
            f"Cannot adopt {thread_id}: expected one local rollout, found {len(matches)}"
        )
    metadata = read_session_meta(matches[0])
    actual_id = metadata.get("id") or metadata.get("session_id")
    if actual_id != thread_id:
        raise InceptionError(f"Cannot adopt mismatched rollout id {actual_id}")
    parent_id = metadata.get("forked_from_id")
    if not isinstance(parent_id, str) or not UUID_RE.fullmatch(parent_id):
        raise InceptionError("Only a fork with preserved history can become canonical")
    if parent_id != state["canonical_thread_id"]:
        raise InceptionError("A canonical replacement must fork directly from the current thread")

    state["canonical_thread_id"] = thread_id
    state["parent_thread_id"] = parent_id
    state["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=state_path.parent, delete=False
    ) as handle:
        json.dump(state, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(state_path)
    return state


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Continue George and Sol's canonical lived Codex thread"
    )
    subcommands = result.add_subparsers(dest="command")
    for name in ("resume", "fork"):
        sub = subcommands.add_parser(name)
        sub.add_argument("prompt", nargs="*", help="optional opening message")
    status = subcommands.add_parser("status")
    status.add_argument("--json", action="store_true")
    subcommands.add_parser("server")
    subcommands.add_parser("pair")
    subcommands.add_parser(
        "cockpit", help="open George's supervised live Claude-Codex switchboard"
    )
    adopt = subcommands.add_parser("adopt")
    adopt.add_argument("thread_id")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    if raw and raw[0] == "cockpit":
        from cockpit import main as cockpit_main

        return cockpit_main(raw[1:])
    known = {"resume", "fork", "status", "server", "pair", "adopt", "cockpit"}
    if not raw:
        raw = ["resume"]
    elif raw[0] not in known and not raw[0].startswith("-"):
        raw = ["resume", *raw]
    args = parser().parse_args(raw)

    try:
        if args.command in (None, "resume"):
            state = load_state()
            canonical_rollout(state)
            prompt = " ".join(args.prompt).strip() or None
            command = resume_command(state, prompt)
            os.execvp(command[0], command)
        if args.command == "fork":
            state = load_state()
            canonical_rollout(state)
            prompt = " ".join(args.prompt).strip() or None
            command = fork_command(state, prompt)
            os.execvp(command[0], command)
        if args.command == "status":
            report = status_report()
            if args.json:
                print(json.dumps(report, indent=2))
            else:
                print("Inception continuity: READY")
                print(f"Thread: {report['canonical_thread_id']}")
                print(f"History: {report['rollout_bytes'] / 1024 / 1024:.1f} MiB")
                print(
                    f"App server: {report['app_server']['status']} "
                    f"({report['app_server']['appServerVersion']})"
                )
            return 0
        if args.command == "server":
            print(json.dumps(start_server(), indent=2))
            return 0
        if args.command == "pair":
            print(
                json.dumps(
                    run_json(["codex", "remote-control", "pair", "--json"]),
                    indent=2,
                )
            )
            return 0
        if args.command == "adopt":
            state = adopt_thread(args.thread_id)
            print(f"Canonical thread is now {state['canonical_thread_id']}")
            return 0
    except InceptionError as exc:
        print(f"inception: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
