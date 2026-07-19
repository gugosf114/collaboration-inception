#!/usr/bin/env python3
"""Search post-office messages with neighboring turns for context."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "query", help="SQLite FTS5 query, e.g. 'trust OR honest OR mistake'"
    )
    parser.add_argument(
        "--root", default=str(Path.home() / "session-post-office" / "latest")
    )
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument("--around", type=int, default=1)
    parser.add_argument("--role", choices=("user", "assistant"))
    parser.add_argument(
        "--snippets",
        action="store_true",
        help="Print a compact FTS excerpt instead of the complete matching message",
    )
    args = parser.parse_args()

    database = Path(args.root).resolve() / "messages.sqlite"
    con = sqlite3.connect(database)
    con.row_factory = sqlite3.Row
    role_sql = "AND m.role = ?" if args.role else ""
    params: list[object] = [args.query]
    if args.role:
        params.append(args.role)
    params.append(args.limit)
    hits = con.execute(
        f"""
        WITH unique_messages AS (SELECT MIN(id) AS id FROM messages GROUP BY text)
        SELECT m.*, snippet(messages_fts, 0, '<<', '>>', ' … ', 64) AS excerpt
        FROM messages_fts f
        JOIN messages m ON m.id = f.rowid
        JOIN unique_messages u ON u.id = m.id
        WHERE messages_fts MATCH ? {role_sql}
        ORDER BY bm25(messages_fts), m.timestamp
        LIMIT ?
        """,
        params,
    ).fetchall()

    print(f"# Query: {args.query}\n")
    print(f"Hits shown: {len(hits)}\n")
    emitted: set[int] = set()
    for hit in hits:
        neighbors = con.execute(
            """
            SELECT * FROM messages
            WHERE session_id = ? AND ordinal BETWEEN ? AND ?
            ORDER BY ordinal
            """,
            (
                hit["session_id"],
                hit["ordinal"] - args.around,
                hit["ordinal"] + args.around,
            ),
        ).fetchall()
        print(
            f"\n---\n\n## {hit['source']} {hit['session_id']} · turn {hit['ordinal']}\n"
        )
        if args.snippets:
            print(f"### {hit['role'].upper()} ★ · {hit['timestamp']}\n")
            print(hit["excerpt"].strip())
            print()
            continue
        for row in neighbors:
            if row["id"] in emitted:
                continue
            emitted.add(row["id"])
            marker = " ★" if row["id"] == hit["id"] else ""
            print(f"### {row['role'].upper()}{marker} · {row['timestamp']}\n")
            print(row["text"].strip())
            print()
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
