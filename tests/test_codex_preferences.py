import json
import subprocess
import sys
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "runtime" / "termux" / "hooks" / "george-core-contract.py"
CLAUDE_HOOK = (
    ROOT
    / "runtime"
    / "termux"
    / "claude"
    / "hooks"
    / "george-core-contract.py"
)
HOOKS_JSON = ROOT / "runtime" / "termux" / "hooks.json"
CLAUDE_HOOKS_JSON = (
    ROOT / "runtime" / "termux" / "claude" / "hooks.snapshot.json"
)
NATIVE_CLAUDE_HOOKS_JSON = (
    ROOT / "runtime" / "termux" / "claude" / "hooks.native.snapshot.json"
)
CONFIG_TOML = ROOT / "runtime" / "termux" / "config.snapshot.toml"
AGENTS_MD = ROOT / "runtime" / "termux" / "AGENTS.snapshot.md"
CORE_MD = ROOT / "runtime" / "termux" / "core-interaction-contract.md"


class CodexPreferenceHookTests(unittest.TestCase):
    def test_user_prompt_submit_adds_only_invisible_developer_context(self):
        completed = subprocess.run(
            [sys.executable, str(HOOK)],
            input=json.dumps(
                {
                    "hook_event_name": "UserPromptSubmit",
                    "prompt": "Use the thing from before.",
                }
            ),
            text=True,
            capture_output=True,
            check=True,
        )
        output = json.loads(completed.stdout)
        self.assertEqual(
            output["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit"
        )
        context = output["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Clarify first", context)
        self.assertIn("Answer fast, short, simple, and to the point", context)
        self.assertIn("Talk like to a toddler", context)
        self.assertIn("Use mistakes", context)
        self.assertIn("George decides", context)
        self.assertNotIn("[hedges]", context)
        self.assertNotIn("[guessed]", context)
        self.assertNotIn("[bottom line]", context)
        self.assertEqual(completed.stderr, "")
        with CONFIG_TOML.open("rb") as config_file:
            config = tomllib.load(config_file)
        self.assertEqual(config["developer_instructions"].strip(), context)

    def test_global_hook_config_loads_only_the_clean_prompt_hook(self):
        config = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
        self.assertEqual(set(config["hooks"]), {"UserPromptSubmit"})
        handlers = config["hooks"]["UserPromptSubmit"][0]["hooks"]
        self.assertEqual(len(handlers), 1)
        self.assertIn("george-core-contract.py", handlers[0]["command"])
        completed = subprocess.run(
            [sys.executable, str(HOOK)], input="{}", text=True,
            capture_output=True, check=True
        )
        context = json.loads(completed.stdout)["hookSpecificOutput"][
            "additionalContext"
        ]
        self.assertEqual(handlers[0]["additionalContextLimit"], 2500)
        self.assertLess(len(context), 2500)

    def test_claude_and_codex_inject_the_same_four_rules(self):
        contexts = []
        for hook in (HOOK, CLAUDE_HOOK):
            completed = subprocess.run(
                [sys.executable, str(hook)],
                input='{"hook_event_name":"UserPromptSubmit"}',
                text=True,
                capture_output=True,
                check=True,
            )
            contexts.append(
                json.loads(completed.stdout)["hookSpecificOutput"][
                    "additionalContext"
                ]
            )
        self.assertEqual(contexts[0], contexts[1])

    def test_toddler_rule_is_in_every_tracked_global_layer(self):
        exact = "Answer fast, short, simple, and to the point"
        for path in (CORE_MD, AGENTS_MD, CONFIG_TOML, HOOK, CLAUDE_HOOK):
            with self.subTest(path=path):
                self.assertIn(exact, path.read_text(encoding="utf-8"))

    def test_claude_uses_only_the_current_reply_hooks(self):
        hooks = json.loads(CLAUDE_HOOKS_JSON.read_text(encoding="utf-8"))
        self.assertEqual(set(hooks), {"UserPromptSubmit", "Stop"})
        commands = [
            hook["command"]
            for event in hooks.values()
            for group in event
            for hook in group["hooks"]
        ]
        self.assertEqual(
            commands,
            [
                "/usr/bin/python3 /root/.claude/hooks/george-core-contract.py",
                "/root/.claude/hooks/answer-length.sh",
                "/root/.claude/hooks/plain-words.sh",
            ],
        )
        self.assertFalse(
            any(
                stale in command
                for command in commands
                for stale in (
                    "claim-limits",
                    "hedge-guard",
                    "audit-reply",
                    "precompact-reinject",
                    "inject-rules.sh",
                )
            )
        )

    def test_native_claude_has_the_same_three_reply_hooks(self):
        hooks = json.loads(
            NATIVE_CLAUDE_HOOKS_JSON.read_text(encoding="utf-8")
        )
        self.assertEqual(set(hooks), {"UserPromptSubmit", "Stop"})
        commands = [
            hook["command"]
            for event in hooks.values()
            for group in event
            for hook in group["hooks"]
        ]
        self.assertEqual(len(commands), 3)
        self.assertTrue(commands[0].endswith("george-core-contract.py"))
        self.assertIn("answer-length.sh", commands[1])
        self.assertIn("plain-words.sh", commands[2])


if __name__ == "__main__":
    unittest.main()
