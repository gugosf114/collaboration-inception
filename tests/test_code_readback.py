import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "runtime" / "termux" / "hooks" / "code-readback.py"
SPEC = importlib.util.spec_from_file_location("code_readback", SCRIPT)
CODE_READBACK = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(CODE_READBACK)


def post_event(**overrides):
    event = {
        "session_id": "session-one",
        "turn_id": "turn-one",
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "tool_use_id": "tool-one",
        "cwd": "/work/app",
        "tool_input": {"command": "pytest -q"},
        "tool_response": {"output": "2 passed", "exit_code": 0},
        "prompt": "PRIVATE USER REQUEST",
        "last_assistant_message": "AUTHOR CLAIM",
    }
    event.update(overrides)
    return event


def stop_event():
    return {
        "session_id": "session-one",
        "turn_id": "turn-one",
        "hook_event_name": "Stop",
        "last_assistant_message": "AUTHOR CLAIM",
    }


class CodeReadbackTests(unittest.TestCase):
    def test_collector_keeps_only_actual_tool_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = CODE_READBACK.collect_post_tool_event(
                post_event(), root=root
            )
            stored = next((root / "pending").rglob("*.jsonl")).read_text(
                encoding="utf-8"
            )
        self.assertEqual(record["event_id"], "E1")
        self.assertIn("pytest -q", stored)
        self.assertIn("2 passed", stored)
        self.assertNotIn("PRIVATE USER REQUEST", stored)
        self.assertNotIn("AUTHOR CLAIM", stored)

    def test_collector_numbers_parallel_feed_records(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = CODE_READBACK.collect_post_tool_event(
                post_event(tool_use_id="one"), root=root
            )
            second = CODE_READBACK.collect_post_tool_event(
                post_event(tool_use_id="two"), root=root
            )
        self.assertEqual(first["event_id"], "E1")
        self.assertEqual(second["event_id"], "E2")

    def test_secrets_are_replaced_before_the_model_prompt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            CODE_READBACK.collect_post_tool_event(
                post_event(
                    tool_response={
                        "output": "Authorization: Bearer secret-token-value"
                    }
                ),
                root=root,
            )
            prompts = []
            CODE_READBACK.process_stop_event(
                stop_event(),
                root=root,
                translator=lambda prompt: prompts.append(prompt)
                or "[E1] The command ended.\nCoverage: E1 through E1 covered.",
            )
        self.assertIn("[REDACTED_SECRET]", prompts[0])
        self.assertNotIn("secret-token-value", prompts[0])

    def test_blind_prompt_has_no_user_request_or_author_story(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            CODE_READBACK.collect_post_tool_event(post_event(), root=root)
            prompts = []
            CODE_READBACK.process_stop_event(
                stop_event(),
                root=root,
                translator=lambda prompt: prompts.append(prompt)
                or "[E1] Two tests passed.\nCoverage: E1 through E1 covered.",
            )
        self.assertIn("pytest -q", prompts[0])
        self.assertNotIn("PRIVATE USER REQUEST", prompts[0])
        self.assertNotIn("AUTHOR CLAIM", prompts[0])
        self.assertIn("You do not know the user's request", prompts[0])

    def test_checker_marks_any_skipped_event_untranslated(self):
        events = [
            {
                "event_id": "E1",
                "input": {"text": "one", "truncated": False},
                "response": {"text": "ok", "truncated": False},
            },
            {
                "event_id": "E2",
                "input": {"text": "two", "truncated": False},
                "response": {"text": "ok", "truncated": False},
            },
        ]
        checked = CODE_READBACK.validate_readback(
            "[E1] The first command passed.", events
        )
        self.assertIn("[E2] UNTRANSLATED", checked)
        self.assertIn("Coverage: E1 through E2 covered.", checked)

    def test_checker_keeps_raw_failure_visible(self):
        events = [
            {
                "event_id": "E1",
                "input": {"text": "build", "truncated": False},
                "response": {
                    "text": "warning: old setting\nexit code 1",
                    "truncated": False,
                },
            }
        ]
        checked = CODE_READBACK.validate_readback(
            "[E1] The build command ended.", events
        )
        self.assertIn("[E1] CHECK RAW", checked)

    def test_stop_requests_exact_visible_readback_and_writes_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            CODE_READBACK.collect_post_tool_event(post_event(), root=root)
            result = CODE_READBACK.process_stop_event(
                stop_event(),
                root=root,
                translator=lambda _prompt: (
                    "[E1] Two tests passed.\nCoverage: E1 through E1 covered."
                ),
            )
            receipts = list((root / "receipts").rglob("*.json"))
        self.assertEqual(result["decision"], "block")
        self.assertIn("ENGLISH READ-BACK — BLIND", result["reason"])
        self.assertIn("[E1] Two tests passed.", result["reason"])
        self.assertIn("Do not print the marker lines", result["reason"])
        self.assertEqual(len(receipts), 1)

    def test_second_stop_proves_the_exact_readback_was_shown(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            CODE_READBACK.collect_post_tool_event(post_event(), root=root)
            first = CODE_READBACK.process_stop_event(
                stop_event(),
                root=root,
                translator=lambda _prompt: (
                    "[E1] Two tests passed.\nCoverage: E1 through E1 covered."
                ),
            )
            marker = first["reason"].split("CODE_READBACK_START\n", 1)[1]
            exact = marker.rsplit("\nCODE_READBACK_END", 1)[0]
            second_event = stop_event()
            second_event["stop_hook_active"] = True
            second_event["last_assistant_message"] = exact
            second = CODE_READBACK.handle_hook_event(second_event, root=root)
            pending = list((root / "display").rglob("pending.json"))
        self.assertEqual(second, {})
        self.assertEqual(pending, [])

    def test_child_translator_never_collects_its_own_work(self):
        old = os.environ.get("CODE_READBACK_CHILD")
        os.environ["CODE_READBACK_CHILD"] = "1"
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                result = CODE_READBACK.handle_hook_event(
                    post_event(), root=root
                )
                pending = list((root / "pending").rglob("*.jsonl"))
        finally:
            if old is None:
                os.environ.pop("CODE_READBACK_CHILD", None)
            else:
                os.environ["CODE_READBACK_CHILD"] = old
        self.assertIsNone(result)
        self.assertEqual(pending, [])

    def test_runner_uses_a_sealed_home_and_disables_web(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "readback"
            parent_home = base / "parent-codex"
            parent_home.mkdir()
            (parent_home / "auth.json").write_text("{}\n", encoding="utf-8")
            captured = {}

            def fake_run(command, **kwargs):
                captured["command"] = command
                captured["environment"] = kwargs["env"]
                output_path = Path(
                    command[command.index("--output-last-message") + 1]
                )
                output_path.write_text("[E1] One command ran.\n", encoding="utf-8")
                return mock.Mock(returncode=0, stdout="", stderr="")

            with mock.patch.dict(
                os.environ,
                {"CODE_READBACK_PARENT_CODEX_HOME": str(parent_home)},
            ), mock.patch.object(
                CODE_READBACK.subprocess, "run", side_effect=fake_run
            ):
                answer = CODE_READBACK.run_blind_translator(
                    "EVENT RECORDS", root=root
                )

        command = captured["command"]
        environment = captured["environment"]
        self.assertEqual(answer, "[E1] One command ran.")
        self.assertIn("--ignore-user-config", command)
        self.assertIn("--ignore-rules", command)
        self.assertIn('web_search="disabled"', command)
        self.assertEqual(environment["HOME"], environment["CODEX_HOME"])
        self.assertEqual(environment["CODE_READBACK_CHILD"], "1")

    def test_hook_config_collects_then_translates(self):
        config = json.loads(
            (ROOT / "runtime" / "termux" / "hooks.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            set(config["hooks"]), {"UserPromptSubmit", "PostToolUse", "Stop"}
        )
        self.assertEqual(config["hooks"]["PostToolUse"][0]["matcher"], "Bash|Edit|Write")
        commands = [
            handler["command"]
            for event in ("PostToolUse", "Stop")
            for group in config["hooks"][event]
            for handler in group["hooks"]
        ]
        self.assertTrue(all(command.endswith("code-readback.py") for command in commands))


if __name__ == "__main__":
    unittest.main()
