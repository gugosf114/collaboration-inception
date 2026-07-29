from __future__ import annotations

import asyncio
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from scripts.live_bridge import (
    LiveBridge,
    consequential_tool_request,
)


class LiveBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.bridge = LiveBridge(Path(self.temporary.name), port=0)
        self.bridge.start()

    def tearDown(self) -> None:
        self.bridge.close()
        self.temporary.cleanup()

    def request(
        self,
        path: str,
        *,
        payload: dict | None = None,
        token: str | None = None,
    ) -> tuple[int, dict]:
        data = None if payload is None else json.dumps(payload).encode()
        headers = {}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(
            f"{self.bridge.url}{path}",
            data=data,
            headers=headers,
            method="POST" if payload is not None else "GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=3) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, json.loads(exc.read())
            finally:
                exc.close()

    def test_binds_loopback_and_requires_bearer_authentication(self) -> None:
        status, payload = self.request("/health")
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "ok")

        status, _ = self.request("/api/state")
        self.assertEqual(status, 401)
        status, payload = self.request("/api/state", token=self.bridge.token)
        self.assertEqual(status, 200)
        self.assertTrue(payload["ready"])
        self.assertEqual(payload["url"], self.bridge.url)

    def test_pairing_code_returns_the_persistent_token(self) -> None:
        status, _ = self.request("/api/pair", payload={"code": "wrong"})
        self.assertEqual(status, 401)
        status, payload = self.request(
            "/api/pair",
            payload={"code": self.bridge.pair_code},
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["token"], self.bridge.token)
        self.assertEqual(
            (Path(self.temporary.name) / "token").read_text().strip(),
            self.bridge.token,
        )

    def test_commands_transcripts_and_long_poll_events_share_one_channel(self) -> None:
        commands: list[str] = []
        self.bridge.set_command_handler(commands.append)
        status, _ = self.request(
            "/api/command",
            payload={"command": "/both test"},
            token=self.bridge.token,
        )
        self.assertEqual(status, 202)
        self.bridge.set_active(True, ["codex"])
        status, payload = self.request(
            "/api/transcript",
            payload={"text": "focus on the failing test"},
            token=self.bridge.token,
        )
        self.assertEqual(status, 202)
        self.assertEqual(payload["command"], "/steer focus on the failing test")
        self.assertEqual(
            commands,
            ["/both test", "/steer focus on the failing test"],
        )
        status, events = self.request("/api/events?after=0", token=self.bridge.token)
        self.assertEqual(status, 200)
        self.assertTrue(
            any(event["type"] == "voice.transcript" for event in events["events"])
        )

    def test_approval_blocks_until_terminal_or_side_panel_resolves_it(self) -> None:
        async def exercise() -> str:
            task = asyncio.create_task(
                self.bridge.request_approval(
                    "codex",
                    "command",
                    {"command": "git push origin main"},
                    timeout=5,
                )
            )
            for _ in range(100):
                waiting = self.bridge.pending_approvals()
                if waiting:
                    break
                await asyncio.sleep(0.01)
            self.assertTrue(waiting)
            self.bridge.resolve_approval(waiting[0]["id"], "acceptForSession")
            return await task

        self.assertEqual(asyncio.run(exercise()), "acceptForSession")
        self.assertEqual(
            asyncio.run(
                self.bridge.request_approval(
                    "codex",
                    "command",
                    {"command": "git push origin release"},
                    timeout=0.01,
                )
            ),
            "acceptForSession",
        )
        self.assertEqual(self.bridge.pending_approvals(), [])


class ConsequentialActionTests(unittest.TestCase):
    def test_detects_external_and_destructive_actions(self) -> None:
        self.assertTrue(
            consequential_tool_request("Bash", {"command": "git push origin main"})
        )
        self.assertTrue(
            consequential_tool_request("Bash", {"command": "rm -rf build/cache"})
        )
        self.assertTrue(
            consequential_tool_request("SendMessage", {"recipient": "customer"})
        )

    def test_ordinary_repo_work_does_not_create_consent_theater(self) -> None:
        self.assertFalse(
            consequential_tool_request("Bash", {"command": "python3 -m unittest"})
        )
        self.assertFalse(
            consequential_tool_request("Edit", {"file_path": "scripts/app.py"})
        )


if __name__ == "__main__":
    unittest.main()
