#!/usr/bin/env python3
"""Verify the installed Inception relationship trajectory without changing it."""

from __future__ import annotations

import hashlib
import json
import tomllib
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
HOME = PROJECT.parent
SOURCE_DIR = PROJECT / "context"
MICROHISTORY = SOURCE_DIR / "MICROHISTORY_V1.md"
COVENANT = SOURCE_DIR / "WORKING_COVENANT.md"
CODEX_AGENTS = HOME / ".codex" / "AGENTS.md"
CODEX_CONFIG = HOME / ".codex" / "config.toml"
CLAUDE_MEMORY = HOME / ".claude" / "CLAUDE.md"
MICROHISTORY_MARKER = "# George–AI microhistory v1\n"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify() -> dict[str, object]:
    source = MICROHISTORY.read_bytes()
    agents = CODEX_AGENTS.read_bytes()
    marker = MICROHISTORY_MARKER.encode()
    marker_offset = agents.find(marker)
    if marker_offset < 0:
        raise RuntimeError("Codex installation is missing the microhistory marker")

    installed = agents[marker_offset:]
    if installed != source:
        raise RuntimeError("Codex microhistory differs from the provider-neutral source")

    config = tomllib.loads(CODEX_CONFIG.read_text(encoding="utf-8"))
    byte_limit = int(config.get("project_doc_max_bytes", 32768))
    if len(agents) > byte_limit:
        raise RuntimeError(
            f"Codex AGENTS.md exceeds startup budget: {len(agents)} > {byte_limit}"
        )

    claude = CLAUDE_MEMORY.read_text(encoding="utf-8")
    required_imports = [f"@{COVENANT}", f"@{MICROHISTORY}"]
    missing_imports = [item for item in required_imports if item not in claude]
    if missing_imports:
        raise RuntimeError(f"Claude installation is missing imports: {missing_imports}")

    return {
        "status": "ok",
        "source_sha256": sha256(source),
        "codex": {
            "agents_bytes": len(agents),
            "startup_limit_bytes": byte_limit,
            "remaining_bytes": byte_limit - len(agents),
            "microhistory_exact_match": True,
        },
        "claude_code": {
            "covenant_imported": True,
            "microhistory_imported": True,
        },
    }


if __name__ == "__main__":
    print(json.dumps(verify(), indent=2))
