import asyncio
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import AsyncMock, patch


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

    def test_parses_explicit_and_natural_action_grants(self):
        explicit = MODULE.parse_operator_command(
            "/act codex implement it, test it, commit it, and push it"
        )
        natural = MODULE.parse_operator_command("Claude!: execute your answer now")

        self.assertEqual(
            (explicit.kind, explicit.target, explicit.text),
            (
                "act",
                "codex",
                "implement it, test it, commit it, and push it",
            ),
        )
        self.assertEqual(
            (natural.kind, natural.target, natural.text),
            ("act", "claude", "execute your answer now"),
        )

    def test_rejects_action_grant_to_both_agents(self):
        with self.assertRaisesRegex(MODULE.CockpitError, "/act claude"):
            MODULE.parse_operator_command("/act both implement it")

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

    def test_parses_plain_status_and_project_commands(self):
        status = MODULE.parse_operator_command("/status")
        where = MODULE.parse_operator_command("/where")
        projects = MODULE.parse_operator_command("/projects")

        self.assertEqual(status.kind, "status")
        self.assertEqual(where.kind, "status")
        self.assertEqual(projects.kind, "projects")

    def test_parses_bounded_visible_dialogue(self):
        default = MODULE.parse_operator_command("/talk Find the strongest answer")
        numbered = MODULE.parse_operator_command(
            "/talk 4 Challenge the deployment plan"
        )
        natural = MODULE.parse_operator_command("talk: Compare both designs")

        self.assertEqual(
            (default.kind, default.replies, default.text),
            ("talk", 2, "Find the strongest answer"),
        )
        self.assertEqual(
            (numbered.kind, numbered.replies, numbered.text),
            ("talk", 4, "Challenge the deployment plan"),
        )
        self.assertEqual(
            (natural.kind, natural.replies, natural.text),
            ("talk", 2, "Compare both designs"),
        )

    def test_rejects_unbounded_or_empty_dialogue(self):
        for command in ("/talk", "/talk 0 stop", "/talk 7 too many", "/talk 3"):
            with self.subTest(command=command):
                with self.assertRaises(MODULE.CockpitError):
                    MODULE.parse_operator_command(command)

    def test_parses_shared_image_and_point_commands(self):
        look = MODULE.parse_operator_command(
            '/look "screenshots/broken page.png" What is wrong here?'
        )
        point = MODULE.parse_operator_command(
            '/point "screenshots/broken page.png" 420 815 Why this button?'
        )

        self.assertEqual(
            (look.kind, look.image, look.text),
            ("look", "screenshots/broken page.png", "What is wrong here?"),
        )
        self.assertEqual(
            (point.kind, point.image, point.point, point.replies, point.text),
            (
                "point",
                "screenshots/broken page.png",
                (420, 815),
                2,
                "Why this button?",
            ),
        )

    def test_rejects_malformed_shared_image_commands(self):
        for command in (
            "/look only-a-file.png",
            "/point image.png x 20 question",
            "/point image.png 10 20",
        ):
            with self.subTest(command=command):
                with self.assertRaises(MODULE.CockpitError):
                    MODULE.parse_operator_command(command)

    def test_parses_three_provider_council_surface_and_arena_controls(self):
        google = MODULE.parse_operator_command("/gemini inspect this")
        talk = MODULE.parse_operator_command(
            "/talk claude antigravity 4 Challenge this"
        )
        council = MODULE.parse_operator_command("/council 2 Decide this")
        browser = MODULE.parse_operator_command(
            '/browser-point "Delete account" Why is this dangerous?'
        )
        exact_browser = MODULE.parse_operator_command(
            "/browser collaboration-inception :: What is open?"
        )
        exact_point = MODULE.parse_operator_command(
            "/browser-point collaboration-inception :: Code :: Explain this tab"
        )
        arena = MODULE.parse_operator_command(
            '/arena claude agy --test "python -m unittest" :: repair it'
        )

        self.assertEqual((google.kind, google.target), ("ask", "antigravity"))
        self.assertEqual(talk.participants, ("claude", "antigravity"))
        self.assertEqual(talk.replies, 4)
        self.assertEqual((council.kind, council.replies), ("council", 2))
        self.assertEqual(browser.source, "Delete account")
        self.assertEqual(exact_browser.source, "collaboration-inception")
        self.assertEqual(exact_browser.text, "What is open?")
        self.assertEqual(exact_point.image, "collaboration-inception")
        self.assertEqual(exact_point.source, "Code")
        self.assertEqual(arena.participants, ("claude", "antigravity"))
        self.assertEqual(arena.test_command, "python -m unittest")

    def test_parses_memory_guard_recovery_and_winner_controls(self):
        correction = MODULE.parse_operator_command(
            "/correct codex Do not call placeholders complete"
        )
        outcome = MODULE.parse_operator_command(
            "/outcome codex testing success 70 tests passed"
        )
        recover = MODULE.parse_operator_command(
            "/off claude It became generic after compaction"
        )
        choose = MODULE.parse_operator_command("/choose run-123 gemini")

        self.assertEqual(correction.target, "codex")
        self.assertEqual(outcome.category, "testing")
        self.assertEqual(outcome.verdict, "success")
        self.assertEqual(outcome.text, "70 tests passed")
        self.assertEqual(recover.kind, "recover")
        self.assertEqual(choose.target, "antigravity")
        self.assertEqual(choose.run_id, "run-123")


