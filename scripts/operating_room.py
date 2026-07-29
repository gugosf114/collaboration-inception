#!/usr/bin/env python3
"""Durable operating-room services for the Inception cockpit.

This module deliberately uses only Python's standard library.  The cockpit can
therefore carry the same relationship ledger, proof arena, replay records, and
surface adapters on Termux, Linux, macOS, and native Windows PowerShell.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


PROJECT = Path(__file__).resolve().parents[1]
RUNTIME = PROJECT / "runtime"
DEFAULT_LEDGER_PATH = RUNTIME / "relationship.sqlite3"
DEFAULT_ARENA_ROOT = RUNTIME / "arena"
DEFAULT_SURFACE_ROOT = RUNTIME / "surfaces"
MAX_SHARED_FILE_BYTES = 25 * 1024 * 1024
IMAGE_SUFFIXES = frozenset({".gif", ".jpeg", ".jpg", ".png", ".webp"})
MODEL_PROVIDERS = frozenset({"antigravity", "claude", "codex"})

CORRECTION_PATTERNS = (
    re.compile(r"\b(that(?:'s| is) (?:wrong|not what i asked))\b", re.I),
    re.compile(r"\b(you (?:missed|forgot|invented|misunderstood|overcomplicated))\b", re.I),
    re.compile(r"\b(no[,— -]+(?:i said|the point is|listen|stop))\b", re.I),
    re.compile(r"\b(correction|that is false|you are wrong)\b", re.I),
)
PROMISE_PATTERN = re.compile(
    r"(?:^|(?<=[.!?])\s+)"
    r"((?:I(?:'ll| will| am going to)|Next I(?:'ll| will))\s+"
    r"[^.!?\n]{8,260}[.!?]?)",
    re.I,
)
WORD_RE = re.compile(r"[a-z0-9][a-z0-9'-]{2,}", re.I)
STOP_WORDS = frozenset(
    {
        "about",
        "after",
        "again",
        "also",
        "because",
        "before",
        "being",
        "could",
        "does",
        "from",
        "have",
        "here",
        "into",
        "just",
        "make",
        "more",
        "need",
        "only",
        "other",
        "please",
        "really",
        "should",
        "some",
        "something",
        "that",
        "their",
        "then",
        "there",
        "these",
        "they",
        "thing",
        "this",
        "those",
        "want",
        "what",
        "when",
        "where",
        "which",
        "with",
        "would",
        "your",
    }
)


class OperatingRoomError(RuntimeError):
    """A durable cockpit service could not complete its operation."""


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def compact(text: str, limit: int) -> str:
    value = re.sub(r"\s+", " ", text).strip()
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"


def terms(text: str) -> set[str]:
    return {
        word.lower()
        for word in WORD_RE.findall(text)
        if word.lower() not in STOP_WORDS
    }


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
        temporary = Path(handle.name)
    with contextlib_chmod(temporary, 0o600):
        pass
    temporary.replace(path)


class contextlib_chmod:
    """Best-effort chmod context that remains harmless on Windows."""

    def __init__(self, path: Path, mode: int):
        self.path = path
        self.mode = mode

    def __enter__(self) -> None:
        try:
            self.path.chmod(self.mode)
        except OSError:
            pass

    def __exit__(self, *_: object) -> None:
        return None


class RelationshipLedger:
    """Inspectable evidence, promises, outcomes, skill, and trajectory state."""

    def __init__(self, path: Path = DEFAULT_LEDGER_PATH):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self._create_schema()
        with contextlib_chmod(self.path, 0o600):
            pass

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;

            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY,
                at TEXT NOT NULL,
                kind TEXT NOT NULL,
                agent TEXT,
                category TEXT NOT NULL DEFAULT 'general',
                confidence REAL NOT NULL DEFAULT 1.0,
                text TEXT NOT NULL,
                data_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS events_kind_at
                ON events(kind, at DESC);
            CREATE INDEX IF NOT EXISTS events_agent_at
                ON events(agent, at DESC);

            CREATE TABLE IF NOT EXISTS promises (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                agent TEXT NOT NULL,
                text TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                confidence REAL NOT NULL DEFAULT 1.0,
                source_turn_id TEXT,
                completed_at TEXT,
                completion_note TEXT
            );
            CREATE INDEX IF NOT EXISTS promises_status
                ON promises(status, created_at DESC);

            CREATE TABLE IF NOT EXISTS outcomes (
                id TEXT PRIMARY KEY,
                at TEXT NOT NULL,
                agent TEXT NOT NULL,
                category TEXT NOT NULL,
                verdict TEXT NOT NULL,
                confidence REAL NOT NULL DEFAULT 1.0,
                prediction TEXT NOT NULL DEFAULT '',
                falsifier TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT '',
                run_id TEXT
            );

            CREATE TABLE IF NOT EXISTS episodes (
                id TEXT PRIMARY KEY,
                at TEXT NOT NULL,
                source TEXT NOT NULL,
                session_id TEXT,
                ordinal INTEGER,
                kind TEXT NOT NULL,
                agent TEXT,
                category TEXT NOT NULL DEFAULT 'general',
                confidence REAL NOT NULL,
                source_exchange_json TEXT NOT NULL,
                inference TEXT NOT NULL,
                counterevidence TEXT NOT NULL DEFAULT '',
                useful_behavior TEXT NOT NULL,
                hypothesis_id TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                source_hash TEXT NOT NULL UNIQUE
            );
            CREATE INDEX IF NOT EXISTS episodes_kind_at
                ON episodes(kind, at DESC);
            CREATE INDEX IF NOT EXISTS episodes_status_at
                ON episodes(status, at DESC);

            CREATE TABLE IF NOT EXISTS missions (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                text TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                source TEXT NOT NULL DEFAULT 'explicit',
                completed_at TEXT,
                completion_note TEXT
            );
            CREATE INDEX IF NOT EXISTS missions_status
                ON missions(status, created_at DESC);

            CREATE TABLE IF NOT EXISTS skills (
                agent TEXT NOT NULL,
                category TEXT NOT NULL,
                successes REAL NOT NULL DEFAULT 0,
                failures REAL NOT NULL DEFAULT 0,
                mixed REAL NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(agent, category)
            );

            CREATE TABLE IF NOT EXISTS trajectories (
                id TEXT PRIMARY KEY,
                at TEXT NOT NULL,
                agent TEXT NOT NULL,
                session_id TEXT,
                reason TEXT NOT NULL,
                drift_json TEXT NOT NULL
            );
            """
        )
        self._ensure_column("outcomes", "recommendation", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column(
            "outcomes", "calibration_error", "REAL NOT NULL DEFAULT 0"
        )
        self.connection.commit()

    def _ensure_column(self, table: str, column: str, declaration: str) -> None:
        columns = {
            row["name"]
            for row in self.connection.execute(f"PRAGMA table_info({table})")
        }
        if column not in columns:
            self.connection.execute(
                f"ALTER TABLE {table} ADD COLUMN {column} {declaration}"
            )

    def close(self) -> None:
        self.connection.close()

    def record_event(
        self,
        kind: str,
        text: str,
        *,
        agent: str | None = None,
        category: str = "general",
        confidence: float = 1.0,
        data: Mapping[str, Any] | None = None,
        event_id: str | None = None,
    ) -> str:
        identifier = event_id or uuid.uuid4().hex
        self.connection.execute(
            """
            INSERT INTO events
                (id, at, kind, agent, category, confidence, text, data_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                identifier,
                now_iso(),
                kind,
                agent,
                category or "general",
                max(0.0, min(1.0, confidence)),
                compact(text, 12_000),
                json.dumps(dict(data or {}), ensure_ascii=False),
            ),
        )
        self.connection.commit()
        return identifier

    def observe_operator(
        self, text: str, recent_agents: Sequence[str] = ()
    ) -> list[str]:
        """Record natural corrections as provisional evidence, never as certainty."""
        matched = [pattern.search(text) for pattern in CORRECTION_PATTERNS]
        if not any(matched):
            return []
        agents = [agent for agent in recent_agents if agent in MODEL_PROVIDERS]
        if not agents:
            agents = [None]
        identifiers = []
        for agent in dict.fromkeys(agents):
            identifiers.append(
                self.record_event(
                    "correction",
                    text,
                    agent=agent,
                    confidence=0.68,
                    data={"source": "natural-language heuristic"},
                )
            )
        return identifiers

    def add_correction(self, agent: str, text: str) -> str:
        self._require_agent(agent)
        return self.record_event(
            "correction",
            text,
            agent=agent,
            confidence=1.0,
            data={"source": "explicit George command"},
        )

    def observe_answer(
        self,
        agent: str,
        text: str,
        *,
        turn_id: str | None = None,
        category: str = "general",
    ) -> list[str]:
        self._require_agent(agent)
        self.record_event(
            "answer",
            text,
            agent=agent,
            category=category,
            data={"turn_id": turn_id},
        )
        created: list[str] = []
        for match in PROMISE_PATTERN.finditer(text):
            promise = compact(match.group(1), 320)
            duplicate = self.connection.execute(
                """
                SELECT id FROM promises
                WHERE agent=? AND status='open' AND lower(text)=lower(?)
                LIMIT 1
                """,
                (agent, promise),
            ).fetchone()
            if duplicate:
                continue
            created.append(
                self.add_promise(
                    agent,
                    promise,
                    confidence=0.62,
                    source_turn_id=turn_id,
                )
            )
        return created

    def add_promise(
        self,
        agent: str,
        text: str,
        *,
        confidence: float = 1.0,
        source_turn_id: str | None = None,
    ) -> str:
        self._require_agent(agent)
        identifier = uuid.uuid4().hex[:12]
        self.connection.execute(
            """
            INSERT INTO promises
                (id, created_at, agent, text, confidence, source_turn_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                identifier,
                now_iso(),
                agent,
                compact(text, 600),
                max(0.0, min(1.0, confidence)),
                source_turn_id,
            ),
        )
        self.connection.commit()
        return identifier

    def resolve_promise(self, identifier: str, note: str = "") -> None:
        changed = self.connection.execute(
            """
            UPDATE promises
            SET status='done', completed_at=?, completion_note=?
            WHERE id=? AND status='open'
            """,
            (now_iso(), compact(note, 600), identifier),
        ).rowcount
        self.connection.commit()
        if changed != 1:
            raise OperatingRoomError(f"Open promise {identifier!r} was not found")

    def record_outcome(
        self,
        agent: str,
        category: str,
        verdict: str,
        note: str = "",
        *,
        confidence: float = 1.0,
        prediction: str = "",
        falsifier: str = "",
        recommendation: str = "",
        run_id: str | None = None,
    ) -> str:
        self._require_agent(agent)
        normalized = verdict.lower().strip()
        if normalized not in {"success", "failure", "mixed"}:
            raise OperatingRoomError("Outcome must be success, failure, or mixed")
        category = compact(category or "general", 80).lower()
        identifier = uuid.uuid4().hex[:12]
        at = now_iso()
        actual = {"success": 1.0, "failure": 0.0, "mixed": 0.5}[normalized]
        calibration_error = abs(max(0.0, min(1.0, confidence)) - actual)
        self.connection.execute(
            """
            INSERT INTO outcomes
                (id, at, agent, category, verdict, confidence,
                 prediction, falsifier, note, run_id, recommendation,
                 calibration_error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                identifier,
                at,
                agent,
                category,
                normalized,
                max(0.0, min(1.0, confidence)),
                compact(prediction, 1_000),
                compact(falsifier, 1_000),
                compact(note, 2_000),
                run_id,
                compact(recommendation, 1_000),
                calibration_error,
            ),
        )
        self.connection.execute(
            """
            INSERT INTO skills(agent, category, successes, failures, mixed, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(agent, category) DO UPDATE SET
                successes=successes+excluded.successes,
                failures=failures+excluded.failures,
                mixed=mixed+excluded.mixed,
                updated_at=excluded.updated_at
            """,
            (
                agent,
                category,
                1.0 if normalized == "success" else 0.0,
                1.0 if normalized == "failure" else 0.0,
                1.0 if normalized == "mixed" else 0.0,
                at,
            ),
        )
        self.record_event(
            "outcome",
            note or normalized,
            agent=agent,
            category=category,
            confidence=confidence,
            data={
                "verdict": normalized,
                "prediction": prediction,
                "falsifier": falsifier,
                "recommendation": recommendation,
                "calibration_error": calibration_error,
                "run_id": run_id,
            },
            event_id=f"outcome-{identifier}",
        )
        self.connection.commit()
        return identifier

    def record_episode(
        self,
        *,
        source: str,
        session_id: str | None,
        ordinal: int | None,
        kind: str,
        agent: str | None,
        category: str,
        confidence: float,
        source_exchange: Mapping[str, Any],
        inference: str,
        counterevidence: str,
        useful_behavior: str,
        source_hash: str,
        hypothesis_id: str | None = None,
        at: str | None = None,
    ) -> str | None:
        if agent is not None:
            self._require_agent(agent)
        normalized_kind = compact(kind.lower() or "evidence", 60)
        identifier = uuid.uuid4().hex[:12]
        try:
            self.connection.execute(
                """
                INSERT INTO episodes(
                    id, at, source, session_id, ordinal, kind, agent, category,
                    confidence, source_exchange_json, inference, counterevidence,
                    useful_behavior, hypothesis_id, source_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    identifier,
                    at or now_iso(),
                    compact(source, 200),
                    compact(session_id or "", 200) or None,
                    ordinal,
                    normalized_kind,
                    agent,
                    compact(category or "general", 80).lower(),
                    max(0.0, min(1.0, confidence)),
                    json.dumps(dict(source_exchange), ensure_ascii=False),
                    compact(inference, 2_000),
                    compact(counterevidence, 2_000),
                    compact(useful_behavior, 2_000),
                    compact(hypothesis_id or "", 120) or None,
                    source_hash,
                ),
            )
        except sqlite3.IntegrityError:
            return None
        self.connection.commit()
        return identifier

    def challenge_episode(self, identifier: str, counterevidence: str) -> None:
        changed = self.connection.execute(
            """
            UPDATE episodes
            SET counterevidence=?, status='challenged'
            WHERE id=?
            """,
            (compact(counterevidence, 2_000), identifier),
        ).rowcount
        self.connection.commit()
        if changed != 1:
            raise OperatingRoomError(f"Evidence episode {identifier!r} was not found")

    def set_mission(self, text: str, source: str = "explicit") -> str:
        clean = compact(text, 2_000)
        if not clean:
            raise OperatingRoomError("A mission cannot be empty")
        self.connection.execute(
            """
            UPDATE missions
            SET status='superseded', completed_at=?, completion_note='Superseded'
            WHERE status='active'
            """,
            (now_iso(),),
        )
        identifier = uuid.uuid4().hex[:12]
        self.connection.execute(
            """
            INSERT INTO missions(id, created_at, text, source)
            VALUES (?, ?, ?, ?)
            """,
            (identifier, now_iso(), clean, compact(source, 80)),
        )
        self.connection.commit()
        return identifier

    def active_mission(self) -> dict[str, Any] | None:
        row = self.connection.execute(
            """
            SELECT id, created_at, text, source
            FROM missions WHERE status='active'
            ORDER BY created_at DESC, rowid DESC LIMIT 1
            """
        ).fetchone()
        return dict(row) if row else None

    def complete_mission(self, note: str = "") -> None:
        changed = self.connection.execute(
            """
            UPDATE missions
            SET status='done', completed_at=?, completion_note=?
            WHERE status='active'
            """,
            (now_iso(), compact(note, 1_000)),
        ).rowcount
        self.connection.commit()
        if changed != 1:
            raise OperatingRoomError("There is no active mission")

    def authority(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT agent, category, successes, failures, mixed, updated_at
            FROM skills
            ORDER BY (successes + failures + mixed) DESC, agent, category
            """
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            evidence = row["successes"] + row["failures"] + row["mixed"]
            # Beta(1,1) prior prevents one result from becoming permanent authority.
            score = (row["successes"] + (0.5 * row["mixed"]) + 1) / (evidence + 2)
            result.append({**dict(row), "evidence": evidence, "score": score})
        return result

    def drift(self, agent: str) -> dict[str, Any]:
        self._require_agent(agent)
        corrections = self.connection.execute(
            """
            SELECT COUNT(*) FROM (
                SELECT 1 FROM events
                WHERE kind='correction' AND (agent=? OR agent IS NULL)
                ORDER BY at DESC LIMIT 20
            )
            """,
            (agent,),
        ).fetchone()[0]
        failures = self.connection.execute(
            """
            SELECT COUNT(*) FROM (
                SELECT 1 FROM outcomes
                WHERE agent=? AND verdict='failure'
                ORDER BY at DESC LIMIT 10
            )
            """,
            (agent,),
        ).fetchone()[0]
        recent_answers = [
            row[0]
            for row in self.connection.execute(
                """
                SELECT text FROM events
                WHERE kind='answer' AND agent=?
                ORDER BY at DESC LIMIT 8
                """,
                (agent,),
            )
        ]
        generic_markers = (
            "let me know if",
            "would you like me to",
            "i'd be happy to",
            "as an ai",
            "great question",
        )
        generic = sum(
            marker in answer.lower()
            for answer in recent_answers
            for marker in generic_markers
        )
        trajectory_markers = {
            "generic": generic_markers,
            "flattering": (
                "you are absolutely right",
                "brilliant",
                "great question",
                "excellent point",
            ),
            "defensive": (
                "to be fair",
                "i was trying to",
                "my intention was",
                "however, i",
            ),
            "argumentative": (
                "you are mistaken",
                "i disagree with your premise",
                "as i already explained",
            ),
            "reckless": (
                "no need to verify",
                "definitely complete",
                "guaranteed",
                "cannot fail",
            ),
            "rabbit-hole": (
                "comprehensive taxonomy",
                "complete list of",
                "all possible",
                "one more improvement",
            ),
        }
        signals = {
            label: sum(
                marker in answer.lower()
                for answer in recent_answers
                for marker in markers
            )
            for label, markers in trajectory_markers.items()
        }
        score = min(
            100,
            (corrections * 18)
            + (failures * 22)
            + sum(signals.values()) * 10,
        )
        if score >= 65:
            state = "off"
        elif score >= 35:
            state = "drifting"
        else:
            state = "productive or insufficient evidence"
        strongest = max(signals, key=signals.get)
        if signals[strongest] > 0 and state != "productive or insufficient evidence":
            state = strongest
        return {
            "agent": agent,
            "score": score,
            "state": state,
            "recent_corrections": corrections,
            "recent_failures": failures,
            "generic_markers": generic,
            "trajectory_signals": signals,
        }

    def snapshot_trajectory(
        self, agent: str, reason: str, session_id: str | None
    ) -> str:
        self._require_agent(agent)
        identifier = uuid.uuid4().hex[:12]
        self.connection.execute(
            """
            INSERT INTO trajectories(id, at, agent, session_id, reason, drift_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                identifier,
                now_iso(),
                agent,
                session_id,
                compact(reason, 2_000),
                json.dumps(self.drift(agent), ensure_ascii=False),
            ),
        )
        self.connection.commit()
        return identifier

    def packet_for(self, prompt: str, limit: int = 3, max_chars: int = 2_400) -> str:
        query = terms(prompt)
        rows = self.connection.execute(
            """
            SELECT id, at, kind, agent, category, confidence, text, data_json
            FROM events
            WHERE kind IN ('correction', 'outcome', 'trajectory', 'decision')
            ORDER BY at DESC LIMIT 400
            """
        ).fetchall()
        scored: list[tuple[float, sqlite3.Row]] = []
        for position, row in enumerate(rows):
            overlap = query & terms(f"{row['category']} {row['text']}")
            if not overlap and row["kind"] not in {"correction", "outcome"}:
                continue
            bonus = 5 if row["kind"] == "correction" else 3
            recency = max(0.0, 1.0 - (position / max(1, len(rows))))
            score = (4 * len(overlap)) + bonus + recency + row["confidence"]
            scored.append((score, row))
        selected = [
            row
            for _, row in sorted(scored, key=lambda item: item[0], reverse=True)[
                :limit
            ]
        ]
        promises = self.connection.execute(
            """
                SELECT id, agent, text, confidence FROM promises
                WHERE status='open'
                ORDER BY created_at DESC, rowid DESC LIMIT 3
            """
        ).fetchall()
        episode_rows = self.connection.execute(
            """
            SELECT id, at, kind, agent, category, confidence, inference,
                   counterevidence, useful_behavior, status
            FROM episodes
            WHERE status IN ('active', 'challenged')
            ORDER BY at DESC, rowid DESC LIMIT 800
            """
        ).fetchall()
        episode_scored: list[tuple[float, sqlite3.Row]] = []
        for position, row in enumerate(episode_rows):
            searchable = (
                f"{row['category']} {row['inference']} {row['useful_behavior']} "
                f"{row['counterevidence']}"
            )
            overlap = query & terms(searchable)
            if not overlap:
                continue
            score = (
                (5 * len(overlap))
                + row["confidence"]
                + max(0.0, 1.0 - (position / max(1, len(episode_rows))))
            )
            episode_scored.append((score, row))
        selected_episodes = [
            row
            for _, row in sorted(
                episode_scored, key=lambda item: item[0], reverse=True
            )[:limit]
        ]
        sections: list[str] = []
        mission = self.active_mission()
        if mission:
            sections.append(f"ACTIVE MISSION: {compact(mission['text'], 700)}")
        for row in selected_episodes:
            status = (
                " CHALLENGED—reconcile before applying"
                if row["status"] == "challenged"
                else ""
            )
            challenged = (
                f" COUNTEREVIDENCE: {compact(row['counterevidence'], 350)}"
                if row["counterevidence"]
                else ""
            )
            sections.append(
                f"EVIDENCE {row['id']} {row['kind'].upper()}{status}, confidence "
                f"{row['confidence']:.2f}: {compact(row['inference'], 500)} "
                f"FUTURE BEHAVIOR: {compact(row['useful_behavior'], 450)}"
                f"{challenged}"
            )
        for row in selected:
            agent = f" ({row['agent']})" if row["agent"] else ""
            sections.append(
                f"{row['kind'].upper()}{agent}, confidence "
                f"{row['confidence']:.2f}: {compact(row['text'], 650)}"
            )
        for row in promises:
            sections.append(
                f"OPEN PROMISE {row['id']} ({row['agent']}, confidence "
                f"{row['confidence']:.2f}): {compact(row['text'], 500)}"
            )
        return compact("\n".join(sections), max_chars)

    def summary(self) -> dict[str, Any]:
        promises = [
            dict(row)
            for row in self.connection.execute(
                """
                SELECT id, created_at, agent, text, confidence
                FROM promises WHERE status='open'
                ORDER BY created_at DESC, rowid DESC LIMIT 20
                """
            )
        ]
        corrections = [
            dict(row)
            for row in self.connection.execute(
                """
                SELECT at, agent, confidence, text
                FROM events WHERE kind='correction'
                ORDER BY at DESC, rowid DESC LIMIT 10
                """
            )
        ]
        outcomes = [
            dict(row)
            for row in self.connection.execute(
                """
                SELECT at, agent, category, verdict, confidence, prediction,
                       recommendation, falsifier, calibration_error, note
                FROM outcomes ORDER BY at DESC, rowid DESC LIMIT 10
                """
            )
        ]
        episodes = [
            dict(row)
            for row in self.connection.execute(
                """
                SELECT id, at, kind, agent, category, confidence, inference,
                       counterevidence, useful_behavior, status
                FROM episodes
                ORDER BY at DESC, rowid DESC LIMIT 20
                """
            )
        ]
        return {
            "active_mission": self.active_mission(),
            "recent_episodes": episodes,
            "open_promises": promises,
            "recent_corrections": corrections,
            "recent_outcomes": outcomes,
            "authority": self.authority(),
            "drift": {
                agent: self.drift(agent) for agent in sorted(MODEL_PROVIDERS)
            },
        }

    @staticmethod
    def _require_agent(agent: str) -> None:
        if agent not in MODEL_PROVIDERS:
            raise OperatingRoomError(
                "Agent must be antigravity, claude, or codex"
            )


def deterministic_objections(prompt: str, draft: str) -> list[str]:
    """Fast proof/flattery/scope checks used alongside the other model."""
    objections: list[str] = []
    lowered = draft.lower()
    if not draft.strip():
        return ["The answer is empty."]
    completion_words = ("done", "fixed", "working", "works", "passes", "shipped")
    evidence_words = (
        "test",
        "verified",
        "checked",
        "commit",
        "output",
        "screenshot",
        "result",
        "live",
    )
    if any(word in lowered for word in completion_words) and not any(
        word in lowered for word in evidence_words
    ):
        objections.append("It claims completion without naming proof.")
    flattering = (
        "great question",
        "brilliant",
        "you are absolutely right",
        "i'd be happy to",
        "excellent idea",
    )
    if any(phrase in lowered for phrase in flattering):
        objections.append("It contains praise that does not advance the work.")
    if re.search(r"\b(want me to|would you like me to|shall i)\b", lowered):
        objections.append("It ends with a hollow continuation question.")
    if lowered.count("\n#") + int(lowered.startswith("#")) >= 5:
        objections.append("It is over-structured for a direct answer.")
    if re.search(r"\b(i cannot|i can't)\b", lowered) and not re.search(
        r"\b(tried|error|blocked|because)\b", lowered
    ):
        objections.append("It refuses without evidence of the actual blocker.")
    if "probably" in lowered and any(word in lowered for word in completion_words):
        objections.append("Its completion claim is internally uncertain.")
    if len(draft) > 8_000 and len(prompt) < 800:
        objections.append("It is probably much longer than the request requires.")
    return objections


@dataclass
class SurfaceArtifact:
    kind: str
    path: Path
    metadata: dict[str, Any] = field(default_factory=dict)


class SurfaceHub:
    """Cross-platform screen, browser, file, and speech adapters."""

    def __init__(
        self,
        root: Path = DEFAULT_SURFACE_ROOT,
        project: Path = PROJECT,
        env: Mapping[str, str] | None = None,
    ):
        self.root = root
        self.project = project
        self.env = dict(os.environ if env is None else env)
        self.root.mkdir(parents=True, exist_ok=True)
        with contextlib_chmod(self.root, 0o700):
            pass

    def stage_file(self, value: str, base: Path) -> SurfaceArtifact:
        source = Path(value).expanduser()
        if not source.is_absolute():
            source = base / source
        try:
            source = source.resolve(strict=True)
        except OSError as exc:
            raise OperatingRoomError(f"Cannot find shared file {value!r}: {exc}") from exc
        if not source.is_file():
            raise OperatingRoomError(f"Shared path is not a file: {source}")
        size = source.stat().st_size
        if size <= 0 or size > MAX_SHARED_FILE_BYTES:
            raise OperatingRoomError(
                f"Shared file must be between 1 byte and "
                f"{MAX_SHARED_FILE_BYTES // 1024 // 1024} MiB"
            )
        destination = self._destination("file", source.suffix.lower())
        shutil.copy2(source, destination)
        with contextlib_chmod(destination, 0o600):
            pass
        return SurfaceArtifact(
            "image" if source.suffix.lower() in IMAGE_SUFFIXES else "file",
            destination,
            {"source_name": source.name, "bytes": size},
        )

    def capture_screen(self) -> SurfaceArtifact:
        output = self._destination("screen", ".png")
        custom = self.env.get("INCEPTION_SCREEN_CAPTURE_COMMAND")
        if custom:
            self._run_template(custom, output=output)
            return self._validated_png("screen", output, {"adapter": "custom"})

        adb = shutil.which("adb")
        if adb:
            devices = subprocess.run(
                [adb, "devices"],
                capture_output=True,
                timeout=10,
                check=False,
            )
            if b"\tdevice" in devices.stdout:
                captured = subprocess.run(
                    [adb, "exec-out", "screencap", "-p"],
                    capture_output=True,
                    timeout=30,
                    check=False,
                )
                if captured.returncode == 0 and captured.stdout.startswith(b"\x89PNG"):
                    output.write_bytes(captured.stdout)
                    return self._validated_png(
                        "screen", output, {"adapter": "adb"}
                    )

        screencap = Path("/system/bin/screencap")
        if screencap.is_file():
            captured = subprocess.run(
                [str(screencap), "-p", str(output)],
                capture_output=True,
                timeout=30,
                check=False,
            )
            if captured.returncode == 0 and output.is_file():
                try:
                    return self._validated_png(
                        "screen", output, {"adapter": "android-screencap"}
                    )
                except OperatingRoomError:
                    # Ordinary Android apps often cannot use screencap. Continue
                    # to the user-taken screenshot fallback.
                    pass

        if os.name == "nt":
            script = self.project / "scripts" / "capture_windows_screen.ps1"
            completed = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(script),
                    "-OutputPath",
                    str(output),
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if completed.returncode == 0 and output.is_file():
                return self._validated_png(
                    "screen", output, {"adapter": "windows-powershell"}
                )

        remote_problems: list[str] = []
        bridge_url = self._agent_bridge_url()
        if bridge_url:
            try:
                return self._agent_bridge_screen(output, bridge_url)
            except OperatingRoomError as exc:
                remote_problems.append(str(exc))

        remote_host = self._browser_ssh_host()
        if remote_host:
            try:
                return self._remote_windows_screen(output, remote_host)
            except OperatingRoomError as exc:
                remote_problems.append(str(exc))

        latest = self._latest_android_screenshot()
        if latest:
            age_seconds = max(0.0, time.time() - latest.stat().st_mtime)
            if age_seconds > 120:
                raise OperatingRoomError(
                    "Android blocked direct screen capture. Take a screenshot "
                    "now, then run /screen again."
                    + (
                        " Configured remote screen routes also failed: "
                        + " | ".join(remote_problems)
                        if remote_problems
                        else ""
                    )
                )
            if latest.suffix.lower() == ".png":
                shutil.copy2(latest, output)
            else:
                converter = shutil.which("magick") or shutil.which("convert")
                if not converter:
                    raise OperatingRoomError(
                        "The newest Android screenshot is JPEG and ImageMagick "
                        "is unavailable to convert it."
                    )
                converted = subprocess.run(
                    [converter, str(latest), str(output)],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
                if converted.returncode != 0:
                    detail = converted.stderr.strip() or converted.stdout.strip()
                    raise OperatingRoomError(
                        f"Cannot convert the Android screenshot: {detail}"
                    )
            return self._validated_png(
                "screen",
                output,
                {
                    "adapter": "latest-screenshot",
                    "source": str(latest),
                    "age_seconds": round(age_seconds, 1),
                },
            )
        raise OperatingRoomError(
            "No screen adapter worked. Connect adb, take a phone screenshot, "
            "connect Agent Bridge, or set INCEPTION_SCREEN_CAPTURE_COMMAND."
            + (
                " Configured remote routes failed: " + " | ".join(remote_problems)
                if remote_problems
                else ""
            )
        )

    def _agent_bridge_url(self) -> str | None:
        """Find an already-configured Agent Bridge without baking in credentials."""
        for key in ("INCEPTION_AGENT_BRIDGE_URL", "BOX_URL"):
            value = self.env.get(key, "").strip()
            if value:
                return self._validated_http_base(value, key)

        config = Path.home() / ".config" / "agent-bridge" / "bridge.env"
        try:
            lines = config.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
        for line in lines:
            match = re.match(
                r"""^\s*(?:export\s+)?BOX_URL\s*=\s*(['"]?)(.*?)\1\s*$""",
                line,
            )
            if match and match.group(2).strip():
                return self._validated_http_base(
                    match.group(2).strip(), f"{config}:BOX_URL"
                )

        # George's existing bridge predates bridge.env. Read its public default
        # only when that sibling project is actually present.
        legacy = Path.home() / "agent-bridge" / "scripts" / "laptop_agent.py"
        try:
            source = legacy.read_text(encoding="utf-8")
        except OSError:
            return None
        match = re.search(
            r"""BOX_URL\s*=\s*os\.environ\.get\(\s*["']BOX_URL["']\s*,\s*["']([^"']+)["']""",
            source,
        )
        if not match:
            return None
        return self._validated_http_base(match.group(1), str(legacy))

    @staticmethod
    def _validated_http_base(value: str, source: str) -> str:
        parsed = urllib.parse.urlsplit(value.rstrip("/"))
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise OperatingRoomError(
                f"{source} must be an http:// or https:// base URL"
            )
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise OperatingRoomError(
                f"{source} must not contain credentials, a query, or a fragment"
            )
        return urllib.parse.urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "")
        )

    def _agent_bridge_screen(
        self, output: Path, base_url: str
    ) -> SurfaceArtifact:
        """Ask the interactive laptop agent for a real desktop screenshot."""
        try:
            pending = self._bridge_json("GET", f"{base_url}/dash/laptop/jobs")
        except (OSError, ValueError) as exc:
            raise OperatingRoomError(f"Agent Bridge is unreachable: {exc}") from exc
        if isinstance(pending, dict) and pending.get("id"):
            raise OperatingRoomError(
                f"Agent Bridge laptop is busy with job {pending['id']}"
            )

        job_id = f"inception-screen-{uuid.uuid4().hex}"
        job = {
            "id": job_id,
            "verb": "screenshot",
            "args": {},
            "ts": int(time.time()),
        }
        try:
            self._bridge_json(
                "POST", f"{base_url}/dash/laptop/job", payload=job
            )
        except (OSError, ValueError) as exc:
            raise OperatingRoomError(
                f"Agent Bridge rejected the screen request: {exc}"
            ) from exc

        deadline = time.monotonic() + 20
        result: Any = {}
        while time.monotonic() < deadline:
            time.sleep(0.25)
            try:
                result = self._bridge_json(
                    "GET", f"{base_url}/dash/laptop/result"
                )
            except (OSError, ValueError):
                continue
            if isinstance(result, dict) and result.get("id") == job_id:
                break
        else:
            raise OperatingRoomError(
                "Agent Bridge laptop did not answer the screen request in 20 seconds"
            )

        if not result.get("ok"):
            detail = str(result.get("output") or "unknown laptop-agent error")
            raise OperatingRoomError(f"Agent Bridge screen failed: {detail}")
        value = result.get("output")
        match = (
            re.match(r"^data:image/(png|jpeg);base64,(.+)$", value, re.S)
            if isinstance(value, str)
            else None
        )
        if not match:
            raise OperatingRoomError(
                "Agent Bridge returned an invalid screen image"
            )
        try:
            image = base64.b64decode(match.group(2), validate=True)
        except ValueError as exc:
            raise OperatingRoomError(
                "Agent Bridge returned corrupt screen data"
            ) from exc
        if not image or len(image) > MAX_SHARED_FILE_BYTES:
            raise OperatingRoomError(
                "Agent Bridge screen was empty or exceeded 25 MiB"
            )
        metadata = {
            "adapter": "agent-bridge-laptop",
            "bridge": base_url,
            "mime": f"image/{match.group(1)}",
        }
        if match.group(1) == "png":
            output.write_bytes(image)
            return self._validated_png("screen", output, metadata)
        if not image.startswith(b"\xff\xd8\xff"):
            raise OperatingRoomError(
                "Agent Bridge returned an invalid JPEG screen image"
            )
        jpeg_output = output.with_suffix(".jpg")
        jpeg_output.write_bytes(image)
        with contextlib_chmod(jpeg_output, 0o600):
            pass
        metadata["bytes"] = len(image)
        return SurfaceArtifact("screen", jpeg_output, metadata)

    def _bridge_json(
        self, method: str, url: str, payload: Mapping[str, Any] | None = None
    ) -> Any:
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            url, data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                body = response.read(MAX_SHARED_FILE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            raise OSError(f"HTTP {exc.code}") from exc
        if len(body) > MAX_SHARED_FILE_BYTES:
            raise ValueError("response exceeded 25 MiB")
        if not body.strip():
            return {}
        # POST endpoints currently answer "ok"; GET endpoints return JSON.
        if body.strip() == b"ok":
            return {}
        return json.loads(body.decode("utf-8"))

    def capture_browser(self, target: str = "") -> SurfaceArtifact:
        output = self._destination("browser", ".png")
        metadata = output.with_suffix(".json")
        custom = self.env.get("INCEPTION_BROWSER_CAPTURE_COMMAND")
        if custom:
            self._run_template(
                custom, output=output, metadata=metadata, target=target
            )
            return self._validated_png(
                "browser", output, self._read_metadata(metadata, "custom")
            )
        script = self.project / "scripts" / "capture_browser.cjs"
        local_problem = ""
        node = shutil.which("node")
        if node and script.is_file():
            command = [
                node,
                str(script),
                "--output",
                str(output),
                "--metadata",
                str(metadata),
            ]
            if target:
                command.extend(("--target", target))
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
                env=self.env,
            )
            if completed.returncode == 0:
                return self._validated_png(
                    "browser", output, self._read_metadata(metadata, "local-cdp")
                )
            local_problem = completed.stderr.strip() or completed.stdout.strip()
        elif not node:
            local_problem = "Node.js is not installed locally"
        else:
            local_problem = f"missing {script.name}"

        remote_host = self._browser_ssh_host()
        if remote_host and script.is_file():
            try:
                return self._remote_browser(
                    "browser", script, output, remote_host, target=target
                )
            except OperatingRoomError as exc:
                raise OperatingRoomError(
                    "Browser capture failed locally and on the configured laptop. "
                    f"Local: {local_problem or 'unavailable'}. "
                    f"Remote: {exc}"
                ) from exc
        raise OperatingRoomError(
            "Browser capture failed locally"
            f"{f': {local_problem}' if local_problem else ''}. "
            "Start Chrome with remote debugging, set "
            "INCEPTION_BROWSER_SSH_HOST, or set "
            "INCEPTION_BROWSER_CAPTURE_COMMAND."
        )

    def point_browser(
        self, target: str, page_target: str = ""
    ) -> SurfaceArtifact:
        if not target.strip():
            raise OperatingRoomError("Browser point needs element text or a CSS selector")
        output = self._destination("browser-point", ".png")
        metadata = output.with_suffix(".json")
        custom = self.env.get("INCEPTION_BROWSER_POINT_COMMAND")
        if custom:
            self._run_template(
                custom,
                output=output,
                metadata=metadata,
                target=target,
                page_target=page_target,
            )
        else:
            script = self.project / "scripts" / "point_browser.cjs"
            local_problem = ""
            node = shutil.which("node")
            if node and script.is_file():
                command = [
                    node,
                    str(script),
                    "--output",
                    str(output),
                    "--metadata",
                    str(metadata),
                    "--target",
                    target,
                ]
                if page_target:
                    command.extend(("--page-target", page_target))
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=60,
                    check=False,
                    env=self.env,
                )
                if completed.returncode == 0:
                    return self._validated_png(
                        "browser-point",
                        output,
                        self._read_metadata(metadata, "local-cdp"),
                    )
                local_problem = completed.stderr.strip() or completed.stdout.strip()
            elif not node:
                local_problem = "Node.js is not installed locally"
            else:
                local_problem = f"missing {script.name}"

            remote_host = self._browser_ssh_host()
            if remote_host and script.is_file():
                try:
                    return self._remote_browser(
                        "browser-point",
                        script,
                        output,
                        remote_host,
                        target=target,
                        page_target=page_target,
                    )
                except OperatingRoomError as exc:
                    raise OperatingRoomError(
                        "Browser pointing failed locally and on the configured laptop. "
                        f"Local: {local_problem or 'unavailable'}. "
                        f"Remote: {exc}"
                    ) from exc
            raise OperatingRoomError(
                "Browser pointing failed locally"
                f"{f': {local_problem}' if local_problem else ''}. "
                "Set INCEPTION_BROWSER_SSH_HOST or "
                "INCEPTION_BROWSER_POINT_COMMAND."
            )
        return self._validated_png(
            "browser-point", output, self._read_metadata(metadata, "local-cdp")
        )

    def _browser_ssh_host(self) -> str | None:
        explicit = self.env.get("INCEPTION_BROWSER_SSH_HOST", "").strip()
        if explicit:
            if re.fullmatch(r"[A-Za-z0-9_.@-]{1,255}", explicit):
                return explicit
            raise OperatingRoomError(
                "INCEPTION_BROWSER_SSH_HOST contains unsafe characters"
            )
        if os.name == "nt" or not shutil.which("ssh"):
            return None
        config = Path.home() / ".ssh" / "config"
        try:
            lines = config.read_text(encoding="utf-8").splitlines()
        except OSError:
            return None
        for line in lines:
            clean = line.split("#", 1)[0].strip()
            parts = clean.split()
            if (
                len(parts) >= 2
                and parts[0].lower() == "host"
                and "laptop" in parts[1:]
            ):
                return "laptop"
        return None

    def _remote_browser(
        self,
        kind: str,
        script: Path,
        output: Path,
        host: str,
        *,
        target: str = "",
        page_target: str = "",
    ) -> SurfaceArtifact:
        ssh = shutil.which("ssh")
        if not ssh:
            raise OperatingRoomError("SSH is not installed")
        configuration = base64.b64encode(
            json.dumps(
                {"target": target, "pageTarget": page_target},
                ensure_ascii=False,
            ).encode("utf-8")
        ).decode("ascii")
        try:
            source = script.read_bytes()
        except OSError as exc:
            raise OperatingRoomError(
                f"Cannot read browser bridge {script.name}: {exc}"
            ) from exc
        completed = subprocess.run(
            [
                ssh,
                "-T",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=10",
                host,
                "node",
                "-",
                "--bridge-config",
                configuration,
            ],
            input=source,
            capture_output=True,
            timeout=90,
            check=False,
        )
        if completed.returncode != 0:
            detail = (
                completed.stderr.decode(errors="replace").strip()
                or completed.stdout.decode(errors="replace").strip()
                or f"exit code {completed.returncode}"
            )
            raise OperatingRoomError(f"remote browser bridge failed: {detail}")
        try:
            payload = json.loads(completed.stdout.decode("utf-8"))
            encoded = payload["pngBase64"]
            metadata = payload.get("metadata") or {}
            if not isinstance(encoded, str) or not isinstance(metadata, dict):
                raise ValueError("unexpected bridge payload")
            image = base64.b64decode(encoded, validate=True)
        except (KeyError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OperatingRoomError(
                "Remote browser bridge returned invalid capture data"
            ) from exc
        if not image or len(image) > MAX_SHARED_FILE_BYTES:
            raise OperatingRoomError(
                "Remote browser capture was empty or exceeded 25 MiB"
            )
        output.write_bytes(image)
        metadata["ssh_host"] = host
        return self._validated_png(kind, output, metadata)

    def _remote_windows_screen(
        self, output: Path, host: str
    ) -> SurfaceArtifact:
        ssh = shutil.which("ssh")
        script = self.project / "scripts" / "capture_windows_screen.ps1"
        if not ssh or not script.is_file():
            raise OperatingRoomError(
                "SSH or the Windows screen helper is unavailable"
            )
        try:
            source = script.read_text(encoding="utf-8")
        except OSError as exc:
            raise OperatingRoomError(
                f"Cannot read Windows screen helper: {exc}"
            ) from exc
        command = f"& {{\n{source}\n}} -AsJson"
        encoded_command = base64.b64encode(
            command.encode("utf-16le")
        ).decode("ascii")
        completed = subprocess.run(
            [
                ssh,
                "-T",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=10",
                host,
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-EncodedCommand",
                encoded_command,
            ],
            capture_output=True,
            timeout=60,
            check=False,
        )
        if completed.returncode != 0:
            detail = (
                completed.stderr.decode(errors="replace").strip()
                or completed.stdout.decode(errors="replace").strip()
                or f"exit code {completed.returncode}"
            )
            raise OperatingRoomError(f"remote Windows capture failed: {detail}")
        try:
            text = completed.stdout.decode("utf-8-sig").strip()
            payload = json.loads(text[text.find("{") :])
            encoded = payload["pngBase64"]
            bounds = payload.get("bounds") or {}
            if not isinstance(encoded, str) or not isinstance(bounds, dict):
                raise ValueError("unexpected bridge payload")
            image = base64.b64decode(encoded, validate=True)
        except (
            KeyError,
            ValueError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as exc:
            raise OperatingRoomError(
                "Remote Windows screen helper returned invalid data"
            ) from exc
        if not image or len(image) > MAX_SHARED_FILE_BYTES:
            raise OperatingRoomError(
                "Remote Windows screen capture was empty or exceeded 25 MiB"
            )
        output.write_bytes(image)
        return self._validated_png(
            "screen",
            output,
            {
                "adapter": "remote-windows-powershell",
                "ssh_host": host,
                "bounds": bounds,
            },
        )

    def listen(self, timeout: int = 45) -> str:
        custom = self.env.get("INCEPTION_SPEECH_COMMAND")
        if custom:
            completed = subprocess.run(
                shlex.split(custom),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                env=self.env,
            )
        elif shutil.which("termux-speech-to-text"):
            completed = subprocess.run(
                ["termux-speech-to-text"],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                env=self.env,
            )
        elif os.name == "nt":
            script = self.project / "scripts" / "listen_windows.ps1"
            completed = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(script),
                    "-TimeoutSeconds",
                    str(timeout),
                ],
                capture_output=True,
                text=True,
                timeout=timeout + 10,
                check=False,
                env=self.env,
            )
        else:
            raise OperatingRoomError(
                "Speech input needs termux-speech-to-text, Windows speech, "
                "or INCEPTION_SPEECH_COMMAND."
            )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise OperatingRoomError(f"Speech recognition failed: {detail}")
        heard = completed.stdout.strip()
        if not heard:
            raise OperatingRoomError("Speech recognition returned no words")
        return heard

    def _destination(self, prefix: str, suffix: str) -> Path:
        return self.root / f"{prefix}-{uuid.uuid4().hex}{suffix}"

    def _run_template(self, template: str, **values: object) -> None:
        rendered = template.format(**{key: str(value) for key, value in values.items()})
        completed = subprocess.run(
            rendered,
            shell=True,
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
            env=self.env,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise OperatingRoomError(f"Surface command failed: {detail}")

    @staticmethod
    def _read_metadata(path: Path, adapter: str) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            value = {}
        if not isinstance(value, dict):
            value = {}
        value.setdefault("adapter", adapter)
        return value

    @staticmethod
    def _validated_png(
        kind: str, output: Path, metadata: dict[str, Any]
    ) -> SurfaceArtifact:
        try:
            header = output.read_bytes()[:8]
        except OSError as exc:
            raise OperatingRoomError(f"Capture produced no readable image: {exc}") from exc
        if header != b"\x89PNG\r\n\x1a\n":
            raise OperatingRoomError("Capture did not produce a valid PNG image")
        with contextlib_chmod(output, 0o600):
            pass
        metadata["bytes"] = output.stat().st_size
        return SurfaceArtifact(kind, output, metadata)

    @staticmethod
    def _latest_android_screenshot() -> Path | None:
        folder = Path("/sdcard/DCIM/Screenshots")
        try:
            images = [
                path
                for path in folder.iterdir()
                if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
            ]
        except OSError:
            return None
        return max(images, key=lambda path: path.stat().st_mtime) if images else None


@dataclass
class TestResult:
    command: str
    returncode: int
    elapsed_seconds: float
    log_path: str
    tail: str

    @property
    def passed(self) -> bool:
        return self.returncode == 0


@dataclass
class ArenaAttempt:
    agent: str
    worktree: str
    commit: str = ""
    changed_files: list[str] = field(default_factory=list)
    diff_stat: str = ""
    diff_excerpt: str = ""
    answer: str = ""
    status: str = "pending"
    test: TestResult | None = None


@dataclass
class ArenaRun:
    id: str
    created_at: str
    project: str
    base_commit: str
    prompt: str
    test_command: str
    attempts: dict[str, ArenaAttempt]
    reviews: dict[str, str] = field(default_factory=dict)
    recommended: str | None = None
    applied_agent: str | None = None
    applied_commit: str | None = None
    undo_commit: str | None = None


class ArenaManager:
    """Two isolated git attempts, mechanical proof, selection, replay, and undo."""

    def __init__(
        self, project: Path, root: Path = DEFAULT_ARENA_ROOT
    ):
        self.project = project.expanduser().resolve()
        self.root = root.expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        with contextlib_chmod(self.root, 0o700):
            pass
        self._git("rev-parse", "--show-toplevel")

    def prepare(
        self,
        prompt: str,
        test_command: str | None = None,
        participants: Sequence[str] = ("claude", "codex"),
    ) -> ArenaRun:
        pair = tuple(dict.fromkeys(participants))
        if len(pair) != 2:
            raise OperatingRoomError("An arena needs two different model names")
        dirty = self._git("status", "--porcelain").stdout.strip()
        if dirty:
            raise OperatingRoomError(
                "The project has existing changes. Commit or preserve them before "
                "starting two isolated builds."
            )
        base = self._git("rev-parse", "HEAD").stdout.strip()
        identifier = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        run_root = self.root / identifier
        run_root.mkdir(parents=True)
        attempts: dict[str, ArenaAttempt] = {}
        for agent in pair:
            worktree = run_root / agent
            completed = self._git(
                "worktree", "add", "--detach", str(worktree), base
            )
            if completed.returncode != 0:
                raise OperatingRoomError(
                    f"Cannot create {agent} isolated worktree: {completed.stderr.strip()}"
                )
            attempts[agent] = ArenaAttempt(agent, str(worktree))
        run = ArenaRun(
            id=identifier,
            created_at=now_iso(),
            project=str(self.project),
            base_commit=base,
            prompt=prompt,
            test_command=test_command or self.detect_test_command(),
            attempts=attempts,
        )
        self.save(run)
        return run

    def detect_test_command(self) -> str:
        config = self.project / ".inception.json"
        if config.is_file():
            try:
                value = json.loads(config.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                value = {}
            command = value.get("test_command") if isinstance(value, dict) else None
            if isinstance(command, str) and command.strip():
                return command.strip()
        if (self.project / "gradlew.bat").is_file() and os.name == "nt":
            return r".\gradlew.bat test"
        if (self.project / "gradlew").is_file():
            return "./gradlew test"
        if (self.project / "package.json").is_file():
            try:
                package = json.loads(
                    (self.project / "package.json").read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError):
                package = {}
            scripts = package.get("scripts") if isinstance(package, dict) else {}
            if isinstance(scripts, dict) and isinstance(scripts.get("test"), str):
                return "npm test"
        if (self.project / "Cargo.toml").is_file():
            return "cargo test"
        if (self.project / "go.mod").is_file():
            return "go test ./..."
        if (self.project / "tests").is_dir():
            if (self.project / "pyproject.toml").is_file() or (
                self.project / "pytest.ini"
            ).is_file():
                return f"{shlex.quote(sys.executable)} -m pytest"
            return (
                f"{shlex.quote(sys.executable)} -m unittest discover -s tests -v"
            )
        return ""

    def finalize_attempt(
        self,
        run: ArenaRun,
        agent: str,
        answer: str,
        status: str,
    ) -> ArenaAttempt:
        if agent not in run.attempts:
            raise OperatingRoomError(f"Unknown arena agent: {agent}")
        attempt = run.attempts[agent]
        worktree = Path(attempt.worktree)
        self._git_at(worktree, "config", "user.name", "Inception Arena")
        self._git_at(
            worktree, "config", "user.email", "inception-arena@local.invalid"
        )
        self._git_at(worktree, "add", "-A")
        staged = self._git_at(worktree, "diff", "--cached", "--quiet")
        if staged.returncode not in {0, 1}:
            raise OperatingRoomError(staged.stderr.strip() or "Cannot inspect arena diff")
        if staged.returncode == 1:
            committed = self._git_at(
                worktree,
                "commit",
                "-m",
                f"inception arena: {agent} attempt",
            )
            if committed.returncode != 0:
                raise OperatingRoomError(
                    f"Cannot preserve {agent} attempt: {committed.stderr.strip()}"
                )
        attempt.commit = self._git_at(worktree, "rev-parse", "HEAD").stdout.strip()
        names = self._git_at(
            worktree, "diff", "--name-only", run.base_commit, attempt.commit
        ).stdout.splitlines()
        attempt.changed_files = [name for name in names if name.strip()]
        attempt.diff_stat = compact(
            self._git_at(
                worktree, "diff", "--stat", run.base_commit, attempt.commit
            ).stdout,
            4_000,
        )
        attempt.diff_excerpt = compact(
            self._git_at(
                worktree, "diff", "--no-ext-diff", run.base_commit, attempt.commit
            ).stdout,
            24_000,
        )
        attempt.answer = compact(answer, 12_000)
        attempt.status = status
        self.save(run)
        return attempt

    def run_tests(self, run: ArenaRun, agent: str, timeout: int = 900) -> TestResult:
        attempt = run.attempts[agent]
        log = self.root / run.id / f"{agent}-tests.log"
        if not run.test_command:
            result = TestResult("", 125, 0.0, str(log), "No test command detected.")
            attempt.test = result
            self.save(run)
            return result
        import time

        started = time.monotonic()
        if os.name == "nt":
            test_command = run.test_command
            # PowerShell treats a quoted executable path as a string unless
            # the call operator is present.
            if re.match(r"""^\s*['"]""", test_command):
                test_command = f"& {test_command}"
            command = [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                test_command,
            ]
        else:
            command = ["/bin/sh", "-lc", run.test_command]
        try:
            completed = subprocess.run(
                command,
                cwd=attempt.worktree,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            output = (completed.stdout or "") + (completed.stderr or "")
            returncode = completed.returncode
        except subprocess.TimeoutExpired as exc:
            output = (
                (exc.stdout.decode() if isinstance(exc.stdout, bytes) else exc.stdout or "")
                + (exc.stderr.decode() if isinstance(exc.stderr, bytes) else exc.stderr or "")
                + f"\nTimed out after {timeout} seconds.\n"
            )
            returncode = 124
        elapsed = time.monotonic() - started
        log.write_text(output, encoding="utf-8")
        with contextlib_chmod(log, 0o600):
            pass
        result = TestResult(
            run.test_command,
            returncode,
            round(elapsed, 2),
            str(log),
            compact("\n".join(output.splitlines()[-30:]), 5_000),
        )
        attempt.test = result
        self.save(run)
        return result

    def recommend(self, run: ArenaRun, review_votes: Mapping[str, str]) -> str:
        agents = tuple(run.attempts)
        if len(agents) != 2:
            raise OperatingRoomError("Arena recommendation requires exactly two attempts")
        passed = {
            agent: bool(attempt.test and attempt.test.passed)
            for agent, attempt in run.attempts.items()
        }
        first, second = agents
        if passed[first] != passed[second]:
            winner = first if passed[first] else second
        else:
            votes = [
                vote.lower().strip()
                for vote in review_votes.values()
                if vote.lower().strip() in set(agents)
            ]
            if votes.count(first) > votes.count(second):
                winner = first
            elif votes.count(second) > votes.count(first):
                winner = second
            else:
                # Equal proof means George decides; do not manufacture certainty.
                winner = "tie"
        run.recommended = winner
        self.save(run)
        return winner

    def choose(self, run_id: str, agent: str) -> str:
        run = self.load(run_id)
        if agent not in run.attempts:
            raise OperatingRoomError(
                f"Winner must be one of: {', '.join(run.attempts)}"
            )
        if self._git("status", "--porcelain").stdout.strip():
            raise OperatingRoomError(
                "The main project changed after the arena started; selection stopped "
                "instead of overwriting those changes."
            )
        current = self._git("rev-parse", "HEAD").stdout.strip()
        if current != run.base_commit:
            raise OperatingRoomError(
                "The main branch moved after the arena started. Replay or rerun the arena."
            )
        commit = run.attempts[agent].commit
        if not commit or commit == run.base_commit:
            raise OperatingRoomError(f"{agent.title()} produced no change to apply")
        applied = self._git("cherry-pick", commit)
        if applied.returncode != 0:
            self._git("cherry-pick", "--abort")
            raise OperatingRoomError(
                f"Winner could not be applied cleanly: {applied.stderr.strip()}"
            )
        run.applied_agent = agent
        run.applied_commit = self._git("rev-parse", "HEAD").stdout.strip()
        self.save(run)
        return run.applied_commit

    def undo(self, run_id: str) -> str:
        run = self.load(run_id)
        if not run.applied_commit:
            raise OperatingRoomError(f"Arena {run_id} has no applied winner")
        if run.undo_commit:
            raise OperatingRoomError(f"Arena {run_id} was already undone")
        if self._git("status", "--porcelain").stdout.strip():
            raise OperatingRoomError("The project has changes; undo stopped safely")
        reverted = self._git("revert", "--no-edit", run.applied_commit)
        if reverted.returncode != 0:
            self._git("revert", "--abort")
            raise OperatingRoomError(
                f"Undo could not be applied cleanly: {reverted.stderr.strip()}"
            )
        run.undo_commit = self._git("rev-parse", "HEAD").stdout.strip()
        self.save(run)
        return run.undo_commit

    def save(self, run: ArenaRun) -> None:
        path = self.root / run.id / "manifest.json"
        atomic_json(path, asdict(run))

    def load(self, run_id: str) -> ArenaRun:
        path = self.root / run_id / "manifest.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise OperatingRoomError(f"Cannot read arena {run_id}: {exc}") from exc
        attempts = {
            agent: ArenaAttempt(
                **{
                    **attempt,
                    "test": (
                        TestResult(**attempt["test"])
                        if isinstance(attempt.get("test"), dict)
                        else None
                    ),
                }
            )
            for agent, attempt in value["attempts"].items()
        }
        return ArenaRun(
            **{
                **value,
                "attempts": attempts,
            }
        )

    def list_runs(self) -> list[ArenaRun]:
        runs: list[ArenaRun] = []
        for manifest in sorted(self.root.glob("*/manifest.json"), reverse=True):
            try:
                runs.append(self.load(manifest.parent.name))
            except OperatingRoomError:
                continue
        return runs

    def replay_text(self, run_id: str) -> str:
        run = self.load(run_id)
        lines = [
            f"Arena {run.id}",
            f"Created: {run.created_at}",
            f"Base: {run.base_commit}",
            f"Request: {run.prompt}",
            f"Tests: {run.test_command or 'none detected'}",
        ]
        for agent in run.attempts:
            attempt = run.attempts[agent]
            proof = (
                "PASS"
                if attempt.test and attempt.test.passed
                else "FAIL"
                if attempt.test
                else "NOT RUN"
            )
            lines.extend(
                (
                    f"{agent.title()}: {attempt.status}; tests {proof}; "
                    f"{len(attempt.changed_files)} changed file(s)",
                    compact(attempt.diff_stat, 1_200),
                )
            )
        lines.append(f"Recommended: {run.recommended or 'not judged'}")
        lines.append(f"Applied: {run.applied_agent or 'not selected'}")
        lines.append(f"Undone: {'yes' if run.undo_commit else 'no'}")
        return "\n".join(line for line in lines if line)

    def comparison_packet(self, run: ArenaRun) -> str:
        sections = [
            f"ORIGINAL REQUEST:\n{run.prompt}",
            f"MECHANICAL TEST COMMAND:\n{run.test_command or 'none detected'}",
        ]
        for agent in run.attempts:
            attempt = run.attempts[agent]
            test = attempt.test
            sections.append(
                f"{agent.upper()} ATTEMPT\n"
                f"Status: {attempt.status}\n"
                f"Changed: {', '.join(attempt.changed_files) or 'nothing'}\n"
                f"Diff stat:\n{attempt.diff_stat or 'none'}\n"
                f"Test: "
                f"{'PASS' if test and test.passed else 'FAIL' if test else 'not run'}\n"
                f"Test tail:\n{test.tail if test else 'none'}\n"
                f"Agent report:\n{compact(attempt.answer, 4_000)}\n"
                f"Diff excerpt:\n{compact(attempt.diff_excerpt, 10_000)}"
            )
        return compact("\n\n".join(sections), 28_000)

    def _git(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return self._git_at(self.project, *arguments)

    @staticmethod
    def _git_at(path: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=path,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if completed.returncode != 0 and arguments[:2] not in {
            ("diff", "--cached"),
            ("status", "--porcelain"),
            ("cherry-pick", "--abort"),
            ("revert", "--abort"),
        }:
            # Callers that need to inspect a non-zero status still receive it for
            # diff --quiet and commit/cherry-pick operations.
            if arguments and arguments[0] in {
                "commit",
                "cherry-pick",
                "revert",
                "diff",
            }:
                return completed
            raise OperatingRoomError(
                f"git {' '.join(arguments)} failed: "
                f"{completed.stderr.strip() or completed.stdout.strip()}"
            )
        return completed
