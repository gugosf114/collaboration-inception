#!/usr/bin/env python3
"""Give George a blind, source-linked English read-back of Codex tool work."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable


TERMUX_CODEX = "/data/data/com.termux/files/usr/bin/codex"
TRANSLATOR_MODEL = "gpt-5.6-luna"
TRANSLATOR_TIMEOUT_SECONDS = 150
MAX_INPUT_CHARS = 12_000
MAX_RESPONSE_CHARS = 28_000
MAX_PROMPT_CHARS = 140_000
MAX_DISPLAY_ATTEMPTS = 2


class ReadbackError(RuntimeError):
    pass


def readback_root() -> Path:
    configured = os.environ.get("CODE_READBACK_ROOT")
    if configured:
        return Path(configured)
    return Path.home() / ".codex" / "code-readback"


def _make_private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)


def _safe_key(value: object) -> str:
    return hashlib.sha256(str(value).encode("utf-8", "replace")).hexdigest()[:24]


def _json_text(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return repr(value)


SECRET_PATTERNS = (
    (
        re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
        "[REDACTED_OPENAI_KEY]",
    ),
    (
        re.compile(r"\b(?:ghp|github_pat)_[A-Za-z0-9_]{16,}\b"),
        "[REDACTED_GITHUB_TOKEN]",
    ),
    (
        re.compile(
            r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;\"']+"
        ),
        r"\1[REDACTED_SECRET]",
    ),
    (
        re.compile(
            r"(?i)\b(api[_-]?key|access[_-]?token|password|secret)"
            r"(\s*[:=]\s*)[^\s,;\"']+"
        ),
        r"\1\2[REDACTED_SECRET]",
    ),
)


def redact_secrets(text: str) -> str:
    redacted = text
    for pattern, replacement in SECRET_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def _clip_text(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    head = limit * 2 // 3
    tail = limit - head
    removed = len(text) - limit
    return (
        text[:head]
        + f"\n[READ-BACK CAPTURE OMITTED {removed} CHARACTERS]\n"
        + text[-tail:],
        True,
    )


def _captured_value(value: Any, limit: int) -> dict[str, Any]:
    raw = _json_text(value)
    digest = hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()
    safe = redact_secrets(raw)
    clipped, truncated = _clip_text(safe, limit)
    return {
        "text": clipped,
        "sha256": digest,
        "truncated": truncated,
        "original_chars": len(safe),
    }


def _event_paths(
    event: dict[str, Any], root: Path
) -> tuple[Path, Path, str, str]:
    session_key = _safe_key(event.get("session_id", "missing-session"))
    turn_key = _safe_key(event.get("turn_id", "missing-turn"))
    pending = root / "pending" / session_key / f"{turn_key}.jsonl"
    lock = root / "locks" / f"{session_key}-{turn_key}.lock"
    return pending, lock, session_key, turn_key


@contextmanager
def _locked(path: Path):
    _make_private_directory(path.parent)
    with path.open("a+", encoding="utf-8") as handle:
        os.chmod(path, 0o600)
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def collect_post_tool_event(event: dict[str, Any], *, root: Path) -> dict[str, Any]:
    pending, lock, _session_key, _turn_key = _event_paths(event, root)
    _make_private_directory(pending.parent)
    with _locked(lock):
        sequence = 1
        if pending.is_file():
            with pending.open("r", encoding="utf-8") as existing:
                sequence += sum(1 for line in existing if line.strip())
        record = {
            "event_id": f"E{sequence}",
            "tool_name": str(event.get("tool_name") or "unknown"),
            "tool_use_id": str(event.get("tool_use_id") or "unknown"),
            "cwd": redact_secrets(str(event.get("cwd") or "unknown")),
            "input": _captured_value(event.get("tool_input"), MAX_INPUT_CHARS),
            "response": _captured_value(
                event.get("tool_response"), MAX_RESPONSE_CHARS
            ),
            "captured_utc": time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
            ),
        }
        with pending.open("a", encoding="utf-8") as output:
            os.chmod(pending, 0o600)
            output.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record


def _claim_events(
    event: dict[str, Any], *, root: Path
) -> tuple[list[dict[str, Any]], Path | None, str, str]:
    pending, lock, session_key, turn_key = _event_paths(event, root)
    with _locked(lock):
        if not pending.is_file() or pending.stat().st_size == 0:
            return [], None, session_key, turn_key
        processing_dir = root / "processing" / session_key
        _make_private_directory(processing_dir)
        claimed = processing_dir / f"{turn_key}-{os.getpid()}.jsonl"
        os.replace(pending, claimed)
    events: list[dict[str, Any]] = []
    with claimed.open("r", encoding="utf-8") as source:
        for line in source:
            if not line.strip():
                continue
            value = json.loads(line)
            if isinstance(value, dict):
                events.append(value)
    events.sort(key=lambda value: int(str(value["event_id"])[1:]))
    return events, claimed, session_key, turn_key


TRANSLATOR_RULES = """You are CODE READ-BACK, a closed-world code translator.

