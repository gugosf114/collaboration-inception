import importlib.util
import contextlib
import io
import os
import sys
import tempfile
import threading
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
    def test_cli_visibly_types_by_default_and_instant_is_explicit(self):
        visible = TERMUX_SEND.parser().parse_args(["pts/2", "hello"])
        instant = TERMUX_SEND.parser().parse_args(
            ["--instant", "pts/2", "hello"]
        )
        self.assertTrue(visible.visible)
        self.assertFalse(instant.visible)

    def test_parses_supported_tty_names(self):
        self.assertEqual(TERMUX_SEND.tty_index("pts/2"), 2)
        self.assertEqual(TERMUX_SEND.tty_index("/dev/pts/17"), 17)
        with self.assertRaises(TERMUX_SEND.SendError):
            TERMUX_SEND.tty_index("2")

    def test_validates_session_titles(self):
        self.assertEqual(
            TERMUX_SEND.validate_session_title("  LinkedIn Applications  "),
            "LinkedIn Applications",
        )
        for title in (
            "",
            "   ",
            "bad\nname",
            "x" * 81,
            "other",
            "pid:123",
            "pts/2",
            "/dev/pts/2",
            "--instant",
        ):
            with self.assertRaises(TERMUX_SEND.SendError):
                TERMUX_SEND.validate_session_title(title)

    def test_registers_resolves_and_replaces_an_exact_live_title(self):
        session = TERMUX_SEND.CodexSession(
            pid=100, tty_index=1, termux_session="termux-1"
        )
        writes = []
        with tempfile.TemporaryDirectory() as directory:
            registry = Path(directory) / "titles.json"
            registered = TERMUX_SEND.register_session_title(
                session,
                "LinkedIn Applications",
                [session],
                registry_path=registry,
                session_renamer=lambda target, title: writes.append((target, title)),
            )
            self.assertEqual(registered, "LinkedIn Applications")
            self.assertEqual(
                TERMUX_SEND.resolve_session_title(
                    "LinkedIn Applications", [session], registry_path=registry
                ),
                session,
            )
            self.assertEqual(
                TERMUX_SEND.select_session(
                    "LinkedIn Applications", [session], registry_path=registry
                ),
                session,
            )
            with self.assertRaisesRegex(TERMUX_SEND.SendError, "No session"):
                TERMUX_SEND.resolve_session_title(
                    "linkedin applications", [session], registry_path=registry
                )

            TERMUX_SEND.register_session_title(
                session,
                "Security Training",
                [session],
                registry_path=registry,
                session_renamer=lambda target, title: writes.append((target, title)),
            )
            titles = TERMUX_SEND.load_title_registry(registry)
            self.assertEqual(list(titles), ["security training"])
            self.assertEqual(titles["security training"]["title"], "Security Training")
        self.assertEqual(
            writes,
            [
                (session, "LinkedIn Applications"),
                (session, "Security Training"),
            ],
        )

    def test_refuses_duplicate_live_and_stale_session_titles(self):
        first = TERMUX_SEND.CodexSession(pid=100, tty_index=1)
        second = TERMUX_SEND.CodexSession(pid=200, tty_index=2)
        with tempfile.TemporaryDirectory() as directory:
            registry = Path(directory) / "titles.json"
            TERMUX_SEND.register_session_title(
                first,
                "LinkedIn Applications",
                [first, second],
                registry_path=registry,
                session_renamer=lambda _target, _title: None,
            )
            with self.assertRaisesRegex(TERMUX_SEND.SendError, "already belongs"):
                TERMUX_SEND.register_session_title(
                    second,
                    "LINKEDIN APPLICATIONS",
                    [first, second],
                    registry_path=registry,
                    session_renamer=lambda _target, _title: None,
                )
            with self.assertRaisesRegex(TERMUX_SEND.SendError, "stale"):
                TERMUX_SEND.resolve_session_title(
                    "LinkedIn Applications", [], registry_path=registry
                )

    def test_recycled_pid_and_tty_do_not_inherit_a_stale_title(self):
        original = TERMUX_SEND.CodexSession(
            pid=100,
            tty_index=1,
            termux_session="termux-1",
            start_time_ticks=111,
        )
        recycled = TERMUX_SEND.CodexSession(
            pid=100,
            tty_index=1,
            termux_session="termux-1",
            start_time_ticks=222,
        )
        with tempfile.TemporaryDirectory() as directory:
            registry = Path(directory) / "titles.json"
            TERMUX_SEND.register_session_title(
                original,
                "LinkedIn Applications",
                [original],
                registry_path=registry,
                session_renamer=lambda _target, _title: None,
            )
            with self.assertRaisesRegex(TERMUX_SEND.SendError, "stale"):
                TERMUX_SEND.resolve_session_title(
                    "LinkedIn Applications", [recycled], registry_path=registry
                )
            self.assertIsNone(
                TERMUX_SEND.session_title(recycled, registry_path=registry)
            )

    def test_malformed_title_registry_fails_with_a_send_error(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = Path(directory) / "titles.json"
            for malformed in (
                "[]\n",
                '{"schema_version": 2, "titles": {"bad": null}}\n',
            ):
                registry.write_text(malformed, encoding="utf-8")
                with self.assertRaisesRegex(TERMUX_SEND.SendError, "Invalid"):
                    TERMUX_SEND.load_title_registry(registry)

    def test_competing_title_registrations_are_serialized(self):
        first_session = TERMUX_SEND.CodexSession(
            pid=100, tty_index=1, start_time_ticks=111
        )
        second_session = TERMUX_SEND.CodexSession(
            pid=200, tty_index=2, start_time_ticks=222
        )
        sessions = [first_session, second_session]
        first_entered = threading.Event()
        release_first = threading.Event()
        second_started = threading.Event()
        successes = []
        failures = []

        def first_writer(_target, _title):
            first_entered.set()
            release_first.wait(1)

        def first_registration():
            try:
                successes.append(
                    TERMUX_SEND.register_session_title(
                        first_session,
                        "LinkedIn Applications",
                        sessions,
                        registry_path=registry,
                        session_renamer=first_writer,
                    )
                )
            except Exception as exc:  # pragma: no cover - failure is asserted below
                failures.append(exc)

        def second_registration():
            second_started.set()
            try:
                successes.append(
                    TERMUX_SEND.register_session_title(
                        second_session,
                        "LinkedIn Applications",
                        sessions,
                        registry_path=registry,
                        session_renamer=lambda _target, _title: None,
                    )
                )
            except TERMUX_SEND.SendError as exc:
                failures.append(exc)

        with tempfile.TemporaryDirectory() as directory:
            registry = Path(directory) / "titles.json"
            first = threading.Thread(target=first_registration)
            second = threading.Thread(target=second_registration)
            first.start()
            self.assertTrue(first_entered.wait(1))
            second.start()
            self.assertTrue(second_started.wait(1))
            second.join(0.05)
            self.assertTrue(second.is_alive())
            release_first.set()
            first.join(1)
            second.join(1)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(successes, ["LinkedIn Applications"])
        self.assertEqual(len(failures), 1)
        self.assertIn("already belongs", str(failures[0]))

    def test_set_title_cli_registers_the_calling_session(self):
        session = TERMUX_SEND.CodexSession(pid=100, tty_index=7)
        with mock.patch.object(
            TERMUX_SEND, "codex_sessions", return_value=[session]
        ), mock.patch.object(
            TERMUX_SEND, "current_ancestor_tty", return_value=7
        ), mock.patch.object(
            TERMUX_SEND,
            "register_session_title",
            return_value="LinkedIn Applications",
        ) as register, contextlib.redirect_stdout(io.StringIO()) as output:
            result = TERMUX_SEND.main(
                ["--set-title", "LinkedIn Applications"]
            )
        self.assertEqual(result, 0)
        register.assert_called_once_with(
            session, "LinkedIn Applications", [session]
        )
        self.assertEqual(
            output.getvalue(),
            "session-name='LinkedIn Applications' verified=true tty=/dev/pts/7\n",
        )

    def test_mcp_notification_null_response_is_accepted(self):
        self.assertEqual(TERMUX_SEND._decode_mcp_json(b"null"), {})

    def test_termux_session_position_uses_creation_order(self):
        session = TERMUX_SEND.CodexSession(
            pid=100, tty_index=7, termux_session="46"
        )
        self.assertEqual(
            TERMUX_SEND.termux_session_position(session, [47, 41, 46]), 2
        )

    def test_screen_state_wait_retries_a_transient_empty_window_failure(self):
        ready = """screen:1080x2520
--- window:1 type:APPLICATION pkg:com.termux title:Termux layer:0 focused:true ---
"""

        class FakeBridge:
            def __init__(self):
                self.calls = 0

            def call(self, tool, arguments=None):
                self.calls += 1
                if self.calls == 1:
                    raise TERMUX_SEND.SendError("No windows available")
                return ready

        bridge = FakeBridge()
        screen = TERMUX_SEND._wait_for_screen_state(
            bridge,
            lambda state: "pkg:com.termux" in state,
            timeout=0.1,
            poll_interval=0,
        )
        self.assertEqual(screen, ready)
        self.assertEqual(bridge.calls, 2)

    def test_open_termux_drawer_retries_a_consumed_gesture(self):
        normal = """screen:1080x2520
--- window:1 type:APPLICATION pkg:com.termux title:Termux layer:0 focused:true ---
node_terminal\tView\t-\t-\tcom.termux:id/terminal_view\t0,0,1080,2200\ton,ena
"""
        drawer = """screen:1080x2520
--- window:1 type:APPLICATION pkg:com.termux title:Termux layer:0 focused:true ---
node_drawer\tLinearLayout\t-\t-\tcom.termux:id/left_drawer\t0,0,700,2200\ton,ena
"""

        class FakeBridge:
            def __init__(self):
                self.calls = []

            def call(self, tool, arguments=None):
                self.calls.append((tool, arguments or {}))
                return "ok"

        bridge = FakeBridge()
        with mock.patch.object(
            TERMUX_SEND,
            "_wait_for_screen_state",
            side_effect=[normal, drawer],
        ):
            screen = TERMUX_SEND._open_termux_drawer(bridge, normal)
        self.assertEqual(screen, drawer)
        self.assertEqual(
            [tool for tool, _arguments in bridge.calls].count("android_swipe"),
            2,
        )

    def test_real_termux_name_is_set_and_read_back_through_the_drawer(self):
        normal = """screen:1080x2520
--- window:1 type:APPLICATION pkg:com.termux title:Termux layer:0 focused:true ---
node_terminal\tView\t-\t-\tcom.termux:id/terminal_view\t0,0,1080,2200\ton,ena
"""
        drawer = """screen:1080x2520
--- window:1 type:APPLICATION pkg:com.termux title:Termux layer:0 focused:true ---
node_drawer\tLinearLayout\t-\t-\tcom.termux:id/left_drawer\t0,0,700,2200\ton,ena
node_first\tTextView\t[1] home\t-\tcom.termux:id/session_title\t0,200,700,400\ton,lclk,ena
node_second\tTextView\t[2] home\t-\tcom.termux:id/session_title\t0,400,700,600\ton,lclk,ena
node_third\tTextView\t[3] home\t-\tcom.termux:id/session_title\t0,600,700,800\ton,lclk,ena
"""
        empty_drawer = """screen:1080x2520
--- window:1 type:APPLICATION pkg:com.termux title:Termux layer:0 focused:true ---
node_drawer\tLinearLayout\t-\t-\tcom.termux:id/left_drawer\t0,0,700,2200\ton,ena
"""
        dialog = """screen:1080x2520
--- window:2 type:APPLICATION pkg:com.termux title:Set session name layer:0 focused:true ---
node_edit_old\tEditText\thome\t-\t-\t100,700,900,850\ton,edt,ena
node_cancel\tButton\tCANCEL\t-\tandroid:id/button2\t600,900,780,1020\ton,clk,ena
node_set_old\tButton\tSET\t-\tandroid:id/button1\t780,900,980,1020\ton,clk,ena
"""
        fresh_dialog = dialog.replace("node_edit_old", "node_edit_fresh")
        typed_dialog = fresh_dialog.replace(
            "node_edit_fresh", "node_edit_typed"
        ).replace("node_set_old", "node_set_new").replace(
            "\tEditText\thome\t", "\tEditText\tLinkedIn Applications\t"
        )
        renamed = """screen:1080x2520
--- window:3 type:INPUT_METHOD pkg:keyboard title:Keyboard layer:1 focused:false ---
node_keyboard\tView\t-\t-\t-\t0,1500,1080,2520\ton,ena
--- window:1 type:APPLICATION pkg:com.termux title:Termux layer:0 focused:true ---
node_drawer2\tLinearLayout\t-\t-\tcom.termux:id/left_drawer\t0,0,700,1400\ton,ena
node_second_done\tTextView\t[2] LinkedIn Applications home\t-\tcom.termux:id/session_title\t0,400,700,600\ton,lclk,ena
"""
        clean_drawer = renamed.replace(
            "--- window:3 type:INPUT_METHOD pkg:keyboard title:Keyboard layer:1 focused:false ---\n"
            "node_keyboard\tView\t-\t-\t-\t0,1500,1080,2520\ton,ena\n",
            "",
        )

        class FakeBridge:
            def __init__(self):
                self.screens = iter(
                    [
                        normal,
                        empty_drawer,
                        drawer,
                        dialog,
                        fresh_dialog,
                        typed_dialog,
                        renamed,
                        clean_drawer,
                        normal,
                    ]
                )
                self.calls = []

            def call(self, tool, arguments=None):
                self.calls.append((tool, arguments or {}))
                if tool == "android_get_screen_state":
                    return next(self.screens)
                if (
                    tool == "android_type_replace_text"
                    and arguments.get("node_id") != "node_edit_fresh"
                ):
                    raise TERMUX_SEND.SendError(
                        "Node 'node_edit_old' not found in accessibility tree"
                    )
                return "ok"

        bridge = FakeBridge()
        session = TERMUX_SEND.CodexSession(
            pid=100, tty_index=7, termux_session="46"
        )
        TERMUX_SEND.rename_termux_app_session(
            session,
            "LinkedIn Applications",
            client=bridge,
            session_numbers=[41, 46, 47],
        )
        self.assertIn(
            (
                "android_long_click_node",
                {"node_id": "node_second"},
            ),
            bridge.calls,
        )
        self.assertIn(
            ("android_click_node", {"node_id": "node_set_new"}), bridge.calls
        )
        self.assertIn(
            (
                "android_type_replace_text",
                {
                    "node_id": "node_edit_fresh",
                    "search": "home",
                    "new_text": "LinkedIn Applications",
                    "typing_speed": 10,
                    "typing_speed_variance": 0,
                },
            ),
            bridge.calls,
        )
        self.assertEqual(
            [tool for tool, _arguments in bridge.calls].count("android_press_back"),
            1,
        )
        self.assertEqual(
            [tool for tool, _arguments in bridge.calls].count("android_swipe"),
            2,
        )

    def test_restore_waits_for_stale_overlays_without_backing_out_of_termux(self):
        overlaid = """screen:1080x2520
--- window:2 type:INPUT_METHOD pkg:keyboard title:Keyboard layer:1 focused:false ---
node_keyboard\tView\t-\t-\t-\t0,1500,1080,2520\ton,ena
--- window:1 type:APPLICATION pkg:com.termux title:Termux layer:0 focused:true ---
node_drawer\tLinearLayout\t-\t-\tcom.termux:id/left_drawer\t0,0,700,1400\ton,ena
"""
        drawer = """screen:1080x2520
--- window:1 type:APPLICATION pkg:com.termux title:Termux layer:0 focused:true ---
node_drawer\tLinearLayout\t-\t-\tcom.termux:id/left_drawer\t0,0,700,1400\ton,ena
"""
        normal = """screen:1080x2520
--- window:1 type:APPLICATION pkg:com.termux title:Termux layer:0 focused:true ---
node_terminal\tView\t-\t-\tcom.termux:id/terminal_view\t0,0,1080,2200\ton,ena
"""

        class FakeBridge:
            def __init__(self):
                self.screens = iter([overlaid, drawer, drawer, normal])
                self.calls = []

            def call(self, tool, arguments=None):
                self.calls.append((tool, arguments or {}))
                if tool == "android_get_screen_state":
                    return next(self.screens)
                return "ok"

        bridge = FakeBridge()
        TERMUX_SEND._restore_termux_terminal(bridge, overlaid)
        tools = [tool for tool, _arguments in bridge.calls]
        self.assertEqual(tools.count("android_press_back"), 1)
        self.assertEqual(tools.count("android_swipe"), 1)

    def test_restore_preserves_a_keyboard_that_was_already_open(self):
        original = """screen:1080x2520
--- window:2 type:INPUT_METHOD pkg:keyboard title:Keyboard layer:1 focused:false ---
node_keyboard\tView\t-\t-\t-\t0,1500,1080,2520\ton,ena
--- window:1 type:APPLICATION pkg:com.termux title:Termux layer:0 focused:true ---
node_terminal\tView\t-\t-\tcom.termux:id/terminal_view\t0,0,1080,1500\ton,ena
"""
        renamed = original.replace(
            "node_terminal\tView\t-\t-\tcom.termux:id/terminal_view\t0,0,1080,1500\ton,ena\n",
            "node_drawer\tLinearLayout\t-\t-\tcom.termux:id/left_drawer\t0,0,700,1400\ton,ena\n",
        )

        class FakeBridge:
            def __init__(self):
                self.calls = []

            def call(self, tool, arguments=None):
                self.calls.append((tool, arguments or {}))
                if tool == "android_get_screen_state":
                    return original
                return "ok"

        bridge = FakeBridge()
        TERMUX_SEND._restore_termux_terminal(
            bridge, renamed, original_screen=original
        )
        tools = [tool for tool, _arguments in bridge.calls]
        self.assertEqual(tools.count("android_press_back"), 0)
        self.assertEqual(tools.count("android_swipe"), 1)

    def test_native_rename_failure_does_not_create_a_registry_record(self):
        session = TERMUX_SEND.CodexSession(pid=100, tty_index=1)
        with tempfile.TemporaryDirectory() as directory:
            registry = Path(directory) / "titles.json"

            def fail(_session, _title):
                raise TERMUX_SEND.SendError("native rename failed")

            with self.assertRaisesRegex(TERMUX_SEND.SendError, "native rename failed"):
                TERMUX_SEND.register_session_title(
                    session,
                    "LinkedIn Applications",
                    [session],
                    registry_path=registry,
                    session_renamer=fail,
                )
            self.assertFalse(registry.exists())

    def test_legacy_osc_registry_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = Path(directory) / "titles.json"
            registry.write_text(
                '{"schema_version": 1, "titles": {}}\n', encoding="utf-8"
            )
            with self.assertRaisesRegex(TERMUX_SEND.SendError, "Invalid"):
                TERMUX_SEND.load_title_registry(registry)

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

    def test_returned_reply_types_back_and_ends_with_exact_receipt(self):
        stream = io.StringIO()
        sleeps = []
        session = TERMUX_SEND.CodexSession(pid=200, tty_index=6)
        TERMUX_SEND.render_returned_reply(
            session,
            "Peer answer.",
            task_id="PO_20260803T120000_A1B2C3",
            turn_id="turn-7",
            visible=True,
            char_delay=0.01,
            stream=stream,
            sleeper=sleeps.append,
        )
        self.assertEqual(
            stream.getvalue(),
            "\npts/6 is typing back (reply)...\n"
            "Peer answer.\n"
            "[received: task=PO_20260803T120000_A1B2C3 "
            "turn=turn-7 from=pts/6 phase=reply]\n",
        )
        self.assertEqual(sleeps, [0.01] * len("Peer answer."))

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

    def test_exchange_allows_the_tui_to_settle_before_injection(self):
        session = TERMUX_SEND.CodexSession(pid=200, tty_index=2)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8") as stream:
            stream.write(
                '{"type":"event_msg","payload":{"type":"task_complete"}}\n'
            )
            stream.flush()
            rollout = Path(stream.name)
            with mock.patch.object(
                TERMUX_SEND, "rollout_paths", return_value=[rollout]
            ), mock.patch.object(
                TERMUX_SEND, "wait_until_idle"
            ) as wait_idle, mock.patch.object(
                TERMUX_SEND.time, "sleep"
            ) as sleep, mock.patch.object(
                TERMUX_SEND, "rollout_is_busy", return_value=False
            ), mock.patch.object(
                TERMUX_SEND, "send_message", return_value=100
            ) as send, mock.patch.object(
                TERMUX_SEND,
                "wait_for_reply_turn",
                return_value=("reply", "turn-1"),
            ):
                result = TERMUX_SEND.exchange_with_session(
                    session,
                    "message",
                    source_tty=1,
                    task_id="PO_20260803T120000_A1B2C3",
                    phase="send",
                    visible=False,
                    reply_timeout=1,
                    idle_timeout=1,
                )
            wait_idle.assert_called_once_with(rollout, 1)
            sleep.assert_called_once_with(TERMUX_SEND.POST_IDLE_SETTLE)
            send.assert_called_once()
            self.assertEqual(result, ("reply", "turn-1", rollout, 100))

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
