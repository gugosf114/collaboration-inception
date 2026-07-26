import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).parents[1] / "scripts" / "inception.py"
SPEC = importlib.util.spec_from_file_location("inception", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)

THREAD = "019f7048-1bb9-7230-b91f-f572d2cbc870"
ROOT = "019f620c-5a5c-7fa3-b3e2-98713c64d1c1"


def state_data():
    return {
        "schema_version": 1,
        "mode": "persistent_resume",
        "canonical_thread_id": THREAD,
        "parent_thread_id": ROOT,
        "lineage_root_thread_id": ROOT,
        "label": "test",
        "updated_at": "2026-07-18T00:00:00-07:00",
    }


class InceptionTests(unittest.TestCase):
    def test_loads_state_and_builds_native_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text(json.dumps(state_data()), encoding="utf-8")
            state = MODULE.load_state(path)

        self.assertEqual(MODULE.resume_command(state), ["codex", "resume", THREAD])
        self.assertEqual(
            MODULE.fork_command(state, "try this"),
            ["codex", "fork", THREAD, "try this"],
        )

    def test_rejects_prompt_replay_mode(self):
        data = state_data()
        data["mode"] = "microhistory_injection"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(MODULE.InceptionError, "persistent_resume"):
                MODULE.load_state(path)

    def test_finds_and_validates_the_exact_rollout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "sessions"
            rollout = root / "2026" / "07" / f"rollout-{THREAD}.jsonl"
            rollout.parent.mkdir(parents=True)
            rollout.write_text(
                json.dumps(
                    {
                        "type": "session_meta",
                        "payload": {
                            "id": THREAD,
                            "session_id": THREAD,
                            "forked_from_id": ROOT,
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            path, metadata = MODULE.canonical_rollout(state_data(), root)

        self.assertEqual(path, rollout)
        self.assertEqual(metadata["forked_from_id"], ROOT)

    def test_adopts_only_a_direct_history_preserving_fork(self):
        child = "019f8888-1bb9-7230-b91f-f572d2cbc870"
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            state_path = base / "state.json"
            state_path.write_text(json.dumps(state_data()), encoding="utf-8")
            rollout = base / "sessions" / f"rollout-{child}.jsonl"
            rollout.parent.mkdir()
            rollout.write_text(
                json.dumps(
                    {
                        "type": "session_meta",
                        "payload": {"id": child, "forked_from_id": THREAD},
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            adopted = MODULE.adopt_thread(child, state_path, base / "sessions")
            saved = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertEqual(adopted["canonical_thread_id"], child)
        self.assertEqual(saved["parent_thread_id"], THREAD)
        self.assertEqual(saved["lineage_root_thread_id"], ROOT)

    def test_server_recovers_from_a_dead_daemon_pid_once(self):
        with tempfile.TemporaryDirectory() as directory:
            pid_path = Path(directory) / "app-server.pid"
            dead_pid = 999_999_999
            pid_path.write_text(json.dumps({"pid": dead_pid}), encoding="utf-8")
            error = MODULE.InceptionError(
                f"failed to read /proc/{dead_pid}/stat: No such file or directory"
            )
            with patch.object(
                MODULE,
                "run_json",
                side_effect=[error, {"status": "connected"}],
            ) as run:
                result = MODULE.start_server(pid_path)

            self.assertEqual(result, {"status": "connected"})
            self.assertEqual(run.call_count, 2)
            self.assertFalse(pid_path.exists())
            self.assertTrue(
                (Path(directory) / f"app-server.pid.stale-{dead_pid}").exists()
            )

    def test_server_does_not_remove_an_unrelated_pid_record(self):
        with tempfile.TemporaryDirectory() as directory:
            pid_path = Path(directory) / "app-server.pid"
            pid_path.write_text(json.dumps({"pid": 12345}), encoding="utf-8")
            with patch.object(
                MODULE,
                "run_json",
                side_effect=MODULE.InceptionError("authentication failed"),
            ):
                with self.assertRaisesRegex(MODULE.InceptionError, "authentication"):
                    MODULE.start_server(pid_path)

            self.assertTrue(pid_path.exists())


if __name__ == "__main__":
    unittest.main()
