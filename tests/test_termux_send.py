import importlib.util
import contextlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).parents[1]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


TERMUX_SEND = load("termux_send", ROOT / "postoffice" / "termux_send.py")


class TermuxSendTests(unittest.TestCase):
    def test_parses_supported_tty_names(self):
        self.assertEqual(TERMUX_SEND.tty_index("pts/2"), 2)
        self.assertEqual(TERMUX_SEND.tty_index("/dev/pts/17"), 17)
        with self.assertRaises(TERMUX_SEND.SendError):
            TERMUX_SEND.tty_index("2")

    def test_rejects_multiline_and_control_input(self):
        self.assertEqual(TERMUX_SEND.validate_message("plain words"), b"plain words")
        for message in ("line one\nline two", "carriage\rreturn", "escape\x1b"):
            with self.assertRaises(TERMUX_SEND.SendError):
                TERMUX_SEND.validate_message(message)

    def test_other_selects_the_only_noncalling_session(self):
        sessions = [
            TERMUX_SEND.CodexSession(pid=100, tty_index=1),
            TERMUX_SEND.CodexSession(pid=200, tty_index=2),
        ]
        with mock.patch.object(TERMUX_SEND, "current_ancestor_tty", return_value=1):
            selected = TERMUX_SEND.select_session("other", sessions)
        self.assertEqual(selected.pid, 200)

    def test_self_target_is_rejected(self):
        session = TERMUX_SEND.CodexSession(pid=100, tty_index=1)
        with self.assertRaisesRegex(TERMUX_SEND.SendError, "this session"):
            TERMUX_SEND.reject_self_target(session, 1)

    def test_send_rejects_a_stale_codex_process(self):
        session = TERMUX_SEND.CodexSession(pid=200, tty_index=2)
        with mock.patch.object(TERMUX_SEND, "_read_cmdline", return_value=[]):
            with self.assertRaisesRegex(TERMUX_SEND.SendError, "no longer owns"):
                TERMUX_SEND.verify_live_session(session)

    def test_visible_envelope_is_attributed_and_submits_after_a_pause(self):
        envelope = TERMUX_SEND.message_envelope("Check the build", 1)
        self.assertEqual(
            envelope,
            "Check the build\n— Sent from Codex conversation pts/1",
        )
        writes = []
        sleeps = []

        def writer(_fd, payload):
            writes.append(payload)
            return len(payload)

        TERMUX_SEND.transmit_prompt(
            9,
            envelope,
            visible=True,
            char_delay=0.04,
            writer=writer,
            sleeper=sleeps.append,
        )
        self.assertEqual(b"".join(writes), envelope.encode("utf-8") + b"\r")
        self.assertEqual(writes[-1], b"\r")
        self.assertEqual(sleeps[-1], TERMUX_SEND.SUBMIT_DELAY)
        self.assertTrue(all(len(chunk.decode("utf-8")) == 1 for chunk in writes[:-1]))

    def test_fast_mode_separates_text_from_submit(self):
        writes = []
        sleeps = []

        def writer(_fd, payload):
            writes.append(payload)
            return len(payload)

        TERMUX_SEND.transmit_prompt(
            9, "hello", visible=False, writer=writer, sleeper=sleeps.append
        )
        self.assertEqual(writes, [b"hello", b"\r"])
        self.assertEqual(sleeps, [TERMUX_SEND.SUBMIT_DELAY])

    def test_wait_for_reply_correlates_visible_request_and_returns_final_answer(self):
        request = "Check the build\n— Sent from Codex conversation pts/1"
        records = [
            {
                "type": "event_msg",
                "payload": {"type": "task_started", "turn_id": "turn-1"},
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": request}],
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "Normal full reply"}],
                },
            },
            {
                "type": "event_msg",
                "payload": {"type": "task_complete", "turn_id": "turn-1"},
            },
        ]
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8") as stream:
            for record in records:
                stream.write(__import__("json").dumps(record) + "\n")
            stream.flush()
            reply = TERMUX_SEND.wait_for_reply(Path(stream.name), 0, request, timeout=1)
        self.assertEqual(reply, "Normal full reply")

    def test_detects_busy_and_completed_rollouts_from_latest_event(self):
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8") as stream:
            stream.write('{"type":"event_msg","payload":{"type":"task_complete"}}\n')
            stream.write('{"type":"event_msg","payload":{"type":"task_started"}}\n')
            stream.flush()
            path = Path(stream.name)
            self.assertEqual(TERMUX_SEND.latest_task_event(path), "task_started")
            self.assertTrue(TERMUX_SEND.rollout_is_busy(path))
            stream.write('{"type":"event_msg","payload":{"type":"task_complete"}}\n')
            stream.flush()
            self.assertEqual(TERMUX_SEND.latest_task_event(path), "task_complete")
            self.assertFalse(TERMUX_SEND.rollout_is_busy(path))

    def test_unknown_rollout_state_fails_closed(self):
        with tempfile.NamedTemporaryFile() as stream:
            with self.assertRaisesRegex(TERMUX_SEND.SendError, "Cannot prove"):
                TERMUX_SEND.rollout_is_busy(Path(stream.name))

    def test_lifecycle_words_inside_message_text_do_not_fake_idle_state(self):
        records = [
            {
                "type": "event_msg",
                "payload": {"type": "task_started", "turn_id": "live"},
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": 'audit this JSON: {"type":"task_complete"}',
                        }
                    ],
                },
            },
        ]
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8") as stream:
            for record in records:
                stream.write(__import__("json").dumps(record) + "\n")
            stream.flush()
            path = Path(stream.name)
            self.assertEqual(TERMUX_SEND.latest_task_event(path), "task_started")
            self.assertTrue(TERMUX_SEND.rollout_is_busy(path))

    def test_crosscheck_hides_primary_until_final_round(self):
        task_id = "XCHK_20260803T120000_A1B2C3"
        first = TERMUX_SEND.independent_crosscheck_prompt(
            task_id, "Decide whether the repair is complete"
        )
        second = TERMUX_SEND.challenge_crosscheck_prompt(
            task_id,
            "Decide whether the repair is complete",
            "PRIMARY_SECRET conclusion",
        )
        self.assertIn("ROUND 1 OF 2", first)
        self.assertNotIn("PRIMARY_SECRET", first)
        self.assertIn("ROUND 2 OF 2, FINAL ROUND", second)
        self.assertIn("PRIMARY_SECRET", second)
        self.assertIn("UNRESOLVED DISAGREEMENTS", second)

    def test_crosscheck_runs_two_rounds_and_saves_a_complete_record(self):
        session = TERMUX_SEND.CodexSession(pid=200, tty_index=2)
        task_id = "XCHK_20260803T120000_A1B2C3"
        fake_rollout = Path("/tmp/fake-rollout.jsonl")
        final_reply = """VERDICT
Use the primary with one correction.

AGREED FACTS
The transport works.

UNRESOLVED DISAGREEMENTS
One proof remains missing.

EVIDENCE NEEDED
Run the live check.

RECOMMENDED FINAL ACTION
Run it once.
"""
        exchanges = [
            ("Independent answer", "turn-1", fake_rollout, 101),
            (final_reply, "turn-2", fake_rollout, 202),
        ]
        primary = "Primary answer\n\n```python\nx = 1\n```\n"
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            TERMUX_SEND, "new_task_id", return_value=task_id
        ), mock.patch.object(
            TERMUX_SEND, "exchange_with_session", side_effect=exchanges
        ) as exchange, mock.patch.object(
            TERMUX_SEND,
            "target_lock",
            return_value=contextlib.nullcontext(Path(directory) / "target.lock"),
        ):
            record, path = TERMUX_SEND.run_crosscheck(
                session,
                "Original objective",
                primary,
                source_tty=1,
                journal_dir=Path(directory),
                lock_dir=Path(directory) / "locks",
            )
            self.assertEqual(exchange.call_count, 2)
            first_prompt = exchange.call_args_list[0].args[1]
            second_prompt = exchange.call_args_list[1].args[1]
            self.assertNotIn("Primary answer", first_prompt)
            self.assertIn(
                __import__("json").dumps(primary, ensure_ascii=False),
                second_prompt,
            )
            self.assertEqual(record["status"], "complete")
            self.assertEqual([item["round"] for item in record["rounds"]], [1, 2])
            self.assertEqual(
                [item["turn_id"] for item in record["rounds"]],
                ["turn-1", "turn-2"],
            )
            saved = __import__("json").loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["task_id"], task_id)
            self.assertEqual(saved["status"], "complete")
            self.assertEqual(saved["primary_answer"], primary)
            self.assertEqual(
                saved["unresolved_disagreements"], "One proof remains missing."
            )

    def test_wait_until_idle_does_not_inject_into_a_busy_target(self):
        states = iter([True, True, False])
        ticks = iter([0.0, 0.1, 0.2])
        with mock.patch.object(
            TERMUX_SEND, "rollout_is_busy", side_effect=lambda _path: next(states)
        ):
            TERMUX_SEND.wait_until_idle(
                Path("/tmp/fake"),
                timeout=1,
                poll_interval=0,
                clock=lambda: next(ticks),
                sleeper=lambda _seconds: None,
            )

    def test_generic_send_has_a_unique_task_marker_and_waits_by_default(self):
        envelope = TERMUX_SEND.message_envelope(
            "Check the build",
            1,
            task_id="PO_20260803T120000_A1B2C3",
            phase="send",
        )
        self.assertTrue(
            envelope.startswith(
                "[POSTOFFICE id=PO_20260803T120000_A1B2C3 phase=send]"
            )
        )
        self.assertTrue(
            TERMUX_SEND.parser().parse_args(["pts/2", "hello"]).wait_idle
        )
        self.assertFalse(
            TERMUX_SEND.parser().parse_args(
                ["--steer-now", "pts/2", "hello"]
            ).wait_idle
        )

    def test_reply_correlation_ignores_the_wrong_turn_completion(self):
        request = TERMUX_SEND.message_envelope(
            "Check",
            1,
            task_id="PO_20260803T120000_A1B2C3",
        )
        records = [
            {
                "type": "event_msg",
                "payload": {"type": "task_started", "turn_id": "ours"},
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": request}],
                },
            },
            {
                "type": "event_msg",
                "payload": {
                    "type": "task_complete",
                    "turn_id": "not-ours",
                    "last_agent_message": "Wrong answer",
                },
            },
            {
                "type": "event_msg",
                "payload": {
                    "type": "task_complete",
                    "turn_id": "ours",
                    "last_agent_message": "Correct answer",
                },
            },
        ]
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8") as stream:
            for record in records:
                stream.write(__import__("json").dumps(record) + "\n")
            stream.flush()
            reply, turn_id = TERMUX_SEND.wait_for_reply_turn(
                Path(stream.name), 0, request, timeout=1
            )
        self.assertEqual((reply, turn_id), ("Correct answer", "ours"))

    def test_matching_turn_abort_fails_immediately(self):
        request = TERMUX_SEND.message_envelope(
            "Check",
            1,
            task_id="PO_20260803T120000_A1B2C3",
        )
        records = [
            {
                "type": "event_msg",
                "payload": {"type": "task_started", "turn_id": "ours"},
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": request}],
                },
            },
            {
                "type": "event_msg",
                "payload": {"type": "turn_aborted", "turn_id": "ours"},
            },
        ]
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8") as stream:
            for record in records:
                stream.write(__import__("json").dumps(record) + "\n")
            stream.flush()
            with self.assertRaisesRegex(TERMUX_SEND.SendError, "was aborted"):
                TERMUX_SEND.wait_for_reply_turn(
                    Path(stream.name), 0, request, timeout=1
                )

    def test_extra_user_message_marks_the_cross_session_turn_interfered(self):
        request = TERMUX_SEND.message_envelope(
            "Check",
            1,
            task_id="PO_20260803T120000_A1B2C3",
        )
        records = [
            {
                "type": "event_msg",
                "payload": {"type": "task_started", "turn_id": "ours"},
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": request}],
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "New direction"}],
                },
            },
        ]
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8") as stream:
            for record in records:
                stream.write(__import__("json").dumps(record) + "\n")
            stream.flush()
            with self.assertRaisesRegex(TERMUX_SEND.SendError, "interfered"):
                TERMUX_SEND.wait_for_reply_turn(
                    Path(stream.name), 0, request, timeout=1
                )

    @unittest.skipIf(os.name == "nt", "flock is Unix-only")
    def test_target_lock_prevents_two_senders_from_interleaving(self):
        session = TERMUX_SEND.CodexSession(pid=200, tty_index=2)
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            TERMUX_SEND, "verify_live_session"
        ):
            with TERMUX_SEND.target_lock(
                session,
                timeout=1,
                lock_dir=Path(directory),
                poll_interval=0,
            ):
                with self.assertRaisesRegex(TERMUX_SEND.SendError, "stayed occupied"):
                    with TERMUX_SEND.target_lock(
                        session,
                        timeout=0,
                        lock_dir=Path(directory),
                        poll_interval=0,
                    ):
                        pass

    def test_duplicate_record_id_cannot_overwrite_existing_evidence(self):
        record = {"task_id": "XCHK_20260803T120000_A1B2C3", "status": "queued"}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = TERMUX_SEND.write_crosscheck_record(root, record, create=True)
            with self.assertRaisesRegex(TERMUX_SEND.SendError, "already exists"):
                TERMUX_SEND.write_crosscheck_record(root, record, create=True)
            self.assertEqual(
                __import__("json").loads(path.read_text(encoding="utf-8"))["status"],
                "queued",
            )

    def test_oversized_primary_fails_before_any_crosscheck_turn(self):
        session = TERMUX_SEND.CodexSession(pid=200, tty_index=2)
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            TERMUX_SEND, "exchange_with_session"
        ) as exchange:
            with self.assertRaisesRegex(TERMUX_SEND.SendError, "exceeds"):
                TERMUX_SEND.run_crosscheck(
                    session,
                    "Objective",
                    "x" * TERMUX_SEND.MAX_MESSAGE_BYTES,
                    source_tty=1,
                    journal_dir=Path(directory),
                    lock_dir=Path(directory) / "locks",
                )
            exchange.assert_not_called()

    def test_primary_is_not_written_to_journal_before_round_one_finishes(self):
        session = TERMUX_SEND.CodexSession(pid=200, tty_index=2)
        task_id = "XCHK_20260803T120000_A1B2C3"
        final_reply = """VERDICT
Pass.
AGREED FACTS
One.
UNRESOLVED DISAGREEMENTS
None.
EVIDENCE NEEDED
None.
RECOMMENDED FINAL ACTION
Ship.
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calls = 0

            def exchange(_session, _prompt, **_kwargs):
                nonlocal calls
                calls += 1
                saved = __import__("json").loads(
                    (root / f"{task_id}.json").read_text(encoding="utf-8")
                )
                if calls == 1:
                    self.assertNotIn("primary_answer", saved)
                    self.assertNotIn("primary_answer_sha256", saved)
                    return "Independent", "turn-1", Path("/tmp/rollout"), 1
                self.assertEqual(saved["primary_answer"], "HIDDEN PRIMARY")
                self.assertIn("primary_answer_sha256", saved)
                return final_reply, "turn-2", Path("/tmp/rollout"), 1

            with mock.patch.object(
                TERMUX_SEND, "new_task_id", return_value=task_id
            ), mock.patch.object(
                TERMUX_SEND, "exchange_with_session", side_effect=exchange
            ), mock.patch.object(
                TERMUX_SEND,
                "target_lock",
                return_value=contextlib.nullcontext(root / "target.lock"),
            ):
                TERMUX_SEND.run_crosscheck(
                    session,
                    "Objective",
                    "HIDDEN PRIMARY",
                    source_tty=1,
                    journal_dir=root,
                    lock_dir=root / "locks",
                )
            self.assertEqual(calls, 2)

    def test_final_challenge_must_preserve_all_required_sections(self):
        with self.assertRaisesRegex(TERMUX_SEND.SendError, "omitted required"):
            TERMUX_SEND.parse_crosscheck_sections(
                "VERDICT\nPass\nUNRESOLVED DISAGREEMENTS\nNone"
            )
        empty_disagreement = """VERDICT
