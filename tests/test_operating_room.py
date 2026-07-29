import importlib.util
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "operating_room.py"
SPEC = importlib.util.spec_from_file_location("operating_room_tested", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def git(path: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=path,
        text=True,
        capture_output=True,
        check=True,
    )
    return completed.stdout.strip()


class RelationshipLedgerTests(unittest.TestCase):
    def test_learns_corrections_promises_outcomes_authority_and_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = MODULE.RelationshipLedger(Path(directory) / "relationship.db")
            try:
                corrections = ledger.observe_operator(
                    "No, you forgot the real test.", ["codex"]
                )
                promises = ledger.observe_answer(
                    "codex", "I'll run the real test and report the output."
                )
                outcome = ledger.record_outcome(
                    "codex",
                    "testing",
                    "success",
                    "The regression test passed.",
                    prediction="The repair should stop the crash.",
                    falsifier="The crash recurs.",
                    recommendation="Ship the repaired build.",
                )

                self.assertEqual(len(corrections), 1)
                self.assertEqual(len(promises), 1)
                self.assertTrue(outcome)
                packet = ledger.packet_for("test the crash repair")
                self.assertIn("CORRECTION", packet)
                self.assertIn("OUTCOME", packet)
                self.assertIn("OPEN PROMISE", packet)
                authority = ledger.authority()[0]
                self.assertEqual(authority["agent"], "codex")
                self.assertEqual(authority["category"], "testing")
                self.assertGreater(authority["score"], 0.5)
                self.assertGreater(ledger.drift("codex")["score"], 0)
                recorded = ledger.summary()["recent_outcomes"][0]
                self.assertEqual(
                    recorded["recommendation"], "Ship the repaired build."
                )
                self.assertEqual(recorded["calibration_error"], 0.0)

                ledger.resolve_promise(promises[0], "Reported with proof.")
                self.assertEqual(ledger.summary()["open_promises"], [])
            finally:
                ledger.close()

    def test_tracks_one_active_mission_and_correctable_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = MODULE.RelationshipLedger(Path(directory) / "relationship.db")
            try:
                first = ledger.set_mission("Ship the live cockpit.")
                self.assertEqual(ledger.active_mission()["id"], first)
                second = ledger.set_mission("Prove the cockpit on one real task.")
                self.assertEqual(ledger.active_mission()["id"], second)
                episode = ledger.record_episode(
                    source="codex",
                    session_id="session",
                    ordinal=4,
                    kind="correction",
                    agent="codex",
                    category="testing",
                    confidence=0.8,
                    source_exchange={"george": "Run the real test."},
                    inference="George requires real test evidence.",
                    counterevidence="",
                    useful_behavior="Run the test before claiming completion.",
                    source_hash="unique-source",
                )
                self.assertTrue(episode)
                ledger.challenge_episode(
                    episode or "", "For trivial text edits, a full test is unnecessary."
                )
                packet = ledger.packet_for("test completion")
                self.assertIn("ACTIVE MISSION", packet)
                self.assertIn("COUNTEREVIDENCE", packet)
                ledger.complete_mission("Proved.")
                self.assertIsNone(ledger.active_mission())
            finally:
                ledger.close()

    def test_explicit_corrections_are_certain_but_natural_ones_are_provisional(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = MODULE.RelationshipLedger(Path(directory) / "relationship.db")
            try:
                ledger.observe_operator("You misunderstood the request.", ["claude"])
                ledger.add_correction("claude", "Do not shrink a gigantic request.")
                rows = ledger.summary()["recent_corrections"]
                self.assertEqual(rows[0]["confidence"], 1.0)
                self.assertLess(rows[1]["confidence"], 1.0)
            finally:
                ledger.close()

    def test_antigravity_has_the_same_memory_and_authority_support(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = MODULE.RelationshipLedger(Path(directory) / "relationship.db")
            try:
                ledger.add_correction(
                    "antigravity", "Do not invent a completion claim."
                )
                ledger.record_outcome(
                    "antigravity",
                    "testing",
                    "success",
                    "The test passed.",
                )

                summary = ledger.summary()
                self.assertEqual(
                    summary["recent_corrections"][0]["agent"],
                    "antigravity",
                )
                self.assertIn("antigravity", summary["drift"])
                self.assertEqual(
                    summary["authority"][0]["agent"],
                    "antigravity",
                )
            finally:
                ledger.close()


class ObjectionTests(unittest.TestCase):
    def test_catches_fake_completion_flattery_and_hollow_questions(self):
        objections = MODULE.deterministic_objections(
            "Repair it.",
            "Great question. Done. Would you like me to continue?",
        )

        self.assertTrue(any("proof" in item for item in objections))
        self.assertTrue(any("praise" in item for item in objections))
        self.assertTrue(any("continuation" in item for item in objections))


class SurfaceTests(unittest.TestCase):
    def test_stages_a_private_file_without_touching_the_source(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "example.txt"
            source.write_text("shared evidence", encoding="utf-8")
            hub = MODULE.SurfaceHub(base / "surfaces", ROOT, env={})

            artifact = hub.stage_file("example.txt", base)

            self.assertEqual(artifact.kind, "file")
            self.assertEqual(artifact.path.read_text(encoding="utf-8"), "shared evidence")
            self.assertNotEqual(artifact.path, source)

    def test_rejects_fake_png_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            fake = Path(directory) / "fake.png"
            fake.write_text("not an image", encoding="utf-8")

            with self.assertRaisesRegex(MODULE.OperatingRoomError, "valid PNG"):
                MODULE.SurfaceHub._validated_png("screen", fake, {})

    def test_explicit_remote_browser_host_is_validated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            hub = MODULE.SurfaceHub(
                root / "surfaces",
                ROOT,
                env={"INCEPTION_BROWSER_SSH_HOST": "friend-laptop"},
            )
            self.assertEqual(hub._browser_ssh_host(), "friend-laptop")

            unsafe = MODULE.SurfaceHub(
                root / "unsafe",
                ROOT,
                env={"INCEPTION_BROWSER_SSH_HOST": "friend; shutdown"},
            )
            with self.assertRaisesRegex(MODULE.OperatingRoomError, "unsafe"):
                unsafe._browser_ssh_host()

    def test_agent_bridge_screen_accepts_the_live_agents_jpeg(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            hub = MODULE.SurfaceHub(root / "surfaces", ROOT, env={})
            jpeg = b"\xff\xd8\xff" + b"screen pixels"
            encoded = MODULE.base64.b64encode(jpeg).decode("ascii")

            def ordered_bridge_json(method, url, payload=None):
                if url.endswith("/jobs"):
                    return {}
                if url.endswith("/job"):
                    expected_id.append(payload["id"])
                    return {}
                return {
                    "id": expected_id[0],
                    "ok": True,
                    "output": f"data:image/jpeg;base64,{encoded}",
                }

            expected_id = []
            with mock.patch.object(
                hub, "_bridge_json", side_effect=ordered_bridge_json
            ), mock.patch.object(MODULE.time, "sleep"):
                artifact = hub._agent_bridge_screen(
                    root / "surfaces" / "screen.png",
                    "https://bridge.example",
                )

            self.assertEqual(artifact.path.suffix, ".jpg")
            self.assertEqual(artifact.path.read_bytes(), jpeg)
            self.assertEqual(artifact.metadata["adapter"], "agent-bridge-laptop")

    def test_agent_bridge_url_rejects_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            hub = MODULE.SurfaceHub(
                Path(directory) / "surfaces",
                ROOT,
                env={
                    "INCEPTION_AGENT_BRIDGE_URL": (
                        "https://secret@example.test"
                    )
                },
            )
            with self.assertRaisesRegex(
                MODULE.OperatingRoomError, "must not contain credentials"
            ):
                hub._agent_bridge_url()


class ArenaTests(unittest.TestCase):
    def test_isolates_tests_selects_replays_and_reverts_a_winner(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            project = base / "project"
            project.mkdir()
            git(project, "init", "-b", "main")
            git(project, "config", "user.name", "Arena Test")
            git(project, "config", "user.email", "arena@example.invalid")
            (project / "value.txt").write_text("original\n", encoding="utf-8")
            (project / "verify.py").write_text(
                "from pathlib import Path\n"
                "raise SystemExit(0 if Path('value.txt').read_text().strip() == 'good' else 1)\n",
                encoding="utf-8",
            )
            git(project, "add", ".")
            git(project, "commit", "-m", "base")
            command = f'"{sys.executable}" verify.py'
            manager = MODULE.ArenaManager(project, base / "arena")
            run = manager.prepare(
                "Make the value good.",
                command,
                participants=("claude", "antigravity"),
            )
            try:
                Path(run.attempts["claude"].worktree, "value.txt").write_text(
                    "good\n", encoding="utf-8"
                )
                Path(run.attempts["antigravity"].worktree, "value.txt").write_text(
                    "bad\n", encoding="utf-8"
                )
                manager.finalize_attempt(
                    run, "claude", "Changed it and tested it.", "completed"
                )
                manager.finalize_attempt(
                    run, "antigravity", "Changed it.", "completed"
                )
                self.assertTrue(manager.run_tests(run, "claude").passed)
                self.assertFalse(manager.run_tests(run, "antigravity").passed)
                self.assertEqual(manager.recommend(run, {}), "claude")
                self.assertIn("Recommended: claude", manager.replay_text(run.id))

                applied = manager.choose(run.id, "claude")
                self.assertEqual(
                    (project / "value.txt").read_text(encoding="utf-8"), "good\n"
                )
                self.assertEqual(git(project, "rev-parse", "HEAD"), applied)

                reverted = manager.undo(run.id)
                self.assertEqual(
                    (project / "value.txt").read_text(encoding="utf-8"), "original\n"
                )
                self.assertEqual(git(project, "rev-parse", "HEAD"), reverted)
            finally:
                for attempt in run.attempts.values():
                    subprocess.run(
                        ["git", "worktree", "remove", "--force", attempt.worktree],
                        cwd=project,
                        capture_output=True,
                        check=False,
                    )


class WindowsArtifactTests(unittest.TestCase):
    def test_power_shell_installer_and_native_helpers_are_shipped(self):
        installer = (ROOT / "install.ps1").read_text(encoding="utf-8")
        screen = (ROOT / "scripts" / "capture_windows_screen.ps1").read_text(
            encoding="utf-8"
        )
        voice = (ROOT / "scripts" / "listen_windows.ps1").read_text(
            encoding="utf-8"
        )

        self.assertIn("Inception is installed for PowerShell", installer)
        self.assertIn("inception.cmd", installer)
        self.assertIn("CopyFromScreen", screen)
        self.assertIn("SpeechRecognitionEngine", voice)


if __name__ == "__main__":
    unittest.main()
