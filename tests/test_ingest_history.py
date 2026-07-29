from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.ingest_history import extract, load_messages
from scripts.operating_room import RelationshipLedger


class HistoryIngestTests(unittest.TestCase):
    def test_extracts_source_grounded_correction_with_counterevidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            messages_path = root / "messages.jsonl"
            rows = [
                {
                    "source": "codex",
                    "session_id": "one",
                    "ordinal": 1,
                    "timestamp": "2026-07-01T10:00:00Z",
                    "role": "assistant",
                    "text": "I will give George commands to run.",
                },
                {
                    "source": "codex",
                    "session_id": "one",
                    "ordinal": 2,
                    "timestamp": "2026-07-01T10:01:00Z",
                    "role": "user",
                    "text": "No, you misunderstood the request. Do the reachable work.",
                },
                {
                    "source": "codex",
                    "session_id": "one",
                    "ordinal": 3,
                    "timestamp": "2026-07-01T10:02:00Z",
                    "role": "assistant",
                    "text": "I performed the work and verified the output.",
                },
                {
                    "source": "codex",
                    "session_id": "one",
                    "ordinal": 4,
                    "timestamp": "2026-07-01T10:03:00Z",
                    "role": "user",
                    "text": "Actually, ask me when payment or legal truth is involved.",
                },
            ]
            messages_path.write_text(
                "".join(json.dumps(row) + "\n" for row in rows),
                encoding="utf-8",
            )
            ledger = RelationshipLedger(root / "relationship.sqlite3")
            try:
                report = extract(load_messages([messages_path]), ledger)
                self.assertEqual(report["inserted"], 1)
                episode = ledger.connection.execute(
                    "SELECT * FROM episodes"
                ).fetchone()
                self.assertEqual(episode["kind"], "correction")
                self.assertIn("Do the reachable work", episode["inference"])
                self.assertIn("payment", episode["counterevidence"])
                packet = ledger.packet_for("reachable legal payment work")
                self.assertIn("EVIDENCE", packet)
                self.assertIn("COUNTEREVIDENCE", packet)
            finally:
                ledger.close()

    def test_reingestion_is_idempotent(self) -> None:
        rows = [
            {
                "source": "claude",
                "session_id": "two",
                "ordinal": 1,
                "role": "assistant",
                "text": "Would you like me to continue?",
            },
            {
                "source": "claude",
                "session_id": "two",
                "ordinal": 2,
                "role": "user",
                "text": "That is wrong. Continue through the obvious next step.",
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "messages.jsonl"
            path.write_text(
                "".join(json.dumps(row) + "\n" for row in rows),
                encoding="utf-8",
            )
            ledger = RelationshipLedger(root / "ledger.sqlite3")
            try:
                messages = load_messages([path])
                self.assertEqual(extract(messages, ledger)["inserted"], 1)
                report = extract(messages, ledger)
                self.assertEqual(report["inserted"], 0)
                self.assertEqual(report["duplicates"], 1)
            finally:
                ledger.close()


if __name__ == "__main__":
    unittest.main()
