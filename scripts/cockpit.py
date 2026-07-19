#!/usr/bin/env python3
"""George-controlled live switchboard for Codex and Claude.

The cockpit deliberately has no automatic agent-to-agent loop. George grants
every turn, and forwarding an answer is an explicit operator command.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import fcntl
import json
import os
import re
import shlex
import signal
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence


PROJECT = Path(__file__).resolve().parents[1]
CONTINUITY_STATE_PATH = PROJECT / "runtime" / "state.json"
DEFAULT_STATE_PATH = PROJECT / "runtime" / "cockpit-state.json"
DEFAULT_JOURNAL_PATH = PROJECT / "runtime" / "cockpit-events.jsonl"
DEFAULT_LOCK_PATH = PROJECT / "runtime" / "cockpit.lock"
DEFAULT_ERROR_LOG = PROJECT / "runtime" / "cockpit-errors.log"
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

DISCUSSION_INSTRUCTIONS = (
    "You are one endpoint in George's supervised Claude-Codex cockpit. "
    "This surface is discussion-only. Do not edit files, execute shell commands, "
    "call tools, or take external actions. Analyze the message and answer in plain "
    "text. George grants every speaking turn and separately authorizes actions."
)

HELP_TEXT = """Commands:
  /both TEXT              ask Claude and Codex independently
  /claude TEXT            grant Claude one turn
  /codex TEXT             grant Codex one turn
  /pass claude codex      send Claude's last answer to Codex
  /pass codex claude      send Codex's last answer to Claude
  /pass SOURCE TARGET NOTE  forward with George's added instruction
  /last [claude|codex]    show the most recent complete answer
  /sessions               show the persistent pair
  /stop                   interrupt the running turn(s)
  /help                   show this help
  /quit                   close the cockpit

