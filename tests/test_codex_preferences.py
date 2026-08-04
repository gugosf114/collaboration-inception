import json
import subprocess
import sys
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "runtime" / "termux" / "hooks" / "george-core-contract.py"
HOOKS_JSON = ROOT / "runtime" / "termux" / "hooks.json"
CONFIG_TOML = ROOT / "runtime" / "termux" / "config.snapshot.toml"


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
        self.assertIn("genuine ambiguity must be clarified", context)
        self.assertIn("the first substantive result sentence answers it", context)
        self.assertIn("Ideas, recommendations", context)
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
        self.assertEqual(handlers[0]["additionalContextLimit"], 1000)


if __name__ == "__main__":
    unittest.main()
