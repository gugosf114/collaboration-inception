#!/usr/bin/env python3
"""Gather topic-matched excerpts from every indexed session into one job folder."""

from __future__ import annotations

import argparse
import json
import re
import shlex
import sqlite3
from datetime import datetime
from pathlib import Path


def fts_query(terms: str) -> str:
    parts = shlex.split(terms)
    if not parts:
        raise ValueError("at least one search term is required")
    return " OR ".join(f'"{part.replace(chr(34), chr(34) * 2)}"' for part in parts)


def slug(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return cleaned[:48] or "gather"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Search every post-office session and create a per-session evidence package."
    )
    parser.add_argument(
        "terms",
        help="Words or quoted phrases, e.g. 'trust honesty \"speech block\"'. Terms are matched with OR.",
    )
    parser.add_argument("--label", help="Short job name; defaults to the search terms")
    parser.add_argument(
        "--root", default=str(Path.home() / "session-post-office" / "latest")
    )
    parser.add_argument("--limit-per-session", type=int, default=12)
    parser.add_argument("--role", choices=("user", "assistant"))
    parser.add_argument(
        "--fts", action="store_true", help="Treat terms as an exact SQLite FTS5 query"
    )
    args = parser.parse_args()

    root = Path(args.root).resolve()
    database = root / "messages.sqlite"
    if not database.exists():
        raise SystemExit(f"Missing index: {database}")

    query = args.terms if args.fts else fts_query(args.terms)
    label = args.label or args.terms
    job_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{slug(label)}"
    job = root / "jobs" / job_id
    per_session = job / "per-session"
    per_session.mkdir(parents=True)

    con = sqlite3.connect(database)
    con.row_factory = sqlite3.Row
    sessions = con.execute(
        "SELECT source, session_id, COUNT(*) AS messages FROM messages GROUP BY source, session_id ORDER BY source, session_id"
    ).fetchall()

    manifest: dict[str, object] = {
        "job_id": job_id,
        "created_at": datetime.now().astimezone().isoformat(),
        "terms": args.terms,
        "fts_query": query,
        "role": args.role,
        "limit_per_session": args.limit_per_session,
        "sessions": [],
    }
    combined = [
        f"# Gather job — {label}",
        "",
        f"- Job: `{job_id}`",
        f"- Terms: `{args.terms}`",
        f"- FTS query: `{query}`",
        f"- Role: `{args.role or 'user + assistant'}`",
        "",
        "These are retrieval excerpts, not conclusions. Expand only the turns that matter.",
    ]

    role_sql = "AND m.role = ?" if args.role else ""
    for session in sessions:
        params: list[object] = [query, session["session_id"]]
        if args.role:
            params.append(args.role)
        params.append(max(args.limit_per_session * 4, args.limit_per_session))
        rows = con.execute(
            f"""
            SELECT m.*, snippet(messages_fts, 0, '<<', '>>', ' … ', 96) AS excerpt,
                   bm25(messages_fts) AS score
            FROM messages_fts
            JOIN messages m ON m.id = messages_fts.rowid
            WHERE messages_fts MATCH ? AND m.session_id = ? {role_sql}
            ORDER BY score, m.timestamp
            LIMIT ?
            """,
            params,
        ).fetchall()

        unique: list[sqlite3.Row] = []
        seen: set[str] = set()
        for row in rows:
            normalized = row["text"].strip()
            if normalized in seen:
                continue
            seen.add(normalized)
            unique.append(row)
            if len(unique) >= args.limit_per_session:
                break

        filename = f"{session['source']}-{session['session_id']}.md"
        path = per_session / filename
        lines = [
            f"# {session['source']} {session['session_id']}",
            "",
            f"Matches: {len(unique)} of {session['messages']} indexed visible messages",
            "",
        ]
        for row in unique:
            lines.extend(
                [
                    "---",
                    "",
                    f"## {row['role']} turn {row['ordinal']} — {row['timestamp']}",
                    "",
                    row["excerpt"].strip(),
                    "",
                ]
            )
        path.write_text("\n".join(lines), encoding="utf-8")
        combined.extend(
            [
                "",
                f"## {session['source']} `{session['session_id']}` — {len(unique)} matches",
                "",
                f"[Per-session package](per-session/{filename})",
            ]
        )
        for row in unique:
            combined.extend(
                [
                    "",
                    f"- **{row['role']} turn {row['ordinal']}** · {row['timestamp']}",
                    f"  {row['excerpt'].strip()}",
                ]
            )
        manifest["sessions"].append(
            {
                "source": session["source"],
                "session_id": session["session_id"],
                "indexed_messages": session["messages"],
                "matches": len(unique),
                "file": str(path),
            }
        )

    con.close()
    (job / "RESULTS.md").write_text("\n".join(combined) + "\n", encoding="utf-8")
    (job / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (job / "REQUEST.md").write_text(
        f"# Broadcast-style gather request\n\nTerms: `{args.terms}`\n\nFTS query: `{query}`\n",
        encoding="utf-8",
    )
    latest = root / "jobs" / "latest"
    latest.unlink(missing_ok=True)
    latest.symlink_to(job.name)
    print(f"Job: {job}")
    print(f"Sessions: {len(sessions)}")
    print(f"Results: {job / 'RESULTS.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
