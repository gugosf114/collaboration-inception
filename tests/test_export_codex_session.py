import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "export_codex_session.py"
SPEC = importlib.util.spec_from_file_location("export_codex_session", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def record(timestamp, role, text, phase=None):
    payload = {
        "type": "message",
        "role": role,
        "content": [
            {
                "type": "input_text" if role == "user" else "output_text",
                "text": text,
            }
        ],
    }
    if phase is not None:
        payload["phase"] = phase
    return {"timestamp": timestamp, "type": "response_item", "payload": payload}


class ExportSessionTests(unittest.TestCase):
    def test_extracts_visible_messages_and_filters_runtime_injection(self):
        records = [
            {"type": "session_meta", "payload": {"id": "session-123"}},
            record("t0", "user", "<environment_context>hidden</environment_context>"),
            record("t1", "user", "Hello Sol"),
            record("t2", "assistant", "Working on it", "commentary"),
            record("t3", "assistant", "Done", "final_answer"),
            {"type": "event_msg", "payload": {"type": "token_count"}},
        ]
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "session.jsonl"
            source.write_text(
                "".join(json.dumps(item) + "\n" for item in records), encoding="utf-8"
            )
            full, metadata = MODULE.extract(source)

        self.assertEqual(metadata["id"], "session-123")
        self.assertEqual([item["text"] for item in full], ["Hello Sol", "Working on it", "Done"])
        self.assertEqual(
            [item["text"] for item in MODULE.core_messages(full)],
            ["Hello Sol", "Done"],
        )

    def test_marks_images_without_copying_binary_payloads(self):
        content = [
            {"type": "input_text", "text": "Here"},
            {"type": "input_image", "image_url": "data:image/png;base64,AAAA"},
        ]
        self.assertEqual(
            MODULE.content_text(content),
            "Here\n\n[Image attached in the original conversation]",
        )


if __name__ == "__main__":
    unittest.main()