Your whole world is the numbered EVENT RECORDS below.
You do not know the user's request.
You do not know the coding model's plan, claims, or final answer.
Never infer any of them.

Translate only what the records prove into short, plain English sentences.

Rules:
1. Start every sentence with its source tag, such as [E1].
2. Cover every numbered event. Keep odd actions, repeated work, warnings,
   errors, skipped work, and failed results visible.
3. Say what was read, changed, run, created, removed, passed, or failed.
4. Keep a software name when it matters, then explain it with small words.
5. Never polish a strange action into a normal story.
6. Never state purpose or intent unless the raw event itself proves it.
7. If any part is unclear, redacted, or omitted, write:
   [E#] UNTRANSLATED: <the exact missing or unclear part>.
8. Use no heading, greeting, praise, advice, or closing paragraph.
9. End with one line: Coverage: E1 through E<number> covered.

EVENT RECORDS
"""


def _prompt_record(record: dict[str, Any], *, compact: bool = False) -> str:
    input_record = record["input"]
    response_record = record["response"]
    input_text = input_record["text"]
    response_text = response_record["text"]
    prompt_truncated = False
    if compact:
        input_text, input_cut = _clip_text(input_text, 1_200)
        response_text, response_cut = _clip_text(response_text, 2_400)
        prompt_truncated = input_cut or response_cut
    flags = {
        "capture_truncated": bool(
            input_record.get("truncated") or response_record.get("truncated")
        ),
        "prompt_truncated": prompt_truncated,
        "secrets_redacted": "[REDACTED_" in input_text
        or "[REDACTED_" in response_text,
    }
    return (
        f"\n--- {record['event_id']} ---\n"
        f"tool: {record['tool_name']}\n"
        f"working_directory: {record['cwd']}\n"
        f"flags: {json.dumps(flags, sort_keys=True)}\n"
        f"input_sha256: {input_record['sha256']}\n"
        f"INPUT\n{input_text}\n"
        f"response_sha256: {response_record['sha256']}\n"
        f"RESPONSE\n{response_text}\n"
    )


def build_translation_prompt(events: list[dict[str, Any]]) -> str:
    full = TRANSLATOR_RULES + "".join(_prompt_record(event) for event in events)
    if len(full) <= MAX_PROMPT_CHARS:
        return full
    compact = TRANSLATOR_RULES + "".join(
        _prompt_record(event, compact=True) for event in events
    )
    if len(compact) <= MAX_PROMPT_CHARS:
        return compact
    clipped, _truncated = _clip_text(compact, MAX_PROMPT_CHARS)
    return clipped + "\n[THE PROMPT LIMIT OMITTED PART OF THE EVENT RECORDS]\n"


def _ensure_isolated_codex_home(root: Path) -> tuple[Path, Path]:
    translator_home = root / "translator-home"
    sandbox = root / "empty-workspace"
    _make_private_directory(translator_home)
    _make_private_directory(sandbox)

    source_home = Path(
        os.environ.get("CODE_READBACK_PARENT_CODEX_HOME", Path.home() / ".codex")
    )
    auth_source = source_home / "auth.json"
    if not auth_source.is_file():
        raise ReadbackError(f"Codex login file is missing: {auth_source}")
    auth_link = translator_home / "auth.json"
    if auth_link.is_symlink():
        if auth_link.resolve() != auth_source.resolve():
            raise ReadbackError("Blind translator login link points to the wrong file")
    elif auth_link.exists():
        raise ReadbackError("Blind translator login path is not a safe link")
    else:
        auth_link.symlink_to(auth_source)
    return translator_home, sandbox


def run_blind_translator(prompt: str, *, root: Path) -> str:
    translator_home, sandbox = _ensure_isolated_codex_home(root)
    code_binary = os.environ.get("CODE_READBACK_CODEX", TERMUX_CODEX)
    temporary_dir = root / "tmp"
    _make_private_directory(temporary_dir)
    output_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix="readback-",
            suffix=".txt",
            dir=temporary_dir,
            delete=False,
        ) as output_file:
            output_path = Path(output_file.name)
        command = [
            code_binary,
            "exec",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--model",
            TRANSLATOR_MODEL,
            "-c",
            'approval_policy="never"',
            "-c",
            'model_reasoning_effort="low"',
            "-c",
            'web_search="disabled"',
            "--color",
            "never",
            "-C",
            str(sandbox),
            "--output-last-message",
            str(output_path),
            "-",
        ]
        environment = os.environ.copy()
        environment["CODE_READBACK_CHILD"] = "1"
        environment["CODEX_HOME"] = str(translator_home)
        environment["HOME"] = str(translator_home)
        completed = subprocess.run(
            command,
            input=prompt,
            text=True,
            capture_output=True,
            timeout=TRANSLATOR_TIMEOUT_SECONDS,
            env=environment,
            check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()[-800:]
            raise ReadbackError(
                f"blind translator exited {completed.returncode}: {detail}"
            )
        answer = output_path.read_text(encoding="utf-8").strip()
        if not answer:
            raise ReadbackError("blind translator returned no English read-back")
        return answer
    except subprocess.TimeoutExpired as exc:
        raise ReadbackError(
            f"blind translator exceeded {TRANSLATOR_TIMEOUT_SECONDS} seconds"
        ) from exc
    finally:
        if output_path is not None:
            try:
                output_path.unlink()
            except FileNotFoundError:
                pass


FAILURE_WORDS = re.compile(
    r"(?i)\b(error|errors|fail|failed|failure|warning|warnings|exception|"
    r"non[- ]?zero|exit(?:ed)? (?:code|status) [1-9])\b"
)


def _translation_for_event(text: str, event_id: str) -> str:
    start = text.find(f"[{event_id}]")
    if start < 0:
        return ""
    next_tag = re.search(r"\n\[E\d+\]", text[start + len(event_id) + 2 :])
    if next_tag is None:
        return text[start:]
    end = start + len(event_id) + 2 + next_tag.start()
    return text[start:end]


def validate_readback(text: str, events: list[dict[str, Any]]) -> str:
    clean = text.strip()
    repairs: list[str] = []
    expected = [str(event["event_id"]) for event in events]
    for event in events:
        event_id = str(event["event_id"])
        translated = _translation_for_event(clean, event_id)
        if not translated:
            repairs.append(
                f"[{event_id}] UNTRANSLATED: The blind reader skipped this event."
            )
            continue
        if (
            event["input"].get("truncated")
            or event["response"].get("truncated")
        ) and "UNTRANSLATED" not in translated.upper():
            repairs.append(
                f"[{event_id}] UNTRANSLATED: Part of this event was too large "
                "for the blind reader."
            )
        raw_response = str(event["response"].get("text", ""))
        if FAILURE_WORDS.search(raw_response) and not FAILURE_WORDS.search(translated):
            repairs.append(
                f"[{event_id}] CHECK RAW: The source contains a warning or failure "
                "that the English did not name."
            )

    seen = set(re.findall(r"\[E(\d+)\]", clean))
    allowed = {event_id[1:] for event_id in expected}
    extras = sorted(seen - allowed, key=int)
    if extras:
        repairs.append(
            "CHECK RAW: The blind reader named unknown event tags: "
            + ", ".join(f"E{value}" for value in extras)
            + "."
        )

    clean = re.sub(r"\n?Coverage:.*\Z", "", clean, flags=re.IGNORECASE).rstrip()
    if repairs:
        clean += "\n" + "\n".join(repairs)
    coverage = (
        f"Coverage: {expected[0]} through {expected[-1]} covered."
        if expected
        else "Coverage: no tool events."
    )
    return clean + "\n" + coverage


def _save_receipt(
    *,
    root: Path,
    session_key: str,
    turn_key: str,
    events: list[dict[str, Any]],
    readback: str,
) -> Path:
    directory = root / "receipts" / session_key
    _make_private_directory(directory)
    destination = directory / f"{turn_key}.json"
    payload = {
        "schema_version": 1,
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "translator_model": TRANSLATOR_MODEL,
        "closed_world": True,
        "events": events,
        "readback": readback,
    }
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    os.replace(temporary, destination)
    return destination


def _pending_display_path(event: dict[str, Any], root: Path) -> Path:
    session_key = _safe_key(event.get("session_id", "missing-session"))
    return root / "display" / session_key / "pending.json"


def _save_pending_display(
    event: dict[str, Any], *, root: Path, display: str
) -> Path:
    destination = _pending_display_path(event, root)
    _make_private_directory(destination.parent)
    payload = {"display": display, "attempts": 0}
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    os.replace(temporary, destination)
    return destination


def _display_continuation(display: str) -> dict[str, Any]:
    return {
        "decision": "block",
        "reason": (
            "A blind code translator has finished. Your only job now is to "
            "print the exact text between CODE_READBACK_START and "
            "CODE_READBACK_END. Do not add, remove, explain, summarize, or "
            "reformat anything. Do not use any tool. Do not print the marker "
            "lines.\n\nCODE_READBACK_START\n"
            + display
            + "\nCODE_READBACK_END"
        ),
    }


def _finish_pending_display(
    event: dict[str, Any], *, root: Path
) -> dict[str, Any] | None:
    pending = _pending_display_path(event, root)
    if not pending.is_file():
        return None
    data = json.loads(pending.read_text(encoding="utf-8"))
    expected = str(data.get("display") or "").strip()
    actual = str(event.get("last_assistant_message") or "").strip()
    if expected and (actual == expected or expected in actual):
        pending.unlink(missing_ok=True)
        return {}

    attempts = int(data.get("attempts") or 0) + 1
    if attempts >= MAX_DISPLAY_ATTEMPTS:
        pending.unlink(missing_ok=True)
        return {
            "systemMessage": (
                "ENGLISH READ-BACK — DISPLAY FAILED. The checked read-back is "
                "still in its receipt file."
            )
        }
    data["attempts"] = attempts
    pending.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.chmod(pending, 0o600)
    return _display_continuation(expected)


def process_stop_event(
    event: dict[str, Any],
    *,
    root: Path,
    translator: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    events, claimed, session_key, turn_key = _claim_events(event, root=root)
    if not events:
        return {}
    prompt = build_translation_prompt(events)
    try:
        if translator is None:
            raw_readback = run_blind_translator(prompt, root=root)
        else:
            raw_readback = translator(prompt)
    except Exception as exc:  # The raw feed must remain visible after any reader fault.
        reason = redact_secrets(str(exc)).replace("\n", " ")[:800]
        raw_readback = "\n".join(
            f"[{record['event_id']}] UNTRANSLATED: The blind reader failed: {reason}"
            for record in events
        )
    readback = validate_readback(raw_readback, events)
    receipt = _save_receipt(
        root=root,
        session_key=session_key,
        turn_key=turn_key,
        events=events,
        readback=readback,
    )
    if claimed is not None:
        claimed.unlink(missing_ok=True)
    display = (
        "ENGLISH READ-BACK — BLIND\n"
        + readback
        + f"\nReceipt: {receipt}"
    )
    _save_pending_display(event, root=root, display=display)
    return _display_continuation(display)


def handle_hook_event(
    event: dict[str, Any],
    *,
    root: Path | None = None,
    translator: Callable[[str], str] | None = None,
) -> dict[str, Any] | None:
    if os.environ.get("CODE_READBACK_CHILD") == "1":
        return None
    active_root = root or readback_root()
    hook_name = event.get("hook_event_name")
    if hook_name == "PostToolUse":
        collect_post_tool_event(event, root=active_root)
        return None
    if hook_name == "Stop":
        pending_result = _finish_pending_display(event, root=active_root)
        if pending_result is not None:
            return pending_result
        return process_stop_event(event, root=active_root, translator=translator)
    return None


def main() -> int:
    try:
        raw = sys.stdin.read()
        event = json.loads(raw or "{}")
        if not isinstance(event, dict):
            raise ReadbackError("hook input is not a JSON object")
        result = handle_hook_event(event)
        if result is not None:
            print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ValueError, ReadbackError) as exc:
        # Hook failure must never hide or block the original Codex result.
        message = redact_secrets(str(exc)).replace("\n", " ")[:800]
        hook_name = "unknown"
        try:
            hook_name = str(event.get("hook_event_name", "unknown"))
        except UnboundLocalError:
            pass
        if hook_name == "Stop":
            print(
                json.dumps(
                    {
                        "systemMessage": (
                            "ENGLISH READ-BACK — UNTRANSLATED: " + message
                        )
                    }
                )
            )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