Natural forms also work: "both: ...", "claude: ...", and "codex: ...".
No turn advances automatically.
"""


class CockpitError(RuntimeError):
    """The supervised switchboard could not complete an operation."""


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def valid_uuid(value: Any) -> bool:
    return isinstance(value, str) and bool(UUID_RE.fullmatch(value))


def canonical_thread_id(path: Path = CONTINUITY_STATE_PATH) -> str:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CockpitError(
            f"Cannot read canonical continuity state at {path}: {exc}"
        ) from exc
    thread_id = data.get("canonical_thread_id")
    if not valid_uuid(thread_id):
        raise CockpitError(f"Invalid canonical Codex thread id: {thread_id!r}")
    return thread_id


class StateStore:
    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, Any] = {}

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            self.data = {"schema_version": 1}
            return self.data
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CockpitError(
                f"Cannot read cockpit state at {self.path}: {exc}"
            ) from exc
        if data.get("schema_version") != 1:
            raise CockpitError("Unsupported cockpit-state schema")
        for key in ("codex_source_thread_id", "codex_thread_id", "claude_session_id"):
            value = data.get(key)
            if value is not None and not valid_uuid(value):
                raise CockpitError(f"Invalid {key}: {value!r}")
        self.data = data
        return data

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data["schema_version"] = 1
        self.data["updated_at"] = now_iso()
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=self.path.parent, delete=False
        ) as handle:
            json.dump(self.data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            temporary = Path(handle.name)
        os.chmod(temporary, 0o600)
        temporary.replace(self.path)


class Journal:
    def __init__(self, path: Path):
        self.path = path

    def append(self, event: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        record = {"at": now_iso(), **event}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        os.chmod(self.path, 0o600)


class CockpitLock:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = path.open("a+")
        try:
            fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self.handle.close()
            raise CockpitError("Another cockpit is already running") from exc

    def close(self) -> None:
        with contextlib.suppress(OSError):
            fcntl.flock(self.handle, fcntl.LOCK_UN)
        self.handle.close()


@dataclass
class TurnResult:
    agent: str
    text: str
    status: str = "completed"


DeltaCallback = Callable[[str], None]


@dataclass
class _CodexTurn:
    done: asyncio.Future[TurnResult]
    emit: DeltaCallback
    turn_id: str | None = None
    order: list[str] = field(default_factory=list)
    texts: dict[str, str] = field(default_factory=dict)
    phases: dict[str, str | None] = field(default_factory=dict)
    saw_delta: set[str] = field(default_factory=set)

    def remember_item(self, item: dict[str, Any]) -> None:
        if item.get("type") != "agentMessage":
            return
        item_id = item.get("id")
        if not isinstance(item_id, str):
            return
        if item_id not in self.order:
            self.order.append(item_id)
        self.phases[item_id] = item.get("phase")
        text = item.get("text")
        if isinstance(text, str):
            self.texts[item_id] = text

    def final_text(self) -> str:
        finals = [
            self.texts[item_id]
            for item_id in self.order
            if self.phases.get(item_id) == "final_answer" and self.texts.get(item_id)
        ]
        if finals:
            return finals[-1]
        messages = [
            self.texts[item_id] for item_id in self.order if self.texts.get(item_id)
        ]
        return messages[-1] if messages else ""


class CodexEndpoint:
    name = "codex"

    def __init__(
        self,
        cwd: Path,
        thread_id: str | None,
        source_thread_id: str | None,
        error_log: Path = DEFAULT_ERROR_LOG,
        command: Sequence[str] | None = None,
    ):
        self.cwd = cwd
        self.thread_id = thread_id
        self.source_thread_id = source_thread_id
        self.error_log = error_log
        self.command = list(command or ("codex", "app-server", "--stdio"))
        self.process: asyncio.subprocess.Process | None = None
        self.reader_task: asyncio.Task[None] | None = None
        self.pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self.request_id = 0
        self.active: _CodexTurn | None = None
        self.error_handle: Any = None
        self.closing = False

    async def start(self) -> str:
        self.error_log.parent.mkdir(parents=True, exist_ok=True)
        self.error_handle = self.error_log.open("ab", buffering=0)
        self.process = await asyncio.create_subprocess_exec(
            *self.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=self.error_handle,
            cwd=self.cwd,
        )
        self.reader_task = asyncio.create_task(self._read_loop())
        await self._request(
            "initialize",
            {
                "clientInfo": {
                    "name": "george_cockpit",
                    "title": "George Cockpit",
                    "version": "0.1.0",
                },
                # excludeTurns keeps a large resumed history out of the wire
                # response while Codex still retains it natively.
                "capabilities": {"experimentalApi": True},
            },
        )
        await self._send({"method": "initialized", "params": {}})

        safety = {
            "cwd": str(self.cwd),
            "sandbox": "read-only",
            "approvalPolicy": "never",
            "developerInstructions": DISCUSSION_INSTRUCTIONS,
        }
        if self.thread_id:
            response = await self._request(
                "thread/resume",
                {"threadId": self.thread_id, "excludeTurns": True, **safety},
                timeout=180,
            )
        elif self.source_thread_id:
            response = await self._request(
                "thread/fork",
                {"threadId": self.source_thread_id, "excludeTurns": True, **safety},
                timeout=180,
            )
        else:
            response = await self._request("thread/start", safety, timeout=180)
        thread_id = response.get("thread", {}).get("id")
        if not valid_uuid(thread_id):
            raise CockpitError(f"Codex returned an invalid thread id: {thread_id!r}")
        self.thread_id = thread_id
        return thread_id

    async def ask(self, prompt: str, emit: DeltaCallback) -> TurnResult:
        if self.active is not None:
            raise CockpitError("Codex already has an active turn")
        if not self.thread_id:
            raise CockpitError("Codex endpoint has not started")
        loop = asyncio.get_running_loop()
        turn = _CodexTurn(done=loop.create_future(), emit=emit)
        self.active = turn
        try:
            response = await self._request(
                "turn/start",
                {
                    "threadId": self.thread_id,
                    "input": [{"type": "text", "text": prompt}],
                },
            )
            returned_id = response.get("turn", {}).get("id")
            if isinstance(returned_id, str):
                turn.turn_id = returned_id
            return await turn.done
        finally:
            if self.active is turn:
                self.active = None

    async def interrupt(self) -> None:
        turn = self.active
        if turn is None:
            return
        if turn.turn_id and self.thread_id:
            with contextlib.suppress(CockpitError, asyncio.TimeoutError):
                await self._request(
                    "turn/interrupt",
                    {"threadId": self.thread_id, "turnId": turn.turn_id},
                    timeout=15,
                )
        if not turn.done.done():
            turn.done.set_result(TurnResult("codex", turn.final_text(), "interrupted"))

    async def close(self) -> None:
        self.closing = True
        await self.interrupt()
        process = self.process
        if process and process.returncode is None:
            if process.stdin:
                process.stdin.close()
            try:
                await asyncio.wait_for(process.wait(), timeout=3)
            except asyncio.TimeoutError:
                process.terminate()
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(process.wait(), timeout=2)
            if process.returncode is None:
                process.kill()
                await process.wait()
        if self.reader_task and not self.reader_task.done():
            self.reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.reader_task
        if self.error_handle:
            self.error_handle.close()

    async def _request(
        self, method: str, params: dict[str, Any], timeout: float | None = 120
    ) -> dict[str, Any]:
        self.request_id += 1
        request_id = self.request_id
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        await self._send({"method": method, "id": request_id, "params": params})
        try:
            message = await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError as exc:
            self.pending.pop(request_id, None)
            raise CockpitError(f"Codex {method} timed out") from exc
        if "error" in message:
            error = message.get("error") or {}
            raise CockpitError(f"Codex {method} failed: {error.get('message', error)}")
        result = message.get("result")
        if not isinstance(result, dict):
            raise CockpitError(f"Codex {method} returned no result")
        return result

    async def _send(self, message: dict[str, Any]) -> None:
        if (
            not self.process
            or not self.process.stdin
            or self.process.returncode is not None
        ):
            raise CockpitError("Codex app-server is not running")
        self.process.stdin.write((json.dumps(message) + "\n").encode())
        await self.process.stdin.drain()

    async def _read_loop(self) -> None:
        assert self.process and self.process.stdout
        try:
            while line := await self.process.stdout.readline():
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if "id" in message and "method" in message:
                    await self._deny_server_request(message)
                elif "id" in message:
                    future = self.pending.pop(message["id"], None)
                    if future and not future.done():
                        future.set_result(message)
                elif "method" in message:
                    self._notification(message)
        except asyncio.CancelledError:
            raise
        finally:
            if not self.closing:
                error = CockpitError("Codex app-server stopped unexpectedly")
                for future in self.pending.values():
                    if not future.done():
                        future.set_exception(error)
                self.pending.clear()
                if self.active and not self.active.done.done():
                    self.active.done.set_exception(error)

    async def _deny_server_request(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        request_id = message.get("id")
        if method in {
            "item/commandExecution/requestApproval",
            "item/fileChange/requestApproval",
        }:
            await self._send({"id": request_id, "result": {"decision": "cancel"}})
            return
        await self._send(
            {
                "id": request_id,
                "error": {
                    "code": -32000,
                    "message": "George Cockpit discussion mode denies action requests",
                },
            }
        )

    def _notification(self, message: dict[str, Any]) -> None:
        turn = self.active
        if turn is None:
            return
        method = message.get("method")
        params = message.get("params") or {}
        if self.thread_id and params.get("threadId") not in (None, self.thread_id):
            return
        if method == "turn/started":
            turn_id = params.get("turn", {}).get("id")
            if isinstance(turn_id, str):
                turn.turn_id = turn_id
        elif method == "item/started":
            turn.remember_item(params.get("item") or {})
        elif method == "item/agentMessage/delta":
            item_id = params.get("itemId")
            delta = params.get("delta")
            if isinstance(item_id, str) and isinstance(delta, str):
                if item_id not in turn.order:
                    turn.order.append(item_id)
                turn.saw_delta.add(item_id)
                turn.texts[item_id] = turn.texts.get(item_id, "") + delta
                turn.emit(delta)
        elif method == "item/completed":
            item = params.get("item") or {}
            item_id = item.get("id")
            turn.remember_item(item)
            if (
                item.get("type") == "agentMessage"
                and isinstance(item_id, str)
                and item_id not in turn.saw_delta
                and isinstance(item.get("text"), str)
            ):
                turn.emit(item["text"])
        elif method == "turn/completed" and not turn.done.done():
            completed = params.get("turn") or {}
            status = completed.get("status") or "completed"
            if isinstance(status, dict):
                status = next(iter(status), "completed")
            turn.done.set_result(TurnResult("codex", turn.final_text(), str(status)))


@dataclass
class _ClaudeTurn:
    done: asyncio.Future[TurnResult]
    emit: DeltaCallback
    streamed: str = ""
    assistant_messages: list[str] = field(default_factory=list)


def claude_stream_delta(message: dict[str, Any]) -> str:
    if message.get("type") != "stream_event":
        return ""
    event = message.get("event") or {}
    if event.get("type") != "content_block_delta":
        return ""
    delta = event.get("delta") or {}
    if delta.get("type") != "text_delta":
        return ""
    text = delta.get("text")
    return text if isinstance(text, str) else ""


def claude_assistant_text(message: dict[str, Any]) -> str:
    if message.get("type") != "assistant":
        return ""
    content = (message.get("message") or {}).get("content") or []
    return "".join(
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    )


def default_claude_command(cwd: Path, session_id: str | None) -> list[str]:
    flags = [
        "claude",
        "-p",
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--include-partial-messages",
        "--verbose",
        "--permission-mode",
        "dontAsk",
        "--tools",
        "",
        "--append-system-prompt",
        DISCUSSION_INSTRUCTIONS,
    ]
    if session_id:
        flags.extend(("--resume", session_id))

    custom = os.environ.get("GEORGE_COCKPIT_CLAUDE_COMMAND")
    if custom:
        return [*shlex.split(custom), *flags[1:]]
    prefix = os.environ.get("PREFIX", "")
    if "com.termux" in prefix:
        return [
            "proot-distro",
            "login",
            "debian",
            "--",
            "/bin/sh",
            "-c",
            'cd "$1" && shift && exec "$@"',
            "george-cockpit",
            str(cwd),
            *flags,
        ]
    return flags


class ClaudeEndpoint:
    name = "claude"

    def __init__(
        self,
        cwd: Path,
        session_id: str | None,
        session_callback: Callable[[str], None] | None = None,
        error_log: Path = DEFAULT_ERROR_LOG,
        command: Sequence[str] | None = None,
    ):
        self.cwd = cwd
        self.session_id = session_id
        self.session_callback = session_callback
        self.error_log = error_log
        self.command_override = list(command) if command else None
        self.process: asyncio.subprocess.Process | None = None
        self.reader_task: asyncio.Task[None] | None = None
        self.active: _ClaudeTurn | None = None
        self.error_handle: Any = None
        self.closing = False
        self.interrupting = False

    async def start(self) -> None:
        await self._spawn()

    async def _spawn(self) -> None:
        if self.process and self.process.returncode is None:
            return
        self.closing = False
        self.interrupting = False
        self.error_log.parent.mkdir(parents=True, exist_ok=True)
        if not self.error_handle or self.error_handle.closed:
            self.error_handle = self.error_log.open("ab", buffering=0)
        command = self.command_override or default_claude_command(
            self.cwd, self.session_id
        )
        self.process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=self.error_handle,
            cwd=self.cwd,
            start_new_session=True,
        )
        self.reader_task = asyncio.create_task(self._read_loop(self.process))

    async def ask(self, prompt: str, emit: DeltaCallback) -> TurnResult:
        if self.active is not None:
            raise CockpitError("Claude already has an active turn")
        await self._spawn()
        assert self.process and self.process.stdin
        loop = asyncio.get_running_loop()
        turn = _ClaudeTurn(done=loop.create_future(), emit=emit)
        self.active = turn
        message = {
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": prompt}],
            },
        }
        self.process.stdin.write((json.dumps(message) + "\n").encode())
        await self.process.stdin.drain()
        try:
            return await turn.done
        finally:
            if self.active is turn:
                self.active = None

    async def interrupt(self) -> None:
        turn = self.active
        if turn is None:
            return
        self.interrupting = True
        if not turn.done.done():
            text = (
                turn.assistant_messages[-1]
                if turn.assistant_messages
                else turn.streamed
            )
            turn.done.set_result(TurnResult("claude", text, "interrupted"))
        process = self.process
        if process and process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGINT)
            try:
                await asyncio.wait_for(process.wait(), timeout=3)
            except asyncio.TimeoutError:
                process.terminate()
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(process.wait(), timeout=2)
            if process.returncode is None:
                process.kill()
                await process.wait()
        self.process = None
        self.interrupting = False

    async def close(self) -> None:
        self.closing = True
        await self.interrupt()
        process = self.process
        if process and process.returncode is None:
            if process.stdin:
                process.stdin.close()
            try:
                await asyncio.wait_for(process.wait(), timeout=3)
            except asyncio.TimeoutError:
                process.terminate()
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(process.wait(), timeout=2)
            if process.returncode is None:
                process.kill()
                await process.wait()
        if self.reader_task and not self.reader_task.done():
            self.reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.reader_task
        if self.error_handle:
            self.error_handle.close()

    async def _read_loop(self, process: asyncio.subprocess.Process) -> None:
        assert process.stdout
        try:
            while line := await process.stdout.readline():
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                session_id = message.get("session_id")
                if valid_uuid(session_id) and session_id != self.session_id:
                    self.session_id = session_id
                    if self.session_callback:
                        self.session_callback(session_id)
                turn = self.active
                if turn is None:
                    continue
                delta = claude_stream_delta(message)
                if delta:
                    turn.streamed += delta
                    turn.emit(delta)
                    continue
                assistant = claude_assistant_text(message)
                if assistant:
                    turn.assistant_messages.append(assistant)
                    if not turn.streamed:
                        turn.emit(assistant)
                    continue
                if message.get("type") == "result" and not turn.done.done():
                    if message.get("is_error"):
                        detail = (
                            message.get("result")
                            or message.get("subtype")
                            or "unknown error"
                        )
                        turn.done.set_exception(
                            CockpitError(f"Claude failed: {detail}")
                        )
                    else:
                        text = message.get("result")
                        if not isinstance(text, str) or not text:
                            text = (
                                turn.assistant_messages[-1]
                                if turn.assistant_messages
                                else turn.streamed
                            )
                        if not turn.streamed and not turn.assistant_messages and text:
                            turn.emit(text)
                        turn.done.set_result(TurnResult("claude", text, "completed"))
        except asyncio.CancelledError:
            raise
        finally:
            if self.process is process:
                self.process = None
            if not self.closing and not self.interrupting:
                turn = self.active
                if turn and not turn.done.done():
                    turn.done.set_exception(
                        CockpitError("Claude stream stopped unexpectedly")
                    )


class LabeledOutput:
    def __init__(self):
        self.buffers = {"claude": "", "codex": ""}

    def feed(self, agent: str, text: str) -> None:
        buffer = self.buffers.get(agent, "") + text.replace("\r", "")
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            self._print(agent, line)
        while len(buffer) >= 180:
            split_at = buffer.rfind(" ", 0, 180)
            if split_at < 60:
                split_at = 180
            self._print(agent, buffer[:split_at])
            buffer = buffer[split_at:].lstrip()
        self.buffers[agent] = buffer

    def flush(self, agent: str) -> None:
        buffer = self.buffers.get(agent, "")
        if buffer:
            self._print(agent, buffer)
        self.buffers[agent] = ""

    @staticmethod
    def _print(agent: str, line: str) -> None:
        print(f"[{agent.upper()}] {line}", flush=True)


class Broker:
    def __init__(
        self,
        codex: Any,
        claude: Any,
        journal: Journal | None = None,
    ):
        self.endpoints = {"codex": codex, "claude": claude}
        self.journal = journal
        self.last: dict[str, str] = {}
        self.active_agents: set[str] = set()

    async def ask(self, target: str, prompt: str) -> dict[str, TurnResult]:
        if self.active_agents:
            raise CockpitError("A cockpit turn is already running")
        agents = ["claude", "codex"] if target == "both" else [target]
        if any(agent not in self.endpoints for agent in agents):
            raise CockpitError(f"Unknown target: {target}")
        output = LabeledOutput()
        self.active_agents = set(agents)
        if self.journal:
            self.journal.append({"type": "prompt", "target": target, "text": prompt})

        async def run(agent: str) -> tuple[str, TurnResult | BaseException]:
            try:
                result = await self.endpoints[agent].ask(
                    prompt, lambda text: output.feed(agent, text)
                )
                return agent, result
            except BaseException as exc:
                return agent, exc
            finally:
                output.flush(agent)
                self.active_agents.discard(agent)

        pairs = await asyncio.gather(*(run(agent) for agent in agents))
        results: dict[str, TurnResult] = {}
        errors: list[str] = []
        for agent, value in pairs:
            if isinstance(value, BaseException):
                errors.append(f"{agent}: {value}")
                continue
            results[agent] = value
            if value.text:
                self.last[agent] = value.text
            if self.journal:
                self.journal.append(
                    {
                        "type": "answer",
                        "agent": agent,
                        "status": value.status,
                        "text": value.text,
                    }
                )
        if errors:
            raise CockpitError("; ".join(errors))
        return results

    async def pass_answer(
        self, source: str, target: str, note: str = ""
    ) -> dict[str, TurnResult]:
        if (
            source == target
            or source not in self.endpoints
            or target not in self.endpoints
        ):
            raise CockpitError("Use two different agents: claude codex or codex claude")
        answer = self.last.get(source)
        if not answer:
            raise CockpitError(f"There is no completed {source} answer to forward")
        prompt = (
            f"George is forwarding {source.title()}'s answer for your independent review.\n\n"
            f"--- {source.title()} answer ---\n{answer}\n--- end answer ---"
        )
        if note:
            prompt += f"\n\nGeorge's instruction: {note}"
        return await self.ask(target, prompt)

    async def stop(self) -> None:
        await asyncio.gather(
            *(self.endpoints[agent].interrupt() for agent in tuple(self.active_agents)),
            return_exceptions=True,
        )


@dataclass(frozen=True)
class OperatorCommand:
    kind: str
    target: str | None = None
    text: str = ""
    source: str | None = None


def parse_operator_command(line: str) -> OperatorCommand:
    raw = line.strip()
    if not raw:
        return OperatorCommand("empty")
    natural = re.match(
        r"^(both|claude|codex)\s*:\s*(.+)$", raw, re.IGNORECASE | re.DOTALL
    )
    if natural:
        return OperatorCommand(
            "ask", natural.group(1).lower(), natural.group(2).strip()
        )
    if not raw.startswith("/"):
        raise CockpitError("Start with /both, /claude, or /codex (or use 'both: ...')")
    name, _, rest = raw[1:].partition(" ")
    name = name.lower()
    rest = rest.strip()
    if name in {"both", "claude", "codex"}:
        if not rest:
            raise CockpitError(f"/{name} needs a message")
        return OperatorCommand("ask", name, rest)
    if name == "pass":
        parts = rest.split(maxsplit=2)
        if len(parts) < 2:
            raise CockpitError("Use /pass claude codex or /pass codex claude")
        return OperatorCommand(
            "pass",
            target=parts[1].lower(),
            source=parts[0].lower(),
            text=parts[2] if len(parts) == 3 else "",
        )
    if name == "last":
        target = rest.lower() or None
        if target not in (None, "claude", "codex"):
            raise CockpitError("Use /last, /last claude, or /last codex")
        return OperatorCommand("last", target=target)
    if name in {"sessions", "stop", "help", "quit", "exit"}:
        return OperatorCommand("quit" if name == "exit" else name)
    raise CockpitError(f"Unknown command: /{name}")


class InputThread:
    def __init__(self, loop: asyncio.AbstractEventLoop):
        self.loop = loop
        self.queue: asyncio.Queue[str | None] = asyncio.Queue()
        self.thread = threading.Thread(
            target=self._read, daemon=True, name="cockpit-input"
        )

    def start(self) -> None:
        self.thread.start()

    def inject(self, value: str) -> None:
        self.queue.put_nowait(value)

    def _read(self) -> None:
        while True:
            line = sys.stdin.readline()
            if not line:
                self.loop.call_soon_threadsafe(self.queue.put_nowait, None)
                return
            self.loop.call_soon_threadsafe(self.queue.put_nowait, line)


def show_sessions(state: dict[str, Any]) -> None:
    print(f"Codex cockpit thread: {state.get('codex_thread_id') or 'not started'}")
    print(
        f"Claude cockpit session: {state.get('claude_session_id') or 'created on first turn'}"
    )
    print(f"Working directory: {state.get('cwd')}")


async def run_console(broker: Broker, state: dict[str, Any]) -> None:
    print("\nGeorge Cockpit is ready. Both agents are silent and action-disabled.")
    print("Type /help for commands. Type /stop during a response to interrupt it.\n")
    show_sessions(state)
    print("\ngeorge> ", end="", flush=True)

    loop = asyncio.get_running_loop()
    inputs = InputThread(loop)
    inputs.start()
    input_task: asyncio.Task[str | None] = asyncio.create_task(inputs.queue.get())
    active_task: asyncio.Task[dict[str, TurnResult]] | None = None
    running = {"active": False}

    def sigint() -> None:
        inputs.inject("/stop\n" if running["active"] else "/quit\n")

    with contextlib.suppress(NotImplementedError):
        loop.add_signal_handler(signal.SIGINT, sigint)

    try:
        while True:
            waiting: set[asyncio.Task[Any]] = {input_task}
            if active_task:
                waiting.add(active_task)
            completed, _ = await asyncio.wait(
                waiting, return_when=asyncio.FIRST_COMPLETED
            )

            if active_task and active_task in completed:
                try:
                    results = await active_task
                    statuses = ", ".join(
                        f"{agent}={result.status}" for agent, result in results.items()
                    )
                    print(f"[SYSTEM] Turn closed ({statuses or 'no answer'}).")
                except CockpitError as exc:
                    print(f"[SYSTEM] {exc}")
                running["active"] = False
                active_task = None
                print("george> ", end="", flush=True)

            if input_task not in completed:
                continue
            line = await input_task
            input_task = asyncio.create_task(inputs.queue.get())
            if line is None:
                if active_task:
                    await broker.stop()
                return
            try:
                command = parse_operator_command(line)
            except CockpitError as exc:
                print(f"[SYSTEM] {exc}")
                if not active_task:
                    print("george> ", end="", flush=True)
                continue
            if command.kind == "empty":
                if not active_task:
                    print("george> ", end="", flush=True)
                continue
            if active_task:
                if command.kind == "stop":
                    print("[SYSTEM] Interrupting active turn(s)…")
                    await broker.stop()
                elif command.kind == "quit":
                    await broker.stop()
                    with contextlib.suppress(CockpitError):
                        await active_task
                    return
                else:
                    print(
                        "[SYSTEM] A turn is running. Use /stop before granting another."
                    )
                continue
            if command.kind == "quit":
                return
            if command.kind == "help":
                print(HELP_TEXT)
            elif command.kind == "sessions":
                show_sessions(state)
            elif command.kind == "stop":
                print("[SYSTEM] Both agents are already idle.")
            elif command.kind == "last":
                agents = [command.target] if command.target else ["claude", "codex"]
                for agent in agents:
                    text = broker.last.get(agent or "")
                    print(
                        f"[{(agent or '').upper()}] {text or 'No completed answer yet.'}"
                    )
            elif command.kind == "ask":
                assert command.target
                running["active"] = True
                print(
                    f"[SYSTEM] George granted one {command.target} turn. "
                    "Type /stop and Enter (or press Ctrl-C) to interrupt."
                )
                active_task = asyncio.create_task(
                    broker.ask(command.target, command.text)
                )
            elif command.kind == "pass":
                assert command.source and command.target
                running["active"] = True
                print(
                    f"[SYSTEM] George forwarded {command.source} → {command.target}. "
                    "Type /stop and Enter (or press Ctrl-C) to interrupt."
                )
                active_task = asyncio.create_task(
                    broker.pass_answer(command.source, command.target, command.text)
                )
            if not active_task:
                print("george> ", end="", flush=True)
    finally:
        input_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await input_task
        with contextlib.suppress(NotImplementedError):
            loop.remove_signal_handler(signal.SIGINT)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="George-controlled live Claude-Codex switchboard"
    )
    result.add_argument(
        "--cwd",
        type=Path,
        default=Path.cwd(),
        help="working directory visible to both agents (default: current directory)",
    )
    result.add_argument(
        "--codex-source",
        help="Codex thread to fork on the cockpit's first run (default: canonical Inception thread)",
    )
    result.add_argument(
        "--state", type=Path, default=DEFAULT_STATE_PATH, help=argparse.SUPPRESS
    )
    result.add_argument(
        "--journal", type=Path, default=DEFAULT_JOURNAL_PATH, help=argparse.SUPPRESS
    )
    result.add_argument(
        "--lock", type=Path, default=DEFAULT_LOCK_PATH, help=argparse.SUPPRESS
    )
    result.add_argument(
        "--error-log", type=Path, default=DEFAULT_ERROR_LOG, help=argparse.SUPPRESS
    )
    return result


async def async_main(args: argparse.Namespace) -> int:
    cwd = args.cwd.expanduser().resolve()
    if not cwd.is_dir():
        raise CockpitError(f"Working directory does not exist: {cwd}")
    if args.codex_source and not valid_uuid(args.codex_source):
        raise CockpitError(f"Invalid --codex-source: {args.codex_source!r}")

    store = StateStore(args.state)
    state = store.load()
    source = (
        state.get("codex_source_thread_id")
        or args.codex_source
        or canonical_thread_id()
    )
    state["codex_source_thread_id"] = source
    state["cwd"] = str(cwd)
    store.save()

    def remember_claude(session_id: str) -> None:
        state["claude_session_id"] = session_id
        store.save()

    codex = CodexEndpoint(
        cwd,
        state.get("codex_thread_id"),
        source,
        error_log=args.error_log,
    )
    claude = ClaudeEndpoint(
        cwd,
        state.get("claude_session_id"),
        session_callback=remember_claude,
        error_log=args.error_log,
    )
    try:
        print("Starting supervised Codex endpoint…", flush=True)
        state["codex_thread_id"] = await codex.start()
        store.save()
        print("Starting supervised Claude endpoint…", flush=True)
        await claude.start()
        broker = Broker(codex, claude, Journal(args.journal))
        await run_console(broker, state)
        return 0
    finally:
        await asyncio.gather(codex.close(), claude.close(), return_exceptions=True)


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    lock: CockpitLock | None = None
    try:
        lock = CockpitLock(args.lock)
        return asyncio.run(async_main(args))
    except CockpitError as exc:
        print(f"inception cockpit: {exc}", file=sys.stderr)
        return 1
    finally:
        if lock:
            lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
