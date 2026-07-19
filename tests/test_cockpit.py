import asyncio
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).parents[1] / "scripts" / "cockpit.py"
SPEC = importlib.util.spec_from_file_location("cockpit", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

THREAD = "019f7048-1bb9-7230-b91f-f572d2cbc870"
CLAUDE_SESSION = "019f8888-1bb9-7230-b91f-f572d2cbc870"


class CommandTests(unittest.TestCase):
    def test_parses_explicit_and_natural_turn_grants(self):
        explicit = MODULE.parse_operator_command("/both inspect this")
        natural = MODULE.parse_operator_command("Claude: challenge it")

        self.assertEqual(
            (explicit.kind, explicit.target, explicit.text),
            ("ask", "both", "inspect this"),
        )
        self.assertEqual(
            (natural.kind, natural.target, natural.text),
            ("ask", "claude", "challenge it"),
        )

    def test_parses_forwarding_with_georges_instruction(self):
        command = MODULE.parse_operator_command(
            "/pass claude codex focus on the hidden assumption"
        )

        self.assertEqual(command.kind, "pass")
        self.assertEqual(command.source, "claude")
        self.assertEqual(command.target, "codex")
        self.assertEqual(command.text, "focus on the hidden assumption")

    def test_rejects_ungated_bare_text(self):
        with self.assertRaisesRegex(MODULE.CockpitError, "/both"):
            MODULE.parse_operator_command("everybody start talking")

    def test_parses_visible_continuity_controls(self):
        show = MODULE.parse_operator_command("/context")
        full = MODULE.parse_operator_command("/context full")
        disable = MODULE.parse_operator_command("/context off")

        self.assertEqual((show.kind, show.text), ("context", ""))
        self.assertEqual((full.kind, full.text), ("context", "full"))
        self.assertEqual((disable.kind, disable.text), ("context", "off"))


class StateTests(unittest.TestCase):
    def test_state_round_trip_is_private_and_validated(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cockpit-state.json"
            store = MODULE.StateStore(path)
            store.load()
            store.data.update(
                {
                    "codex_source_thread_id": THREAD,
                    "codex_thread_id": THREAD,
                    "claude_session_id": CLAUDE_SESSION,
                }
            )
            store.save()
            loaded = MODULE.StateStore(path).load()

            self.assertEqual(loaded["claude_session_id"], CLAUDE_SESSION)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_rejects_invalid_persisted_session(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cockpit-state.json"
            path.write_text(
                json.dumps({"schema_version": 1, "claude_session_id": "not-a-session"}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(MODULE.CockpitError, "claude_session_id"):
                MODULE.StateStore(path).load()


class ContinuityTests(unittest.TestCase):
    def test_default_lineage_contains_georges_demonstrated_relationship(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = MODULE.ContinuityEngine(
                MODULE.DEFAULT_COVENANT_PATH,
                MODULE.DEFAULT_MICROHISTORY_PATH,
                Path(directory) / "missing.jsonl",
            )

        self.assertEqual(engine.microhistory_episode_count, 10)
        self.assertIn("Nick", engine.microhistory)
        self.assertIn("grandfather", engine.microhistory.lower())
        self.assertIn("Wholehearted, not certain", engine.covenant)

    def test_retrieves_only_relevant_bounded_prior_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            covenant_path = base / "covenant.md"
            microhistory_path = base / "microhistory.md"
            journal_path = base / "events.jsonl"
            covenant_path.write_text(
                "Tell the truth and inspect failure modes.", encoding="utf-8"
            )
            microhistory_path.write_text(
                "## 1. Wholeheartedness\nGeorge's grandfather example.",
                encoding="utf-8",
            )
            journal = MODULE.Journal(journal_path)
            journal.append(
                {
                    "type": "prompt",
                    "turn_id": "business-turn",
                    "target": "both",
                    "text": "Assess the orchid business distribution and feasibility.",
                }
            )
            journal.append(
                {
                    "type": "answer",
                    "turn_id": "business-turn",
                    "agent": "claude",
                    "status": "completed",
                    "text": "ORCHID-731 failed when customer acquisition became too expensive.",
                }
            )
            journal.append(
                {
                    "type": "prompt",
                    "turn_id": "theme-turn",
                    "target": "codex",
                    "text": "Change the Android theme color.",
                }
            )
            journal.append(
                {
                    "type": "answer",
                    "turn_id": "theme-turn",
                    "agent": "codex",
                    "status": "completed",
                    "text": "Use blue for the toolbar.",
                }
            )

            engine = MODULE.ContinuityEngine(
                covenant_path, microhistory_path, journal_path
            )
            packet = engine.packet_for("Why might orchid distribution fail?")

        self.assertEqual(packet.episode_ids, ("business-turn",))
        self.assertIn("ORCHID-731", packet.evidence)
        self.assertNotIn("toolbar", packet.evidence)
        self.assertLessEqual(len(packet.evidence), 2_400)
        self.assertIn("George's current message", packet.wrap("Current question"))

    def test_no_match_means_no_invented_episode(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            covenant_path = base / "covenant.md"
            microhistory_path = base / "microhistory.md"
            covenant_path.write_text("Tell the truth.", encoding="utf-8")
            microhistory_path.write_text(
                "## 1. Repair\nMistakes are repaired together.", encoding="utf-8"
            )
            engine = MODULE.ContinuityEngine(
                covenant_path, microhistory_path, base / "missing.jsonl"
            )

            packet = engine.packet_for("A completely new subject")

        self.assertEqual(packet.episode_count, 0)
        self.assertEqual(packet.wrap("Current question"), "Current question")

    def test_endpoint_relationship_seed_includes_rules_and_lived_examples(self):
        instructions = MODULE.endpoint_instructions(
            "WHOLEHEARTED COVENANT", "GRANDFATHER CHRONOLOGY"
        )

        self.assertIn("WHOLEHEARTED COVENANT", instructions)
        self.assertIn("GRANDFATHER CHRONOLOGY", instructions)
        self.assertIn("Do not recite", instructions)
        self.assertIn("claim you personally lived", instructions)
        self.assertIn("discussion-only boundary overrides", instructions)


class ProtocolParsingTests(unittest.TestCase):
    def test_extracts_claude_stream_delta_and_completed_message(self):
        delta = {
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "hello"},
            },
        }
        assistant = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "hello "},
                    {"type": "text", "text": "George"},
                ]
            },
        }

        self.assertEqual(MODULE.claude_stream_delta(delta), "hello")
        self.assertEqual(MODULE.claude_assistant_text(assistant), "hello George")

    def test_termux_claude_command_is_streaming_and_tool_disabled(self):
        with patch.dict(
            os.environ, {"PREFIX": "/data/data/com.termux/files/usr"}, clear=False
        ):
            command = MODULE.default_claude_command(
                Path("/tmp/work"), CLAUDE_SESSION, "SHARED CONTRACT TEST"
            )

        self.assertEqual(command[:4], ["proot-distro", "login", "debian", "--"])
        self.assertIn("stream-json", command)
        tools_index = command.index("--tools")
        self.assertEqual(command[tools_index + 1], "")
        self.assertIn("SHARED CONTRACT TEST", command)
        self.assertEqual(command[-2:], ["--resume", CLAUDE_SESSION])


class FakeCodexEndpoint(MODULE.CodexEndpoint):
    def __init__(self):
        super().__init__(
            Path("/tmp"), THREAD, None, instructions="SHARED CONTRACT TEST"
        )
        self.sent = []

    async def _request(self, method, params, timeout=120):
        self.sent.append((method, params))
        if method == "initialize":
            return {
                "codexHome": "/tmp/.codex",
                "platformFamily": "unix",
                "platformOs": "linux",
                "userAgent": "test",
            }
        if method == "thread/resume":
            return {"thread": {"id": THREAD}}
        raise AssertionError(method)

    async def _send(self, message):
        self.sent.append((message.get("method"), message.get("params")))

    async def _read_loop(self):
        return


class CodexHandshakeTests(unittest.IsolatedAsyncioTestCase):
    async def test_opts_into_experimental_api_before_excluding_large_history(self):
        with tempfile.TemporaryDirectory() as directory:
            endpoint = FakeCodexEndpoint()
            endpoint.error_log = Path(directory) / "errors.log"

            # Avoid spawning a process while exercising the exact handshake params.
            async def fake_spawn(*args, **kwargs):
                return object()

            with patch.object(asyncio, "create_subprocess_exec", fake_spawn):
                thread_id = await endpoint.start()

            initialize = endpoint.sent[0]
            resume = next(
                entry for entry in endpoint.sent if entry[0] == "thread/resume"
            )
            self.assertTrue(initialize[1]["capabilities"]["experimentalApi"])
            self.assertTrue(resume[1]["excludeTurns"])
            self.assertEqual(resume[1]["developerInstructions"], "SHARED CONTRACT TEST")
            self.assertEqual(thread_id, THREAD)
            endpoint.error_handle.close()


class FakeEndpoint:
    def __init__(self, name):
        self.name = name
        self.calls = []
        self.interruptions = 0

    async def ask(self, prompt, emit):
        self.calls.append(prompt)
        emit(f"{self.name} answer")
        await asyncio.sleep(0)
        return MODULE.TurnResult(self.name, f"{self.name} answer")

    async def interrupt(self):
        self.interruptions += 1


class SlowEndpoint(FakeEndpoint):
    def __init__(self, name):
        super().__init__(name)
        self.started = asyncio.Event()
        self.released = asyncio.Event()

    async def ask(self, prompt, emit):
        self.calls.append(prompt)
        self.started.set()
        await self.released.wait()
        return MODULE.TurnResult(self.name, "", "interrupted")

    async def interrupt(self):
        await super().interrupt()
        self.released.set()


class BrokerTests(unittest.IsolatedAsyncioTestCase):
    async def test_both_get_exact_prompt_once_and_never_auto_advance(self):
        codex = FakeEndpoint("codex")
        claude = FakeEndpoint("claude")
        broker = MODULE.Broker(codex, claude)

        with redirect_stdout(io.StringIO()):
            results = await broker.ask("both", "same exact question")

        self.assertEqual(claude.calls, ["same exact question"])
        self.assertEqual(codex.calls, ["same exact question"])
        self.assertEqual(set(results), {"claude", "codex"})
        self.assertFalse(broker.active_agents)

    async def test_forwarding_requires_george_and_targets_only_one_agent(self):
        codex = FakeEndpoint("codex")
        claude = FakeEndpoint("claude")
        broker = MODULE.Broker(codex, claude)
        broker.last["claude"] = "Claude's proposal"

        with redirect_stdout(io.StringIO()):
            await broker.pass_answer("claude", "codex", "Find the flaw")

        self.assertEqual(len(codex.calls), 1)
        self.assertIn("Claude's proposal", codex.calls[0])
        self.assertIn("Find the flaw", codex.calls[0])
        self.assertEqual(claude.calls, [])

    async def test_both_receive_the_same_automatically_retrieved_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            covenant_path = base / "covenant.md"
            microhistory_path = base / "microhistory.md"
            journal_path = base / "events.jsonl"
            covenant_path.write_text("Inspect failure modes.", encoding="utf-8")
            microhistory_path.write_text(
                "## 1. Wholeheartedness\nThe relationship survives mistakes.",
                encoding="utf-8",
            )
            journal = MODULE.Journal(journal_path)
            journal.append(
                {
                    "type": "prompt",
                    "turn_id": "orchid-turn",
                    "target": "both",
                    "text": "Review orchid distribution.",
                }
            )
            journal.append(
                {
                    "type": "answer",
                    "turn_id": "orchid-turn",
                    "agent": "codex",
                    "status": "completed",
                    "text": "The evidence marker is ORCHID-731.",
                }
            )
            codex = FakeEndpoint("codex")
            claude = FakeEndpoint("claude")
            broker = MODULE.Broker(
                codex,
                claude,
                journal,
                continuity=MODULE.ContinuityEngine(
                    covenant_path, microhistory_path, journal_path
                ),
            )

            with redirect_stdout(io.StringIO()):
                await broker.ask("both", "Reconsider orchid distribution.")

        self.assertEqual(claude.calls, codex.calls)
        self.assertIn("ORCHID-731", claude.calls[0])
        self.assertIn("Reconsider orchid distribution.", claude.calls[0])

    async def test_stop_interrupts_every_active_endpoint(self):
        codex = SlowEndpoint("codex")
        claude = SlowEndpoint("claude")
        broker = MODULE.Broker(codex, claude)

        with redirect_stdout(io.StringIO()):
            turn = asyncio.create_task(broker.ask("both", "wait for George"))
            await asyncio.gather(codex.started.wait(), claude.started.wait())
            await broker.stop()
            results = await turn

        self.assertEqual(codex.interruptions, 1)
        self.assertEqual(claude.interruptions, 1)
        self.assertEqual(results["codex"].status, "interrupted")
        self.assertEqual(results["claude"].status, "interrupted")


if __name__ == "__main__":
    unittest.main()