Pass.
AGREED FACTS
One.
UNRESOLVED DISAGREEMENTS
EVIDENCE NEEDED
None.
RECOMMENDED FINAL ACTION
Ship.
"""
        with self.assertRaisesRegex(TERMUX_SEND.SendError, "left required"):
            TERMUX_SEND.parse_crosscheck_sections(empty_disagreement)

    def test_round_two_failure_preserves_the_independent_answer(self):
        session = TERMUX_SEND.CodexSession(pid=200, tty_index=2)
        task_id = "XCHK_20260803T120000_A1B2C3"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with mock.patch.object(
                TERMUX_SEND, "new_task_id", return_value=task_id
            ), mock.patch.object(
                TERMUX_SEND,
                "exchange_with_session",
                side_effect=[
                    ("Independent survives", "turn-1", Path("/tmp/rollout"), 1),
                    TERMUX_SEND.SendError("challenge failed"),
                ],
            ), mock.patch.object(
                TERMUX_SEND,
                "target_lock",
                return_value=contextlib.nullcontext(root / "target.lock"),
            ):
                with self.assertRaisesRegex(TERMUX_SEND.SendError, "challenge failed"):
                    TERMUX_SEND.run_crosscheck(
                        session,
                        "Objective",
                        "Primary",
                        source_tty=1,
                        journal_dir=root,
                        lock_dir=root / "locks",
                    )
            saved = __import__("json").loads(
                (root / f"{task_id}.json").read_text(encoding="utf-8")
            )
            self.assertEqual(saved["status"], "failed")
            self.assertEqual(saved["failed_phase"], "challenge")
            self.assertEqual(saved["rounds"][0]["reply"], "Independent survives")

    @unittest.skipUnless(os.name == "posix" and hasattr(os, "fork"), "Linux PTY test")
    def test_cross_process_fd_delivery(self):
        try:
            token = TERMUX_SEND.cross_process_self_test()
        except TERMUX_SEND.SendError as exc:
            if "errno 1" in str(exc) or "errno 38" in str(exc):
                self.skipTest(str(exc))
            raise
        self.assertEqual(token, "POST_OFFICE_PTY_SELF_TEST")


if __name__ == "__main__":
    unittest.main()