class LogicalInputTests(unittest.IsolatedAsyncioTestCase):
    async def test_reassembles_the_exact_one_row_council_cutoff(self):
        command = (
            "/council 3 Design the strongest way to stop two AI models from "
            "overwriting each other. Attack every weak assumption and finish "
            "with one agreed testable solution."
        )
        self.assertEqual(command[:62], command.split(" overwriting", 1)[0])
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        queue.put_nowait(command[62:120] + "\n")
        queue.put_nowait(command[120:] + "\n")

        logical, line_count, deferred = await MODULE.collect_logical_input(
            command[:62] + "\n",
            queue,
            quiet_seconds=0.01,
        )

        self.assertEqual(line_count, 3)
        self.assertEqual(deferred, [])
        assert logical is not None
        parsed = MODULE.parse_operator_command(logical)
        expected_topic = command.split(" ", 2)[2]
        self.assertEqual(" ".join(parsed.text.split()), expected_topic)

    async def test_bracketed_multiline_paste_keeps_embedded_newlines(self):
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        queue.put_nowait(
            "second line\nthird line"
            + MODULE.BRACKETED_PASTE_END
            + "\n"
        )

        logical, line_count, deferred = await MODULE.collect_logical_input(
            MODULE.BRACKETED_PASTE_START + "/council 2 first line\n",
            queue,
            quiet_seconds=0.01,
        )

        self.assertEqual(
            logical,
            "/council 2 first line\nsecond line\nthird line",
        )
        self.assertEqual(line_count, 2)
        self.assertEqual(deferred, [])

    async def test_a_second_pasted_command_is_deferred_not_swallowed(self):
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        queue.put_nowait("/quit\n")

        logical, line_count, deferred = await MODULE.collect_logical_input(
            "/talk 1 first command\n",
            queue,
            quiet_seconds=0.01,
        )

        self.assertEqual(logical, "/talk 1 first command")
        self.assertEqual(line_count, 1)
        self.assertEqual(deferred, ["/quit\n"])


class ProviderSelectionTests(unittest.TestCase):
    def test_codex_is_not_required_when_claude_and_antigravity_are_available(self):
        selected = MODULE.select_providers(
            None,
            {},
            available=("claude", "antigravity"),
        )

        self.assertEqual(selected, ("claude", "antigravity"))

    def test_aliases_select_any_explicit_pair(self):
        selected = MODULE.select_providers(
            "gemini,codex",
            {},
            available=("claude", "codex", "antigravity"),
        )

        self.assertEqual(selected, ("antigravity", "codex"))

    def test_one_model_is_rejected(self):
        with self.assertRaisesRegex(MODULE.CockpitError, "any two"):
            MODULE.select_providers(None, {}, available=("claude",))


