#!/usr/bin/env python3
"""Print one indexed visible turn without loading an entire transcript."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("session_id")
    parser.add_argument("ordinal", type=int)
    parser.add_argument(
        "--root", default=str(Path.home() / "session-post-office" / "latest")
    )
    parser.add_argument("--max-chars", type=int, default=12_000)
    args = parser.parse_args()

    con = sqlite3.connect(Path(args.root).resolve() / "messages.sqlite")
    con.row_factory = sqlite3.Row
    row = con.execute(
        "SELECT * FROM messages WHERE session_id = ? AND ordinal = ?",
        (args.session_id, args.ordinal),
    ).fetchone()
    con.close()
    if row is None:
        raise SystemExit("turn not found")
    text = row["text"].strip()
    print(
        f"# {row['source']} {row['session_id']} · {row['role']} turn {row['ordinal']}"
    )
    print(f"# {row['timestamp']} · {len(text)} characters\n")
    print(text[: args.max_chars])
    if len(text) > args.max_chars:
        print(f"\n[truncated {len(text) - args.max_chars} characters]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
