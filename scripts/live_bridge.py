#!/usr/bin/env python3
"""Authenticated localhost control plane for the Inception cockpit.

The bridge deliberately uses only Python's standard library.  A Chrome side
panel, LAV, or another local surface can pair once, stream cockpit events,
submit commands, steer active work, and answer consequential-action approvals.
It binds to loopback only and never exposes the bearer token in a URL.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import parse_qs, urlsplit


DEFAULT_BRIDGE_ROOT = Path(__file__).resolve().parents[1] / "runtime" / "bridge"
DEFAULT_BRIDGE_HOST = "127.0.0.1"
DEFAULT_BRIDGE_PORT = 8765
MAX_BODY_BYTES = 2 * 1024 * 1024
MAX_EVENTS = 2_000
PAIR_ATTEMPT_LIMIT = 10
APPROVAL_TIMEOUT_SECONDS = 10 * 60


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class BridgeError(RuntimeError):
    """The local cockpit bridge could not complete an operation."""


@dataclass
class PendingApproval:
    identifier: str
    provider: str
    kind: str
    detail: dict[str, Any]
    created_at: str = field(default_factory=now_iso)
    decision: str | None = None
    resolved_at: str | None = None
    condition: threading.Condition = field(default_factory=threading.Condition)

    def public(self) -> dict[str, Any]:
        return {
            "id": self.identifier,
            "provider": self.provider,
            "kind": self.kind,
            "detail": self.detail,
            "created_at": self.created_at,
            "decision": self.decision,
            "resolved_at": self.resolved_at,
        }


class LiveBridge:
    """Thread-safe event, command, approval, and control-state broker."""

    def __init__(
        self,
        root: Path = DEFAULT_BRIDGE_ROOT,
        host: str = DEFAULT_BRIDGE_HOST,
        port: int = DEFAULT_BRIDGE_PORT,
    ):
        if host not in {"127.0.0.1", "::1"}:
            raise BridgeError("The live bridge must bind to loopback")
        if not 0 <= int(port) <= 65535:
            raise BridgeError("The live bridge port must be between 0 and 65535")
        self.root = root.expanduser().resolve()
        self.host = host
        self.port = int(port)
        self.token_path = self.root / "token"
        self.info_path = self.root / "bridge.json"
        self._token = ""
        self._pair_code = ""
        self._pair_attempts = 0
        self._events: deque[dict[str, Any]] = deque(maxlen=MAX_EVENTS)
        self._event_sequence = 0
        self._event_condition = threading.Condition()
        self._command_handler: Callable[[str], None] | None = None
        self._queued_commands: deque[str] = deque()
        self._approvals: dict[str, PendingApproval] = {}
        self._session_grants: set[tuple[str, str]] = set()
        self._approval_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._active = False
        self._active_agents: list[str] = []
        self._control_owner = "human"
        self._last_human_activity = now_iso()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def pair_code(self) -> str:
        return self._pair_code

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def token(self) -> str:
        return self._token

    def start(self) -> None:
        if self._server is not None:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        self._token = self._load_or_create_token()
        self._pair_code = f"{secrets.randbelow(1_000_000):06d}"
        handler = self._handler_type()
        try:
            server = ThreadingHTTPServer((self.host, self.port), handler)
        except OSError as exc:
            raise BridgeError(
                f"Cannot start live bridge on {self.host}:{self.port}: {exc}"
            ) from exc
        server.daemon_threads = True
        self._server = server
        self.port = int(server.server_address[1])
        self._write_info()
        self._thread = threading.Thread(
            target=server.serve_forever,
            daemon=True,
            name="inception-live-bridge",
        )
        self._thread.start()
        self.publish("bridge.ready", {"url": self.url})

    def close(self) -> None:
        server = self._server
        self._server = None
        if server is not None:
            server.shutdown()
            server.server_close()
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=2)
        with self._approval_lock:
            pending = [
                approval
                for approval in self._approvals.values()
                if approval.decision is None
            ]
        for approval in pending:
            self.resolve_approval(approval.identifier, "cancel")
        self.publish("bridge.closed", {})

    def set_command_handler(self, handler: Callable[[str], None]) -> None:
        self._command_handler = handler
        while self._queued_commands:
            handler(self._queued_commands.popleft())

    def submit_command(self, command: str) -> None:
        clean = command.strip()
        if not clean:
            raise BridgeError("A cockpit command cannot be empty")
        if len(clean) > 200_000:
            raise BridgeError("Cockpit command exceeds 200,000 characters")
        handler = self._command_handler
        if handler is None:
            self._queued_commands.append(clean)
        else:
            handler(clean)
        self.publish("operator.command", {"command": clean})

    def submit_transcript(self, text: str) -> str:
        clean = text.strip()
        if not clean:
            raise BridgeError("Speech transcript cannot be empty")
        with self._state_lock:
            active = self._active
        if clean.startswith("/"):
            command = clean
        elif active:
            command = f"/steer {clean}"
        else:
            command = f"/both {clean}"
        self.submit_command(command)
        self.publish("voice.transcript", {"text": clean, "command": command})
        return command

    def publish(self, event_type: str, data: Mapping[str, Any] | None = None) -> int:
        with self._event_condition:
            self._event_sequence += 1
            record = {
                "sequence": self._event_sequence,
                "at": now_iso(),
                "type": event_type,
                **dict(data or {}),
            }
            self._events.append(record)
            self._event_condition.notify_all()
            return self._event_sequence

    def events_after(self, sequence: int, timeout: float = 0) -> list[dict[str, Any]]:
        deadline = time.monotonic() + max(0.0, min(timeout, 25.0))
        with self._event_condition:
            while (
                self._event_sequence <= sequence
                and timeout > 0
                and time.monotonic() < deadline
            ):
                self._event_condition.wait(timeout=deadline - time.monotonic())
            return [
                dict(event)
                for event in self._events
                if int(event["sequence"]) > sequence
            ]

    def set_active(self, active: bool, agents: list[str] | None = None) -> None:
        with self._state_lock:
            self._active = bool(active)
            self._active_agents = list(agents or [])
            if active:
                self._control_owner = "agent"
            else:
                self._control_owner = "human"
        self.publish(
            "cockpit.active",
            {"active": bool(active), "agents": list(agents or [])},
        )

    def note_human_activity(self, source: str = "browser") -> None:
        with self._state_lock:
            self._control_owner = "human"
            self._last_human_activity = now_iso()
        self.publish("control.human", {"source": source})

    def hand_back(self) -> None:
        with self._state_lock:
            self._control_owner = "agent" if self._active else "human"
        self.publish("control.handback", {"owner": self._control_owner})

    def state(self) -> dict[str, Any]:
        with self._state_lock:
            active = self._active
            agents = list(self._active_agents)
            owner = self._control_owner
            human_at = self._last_human_activity
        with self._approval_lock:
            approvals = [
                approval.public()
                for approval in self._approvals.values()
                if approval.decision is None
            ]
        return {
            "ready": self._server is not None,
            "url": self.url,
            "active": active,
            "active_agents": agents,
            "control_owner": owner,
            "last_human_activity": human_at,
            "event_sequence": self._event_sequence,
            "pending_approvals": approvals,
        }

    async def request_approval(
        self,
        provider: str,
        kind: str,
        detail: Mapping[str, Any],
        timeout: float = APPROVAL_TIMEOUT_SECONDS,
    ) -> str:
        return await asyncio.to_thread(
            self._request_approval_blocking,
            provider,
            kind,
            dict(detail),
            timeout,
        )

    def _request_approval_blocking(
        self,
        provider: str,
        kind: str,
        detail: dict[str, Any],
        timeout: float,
    ) -> str:
        with self._approval_lock:
            already_granted = (provider, kind) in self._session_grants
        if already_granted:
            self.publish(
                "approval.session-grant",
                {"provider": provider, "kind": kind, "detail": detail},
            )
            return "acceptForSession"
        identifier = uuid.uuid4().hex[:12]
        approval = PendingApproval(identifier, provider, kind, detail)
        with self._approval_lock:
            self._approvals[identifier] = approval
        self.publish("approval.requested", approval.public())
        summary = detail.get("command") or detail.get("reason") or kind
        print(
            f"\n[APPROVAL {identifier}] {provider.title()} requests {summary}. "
            f"Use /approve {identifier}, /approve-session {identifier}, "
            f"or /deny {identifier}.",
            flush=True,
        )
        deadline = time.monotonic() + max(1.0, timeout)
        with approval.condition:
            while approval.decision is None and time.monotonic() < deadline:
                approval.condition.wait(timeout=deadline - time.monotonic())
        if approval.decision is None:
            self.resolve_approval(identifier, "cancel")
        return approval.decision or "cancel"

    def resolve_approval(self, identifier: str, decision: str) -> None:
        normalized = decision.strip()
        aliases = {
            "approve": "accept",
            "once": "accept",
            "session": "acceptForSession",
            "approve-session": "acceptForSession",
            "deny": "decline",
        }
        normalized = aliases.get(normalized, normalized)
        if normalized not in {
            "accept",
            "acceptForSession",
            "decline",
            "cancel",
        }:
            raise BridgeError(f"Unsupported approval decision: {decision!r}")
        with self._approval_lock:
            approval = self._approvals.get(identifier)
        if approval is None:
            raise BridgeError(f"Unknown approval request: {identifier}")
        with approval.condition:
            if approval.decision is not None:
                raise BridgeError(f"Approval request {identifier} is already resolved")
            approval.decision = normalized
            approval.resolved_at = now_iso()
            approval.condition.notify_all()
        if normalized == "acceptForSession":
            with self._approval_lock:
                self._session_grants.add((approval.provider, approval.kind))
        self.publish("approval.resolved", approval.public())

    def pending_approvals(self) -> list[dict[str, Any]]:
        with self._approval_lock:
            return [
                approval.public()
                for approval in self._approvals.values()
                if approval.decision is None
            ]

    def _load_or_create_token(self) -> str:
        try:
            token = self.token_path.read_text(encoding="utf-8").strip()
        except OSError:
            token = ""
        if len(token) < 32:
            token = secrets.token_urlsafe(32)
            temporary = self.token_path.with_suffix(".tmp")
            temporary.write_text(token + "\n", encoding="utf-8")
            os.chmod(temporary, 0o600)
            temporary.replace(self.token_path)
        os.chmod(self.token_path, 0o600)
        return token

    def _write_info(self) -> None:
        payload = {
            "schema_version": 1,
            "url": self.url,
            "host": self.host,
            "port": self.port,
            "pid": os.getpid(),
            "started_at": now_iso(),
        }
        temporary = self.info_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.chmod(temporary, 0o600)
        temporary.replace(self.info_path)

    def _handler_type(self) -> type[BaseHTTPRequestHandler]:
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "InceptionBridge/1"

            def log_message(self, _format: str, *_args: object) -> None:
                return

            def do_OPTIONS(self) -> None:  # noqa: N802
                self.send_response(HTTPStatus.NO_CONTENT)
                self._cors_headers()
                self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.end_headers()

            def do_GET(self) -> None:  # noqa: N802
                route = urlsplit(self.path)
                if route.path == "/health":
                    self._json(HTTPStatus.OK, {"status": "ok"})
                    return
                if not self._authorized():
                    return
                if route.path == "/api/state":
                    self._json(HTTPStatus.OK, bridge.state())
                    return
                if route.path == "/api/approvals":
                    self._json(
                        HTTPStatus.OK,
                        {"approvals": bridge.pending_approvals()},
                    )
                    return
                if route.path == "/api/events":
                    query = parse_qs(route.query)
                    try:
                        after = int((query.get("after") or ["0"])[0])
                        timeout = float((query.get("timeout") or ["0"])[0])
                    except ValueError:
                        self._error(HTTPStatus.BAD_REQUEST, "Invalid event cursor")
                        return
                    events = bridge.events_after(after, timeout)
                    self._json(
                        HTTPStatus.OK,
                        {
                            "events": events,
                            "sequence": bridge._event_sequence,
                        },
                    )
                    return
                self._error(HTTPStatus.NOT_FOUND, "Unknown bridge endpoint")

            def do_POST(self) -> None:  # noqa: N802
                route = urlsplit(self.path)
                if route.path == "/api/pair":
                    payload = self._body()
                    if payload is None:
                        return
                    bridge._pair_attempts += 1
                    if bridge._pair_attempts > PAIR_ATTEMPT_LIMIT:
                        self._error(
                            HTTPStatus.TOO_MANY_REQUESTS,
                            "Pairing attempt limit reached; restart the cockpit",
                        )
                        return
                    if not secrets.compare_digest(
                        str(payload.get("code") or ""),
                        bridge.pair_code,
                    ):
                        self._error(HTTPStatus.UNAUTHORIZED, "Invalid pairing code")
                        return
                    bridge._pair_attempts = 0
                    self._json(HTTPStatus.OK, {"token": bridge.token, "url": bridge.url})
                    bridge.publish("bridge.paired", {"origin": self.headers.get("Origin", "")})
                    return
                if not self._authorized():
                    return
                payload = self._body()
                if payload is None:
                    return
                try:
                    if route.path == "/api/command":
                        bridge.submit_command(str(payload.get("command") or ""))
                        self._json(HTTPStatus.ACCEPTED, {"accepted": True})
                        return
                    if route.path == "/api/transcript":
                        command = bridge.submit_transcript(
                            str(payload.get("text") or "")
                        )
                        self._json(
                            HTTPStatus.ACCEPTED,
                            {"accepted": True, "command": command},
                        )
                        return
                    if route.path == "/api/human-activity":
                        bridge.note_human_activity(
                            str(payload.get("source") or "browser")
                        )
                        self._json(HTTPStatus.OK, bridge.state())
                        return
                    if route.path == "/api/handback":
                        bridge.hand_back()
                        self._json(HTTPStatus.OK, bridge.state())
                        return
                    if route.path.startswith("/api/approvals/"):
                        identifier = route.path.rsplit("/", 1)[-1]
                        bridge.resolve_approval(
                            identifier,
                            str(payload.get("decision") or ""),
                        )
                        self._json(HTTPStatus.OK, {"resolved": identifier})
                        return
                except BridgeError as exc:
                    self._error(HTTPStatus.BAD_REQUEST, str(exc))
                    return
                self._error(HTTPStatus.NOT_FOUND, "Unknown bridge endpoint")

            def _authorized(self) -> bool:
                value = self.headers.get("Authorization", "")
                expected = f"Bearer {bridge.token}"
                if not secrets.compare_digest(value, expected):
                    self._error(HTTPStatus.UNAUTHORIZED, "Bearer token required")
                    return False
                return True

            def _body(self) -> dict[str, Any] | None:
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    size = -1
                if size < 0 or size > MAX_BODY_BYTES:
                    self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Request body is too large")
                    return None
                try:
                    payload = json.loads(self.rfile.read(size) or b"{}")
                except (UnicodeDecodeError, json.JSONDecodeError):
                    self._error(HTTPStatus.BAD_REQUEST, "Request body must be JSON")
                    return None
                if not isinstance(payload, dict):
                    self._error(HTTPStatus.BAD_REQUEST, "Request body must be an object")
                    return None
                return payload

            def _error(self, status: HTTPStatus, message: str) -> None:
                self._json(status, {"error": message})

            def _json(self, status: HTTPStatus, payload: Mapping[str, Any]) -> None:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self._cors_headers()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _cors_headers(self) -> None:
                origin = self.headers.get("Origin", "")
                if origin.startswith("chrome-extension://") or origin in {
                    "http://127.0.0.1",
                    "http://localhost",
                }:
                    self.send_header("Access-Control-Allow-Origin", origin)
                    self.send_header("Vary", "Origin")

        return Handler


def consequential_tool_request(tool_name: str, tool_input: Mapping[str, Any]) -> bool:
    """Return true for actions that need a separate human approval."""
    name = tool_name.lower()
    text = json.dumps(dict(tool_input), ensure_ascii=False).lower()
    if name in {
        "delete",
        "sendmessage",
        "send_message",
        "purchase",
        "payment",
        "publish",
        "deploy",
    }:
        return True
    command_markers = (
        "git push",
        "git reset --hard",
        "git clean -",
        "git branch -d",
        "gh pr create",
        "gh pr merge",
        "gh pr close",
        "gh issue close",
        "gh release create",
        "gh release delete",
        "npm publish",
        "npm unpublish",
        "npm deprecate",
        "pypi",
        "twine upload",
        "firebase deploy",
        "firebase hosting:disable",
        "wrangler deploy",
        "wrangler delete",
        "gcloud run deploy",
        "kubectl apply",
        "kubectl delete",
        "terraform apply",
        "terraform destroy",
        "curl -x post",
        "curl --request post",
        "curl -d ",
        "curl --data",
        "curl --form",
        "curl -t ",
        "curl --upload-file",
        "rm -",
        "\"rm ",
        "rmdir ",
        "unlink ",
        "shred ",
        "del /",
        "remove-item",
        "send email",
        "send message",
        "purchase",
        "payment",
    )
    return any(marker in text for marker in command_markers)
