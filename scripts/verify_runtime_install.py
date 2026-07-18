#!/usr/bin/env python3
"""Verify native persistent-thread continuity without changing it."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from inception import (
    STATE_PATH,
    canonical_rollout,
    daemon_status,
    load_state,
)


PROJECT = Path(__file__).resolve().parents[1]
HOME = Path(os.environ.get("HOME", str(PROJECT.parent)))
CODEX_AGENTS = HOME / ".codex" / "AGENTS.md"
CLAUDE_MEMORY = HOME / ".claude" / "CLAUDE.md"
MICROHISTORY = PROJECT / "context" / "MICROHISTORY_V1.md"
COVENANT = PROJECT / "context" / "WORKING_COVENANT.md"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify() -> dict[str, object]:
    state = load_state(STATE_PATH)
    rollout, metadata = canonical_rollout(state)
    if metadata.get("forked_from_id") != state["parent_thread_id"]:
        raise RuntimeError(
            "Canonical rollout lineage differs from runtime/state.json"
        )

    agents = CODEX_AGENTS.read_text(encoding="utf-8")
    claude = CLAUDE_MEMORY.read_text(encoding="utf-8")
    forbidden = {
        "codex_microhistory": "# George–AI microhistory v1" in agents,
        "claude_microhistory": str(MICROHISTORY) in claude,
        "claude_covenant": str(COVENANT) in claude,
    }
    present = [name for name, found in forbidden.items() if found]
    if present:
        raise RuntimeError(f"Automatic relationship injection remains: {present}")

    daemon = daemon_status()
    if daemon.get("status") != "running":
        raise RuntimeError(f"Codex app-server is not running: {daemon}")

    return {
        "status": "ok",
        "mode": state["mode"],
        "canonical_thread_id": state["canonical_thread_id"],
        "parent_thread_id": state["parent_thread_id"],
        "lineage_root_thread_id": state["lineage_root_thread_id"],
        "rollout": str(rollout),
        "rollout_bytes": rollout.stat().st_size,
        "app_server": {
            "status": daemon["status"],
            "version": daemon["appServerVersion"],
            "socket": daemon["socketPath"],
        },
        "automatic_prompt_injection": False,
        "recovery_artifacts": {
            "microhistory_sha256": sha256(MICROHISTORY),
            "covenant_sha256": sha256(COVENANT),
        },
    }


if __name__ == "__main__":
    print(json.dumps(verify(), indent=2))
