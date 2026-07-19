import importlib.util
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


INDEX = load("postoffice_index", ROOT / "postoffice" / "index.py")
GATHER = load("postoffice_gather", ROOT / "postoffice" / "gather.py")


class PostOfficeTests(unittest.TestCase):
    def test_extracts_visible_codex_message(self):
        record = {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": [
                    {"type": "output_text", "text": "Visible answer"},
                    {"type": "tool_result", "text": "hidden payload"},
                ],
            },
        }
        self.assertEqual(
            INDEX.visible_codex_message(record),
            ("assistant", "Visible answer"),
        )

    def test_extracts_claude_text_without_tool_payload(self):
        record = {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Visible answer"},
                    {"type": "tool_use", "input": {"secret": "no"}},
                ],
            },
        }
        self.assertEqual(
            INDEX.visible_claude_message(record),
            ("assistant", "Visible answer"),
        )

    def test_builds_safe_phrase_query_and_slug(self):
        self.assertEqual(
            GATHER.fts_query('trust "speech block"'),
            '"trust" OR "speech block"',
        )
        self.assertEqual(
            GATHER.slug("George + Sol / Continuity"), "george-sol-continuity"
        )

    def test_shell_entrypoints_parse(self):
        for script in (ROOT / "postoffice" / "po", ROOT / "postoffice" / "collect.sh"):
            subprocess.run(["bash", "-n", str(script)], check=True)


if __name__ == "__main__":
    unittest.main()