class ModelSelectionTests(unittest.TestCase):
    def test_quality_first_defaults_are_explicit(self):
        self.assertEqual(MODULE.DEFAULT_CODEX_MODEL, "gpt-5.6-sol")
        self.assertEqual(MODULE.DEFAULT_CODEX_REASONING_EFFORT, "max")
        self.assertEqual(MODULE.DEFAULT_CLAUDE_MODEL, "claude-opus-4-8")
        self.assertEqual(MODULE.DEFAULT_CLAUDE_EFFORT, "max")
        self.assertEqual(
            MODULE.DEFAULT_ANTIGRAVITY_MODEL,
            "Gemini 3.1 Pro (High)",
        )

    def test_claude_command_pins_opus_4_8_instead_of_moving_alias(self):
        command = MODULE.default_claude_command(Path("/tmp"), None)

        self.assertEqual(
            command[command.index("--model") + 1],
            "claude-opus-4-8",
        )
        self.assertEqual(command[command.index("--effort") + 1], "max")


class ProjectResolutionTests(unittest.TestCase):
    @staticmethod
    def make_project(home: Path, name: str) -> Path:
        project = home / name
        (project / ".git").mkdir(parents=True)
        return project

    def test_bare_termux_launch_resumes_last_project_not_phone_home(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            last = self.make_project(home, "collaboration-inception")
            expected = last.resolve()

            cwd, source = MODULE.resolve_working_directory(
                [], None, {"cwd": str(last)}, launch_cwd=home, home=home
            )

        self.assertEqual(cwd, expected)
        self.assertEqual(source, "last project")

    def test_spoken_project_name_resolves_without_cd_or_hyphen(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            agent_bridge = self.make_project(home, "agent-bridge")
            expected = agent_bridge.resolve()
            self.make_project(home, "collaboration-inception")

            cwd, source = MODULE.resolve_working_directory(
                ["agent", "bridge"], None, {}, launch_cwd=home, home=home
            )

        self.assertEqual(cwd, expected)
        self.assertEqual(source, "named project")

    def test_unknown_project_error_lists_valid_names(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            self.make_project(home, "agent-bridge")

            with self.assertRaisesRegex(
                MODULE.CockpitError, "Available projects: agent-bridge"
            ):
                MODULE.resolve_named_project("does not exist", home)

    def test_launcher_accepts_plain_project_words(self):
        args = MODULE.parser().parse_args(["agent", "bridge"])

        self.assertEqual(args.project, ["agent", "bridge"])
        self.assertIsNone(args.cwd)


class SharedImageTests(unittest.TestCase):
    def test_stages_a_private_untracked_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "screen.png"
            source.write_bytes(b"image bytes")
            staged = MODULE.stage_shared_image(
                str(source),
                base,
                destination_dir=base / "private",
            )

            self.assertEqual(staged.read_bytes(), b"image bytes")
            if os.name != "nt":
                self.assertEqual(staged.stat().st_mode & 0o777, 0o600)
                self.assertEqual(staged.parent.stat().st_mode & 0o777, 0o700)

    def test_marks_georges_point_with_a_circle_and_crosshair(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "screen.png"
            destination = base / "marked.png"
            source.write_bytes(b"image bytes")
            commands = []

            def fake_run(command, **_kwargs):
                commands.append(command)
                if command[1] == "identify":
                    return subprocess.CompletedProcess(command, 0, "1000 800", "")
                Path(command[-1]).write_bytes(b"marked")
                return subprocess.CompletedProcess(command, 0, "", "")

            with (
                patch.object(MODULE.shutil, "which", return_value="/bin/magick"),
                patch.object(MODULE.subprocess, "run", side_effect=fake_run),
            ):
                dimensions = MODULE.annotate_shared_image(
                    source, destination, 420, 315
                )

            self.assertEqual(dimensions, (1000, 800))
            self.assertEqual(destination.read_bytes(), b"marked")
            rendered = " ".join(commands[1])
            self.assertIn("circle 420,315", rendered)
            self.assertIn("line 380,315 460,315", rendered)


class PortableLineageTests(unittest.TestCase):
    def test_copy_without_private_continuity_state_starts_fresh(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)

            source = MODULE.select_codex_source_thread(
                {},
                None,
                continuity_path=base / "missing-state.json",
                session_root=base / "missing-sessions",
            )

        self.assertIsNone(source)

    def test_downloaded_copy_starts_fresh_without_georges_rollout(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            continuity = base / "state.json"
            continuity.write_text(
                json.dumps({"canonical_thread_id": THREAD}), encoding="utf-8"
            )

            source = MODULE.select_codex_source_thread(
                {},
                None,
                continuity_path=continuity,
                session_root=base / "empty-sessions",
            )

        self.assertIsNone(source)

    def test_georges_install_keeps_the_native_lineage(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            continuity = base / "state.json"
            continuity.write_text(
                json.dumps({"canonical_thread_id": THREAD}), encoding="utf-8"
            )
            rollout = base / "sessions" / f"rollout-{THREAD}.jsonl"
            rollout.parent.mkdir()
            rollout.write_text("{}\n", encoding="utf-8")

            source = MODULE.select_codex_source_thread(
                {},
                None,
                continuity_path=continuity,
                session_root=base / "sessions",
            )

        self.assertEqual(source, THREAD)


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
            if os.name != "nt":
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
        self.assertIn("cockpit mode attached", instructions)

    def test_turn_modes_make_action_and_discussion_boundaries_explicit(self):
        discussion = MODULE.mode_prompt("compare this", "discussion")
        work = MODULE.mode_prompt("inspect this", "work")
        action = MODULE.mode_prompt("ship it", "action")

        self.assertIn("COCKPIT MODE: DISCUSSION", discussion)
        self.assertIn("read-only", discussion)
        self.assertIn("COCKPIT MODE: WORK", work)
        self.assertIn("If he asks for an answer or review only", work)
        self.assertIn("COCKPIT MODE: ACTION", action)
        self.assertIn("Perform the requested work now", action)


class ProtocolParsingTests(unittest.TestCase):
    def test_claude_stream_can_hold_the_largest_base64_image_record(self):
        largest_base64_record = (MODULE.MAX_ATTACHMENT_BYTES * 4 // 3) + 16_384

        self.assertGreater(MODULE.CLAUDE_STREAM_LIMIT, largest_base64_record)

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

    def test_termux_claude_command_uses_streaming_permission_protocol(self):
        with patch.dict(
            os.environ, {"PREFIX": "/data/data/com.termux/files/usr"}, clear=False
        ):
            command = MODULE.default_claude_command(
                Path("/tmp/work"), CLAUDE_SESSION, "SHARED CONTRACT TEST"
            )

        self.assertEqual(command[:4], ["proot-distro", "login", "debian", "--"])
        self.assertIn("stream-json", command)
        tools_index = command.index("--tools")
        self.assertEqual(command[tools_index + 1], "default")
        mode_index = command.index("--permission-mode")
        self.assertEqual(command[mode_index + 1], "default")
        prompt_tool_index = command.index("--permission-prompt-tool")
        self.assertEqual(command[prompt_tool_index + 1], "stdio")
        self.assertNotIn("--allowedTools", command)
        self.assertIn("SHARED CONTRACT TEST", command)
        self.assertEqual(command[-2:], ["--resume", CLAUDE_SESSION])


class ClaudePermissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_tool_callback_follows_georges_per_turn_grant(self):
        endpoint = MODULE.ClaudeEndpoint(Path("/tmp"), CLAUDE_SESSION)
        endpoint._write_message = AsyncMock()
        request = {
            "type": "control_request",
            "request_id": "permission-1",
            "request": {
                "subtype": "can_use_tool",
                "tool_name": "Bash",
                "input": {"command": "git push origin main"},
            },
        }
        loop = asyncio.get_running_loop()
        endpoint.active = MODULE._ClaudeTurn(
            done=loop.create_future(), emit=lambda _text: None, working=False
        )

        await endpoint._handle_control_request(request)
        denied = endpoint._write_message.await_args.args[0]
        self.assertEqual(
            denied["response"]["response"]["behavior"], "deny"
        )

        endpoint._write_message.reset_mock()
        endpoint.active.working = True
        await endpoint._handle_control_request(request)
        allowed = endpoint._write_message.await_args.args[0]
        self.assertEqual(
            allowed["response"]["response"]["behavior"], "allow"
        )
        self.assertEqual(
            allowed["response"]["response"]["updatedInput"],
            {"command": "git push origin main"},
        )

    async def test_read_only_turn_can_open_only_its_attached_image(self):
        with tempfile.TemporaryDirectory() as directory:
            shared = Path(directory) / "shared.png"
            other = Path(directory) / "other.png"
            shared.write_bytes(b"shared")
            other.write_bytes(b"other")
            endpoint = MODULE.ClaudeEndpoint(Path("/tmp"), CLAUDE_SESSION)
            endpoint._write_message = AsyncMock()
            loop = asyncio.get_running_loop()
            endpoint.active = MODULE._ClaudeTurn(
                done=loop.create_future(),
                emit=lambda _text: None,
                working=False,
                attachments=(shared.resolve(),),
            )

            async def permission(path: Path):
                endpoint._write_message.reset_mock()
                await endpoint._handle_control_request(
                    {
                        "type": "control_request",
                        "request_id": f"read-{path.name}",
                        "request": {
                            "subtype": "can_use_tool",
                            "tool_name": "Read",
                            "input": {"file_path": str(path)},
                        },
                    }
                )
                return endpoint._write_message.await_args.args[0]["response"][
                    "response"
                ]["behavior"]

            self.assertEqual(await permission(shared), "allow")
            self.assertEqual(await permission(other), "deny")


class AntigravityEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_antigravity_refuses_a_silent_flash_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            helper = base / "fake_agy.py"
            helper.write_text(
                "print('Gemini 3.6 Flash (High)')\n",
                encoding="utf-8",
            )
            endpoint = MODULE.AntigravityEndpoint(
                base,
                command=[sys.executable, str(helper)],
                validate_model=True,
            )

            with self.assertRaisesRegex(
                MODULE.CockpitError,
                "will not silently downgrade",
            ):
                await endpoint.start()

    async def test_native_antigravity_uses_sandboxed_print_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            helper = base / "fake_agy.py"
            helper.write_text(
                "import json, sys\nprint(json.dumps(sys.argv[1:]))\n",
                encoding="utf-8",
            )
            endpoint = MODULE.AntigravityEndpoint(
                base,
                command=[sys.executable, str(helper)],
                instructions="SHARED CONTRACT",
            )

            result = await endpoint.ask(
                "Inspect this.",
                lambda _text: None,
                working=False,
            )
            arguments = json.loads(result.text)

            self.assertIn("--sandbox", arguments)
            self.assertIn("--print", arguments)
            self.assertEqual(
                arguments[arguments.index("--model") + 1],
                "Gemini 3.1 Pro (High)",
            )
            self.assertIn("SHARED CONTRACT", arguments[-1])
            self.assertTrue(endpoint.authenticated)

    async def test_legacy_gemini_can_run_without_codex(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            helper = base / "gemini-fake.py"
            helper.write_text(
                "import json, sys\nprint(json.dumps(sys.argv[1:]))\n",
                encoding="utf-8",
            )
            endpoint = MODULE.AntigravityEndpoint(
                base,
                command=[sys.executable, str(helper)],
                instructions="SHARED CONTRACT",
            )

            result = await endpoint.ask(
                "Inspect this.",
                lambda _text: None,
                working=True,
            )
            arguments = json.loads(result.text)

            self.assertTrue(endpoint.legacy_gemini)
            self.assertIn("--session-id", arguments)
            self.assertIn("yolo", arguments)
            self.assertIn("--prompt", arguments)
            self.assertEqual(
                arguments[arguments.index("--model") + 1],
                "gemini-3.1-pro-preview",
            )

    async def test_antigravity_does_not_mistake_auth_timeout_for_an_answer(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            helper = base / "fake_agy.py"
            helper.write_text(
                "print('Authentication required.')\n"
                "print('Waiting for authentication (timeout 30s)...')\n"
                "print('Error: authentication timed out.')\n",
                encoding="utf-8",
            )
            endpoint = MODULE.AntigravityEndpoint(
                base,
                command=[sys.executable, str(helper)],
            )

            with self.assertRaisesRegex(MODULE.CockpitError, "needs sign-in"):
                await endpoint.ask("Inspect this.", lambda _text: None)

            self.assertFalse(endpoint.authenticated)


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
        if method == "turn/start":
            assert self.active is not None
            active = self.active
            asyncio.get_running_loop().call_soon(
                active.done.set_result,
                MODULE.TurnResult("codex", "done"),
            )
            return {"turn": {"id": "turn-1"}}
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
            self.assertEqual(resume[1]["model"], "gpt-5.6-sol")
            self.assertEqual(
                resume[1]["config"]["model_reasoning_effort"],
                "max",
            )
            self.assertEqual(thread_id, THREAD)
            endpoint.error_handle.close()

    async def test_each_codex_turn_resets_read_only_or_full_working_policy(self):
        endpoint = FakeCodexEndpoint()

        await endpoint.ask("compare", lambda _text: None, working=False)
        await endpoint.ask("implement", lambda _text: None, working=True)

        turns = [entry[1] for entry in endpoint.sent if entry[0] == "turn/start"]
        self.assertEqual(turns[0]["sandboxPolicy"]["type"], "readOnly")
        self.assertFalse(turns[0]["sandboxPolicy"]["networkAccess"])
        self.assertEqual(turns[1]["sandboxPolicy"]["type"], "dangerFullAccess")
        self.assertEqual(turns[0]["approvalPolicy"], "never")
        self.assertEqual(turns[1]["approvalPolicy"], "never")
        self.assertEqual(turns[1]["cwd"], str(endpoint.cwd))

    async def test_codex_receives_the_shared_image_as_native_local_input(self):
        endpoint = FakeCodexEndpoint()
        image = Path("/tmp/shared-cockpit.png")

        await endpoint.ask(
            "inspect the marked area",
            lambda _text: None,
            working=False,
            attachments=(image,),
        )

        turn = next(entry[1] for entry in endpoint.sent if entry[0] == "turn/start")
        self.assertEqual(
            turn["input"],
            [
                {"type": "text", "text": "inspect the marked area"},
                {"type": "localImage", "path": str(image.resolve())},
            ],
        )

    async def test_codex_close_releases_the_owning_subprocess_transport(self):
        endpoint = FakeCodexEndpoint()

        class Transport:
            closed = False

            def close(self):
                self.closed = True

        transport = Transport()
        endpoint.process = type(
            "ExitedProcess",
            (),
            {"returncode": 0, "_transport": transport},
        )()

        await endpoint.close()

        self.assertTrue(transport.closed)


class FakeEndpoint:
    def __init__(self, name):
        self.name = name
        self.calls = []
        self.attachment_calls = []
        self.interruptions = 0

    async def ask(self, prompt, emit, working=False, attachments=()):
        self.calls.append((prompt, working))
        self.attachment_calls.append(tuple(attachments))
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

    async def ask(self, prompt, emit, working=False, attachments=()):
        self.calls.append((prompt, working))
        self.attachment_calls.append(tuple(attachments))
        self.started.set()
        await self.released.wait()
        return MODULE.TurnResult(self.name, "", "interrupted")

    async def interrupt(self):
        await super().interrupt()
        self.released.set()


class BrokerTests(unittest.IsolatedAsyncioTestCase):
    async def test_status_names_each_requested_model(self):
        codex = FakeEndpoint("codex")
        codex.model_label = "GPT-5.6 Sol (max)"
        claude = FakeEndpoint("claude")
        claude.model_label = "Claude Opus 4.8 (max)"
        broker = MODULE.Broker(codex, claude)

        output = io.StringIO()
        with redirect_stdout(output):
            MODULE.show_status(broker, {"cwd": "/tmp/project"})

        rendered = output.getvalue()
        self.assertIn("Codex:", rendered)
        self.assertIn("[GPT-5.6 Sol (max)]", rendered)
        self.assertIn("Claude:", rendered)
        self.assertIn("[Claude Opus 4.8 (max)]", rendered)

    async def test_three_model_council_challenges_every_opening_answer(self):
        codex = FakeEndpoint("codex")
        claude = FakeEndpoint("claude")
        antigravity = FakeEndpoint("antigravity")
        broker = MODULE.Broker(codex, claude, antigravity=antigravity)

        with redirect_stdout(io.StringIO()):
            results = await broker.council("Find the strongest repair.", rounds=1)

        self.assertEqual(set(results), {"claude", "codex", "antigravity"})
        self.assertEqual(len(codex.calls), 2)
        self.assertEqual(len(claude.calls), 2)
        self.assertEqual(len(antigravity.calls), 2)
        for endpoint in (codex, claude, antigravity):
            challenge = endpoint.calls[1][0]
            self.assertIn("Claude", challenge)
            self.assertIn("Codex", challenge)
            self.assertIn("Antigravity", challenge)

    async def test_default_guard_checks_a_draft_before_the_only_working_turn(self):
        codex = FakeEndpoint("codex")
        claude = FakeEndpoint("claude")
        broker = MODULE.Broker(codex, claude)

        with redirect_stdout(io.StringIO()):
            results = await broker.guarded_ask(
                "codex", "Repair and test it.", mode="action"
            )

        self.assertEqual(set(results), {"codex"})
        self.assertEqual([working for _, working in codex.calls], [False, True])
        self.assertEqual([working for _, working in claude.calls], [False])
        self.assertIn("Claude's critique", codex.calls[-1][0])

    async def test_three_model_guard_uses_both_independent_critics(self):
        codex = FakeEndpoint("codex")
        claude = FakeEndpoint("claude")
        antigravity = FakeEndpoint("antigravity")
        broker = MODULE.Broker(codex, claude, antigravity=antigravity)

        with redirect_stdout(io.StringIO()):
            await broker.guarded_ask("codex", "Repair and prove it.", mode="work")

        final_prompt = codex.calls[-1][0]
        self.assertIn("Claude's critique", final_prompt)
        self.assertIn("Antigravity's critique", final_prompt)
        self.assertEqual([working for _, working in claude.calls], [False])
        self.assertEqual([working for _, working in antigravity.calls], [False])

    async def test_plain_status_names_project_and_both_connections(self):
        codex = FakeEndpoint("codex")
        claude = FakeEndpoint("claude")
        codex.process = type("Process", (), {"returncode": None})()
        claude.process = type("Process", (), {"returncode": None})()
        broker = MODULE.Broker(codex, claude)
        output = io.StringIO()

        with redirect_stdout(output):
            MODULE.show_status(
                broker,
                {"cwd": "/data/data/com.termux/files/home/agent-bridge"},
            )

        visible = output.getvalue()
        self.assertIn("Working on: agent-bridge", visible)
        self.assertIn("Claude: CONNECTED", visible)
        self.assertIn("Codex: CONNECTED", visible)
        self.assertIn("Both agents are connected", visible)

    async def test_status_names_only_the_agent_working_now(self):
        codex = FakeEndpoint("codex")
        claude = FakeEndpoint("claude")
        codex.process = type("Process", (), {"returncode": None})()
        claude.process = type("Process", (), {"returncode": None})()
        broker = MODULE.Broker(codex, claude)
        broker.active_agents = {"claude"}
        output = io.StringIO()

        with redirect_stdout(output):
            MODULE.show_status(broker, {"cwd": "/phone/agent-bridge"})

        visible = output.getvalue()
        self.assertIn("Claude: WORKING NOW", visible)
        self.assertIn("Codex: CONNECTED — waiting for George", visible)
        self.assertIn("A turn is running now: Claude", visible)
        self.assertNotIn("Nothing happens until", visible)

    async def test_both_get_exact_prompt_once_and_never_auto_advance(self):
        codex = FakeEndpoint("codex")
        claude = FakeEndpoint("claude")
        broker = MODULE.Broker(codex, claude)

        with redirect_stdout(io.StringIO()):
            results = await broker.ask("both", "same exact question")

        self.assertEqual(len(claude.calls), 1)
        self.assertEqual(len(codex.calls), 1)
        self.assertFalse(claude.calls[0][1])
        self.assertFalse(codex.calls[0][1])
        self.assertIn("COCKPIT MODE: DISCUSSION", claude.calls[0][0])
        self.assertIn("same exact question", claude.calls[0][0])
        self.assertEqual(set(results), {"claude", "codex"})
        self.assertFalse(broker.active_agents)

    async def test_single_agent_turns_work_and_action_grants_real_tools(self):
        codex = FakeEndpoint("codex")
        claude = FakeEndpoint("claude")
        broker = MODULE.Broker(codex, claude)

        with redirect_stdout(io.StringIO()):
            await broker.ask("codex", "inspect and fix it")
            await broker.ask("claude", "execute now", mode="action")

        self.assertTrue(codex.calls[0][1])
        self.assertIn("COCKPIT MODE: WORK", codex.calls[0][0])
        self.assertTrue(claude.calls[0][1])
        self.assertIn("COCKPIT MODE: ACTION", claude.calls[0][0])

    async def test_both_cannot_receive_working_permissions(self):
        broker = MODULE.Broker(FakeEndpoint("codex"), FakeEndpoint("claude"))

        with self.assertRaisesRegex(MODULE.CockpitError, "/both is read-only"):
            await broker.ask("both", "edit the same checkout", mode="action")

    async def test_forwarding_requires_george_and_targets_only_one_agent(self):
        codex = FakeEndpoint("codex")
        claude = FakeEndpoint("claude")
        broker = MODULE.Broker(codex, claude)
        broker.last["claude"] = "Claude's proposal"

        with redirect_stdout(io.StringIO()):
            await broker.pass_answer("claude", "codex", "Find the flaw")

        self.assertEqual(len(codex.calls), 1)
        self.assertFalse(codex.calls[0][1])
        self.assertIn("Claude's proposal", codex.calls[0][0])
        self.assertIn("Find the flaw", codex.calls[0][0])
        self.assertEqual(claude.calls, [])

    async def test_talk_is_visible_bounded_and_truly_cross_agent(self):
        codex = FakeEndpoint("codex")
        claude = FakeEndpoint("claude")
        broker = MODULE.Broker(codex, claude)
        output = io.StringIO()

        with redirect_stdout(output):
            results = await broker.talk("Choose the repair", reply_turns=2)

        self.assertEqual(len(claude.calls), 2)
        self.assertEqual(len(codex.calls), 2)
        self.assertTrue(all(not working for _, working in claude.calls + codex.calls))
        self.assertIn("claude answer", codex.calls[1][0])
        self.assertIn("codex answer", claude.calls[1][0])
        self.assertIn("Original topic: Choose the repair", codex.calls[1][0])
        self.assertIn("[TALK] Reply 1/2: Codex answers Claude.", output.getvalue())
        self.assertEqual(set(results), {"claude", "codex"})

    async def test_look_gives_both_agents_the_exact_same_private_image(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "screen.png"
            source.write_bytes(b"screen")
            codex = FakeEndpoint("codex")
            claude = FakeEndpoint("claude")
            broker = MODULE.Broker(
                codex,
                claude,
                attachment_dir=base / "attachments",
            )

            with redirect_stdout(io.StringIO()):
                await broker.look(str(source), "Inspect this")

            self.assertEqual(len(codex.attachment_calls), 1)
            self.assertEqual(codex.attachment_calls, claude.attachment_calls)
            shared = codex.attachment_calls[0][0]
            self.assertEqual(shared.read_bytes(), b"screen")

    async def test_stop_ends_dialogue_before_any_cross_agent_reply(self):
        codex = SlowEndpoint("codex")
        claude = SlowEndpoint("claude")
        broker = MODULE.Broker(codex, claude)

        with redirect_stdout(io.StringIO()):
            dialogue = asyncio.create_task(
                broker.talk("Wait for George", reply_turns=6)
            )
            await asyncio.gather(codex.started.wait(), claude.started.wait())
            await broker.stop()
            await dialogue

        self.assertEqual(len(codex.calls), 1)
        self.assertEqual(len(claude.calls), 1)

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
        self.assertFalse(claude.calls[0][1])
        self.assertIn("ORCHID-731", claude.calls[0][0])
        self.assertIn("Reconsider orchid distribution.", claude.calls[0][0])

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
