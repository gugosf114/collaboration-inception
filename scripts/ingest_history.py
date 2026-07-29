#!/usr/bin/env python3
"""Extract inspectable relationship evidence from post-office messages.

Input is the standardized ``messages.jsonl`` produced by ``postoffice/index.py``.
The extractor works session-by-session outside the model context window.  It
keeps the source exchange, inference, possible counterevidence, confidence,
date, and concrete future behavior in the relationship ledger.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Sequence

try:
    from operating_room import (
        CORRECTION_PATTERNS,
        DEFAULT_LEDGER_PATH,
        RelationshipLedger,
        compact,
    )
except ModuleNotFoundError:
    from scripts.operating_room import (  # type: ignore[no-redef]
        CORRECTION_PATTERNS,
        DEFAULT_LEDGER_PATH,
        RelationshipLedger,
        compact,
    )


INTERRUPTION_RE = re.compile(
    r"\b(stop|wait|hold on|pause|listen|you are missing the point)\b",
    re.I,
)
APPROVAL_RE = re.compile(
    r"\b(exactly|that's it|that is it|yes[,!. ]|good[,!. ]|works|perfect)\b",
    re.I,
)
SUCCESS_RE = re.compile(
    r"\b(it worked|tests? passed|ci passed|shipped|deployed|installed|fixed|done for real)\b",
    re.I,
)
REVERSAL_RE = re.compile(
    r"\b(actually|except|but that|no[,— -]|changed my mind|not anymore)\b",
    re.I,
)


def load_messages(paths: Iterable[Path]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for path in paths:
        with path.expanduser().resolve().open(
            encoding="utf-8", errors="replace"
        ) as handle:
            for line_number, line in enumerate(handle, 1):
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(value, dict):
                    continue
                role = value.get("role")
                text = value.get("text")
                session = value.get("session_id")
                if (
                    role not in {"user", "assistant"}
                    or not isinstance(text, str)
                    or not text.strip()
                    or not isinstance(session, str)
                    or not session
                ):
                    continue
                messages.append(
                    {
                        **value,
                        "text": text.strip(),
                        "_input": str(path),
                        "_line": line_number,
                    }
                )
    return messages


def episode_kind(text: str) -> str | None:
    if any(pattern.search(text) for pattern in CORRECTION_PATTERNS):
        return "correction"
    if INTERRUPTION_RE.search(text):
        return "interruption"
    if SUCCESS_RE.search(text):
        return "success"
    if APPROVAL_RE.search(text):
        return "approval"
    return None


def category_for(text: str) -> str:
    lowered = text.lower()
    categories = {
        "testing": ("test", "ci", "crash", "bug", "regression"),
        "shipping": ("ship", "deploy", "release", "publish", "play console"),
        "design": ("design", "ui", "layout", "screen", "button"),
        "communication": ("explain", "wording", "email", "message", "voice"),
        "continuity": ("memory", "context", "relationship", "transcript"),
        "strategy": ("customer", "revenue", "business", "priority", "decision"),
    }
    scores = {
        category: sum(marker in lowered for marker in markers)
        for category, markers in categories.items()
    }
    best = max(scores, key=scores.get)
    return best if scores[best] else "general"


def nearest(
    messages: Sequence[dict[str, Any]],
    index: int,
    role: str,
    direction: int,
) -> dict[str, Any] | None:
    cursor = index + direction
    while 0 <= cursor < len(messages) and abs(cursor - index) <= 3:
        if messages[cursor]["role"] == role:
            return messages[cursor]
        cursor += direction
    return None


def inference_for(
    kind: str,
    operator_text: str,
    prior: str,
    repair: str,
) -> tuple[str, str, float]:
    if kind == "correction":
        return (
            f"George corrected the agent: {compact(operator_text, 900)}",
            (
                "Apply the correction immediately, name the concrete miss, and "
                f"prefer the demonstrated repair when relevant: {compact(repair, 650)}"
                if repair
                else "Apply the correction immediately and verify the repaired behavior."
            ),
            0.82,
        )
    if kind == "interruption":
        return (
            f"George interrupted this trajectory: {compact(operator_text, 900)}",
            "Stop the current line of work, preserve what is useful, and follow the redirect before expanding.",
            0.76,
        )
    if kind == "success":
        return (
            f"George reported an externally observable success: {compact(operator_text, 900)}",
            f"Reuse the behavior that preceded the result: {compact(prior, 750)}",
            0.78,
        )
    return (
        f"George approved the preceding behavior: {compact(operator_text, 900)}",
        f"Prefer the accepted pattern when the same situation recurs: {compact(prior, 750)}",
        0.68,
    )


def extract(
    messages: Sequence[dict[str, Any]],
    ledger: RelationshipLedger | None,
) -> dict[str, int]:
    sessions: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for message in messages:
        sessions[
            (str(message.get("source") or "unknown"), message["session_id"])
        ].append(message)
    counts = {
        "sessions": len(sessions),
        "messages": len(messages),
        "candidates": 0,
        "inserted": 0,
        "duplicates": 0,
    }
    for (source, session_id), rows in sessions.items():
        rows.sort(key=lambda row: int(row.get("ordinal") or row["_line"]))
        for index, row in enumerate(rows):
            if row["role"] != "user":
                continue
            kind = episode_kind(row["text"])
            if kind is None:
                continue
            prior = nearest(rows, index, "assistant", -1)
            if prior is None:
                continue
            repair = nearest(rows, index, "assistant", 1)
            followup = nearest(rows, index, "user", 1)
            counts["candidates"] += 1
            inference, useful_behavior, confidence = inference_for(
                kind,
                row["text"],
                prior["text"],
                repair["text"] if repair else "",
            )
            counterevidence = (
                followup["text"]
                if followup and REVERSAL_RE.search(followup["text"])
                else ""
            )
            source_exchange = {
                "assistant_before": prior["text"],
                "george": row["text"],
                "assistant_after": repair["text"] if repair else "",
                "george_followup": followup["text"] if followup else "",
                "timing": {
                    "assistant_before": prior.get("timestamp"),
                    "george": row.get("timestamp"),
                    "assistant_after": repair.get("timestamp") if repair else None,
                    "george_followup": followup.get("timestamp") if followup else None,
                },
            }
            ordinal = int(row.get("ordinal") or row["_line"])
            digest = hashlib.sha256(
                (
                    f"{source}\0{session_id}\0{ordinal}\0{row['text']}"
                ).encode("utf-8")
            ).hexdigest()
            hypothesis_id = hashlib.sha256(
                re.sub(r"\s+", " ", inference.lower()).encode("utf-8")
            ).hexdigest()[:16]
            if ledger is None:
                counts["inserted"] += 1
                continue
            identifier = ledger.record_episode(
                source=source,
                session_id=session_id,
                ordinal=ordinal,
                kind=kind,
                agent=source if source in {"claude", "codex", "antigravity"} else None,
                category=category_for(
                    " ".join(
                        (
                            prior["text"],
                            row["text"],
                            repair["text"] if repair else "",
                        )
                    )
                ),
                confidence=confidence,
                source_exchange=source_exchange,
                inference=inference,
                counterevidence=counterevidence,
                useful_behavior=useful_behavior,
                source_hash=digest,
                hypothesis_id=hypothesis_id,
                at=str(row.get("timestamp") or ""),
            )
            if identifier:
                counts["inserted"] += 1
            else:
                counts["duplicates"] += 1
    return counts


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Extract relationship evidence from post-office messages.jsonl"
    )
    result.add_argument("messages", nargs="+", type=Path)
    result.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER_PATH)
    result.add_argument(
        "--dry-run",
        action="store_true",
        help="count candidate episodes without changing the ledger",
    )
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    messages = load_messages(args.messages)
    ledger = None if args.dry_run else RelationshipLedger(args.ledger)
    try:
        counts = extract(messages, ledger)
    finally:
        if ledger is not None:
            ledger.close()
    print(json.dumps(counts, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
