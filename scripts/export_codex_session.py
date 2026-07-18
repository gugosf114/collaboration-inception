#!/usr/bin/env python3
"""Export a Codex rollout JSONL into portable conversation transcripts."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


INJECTED_USER_PREFIXES = (
    "<environment_context>",
    "# AGENTS.md instructions",
    "<permissions instructions>",
)


def iter_records(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON on line {line_number}: {exc}") from exc


def content_text(content: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for item in content:
        item_type = item.get("type")
        if item_type in {"input_text", "output_text"}:
            text = item.get("text", "")
            if text:
                parts.append(text)
        elif item_type in {"input_image", "output_image", "image"}:
            parts.append("[Image attached in the original conversation]")
    return "\n\n".join(parts).strip()


def is_injected_user_message(text: str) -> bool:
    stripped = text.lstrip()
    return any(stripped.startswith(prefix) for prefix in INJECTED_USER_PREFIXES)


def extract(path: Path, include_injected: bool = False) -> tuple[list[dict[str, str]], dict[str, Any]]:
    messages: list[dict[str, str]] = []
    session_meta: dict[str, Any] = {}

    for record in iter_records(path):
        if record.get("type") == "session_meta" and not session_meta:
            payload = record.get("payload")
            if isinstance(payload, dict):
                session_meta = payload
            continue

        if record.get("type") != "response_item":
            continue
        payload = record.get("payload") or {}
        if payload.get("type") != "message":
            continue
        role = payload.get("role")
        if role not in {"user", "assistant"}:
            continue

        text = content_text(payload.get("content") or [])
        if not text:
            continue
        if role == "user" and not include_injected and is_injected_user_message(text):
            continue

        messages.append(
            {
                "timestamp": str(record.get("timestamp") or ""),
                "role": role,
                "phase": str(payload.get("phase") or ""),
                "text": text,
            }
        )

    return messages, session_meta


def core_messages(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    return [
        message
        for message in messages
        if message["role"] == "user"
        or (message["role"] == "assistant" and message["phase"] == "final_answer")
    ]


def render_markdown(
    messages: list[dict[str, str]],
    title: str,
    source: Path,
    user_name: str,
    assistant_name: str,
) -> str:
    lines = [
        f"# {title}",
        "",
        f"> Portable transcript derived from `{source.name}`.",
        "> Chronology and wording are preserved; tool machinery and injected runtime instructions are omitted.",
        "",
    ]
    for message in messages:
        if message["role"] == "user":
            speaker = user_name
        else:
            phase = f", {message['phase']}" if message["phase"] else ""
            speaker = f"{assistant_name}{phase}"
        timestamp = f" — {message['timestamp']}" if message["timestamp"] else ""
        lines.extend((f"## {speaker}{timestamp}", "", message["text"], ""))
    return "\n".join(lines).rstrip() + "\n"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_jsonl(path: Path, messages: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for message in messages:
            handle.write(json.dumps(message, ensure_ascii=False) + "\n")


def rough_tokens(text: str) -> int:
    return round(len(text) / 4)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", type=Path, help="Codex rollout JSONL file")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--prefix", default="codex-session")
    parser.add_argument("--user-name", default="George")
    parser.add_argument("--assistant-name", default="Sol")
    parser.add_argument("--include-injected", action="store_true")
    args = parser.parse_args()

    source = args.session.expanduser().resolve()
    if not source.is_file():
        parser.error(f"Session file not found: {source}")

    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    full, session_meta = extract(source, include_injected=args.include_injected)
    core = core_messages(full)

    core_text = render_markdown(
        core,
        "Core conversation transcript",
        source,
        args.user_name,
        args.assistant_name,
    )
    full_text = render_markdown(
        full,
        "Full visible conversation transcript",
        source,
        args.user_name,
        args.assistant_name,
    )

    core_path = output_dir / f"{args.prefix}.core.md"
    full_path = output_dir / f"{args.prefix}.full.md"
    jsonl_path = output_dir / f"{args.prefix}.messages.jsonl"
    metadata_path = output_dir / f"{args.prefix}.metadata.json"

    core_path.write_text(core_text, encoding="utf-8")
    full_path.write_text(full_text, encoding="utf-8")
    write_jsonl(jsonl_path, full)

    assistant_phases: dict[str, int] = {}
    for message in full:
        if message["role"] == "assistant":
            phase = message["phase"] or "unspecified"
            assistant_phases[phase] = assistant_phases.get(phase, 0) + 1

    metadata = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "source": str(source),
        "session_id": session_meta.get("id") or session_meta.get("session_id"),
        "source_bytes": source.stat().st_size,
        "full_message_count": len(full),
        "core_message_count": len(core),
        "assistant_phases": assistant_phases,
        "outputs": {
            "core": {
                "path": str(core_path),
                "bytes": core_path.stat().st_size,
                "rough_tokens": rough_tokens(core_text),
                "sha256": sha256(core_path),
            },
            "full": {
                "path": str(full_path),
                "bytes": full_path.stat().st_size,
                "rough_tokens": rough_tokens(full_text),
                "sha256": sha256(full_path),
            },
            "messages": {
                "path": str(jsonl_path),
                "bytes": jsonl_path.stat().st_size,
                "sha256": sha256(jsonl_path),
            },
        },
    }
    metadata_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(metadata, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
