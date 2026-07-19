#!/usr/bin/env python3
"""Build a local, searchable message index from a post-office snapshot."""

from __future__ import annotations

import csv
import json
import sqlite3
import sys
from pathlib import Path


def visible_codex_message(record: dict) -> tuple[str, str] | None:
    if record.get("type") != "response_item":
        return None
    payload = record.get("payload") or {}
    if payload.get("type") != "message" or payload.get("role") not in {
        "user",
        "assistant",
    }:
        return None
    parts = [
        item.get("text", "")
        for item in payload.get("content") or []
        if item.get("type") in {"input_text", "output_text"}
    ]
    text = "\n".join(part for part in parts if part).strip()
    return (payload["role"], text) if text else None


def visible_claude_message(record: dict) -> tuple[str, str] | None:
    if record.get("type") not in {"user", "assistant"}:
        return None
    message = record.get("message") or {}
    role = message.get("role") or record["type"]
    content = message.get("content")
    if isinstance(content, str):
        text = content.strip()
    elif isinstance(content, list):
        # Deliberately exclude thinking and tool payloads. The raw transcript
        # remains available when tool-level evidence is later required.
        text = "\n".join(
            item.get("text", "")
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        ).strip()
    else:
        text = ""
    return (role, text) if text else None


def main() -> int:
    root = Path(
        sys.argv[1]
        if len(sys.argv) > 1
        else Path.home() / "session-post-office" / "latest"
    )
    root = root.resolve()
    manifest = root / "manifest.tsv"
    database = root / "messages.sqlite"
    export = root / "messages.jsonl"

    if not manifest.exists():
        raise SystemExit(f"Missing manifest: {manifest}")

    database.unlink(missing_ok=True)
    export.unlink(missing_ok=True)
    con = sqlite3.connect(database)
    con.executescript(
        """
        PRAGMA journal_mode=WAL;
        CREATE TABLE messages (
          id INTEGER PRIMARY KEY,
          source TEXT NOT NULL,
          session_id TEXT NOT NULL,
          ordinal INTEGER NOT NULL,
          timestamp TEXT,
          role TEXT NOT NULL,
          text TEXT NOT NULL,
          raw_file TEXT NOT NULL
        );
        CREATE INDEX messages_session_ordinal ON messages(session_id, ordinal);
        CREATE VIRTUAL TABLE messages_fts USING fts5(
          text, content='messages', content_rowid='id', tokenize='unicode61 remove_diacritics 2'
        );
        """
    )

    total = 0
    with (
        manifest.open(encoding="utf-8", newline="") as mf,
        export.open("w", encoding="utf-8") as out,
    ):
        for row in csv.DictReader(mf, delimiter="\t"):
            raw_file = Path(row["raw_file"])
            parser = (
                visible_codex_message
                if row["source"] == "codex"
                else visible_claude_message
            )
            ordinal = 0
            with raw_file.open(encoding="utf-8", errors="replace") as source:
                for line in source:
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    visible = parser(record)
                    if not visible:
                        continue
                    role, text = visible
                    ordinal += 1
                    cursor = con.execute(
                        "INSERT INTO messages(source,session_id,ordinal,timestamp,role,text,raw_file) VALUES(?,?,?,?,?,?,?)",
                        (
                            row["source"],
                            row["session_id"],
                            ordinal,
                            record.get("timestamp"),
                            role,
                            text,
                            str(raw_file),
                        ),
                    )
                    entry = {
                        "id": cursor.lastrowid,
                        "source": row["source"],
                        "session_id": row["session_id"],
                        "ordinal": ordinal,
                        "timestamp": record.get("timestamp"),
                        "role": role,
                        "text": text,
                    }
                    out.write(json.dumps(entry, ensure_ascii=False) + "\n")
                    total += 1

    con.execute("INSERT INTO messages_fts(rowid,text) SELECT id,text FROM messages")
    con.commit()
    sessions = con.execute(
        "SELECT COUNT(DISTINCT session_id) FROM messages"
    ).fetchone()[0]
    con.close()
    print(f"Indexed {total} visible messages from {sessions} sessions")
    print(database)
    print(export)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
