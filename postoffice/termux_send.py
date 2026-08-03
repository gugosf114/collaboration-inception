#!/usr/bin/env python3
"""Type a message into an existing raw Termux Codex terminal.

Termux owns the PTY master for every raw terminal session.  On George's
same-UID, debuggable Termux build, pidfd_getfd can duplicate that already-open
master without reopening /dev/ptmx (which would create a different PTY).
Writing to the duplicate is therefore the same input path as keyboard typing.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


SYS_PIDFD_OPEN = 434
SYS_PIDFD_GETFD = 438
TIOCGPTN = 0x80045430
MAX_MESSAGE_BYTES = 16 * 1024
VISIBLE_CHAR_DELAY = 0.04
REPLY_CHAR_DELAY = 0.01
SUBMIT_DELAY = 0.35
POST_IDLE_SETTLE = 1.0
REPLY_TIMEOUT = 900
IDLE_TIMEOUT = 900
TASK_EVENT_RE = re.compile(
    rb'"type"\s*:\s*"(task_started|task_complete|turn_aborted)"'
)
DEFAULT_CROSSCHECK_DIR = (
    Path("/data/data/com.termux/files/home/postoffice") / "crosschecks"
)
DEFAULT_LOCK_DIR = Path("/data/data/com.termux/files/home/postoffice") / "locks"
DEFAULT_TITLE_REGISTRY = (
    Path("/data/data/com.termux/files/home/postoffice") / "session-titles.json"
)
AGENT_BRIDGE_MCP_URL = "http://127.0.0.1:8080/mcp"
AGENT_BRIDGE_MCP_TOKEN = "agentbridge-mcp"
AGENT_BRIDGE_TIMEOUT = 10.0
TERMUX_SESSION_NUMBER_ENV = (
    "SHELL_CMD__APP_TERMINAL_SESSION_NUMBER_SINCE_APP_START"
)


class SendError(RuntimeError):
    pass


def _decode_mcp_json(payload: bytes) -> dict[str, Any]:
    text = payload.decode("utf-8", "replace").strip()
    if not text:
        return {}
    if text.startswith("data:") or text.startswith("event:"):
        data_lines = [
            line.removeprefix("data:").lstrip()
            for line in text.splitlines()
            if line.startswith("data:")
        ]
        text = "\n".join(data_lines).strip()
    try:
        result = json.loads(text)
    except ValueError as exc:
        raise SendError("Agent Bridge returned malformed MCP JSON") from exc
    if result is None:
        return {}
    if not isinstance(result, dict):
        raise SendError("Agent Bridge returned a non-object MCP response")
    return result


class AgentBridgeMCP:
    """Small session-scoped client for George's on-device accessibility bridge."""

    def __init__(
        self,
        url: str = AGENT_BRIDGE_MCP_URL,
        token: str = AGENT_BRIDGE_MCP_TOKEN,
        timeout: float = AGENT_BRIDGE_TIMEOUT,
    ) -> None:
        self.url = url
        self.token = token
        self.timeout = timeout
        self.session_id: str | None = None

    def _post(self, payload: dict[str, Any]) -> tuple[dict[str, Any], Any]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {self.token}",
        }
        if self.session_id:
            headers["mcp-session-id"] = self.session_id
        request = urllib.request.Request(
            self.url,
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read()
                response_headers = response.headers
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise SendError(f"Agent Bridge MCP is unavailable: {exc}") from exc
        decoded = _decode_mcp_json(body)
        if decoded.get("error"):
            raise SendError(f"Agent Bridge MCP error: {decoded['error']}")
        return decoded, response_headers

    def initialize(self) -> None:
        response, headers = self._post(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {
                        "name": "post-office-termux-title",
                        "version": "1",
                    },
                },
            }
        )
        session_id = headers.get("mcp-session-id")
        if not session_id:
            raise SendError("Agent Bridge MCP did not return a session id")
        if "result" not in response:
            raise SendError("Agent Bridge MCP initialization did not complete")
        self.session_id = session_id
        self._post(
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
            }
        )

    def call(self, tool: str, arguments: dict[str, Any] | None = None) -> str:
        if self.session_id is None:
            self.initialize()
        response, _headers = self._post(
            {
                "jsonrpc": "2.0",
                "id": secrets.randbelow(2**31 - 1) + 1,
                "method": "tools/call",
                "params": {"name": tool, "arguments": arguments or {}},
            }
        )
        result = response.get("result")
        if not isinstance(result, dict):
            raise SendError(f"Agent Bridge tool {tool!r} returned no result")
        text = "\n".join(
            block.get("text", "")
            for block in result.get("content", [])
            if isinstance(block, dict) and block.get("type") == "text"
        )
        if result.get("isError"):
            raise SendError(f"Agent Bridge tool {tool!r} failed: {text.strip()}")
        return text


@dataclass(frozen=True)
class CodexSession:
    pid: int
    tty_index: int
    termux_session: str | None = None
    start_time_ticks: int | None = None

    @property
    def tty_path(self) -> str:
        return f"/dev/pts/{self.tty_index}"


def validate_session_title(value: str) -> str:
    title = value.strip()
    if not title:
        raise SendError("Session title is empty")
    if len(title) > 80:
        raise SendError("Session title exceeds 80 characters")
    if any(ord(character) < 32 or ord(character) == 127 for character in title):
        raise SendError("Session title contains a control character")
    if (
        title.startswith("-")
        or title.casefold() == "other"
        or title.casefold().startswith("pid:")
        or re.fullmatch(r"(?:/dev/)?pts/\d+", title, re.IGNORECASE) is not None
    ):
        raise SendError(f"Session title {title!r} is reserved for routing")
    return title


def load_title_registry(path: Path = DEFAULT_TITLE_REGISTRY) -> dict[str, dict]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise SendError(f"Cannot read session-title registry {path}: {exc}") from exc
    if (
        not isinstance(data, dict)
        or data.get("schema_version") != 2
        or not isinstance(data.get("titles"), dict)
    ):
        raise SendError(f"Invalid session-title registry: {path}")
    titles = data["titles"]
    for key, record in titles.items():
        if (
            not isinstance(key, str)
            or not isinstance(record, dict)
            or not isinstance(record.get("title"), str)
            or key != record["title"].casefold()
            or type(record.get("pid")) is not int
            or type(record.get("tty_index")) is not int
            or record.get("native_name_verified") is not True
            or (
                record.get("termux_session") is not None
                and not isinstance(record.get("termux_session"), str)
            )
            or (
                record.get("start_time_ticks") is not None
                and type(record.get("start_time_ticks")) is not int
            )
        ):
            raise SendError(f"Invalid session-title registry record in {path}")
    return titles


def save_title_registry(
    titles: dict[str, dict], path: Path = DEFAULT_TITLE_REGISTRY
) -> None:
    temporary: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {"schema_version": 2, "titles": titles},
            indent=2,
            ensure_ascii=False,
        ) + "\n"
        temporary = path.with_name(
            f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
        )
        temporary.write_text(payload, encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except OSError as exc:
        raise SendError(f"Cannot write session-title registry {path}: {exc}") from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def _screen_nodes(screen: str) -> list[dict[str, str]]:
    nodes: list[dict[str, str]] = []
    window = ""
    for line in screen.splitlines():
        if line.startswith("--- window:"):
            window = line
            continue
        columns = line.split("\t")
        if len(columns) != 7 or not columns[0].startswith("node_"):
            continue
        nodes.append(
            {
                "node_id": columns[0],
                "class": columns[1],
                "text": columns[2],
                "description": columns[3],
                "resource_id": columns[4],
                "bounds": columns[5],
                "flags": columns[6],
                "window": window,
            }
        )
    return nodes


def active_termux_session_numbers() -> set[int]:
    numbers: set[int] = set()
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if _stdin_tty(pid) is None:
            continue
        raw_number = _read_environ(pid).get(TERMUX_SESSION_NUMBER_ENV)
        if raw_number is None:
            continue
        try:
            numbers.add(int(raw_number))
        except ValueError:
            continue
    return numbers


def termux_session_position(
    session: CodexSession,
    session_numbers: Sequence[int] | None = None,
) -> int:
    if session.termux_session is None:
        raise SendError("The current Termux session has no creation number")
    try:
        current_number = int(session.termux_session)
    except ValueError as exc:
        raise SendError(
            f"Invalid Termux session creation number: {session.termux_session!r}"
        ) from exc
    numbers = sorted(
        set(session_numbers)
        if session_numbers is not None
        else active_termux_session_numbers()
    )
    if current_number not in numbers:
        raise SendError(
            f"Current Termux session {current_number} is missing from the live drawer"
        )
    return numbers.index(current_number) + 1


def _screen_state(client: AgentBridgeMCP) -> str:
    return client.call("android_get_screen_state", {})


def _focused_application_package(screen: str) -> str | None:
    match = re.search(
        r"^--- window:.*\btype:APPLICATION\s+pkg:([^\s]+).*\bfocused:true\s+---$",
        screen,
        re.MULTILINE,
    )
    return match.group(1) if match else None


def _drawer_session_node(screen: str, position: int) -> dict[str, str] | None:
    prefix = f"[{position}] "
    for node in _screen_nodes(screen):
        if (
            node["resource_id"] == "com.termux:id/session_title"
            and node["text"].startswith(prefix)
        ):
            return node
    return None


def _dialog_node(
    screen: str,
    *,
    class_name: str | None = None,
    text: str | None = None,
) -> dict[str, str] | None:
    for node in _screen_nodes(screen):
        if "title:Set session name" not in node["window"]:
            continue
        if class_name is not None and node["class"] != class_name:
            continue
        if text is not None and node["text"] != text:
            continue
        return node
    return None


def _restore_termux_terminal(client: AgentBridgeMCP, screen: str) -> None:
    current = screen
    for _attempt in range(3):
        if (
            "title:Set session name" not in current
            and "type:INPUT_METHOD" not in current
            and "com.termux:id/left_drawer" not in current
        ):
            return
        client.call("android_press_back", {})
        current = _screen_state(client)


def rename_termux_app_session(
    session: CodexSession,
    title: str,
    *,
    client: AgentBridgeMCP | None = None,
    session_numbers: Sequence[int] | None = None,
) -> None:
    """Rename Termux's app-owned bold session label through its real UI."""

    position = termux_session_position(session, session_numbers)
    bridge = client or AgentBridgeMCP()
    screen = _screen_state(bridge)
    original_package = _focused_application_package(screen)
    if original_package != "com.termux":
        bridge.call("android_open_app", {"package_id": "com.termux"})
        bridge.call("android_wait_for_idle", {"timeout": 3000})
        screen = _screen_state(bridge)
        if _focused_application_package(screen) != "com.termux":
            raise SendError("Agent Bridge could not bring Termux to the foreground")

    final_screen = screen
    try:
        if "com.termux:id/left_drawer" not in screen:
            size_match = re.search(r"^screen:(\d+)x(\d+)", screen, re.MULTILINE)
            if size_match is None:
                raise SendError("Agent Bridge did not report the phone screen size")
            width, height = map(int, size_match.groups())
            bridge.call(
                "android_swipe",
                {
                    "x1": max(2, width // 100),
                    "y1": height * 2 // 5,
                    "x2": width * 4 // 5,
                    "y2": height * 2 // 5,
                    "duration": 350,
                },
            )
            bridge.call("android_wait_for_idle", {"timeout": 3000})
            screen = _screen_state(bridge)
            final_screen = screen

        for attempt in range(2):
            session_node = _drawer_session_node(screen, position)
            if session_node is None:
                raise SendError(
                    f"Termux drawer row [{position}] for {session.tty_path} was not found"
                )
            try:
                bridge.call(
                    "android_long_click_node", {"node_id": session_node["node_id"]}
                )
                break
            except SendError:
                if attempt == 1:
                    raise
                screen = _screen_state(bridge)
                final_screen = screen
        bridge.call(
            "android_wait_for_node",
            {"by": "text", "value": "Set session name", "timeout": 3000},
        )
        screen = _screen_state(bridge)
        final_screen = screen
        edit = _dialog_node(screen, class_name="EditText")
        if edit is None:
            raise SendError("Termux session-name field was not found")
        bridge.call("android_type_clear_text", {"node_id": edit["node_id"]})
        bridge.call(
            "android_type_append_text",
            {
                "node_id": edit["node_id"],
                "text": title,
                "typing_speed": 10,
                "typing_speed_variance": 0,
            },
        )
        screen = _screen_state(bridge)
        final_screen = screen
        confirm = _dialog_node(screen, text="SET")
        if confirm is None:
            raise SendError("Termux session-name SET button was not found")
        bridge.call("android_click_node", {"node_id": confirm["node_id"]})
        bridge.call("android_wait_for_idle", {"timeout": 3000})
        final_screen = _screen_state(bridge)
        renamed = _drawer_session_node(final_screen, position)
        expected = f"[{position}] {title}"
        if renamed is None or not (
            renamed["text"] == expected
            or renamed["text"].startswith(expected + " ")
        ):
            raise SendError(
                f"Termux did not display the new bold session name {title!r}"
            )
    finally:
        try:
            _restore_termux_terminal(bridge, final_screen)
        except SendError:
            pass
        if original_package and original_package != "com.termux":
            try:
                bridge.call("android_open_app", {"package_id": original_package})
            except SendError:
                pass


@contextmanager
def title_registry_lock(path: Path = DEFAULT_TITLE_REGISTRY):
    import fcntl

    lock_path = path.with_name(f".{path.name}.lock")
    handle = None
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = lock_path.open("a+", encoding="utf-8")
        os.chmod(lock_path, 0o600)
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    except OSError as exc:
        if handle is not None:
            handle.close()
        raise SendError(f"Cannot lock session-title registry {path}: {exc}") from exc
    try:
        yield
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def _record_is_live(
    record: dict, sessions: Sequence[CodexSession]
) -> CodexSession | None:
    matches = [
        session
        for session in sessions
        if session.pid == record.get("pid")
        and session.tty_index == record.get("tty_index")
        and session.termux_session == record.get("termux_session")
        and session.start_time_ticks == record.get("start_time_ticks")
    ]
    return matches[0] if len(matches) == 1 else None


def register_session_title(
    session: CodexSession,
    title: str,
    sessions: Sequence[CodexSession],
    *,
    registry_path: Path = DEFAULT_TITLE_REGISTRY,
    session_renamer=rename_termux_app_session,
) -> str:
    title = validate_session_title(title)
    with title_registry_lock(registry_path):
        key = title.casefold()
        titles = load_title_registry(registry_path)
        existing = titles.get(key)
        if existing is not None:
            owner = _record_is_live(existing, sessions)
            if owner is not None and owner != session:
                raise SendError(
                    f"Session title {title!r} already belongs to {owner.tty_path}"
                )
        titles = {
            name: record
            for name, record in titles.items()
            if not (
                record.get("pid") == session.pid
                and record.get("tty_index") == session.tty_index
            )
        }
        titles[key] = {
            "title": title,
            "pid": session.pid,
            "tty_index": session.tty_index,
            "termux_session": session.termux_session,
            "start_time_ticks": session.start_time_ticks,
            "native_name_verified": True,
            "registered_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        session_renamer(session, title)
        save_title_registry(titles, registry_path)
    return title


def session_title(
    session: CodexSession,
    *,
    registry_path: Path = DEFAULT_TITLE_REGISTRY,
) -> str | None:
    for record in load_title_registry(registry_path).values():
        if _record_is_live(record, [session]) is not None:
            title = record.get("title")
            return title if isinstance(title, str) else None
    return None


def resolve_session_title(
    title: str,
    sessions: Sequence[CodexSession],
    *,
    registry_path: Path = DEFAULT_TITLE_REGISTRY,
) -> CodexSession:
    title = validate_session_title(title)
    record = load_title_registry(registry_path).get(title.casefold())
    if record is None or record.get("title") != title:
        raise SendError(f"No session is titled {title!r}")
    session = _record_is_live(record, sessions)
    if session is None:
        raise SendError(
            f"Session title {title!r} is stale; name that live session again"
        )
    return session


def tty_index(value: str) -> int:
    match = re.fullmatch(r"(?:/dev/)?pts/(\d+)", value)
    if not match:
        raise SendError(f"Expected a target such as pts/2; got {value!r}")
    return int(match.group(1))


def _read_cmdline(pid: int) -> list[str]:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        return []
    return [part.decode("utf-8", "replace") for part in raw.split(b"\0") if part]


def _read_environ(pid: int) -> dict[str, str]:
    try:
        raw = Path(f"/proc/{pid}/environ").read_bytes()
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        return {}
    result: dict[str, str] = {}
    for item in raw.split(b"\0"):
        key, marker, value = item.partition(b"=")
        if marker:
            result[key.decode("utf-8", "replace")] = value.decode(
                "utf-8", "replace"
            )
    return result


def _process_start_time_ticks(pid: int) -> int | None:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        return None
    marker = stat.rfind(") ")
    if marker < 0:
        return None
    fields_after_command = stat[marker + 2 :].split()
    try:
        return int(fields_after_command[19])
    except (IndexError, ValueError):
        return None


def _stdin_tty(pid: int) -> int | None:
    try:
        target = os.readlink(f"/proc/{pid}/fd/0")
        return tty_index(target)
    except (FileNotFoundError, PermissionError, ProcessLookupError, SendError):
        return None


def codex_sessions() -> list[CodexSession]:
    sessions: list[CodexSession] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        command = _read_cmdline(pid)
        if not command or not command[0].endswith("/codex.bin"):
            continue
        index = _stdin_tty(pid)
        if index is None:
            continue
        env = _read_environ(pid)
        sessions.append(
            CodexSession(
                pid=pid,
                tty_index=index,
                termux_session=env.get(
                    "SHELL_CMD__APP_TERMINAL_SESSION_NUMBER_SINCE_APP_START"
                ),
                start_time_ticks=_process_start_time_ticks(pid),
            )
        )
    return sorted(sessions, key=lambda item: item.tty_index)


def _parent_pid(pid: int) -> int | None:
    try:
        text = Path(f"/proc/{pid}/status").read_text(encoding="utf-8")
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        return None
    match = re.search(r"^PPid:\s+(\d+)$", text, re.MULTILINE)
    return int(match.group(1)) if match else None


def current_ancestor_tty() -> int | None:
    pid = os.getpid()
    seen: set[int] = set()
    while pid > 1 and pid not in seen:
        seen.add(pid)
        index = _stdin_tty(pid)
        if index is not None:
            return index
        parent = _parent_pid(pid)
        if parent is None:
            break
        pid = parent
    return None


def select_session(
    selector: str,
    sessions: Sequence[CodexSession],
    *,
    registry_path: Path = DEFAULT_TITLE_REGISTRY,
) -> CodexSession:
    if selector == "other":
        current = current_ancestor_tty()
        if current is None:
            raise SendError("Could not identify the calling Codex terminal")
        matches = [item for item in sessions if item.tty_index != current]
    elif selector.startswith("pid:"):
        try:
            wanted_pid = int(selector.removeprefix("pid:"))
        except ValueError as exc:
            raise SendError(f"Invalid Codex PID selector: {selector!r}") from exc
        matches = [item for item in sessions if item.pid == wanted_pid]
    else:
        try:
            wanted_tty = tty_index(selector)
        except SendError:
            return resolve_session_title(
                selector, sessions, registry_path=registry_path
            )
        matches = [item for item in sessions if item.tty_index == wanted_tty]
    if len(matches) != 1:
        found = ", ".join(item.tty_path for item in matches) or "none"
        raise SendError(f"Target {selector!r} matched {len(matches)} sessions: {found}")
    return matches[0]


def reject_self_target(session: CodexSession, source_tty: int) -> None:
    if session.tty_index == source_tty:
        raise SendError(f"Refusing to send to this session ({session.tty_path})")


def termux_app_pid() -> int:
    inherited = os.environ.get("TERMUX_APP__PID")
    try:
        inherited_pid = int(inherited) if inherited else None
    except ValueError:
        inherited_pid = None
    if inherited_pid and _read_cmdline(inherited_pid)[:1] == ["com.termux"]:
        return inherited_pid
    for entry in Path("/proc").iterdir():
        if (
            entry.name.isdigit()
            and _read_cmdline(int(entry.name))[:1] == ["com.termux"]
        ):
            return int(entry.name)
    raise SendError("The com.termux app process is not running")


def master_fd_for_tty(app_pid: int, index: int) -> int:
    matches: list[int] = []
    fdinfo_dir = Path(f"/proc/{app_pid}/fdinfo")
    try:
        entries = list(fdinfo_dir.iterdir())
    except (FileNotFoundError, PermissionError, ProcessLookupError) as exc:
        raise SendError(f"Cannot inspect Termux process {app_pid}: {exc}") from exc
    for entry in entries:
        try:
            info = entry.read_text(encoding="utf-8")
            target = os.readlink(f"/proc/{app_pid}/fd/{entry.name}")
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        match = re.search(r"^tty-index:\s+(\d+)$", info, re.MULTILINE)
        if match and int(match.group(1)) == index and target in {
            "/dev/ptmx",
            "/dev/pts/ptmx",
        }:
            matches.append(int(entry.name))
    if len(matches) != 1:
        raise SendError(
            f"Expected one Termux PTY master for /dev/pts/{index}; found {len(matches)}"
        )
    return matches[0]


def _syscall_fd(number: int, *arguments: int) -> int:
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    result = libc.syscall(number, *arguments)
    if result < 0:
        error = ctypes.get_errno()
        raise SendError(f"{os.strerror(error)} (syscall {number}, errno {error})")
    return int(result)


def duplicate_remote_fd(pid: int, fd: int) -> int:
    pidfd = _syscall_fd(SYS_PIDFD_OPEN, pid, 0)
    try:
        return _syscall_fd(SYS_PIDFD_GETFD, pidfd, fd, 0)
    finally:
        os.close(pidfd)


def peer_tty_index(master_fd: int) -> int:
    import fcntl
    import struct

    packed = fcntl.ioctl(master_fd, TIOCGPTN, struct.pack("I", 0))
    return struct.unpack("I", packed)[0]


def validate_message(message: str) -> bytes:
    if not message:
        raise SendError("Message is empty")
    if any(ord(character) < 32 or ord(character) == 127 for character in message):
        raise SendError("Message cannot contain control characters or newlines")
    encoded = message.encode("utf-8")
    if len(encoded) > MAX_MESSAGE_BYTES:
        raise SendError(f"Message exceeds {MAX_MESSAGE_BYTES} bytes")
    return encoded


def verify_live_session(session: CodexSession) -> None:
    command = _read_cmdline(session.pid)
    if (
        not command
        or not command[0].endswith("/codex.bin")
        or _stdin_tty(session.pid) != session.tty_index
    ):
        raise SendError(
            f"Codex PID {session.pid} no longer owns {session.tty_path}"
        )


def rollout_paths(session: CodexSession) -> list[Path]:
    paths: list[Path] = []
    fd_root = Path(f"/proc/{session.pid}/fd")
    try:
        entries = list(fd_root.iterdir())
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        return paths
    for entry in entries:
        try:
            target = Path(os.readlink(entry))
            if target.suffix != ".jsonl" or "/.codex/sessions/" not in str(target):
                continue
            with target.open("rb") as stream:
                record = json.loads(stream.readline())
        except (FileNotFoundError, PermissionError, ProcessLookupError, OSError, ValueError):
            continue
        payload = record.get("payload", {})
        if payload.get("source") == "cli" and payload.get("originator") == "codex-tui":
            paths.append(target)
    return sorted(paths, key=lambda path: path.stat().st_mtime, reverse=True)


def latest_task_event(rollout: Path, chunk_size: int = 64 * 1024) -> str | None:
    """Return the newest task lifecycle event without loading the rollout."""
    try:
        size = rollout.stat().st_size
        with rollout.open("rb") as stream:
            position = size
            later_prefix = b""
            while position > 0:
                take = min(chunk_size, position)
                position -= take
                stream.seek(position)
                block = stream.read(take) + later_prefix
                matches = list(TASK_EVENT_RE.finditer(block))
                if matches:
                    return matches[-1].group(1).decode("ascii")
                later_prefix = block[:128]
    except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
        return None
    return None


def rollout_is_busy(rollout: Path) -> bool:
    event = latest_task_event(rollout)
    if event is None:
        raise SendError(f"Cannot prove whether target rollout is idle: {rollout}")
    return event == "task_started"


def wait_until_idle(
    rollout: Path,
    timeout: float = IDLE_TIMEOUT,
    *,
    poll_interval: float = 0.25,
    clock=time.monotonic,
    sleeper=time.sleep,
) -> None:
    deadline = clock() + timeout
    while rollout_is_busy(rollout):
        if clock() >= deadline:
            raise SendError(
                f"Target stayed busy for {timeout:g} seconds; message was not injected"
            )
        sleeper(poll_interval)


@contextmanager
def target_lock(
    session: CodexSession,
    timeout: float = IDLE_TIMEOUT,
    *,
    lock_dir: Path = DEFAULT_LOCK_DIR,
    poll_interval: float = 0.25,
):
    import fcntl

    lock_dir.mkdir(parents=True, exist_ok=True)
    path = lock_dir / f"pts-{session.tty_index}.lock"
    handle = path.open("a+", encoding="utf-8")
    os.chmod(path, 0o600)
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise SendError(
                        f"Target queue for {session.tty_path} stayed occupied for "
                        f"{timeout:g} seconds"
                    )
                time.sleep(poll_interval)
        verify_live_session(session)
        handle.seek(0)
        handle.truncate()
        handle.write(
            json.dumps(
                {
                    "owner_pid": os.getpid(),
                    "target_pid": session.pid,
                    "target_tty": session.tty_path,
                    "locked_utc": time.strftime(
                        "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                    ),
                }
            )
            + "\n"
        )
        handle.flush()
        yield path
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def _content_text(record: dict, role: str) -> str:
    if record.get("type") != "response_item":
        return ""
    payload = record.get("payload", {})
    if payload.get("type") != "message" or payload.get("role") != role:
        return ""
    wanted = "input_text" if role == "user" else "output_text"
    return "\n".join(
        part.get("text", "")
        for part in payload.get("content", [])
        if part.get("type") == wanted
    )


def wait_for_reply_turn(
    rollout: Path,
    baseline: int,
    request_text: str,
    timeout: float = REPLY_TIMEOUT,
) -> tuple[str, str]:
    deadline = time.monotonic() + timeout
    offset = baseline
    partial = b""
    saw_request = False
    pending_turn_id: str | None = None
    active_turn_id: str | None = None
    assistant_messages: list[str] = []
    while time.monotonic() < deadline:
        try:
            with rollout.open("rb") as stream:
                stream.seek(offset)
                chunk = partial + stream.read()
                offset = stream.tell()
        except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
            time.sleep(0.25)
            continue
        lines = chunk.split(b"\n")
        partial = lines.pop()
        for line in lines:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            payload = record.get("payload", {})
            if (
                record.get("type") == "event_msg"
                and payload.get("type") == "task_started"
            ):
                pending_turn_id = payload.get("turn_id")
                if saw_request and active_turn_id is None:
                    active_turn_id = pending_turn_id
                continue
            user_text = _content_text(record, "user")
            if request_text == user_text:
                saw_request = True
                active_turn_id = active_turn_id or pending_turn_id
                continue
            if saw_request:
                if user_text:
                    raise SendError(
                        f"Receiving turn {active_turn_id or 'unknown'} was "
                        "interfered with by another user message"
                    )
                assistant = _content_text(record, "assistant")
                if assistant:
                    assistant_messages.append(assistant)
                if (
                    record.get("type") == "event_msg"
                    and payload.get("type") == "task_complete"
                    and active_turn_id is not None
                    and payload.get("turn_id") == active_turn_id
                ):
                    answer = payload.get("last_agent_message")
                    if answer:
                        return answer, active_turn_id
                    if assistant_messages:
                        return assistant_messages[-1], active_turn_id
                    raise SendError("The receiving Codex completed without an answer")
                if (
                    record.get("type") == "event_msg"
                    and payload.get("type") == "turn_aborted"
                    and active_turn_id is not None
                    and payload.get("turn_id") == active_turn_id
                ):
                    raise SendError(
                        f"Receiving Codex turn {active_turn_id} was aborted"
                    )
        time.sleep(0.25)
    identity = active_turn_id or "unknown"
    raise SendError(
        f"No reply for receiving turn {identity} arrived within {timeout:g} seconds"
    )


def wait_for_reply(
    rollout: Path,
    baseline: int,
    request_text: str,
    timeout: float = REPLY_TIMEOUT,
) -> str:
    reply, _turn_id = wait_for_reply_turn(
        rollout,
        baseline,
        request_text,
        timeout,
    )
    return reply


def message_envelope(
    message: str,
    source_tty: int,
    *,
    task_id: str | None = None,
    phase: str = "send",
) -> str:
    validate_message(message)
    marker = f"[POSTOFFICE id={task_id} phase={phase}] " if task_id else ""
    return (
        f"{marker}{message}\n"
        f"— Sent from Codex conversation pts/{source_tty}"
    )


def new_task_id(prefix: str = "PO") -> str:
    if not re.fullmatch(r"[A-Z][A-Z0-9]*", prefix):
        raise SendError(f"Unsafe task ID prefix: {prefix!r}")
    return (
        f"{prefix}_{time.strftime('%Y%m%dT%H%M%S_')}"
        f"{secrets.token_hex(8).upper()}"
    )


def json_prompt_text(value: str, label: str) -> str:
    if not value.strip():
        raise SendError(f"{label} is empty")
    return json.dumps(value, ensure_ascii=False)


def independent_crosscheck_prompt(task_id: str, objective: str) -> str:
    encoded_objective = json_prompt_text(objective, "Objective")
    prompt = (
        f"CROSS-CHECK {task_id}, ROUND 1 OF 2. Act as the independent Codex "
        "counterpart. Read only the original objective below; the primary "
        "Codex answer is deliberately hidden. Solve it independently. Do not "
        "execute external actions. State your answer, evidence, assumptions, "
        "and what would falsify it. Preserve any uncertainty as a concrete "
        "missing fact. Decode the following JSON string exactly; escaped "
        f"newlines and spacing are meaningful. ORIGINAL OBJECTIVE JSON: {encoded_objective}"
    )
    validate_message(prompt)
    return prompt


def challenge_crosscheck_prompt(
    task_id: str,
    objective: str,
    primary_answer: str,
) -> str:
    encoded_objective = json_prompt_text(objective, "Objective")
    encoded_primary = json_prompt_text(primary_answer, "Primary answer")
    prompt = (
        f"CROSS-CHECK {task_id}, ROUND 2 OF 2, FINAL ROUND. Compare your prior "
        "independent answer with the primary Codex answer below. Challenge only "
        "material differences. Return these labeled sections: VERDICT, AGREED "
        "FACTS, UNRESOLVED DISAGREEMENTS, EVIDENCE NEEDED, RECOMMENDED FINAL "
        "ACTION. Do not erase disagreement to manufacture consensus. Do not "
        "execute external actions. Decode both JSON strings exactly; escaped "
        "newlines and spacing are meaningful. ORIGINAL OBJECTIVE JSON: "
        f"{encoded_objective} PRIMARY CODEX ANSWER JSON: {encoded_primary}"
    )
    validate_message(prompt)
    return prompt


CROSSCHECK_SECTION_NAMES = (
    "VERDICT",
    "AGREED FACTS",
    "UNRESOLVED DISAGREEMENTS",
    "EVIDENCE NEEDED",
    "RECOMMENDED FINAL ACTION",
)


def parse_crosscheck_sections(reply: str) -> dict[str, str]:
    headings: list[tuple[int, int, str]] = []
    for name in CROSSCHECK_SECTION_NAMES:
        pattern = re.compile(
            rf"(?im)^\s*(?:#{{1,6}}\s*)?(?:\*\*)?{re.escape(name)}"
            rf"(?:\*\*)?\s*:?\s*$"
        )
        match = pattern.search(reply)
        if match:
            headings.append((match.start(), match.end(), name))
    found = {item[2] for item in headings}
    missing = [name for name in CROSSCHECK_SECTION_NAMES if name not in found]
    if missing:
        raise SendError(
            "Final challenge omitted required sections: " + ", ".join(missing)
        )
    headings.sort()
    sections: dict[str, str] = {}
    for index, (_start, end, name) in enumerate(headings):
        next_start = headings[index + 1][0] if index + 1 < len(headings) else len(reply)
        sections[name] = reply[end:next_start].strip()
    empty = [name for name, value in sections.items() if not value]
    if empty:
        raise SendError(
            "Final challenge left required sections empty: " + ", ".join(empty)
        )
    return sections


def _write_all(fd: int, payload: bytes, writer=os.write) -> int:
    sent = 0
    while sent < len(payload):
        sent += writer(fd, payload[sent:])
    return sent


def transmit_prompt(
    master: int,
    envelope: str,
    *,
    visible: bool,
    char_delay: float = VISIBLE_CHAR_DELAY,
    writer=os.write,
    sleeper=time.sleep,
) -> int:
    sent = 0
    if visible:
        for character in envelope:
            payload = b"\n" if character == "\n" else character.encode("utf-8")
            sent += _write_all(master, payload, writer)
            sleeper(char_delay)
    else:
        sent += _write_all(master, envelope.encode("utf-8"), writer)
    # Codex buffers rapid key bursts as paste. Submit must arrive after that
    # window closes or Enter is absorbed into the paste instead of submitted.
    sleeper(SUBMIT_DELAY)
    sent += _write_all(master, b"\r", writer)
    return sent


def render_returned_reply(
    session: CodexSession,
    reply: str,
    *,
    task_id: str,
    turn_id: str,
    phase: str = "reply",
    visible: bool = True,
    char_delay: float = REPLY_CHAR_DELAY,
    stream=None,
    sleeper=time.sleep,
) -> None:
    """Show a captured peer answer as a visible typed-back receipt."""
    stream = stream or sys.stdout
    peer = session.tty_path.removeprefix("/dev/")
    stream.write(f"\n{peer} is typing back ({phase})...\n")
    stream.flush()
    if visible:
        for character in reply:
            stream.write(character)
            stream.flush()
            sleeper(char_delay)
    else:
        stream.write(reply)
    if not reply.endswith("\n"):
        stream.write("\n")
    stream.write(
        f"[received: task={task_id} turn={turn_id} from={peer} phase={phase}]\n"
    )
    stream.flush()


def send_message(
    session: CodexSession,
    message: str,
    *,
    visible: bool = False,
    source_tty: int,
    task_id: str | None = None,
    phase: str = "send",
) -> int:
    envelope = message_envelope(
        message,
        source_tty,
        task_id=task_id,
        phase=phase,
    )
    verify_live_session(session)
    app_pid = termux_app_pid()
    remote_master = master_fd_for_tty(app_pid, session.tty_index)
    master = duplicate_remote_fd(app_pid, remote_master)
    try:
        actual = peer_tty_index(master)
        if actual != session.tty_index:
            raise SendError(
                f"PTY changed before send: expected {session.tty_index}, got {actual}"
            )
        verify_live_session(session)
        return transmit_prompt(master, envelope, visible=visible)
    finally:
        os.close(master)


def exchange_with_session(
    session: CodexSession,
    message: str,
    *,
    source_tty: int,
    task_id: str,
    phase: str,
    visible: bool,
    reply_timeout: float,
    idle_timeout: float,
) -> tuple[str, str, Path, int]:
    rollouts = rollout_paths(session)
    if not rollouts:
        raise SendError(f"No live Codex rollout found for PID {session.pid}")
    rollout = rollouts[0]
    wait_until_idle(rollout, idle_timeout)
    time.sleep(POST_IDLE_SETTLE)
    if rollout_is_busy(rollout):
        wait_until_idle(rollout, idle_timeout)
    current_rollouts = rollout_paths(session)
    if not current_rollouts or current_rollouts[0] != rollout:
        raise SendError("Target rollout changed while the message was queued")
    baseline = rollout.stat().st_size
    envelope = message_envelope(
        message,
        source_tty,
        task_id=task_id,
        phase=phase,
    )
    sent = send_message(
        session,
        message,
        visible=visible,
        source_tty=source_tty,
        task_id=task_id,
        phase=phase,
    )
    reply, turn_id = wait_for_reply_turn(
        rollout,
        baseline,
        envelope,
        timeout=reply_timeout,
    )
    return reply, turn_id, rollout, sent


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def write_crosscheck_record(
    directory: Path,
    record: dict,
    *,
    create: bool = False,
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    task_id = str(record["task_id"])
    if not re.fullmatch(r"XCHK_[A-Z0-9_]+", task_id):
        raise SendError(f"Unsafe task ID: {task_id!r}")
    destination = directory / f"{task_id}.json"
    payload = json.dumps(record, ensure_ascii=False, indent=2) + "\n"
    if create:
        try:
            descriptor = os.open(
                destination,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError as exc:
            raise SendError(f"Cross-check record already exists: {destination}") from exc
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        return destination
    temporary = destination.with_name(
        f".{destination.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
    )
    temporary.write_text(
        payload,
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    os.replace(temporary, destination)
    return destination


def run_crosscheck(
    session: CodexSession,
    objective: str,
    primary_answer: str,
    *,
    source_tty: int,
    visible: bool = False,
    reply_timeout: float = REPLY_TIMEOUT,
    idle_timeout: float = IDLE_TIMEOUT,
    journal_dir: Path = DEFAULT_CROSSCHECK_DIR,
    lock_dir: Path = DEFAULT_LOCK_DIR,
    task_id: str | None = None,
) -> tuple[dict, Path]:
    task_id = task_id or new_task_id("XCHK")
    raw_objective = objective
    raw_primary_answer = primary_answer
    # Build and size-check both rounds before the first message leaves.
    independent_prompt = independent_crosscheck_prompt(task_id, raw_objective)
    challenge_prompt = challenge_crosscheck_prompt(
        task_id,
        raw_objective,
        raw_primary_answer,
    )
    record: dict = {
        "schema_version": 1,
        "task_id": task_id,
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "status": "queued",
        "source_tty": f"pts/{source_tty}",
        "target_tty": session.tty_path.removeprefix("/dev/"),
        "target_pid": session.pid,
        "target_termux_session": session.termux_session,
        "objective": raw_objective,
        "objective_sha256": _sha256(raw_objective),
        "primary_answer_withheld_until_round": 2,
        "rounds": [],
    }
    path = write_crosscheck_record(journal_dir, record, create=True)
    failed_phase = "queue"
    try:
        with target_lock(
            session,
            idle_timeout,
            lock_dir=lock_dir,
        ) as lock_path:
            record["lock"] = str(lock_path)
            record["status"] = "waiting_for_independent_answer"
            write_crosscheck_record(journal_dir, record)

            failed_phase = "independent"
            independent_started = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
            )
            independent_reply, turn_id, rollout, sent = exchange_with_session(
                session,
                independent_prompt,
                source_tty=source_tty,
                task_id=task_id,
                phase="independent",
                visible=visible,
                reply_timeout=reply_timeout,
                idle_timeout=idle_timeout,
            )
            record["rollout"] = str(rollout)
            record["rounds"].append(
                {
                    "round": 1,
                    "phase": "independent",
                    "role": "independent_answer",
                    "prompt": independent_prompt,
                    "turn_id": turn_id,
                    "started_utc": independent_started,
                    "completed_utc": time.strftime(
                        "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                    ),
                    "reply": independent_reply,
                    "reply_sha256": _sha256(independent_reply),
                    "bytes_sent": sent,
                }
            )
            # The journal reveals the primary only after the independent turn ends.
            record["primary_answer"] = raw_primary_answer
            record["primary_answer_sha256"] = _sha256(raw_primary_answer)
            record["status"] = "waiting_for_final_challenge"
            write_crosscheck_record(journal_dir, record)

            failed_phase = "challenge"
            challenge_started = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
            )
            challenge_reply, turn_id, rollout, sent = exchange_with_session(
                session,
                challenge_prompt,
                source_tty=source_tty,
                task_id=task_id,
                phase="challenge",
                visible=visible,
                reply_timeout=reply_timeout,
                idle_timeout=idle_timeout,
            )
            parsed_sections = parse_crosscheck_sections(challenge_reply)
            record["rollout"] = str(rollout)
            record["rounds"].append(
                {
                    "round": 2,
                    "phase": "challenge",
                    "role": "bounded_challenge",
                    "prompt": challenge_prompt,
                    "turn_id": turn_id,
                    "started_utc": challenge_started,
                    "completed_utc": time.strftime(
                        "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                    ),
                    "reply": challenge_reply,
                    "reply_sha256": _sha256(challenge_reply),
                    "sections": parsed_sections,
                    "bytes_sent": sent,
                }
            )
            record["unresolved_disagreements"] = parsed_sections[
                "UNRESOLVED DISAGREEMENTS"
            ]
        record["status"] = "complete"
        record["completed_utc"] = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
        )
        path = write_crosscheck_record(journal_dir, record)
        return record, path
    except Exception as exc:
        record["status"] = "failed"
        record["failed_phase"] = failed_phase
        record["error_type"] = type(exc).__name__
        record["error"] = str(exc)
        write_crosscheck_record(journal_dir, record)
        raise


def cross_process_self_test() -> str:
    import pty
    import select
    import tty

    master, slave = pty.openpty()
    tty.setraw(slave)
    stop_read, stop_write = os.pipe()
    child = os.fork()
    if child == 0:
        os.close(slave)
        os.close(stop_write)
        os.read(stop_read, 1)
        os._exit(0)

    os.close(master)
    os.close(stop_read)
    duplicate = -1
    try:
        duplicate = duplicate_remote_fd(child, master)
        token = b"POST_OFFICE_PTY_SELF_TEST"
        os.write(duplicate, token)
        readable, _, _ = select.select([slave], [], [], 2)
        received = os.read(slave, len(token)) if readable else b""
        if received != token:
            raise SendError(f"Self-test delivery mismatch: {received!r}")
        return token.decode("ascii")
    finally:
        if duplicate >= 0:
            os.close(duplicate)
        os.close(slave)
        os.write(stop_write, b"x")
        os.close(stop_write)
        os.waitpid(child, 0)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Type into another live raw Termux Codex session"
    )
    result.add_argument("--list", action="store_true", help="list raw Codex sessions")
    result.add_argument(
        "--set-title",
        metavar="TITLE",
        help="name the calling raw Termux Codex session",
    )
    result.add_argument(
        "--self-test", action="store_true", help="test delivery on a disposable PTY"
    )
    visibility = result.add_mutually_exclusive_group()
    visibility.add_argument(
        "--visible",
        dest="visible",
        action="store_true",
        help="type at readable speed (the default)",
    )
    visibility.add_argument(
        "--instant",
        dest="visible",
        action="store_false",
        help="inject the whole prompt at once instead of visibly typing it",
    )
    result.add_argument(
        "--no-wait", action="store_true", help="submit without printing the reply"
    )
    result.add_argument(
        "--wait-idle",
        dest="wait_idle",
        action="store_true",
        help="wait for the target turn to finish before sending",
    )
    result.add_argument(
        "--steer-now",
        dest="wait_idle",
        action="store_false",
        help="explicitly inject into the current active target turn",
    )
    result.set_defaults(wait_idle=True, visible=True)
    result.add_argument(
        "--cross-check",
        action="store_true",
        help="run a blind answer and one final challenge round",
    )
    result.add_argument(
        "--primary-file",
        type=Path,
        help="UTF-8 file containing the primary Codex answer",
    )
    result.add_argument(
        "--journal-dir",
        type=Path,
        default=DEFAULT_CROSSCHECK_DIR,
        help=argparse.SUPPRESS,
    )
    result.add_argument(
        "--idle-timeout",
        type=float,
        default=IDLE_TIMEOUT,
        help="seconds to wait for a busy target",
    )
    result.add_argument(
        "--reply-timeout",
        type=float,
        default=REPLY_TIMEOUT,
        help="seconds to wait for each reply",
    )
    result.add_argument(
        "target",
        nargs="?",
        help="exact session title, other, pts/N, /dev/pts/N, or pid:PID",
    )
    result.add_argument("message", nargs="?", help="one submitted Codex message")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.self_test:
            token = cross_process_self_test()
            print(f"PTY delivery passed: {token}")
            return 0
        sessions = codex_sessions()
        if args.set_title is not None:
            current = current_ancestor_tty()
            if current is None:
                raise SendError("Could not identify the calling Codex terminal")
            matches = [session for session in sessions if session.tty_index == current]
            if len(matches) != 1:
                raise SendError(
                    f"Calling terminal /dev/pts/{current} matched "
                    f"{len(matches)} live Codex sessions"
                )
            title = register_session_title(matches[0], args.set_title, sessions)
            print(
                f"session-name={title!r} verified=true tty={matches[0].tty_path}"
            )
            return 0
        if args.list:
            current = current_ancestor_tty()
            if not sessions:
                print("No raw Termux Codex sessions found.")
            for session in sessions:
                marker = "*" if session.tty_index == current else " "
                termux_number = session.termux_session or "?"
                title = session_title(session)
                title_text = repr(title) if title is not None else "unassigned"
                print(
                    f"{marker} {session.tty_path}  codex-pid={session.pid}  "
                    f"termux-session={termux_number}  title={title_text}"
                )
            return 0
        if not args.target or args.message is None:
            raise SendError(
                "Usage: po send <title|pts/N|pid:PID> <message> or "
                "po send-other <message>"
            )
        if args.idle_timeout <= 0 or args.reply_timeout <= 0:
            raise SendError("Timeouts must be greater than zero")
        session = select_session(args.target, sessions)
        source = current_ancestor_tty()
        if source is None:
            raise SendError("Could not identify the sending Codex terminal")
        reject_self_target(session, source)
        if not args.wait_idle and not args.no_wait:
            raise SendError("--steer-now requires --no-wait")
        destination_title = session_title(session)
        destination = (
            repr(destination_title)
            if destination_title is not None
            else session.tty_path
        )
        print(
            f"destination={destination} tty={session.tty_path}",
            f"codex-pid={session.pid}",
            flush=True,
        )
        if args.cross_check:
            if args.no_wait:
                raise SendError("Cross-check cannot be combined with --no-wait")
            if args.primary_file is None:
                raise SendError("Cross-check requires --primary-file PATH")
            try:
                primary_answer = args.primary_file.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                raise SendError(
                    f"Cannot read primary answer {args.primary_file}: {exc}"
                ) from exc
            task_id = new_task_id("XCHK")
            print(f"task={task_id}", flush=True)
            record, path = run_crosscheck(
                session,
                args.message,
                primary_answer,
                source_tty=source,
                visible=args.visible,
                reply_timeout=args.reply_timeout,
                idle_timeout=args.idle_timeout,
                journal_dir=args.journal_dir,
                task_id=task_id,
            )
            independent = record["rounds"][0]["reply"]
            challenge = record["rounds"][1]["reply"]
            render_returned_reply(
                session,
                independent,
                task_id=task_id,
                turn_id=record["rounds"][0]["turn_id"],
                phase="independent answer",
                visible=args.visible,
            )
            render_returned_reply(
                session,
                challenge,
                task_id=task_id,
                turn_id=record["rounds"][1]["turn_id"],
                phase="final challenge",
                visible=args.visible,
            )
            print(f"cross-check complete: {record['task_id']}")
            print(f"record: {path}")
            return 0
        task_id = new_task_id("PO")
        print(f"task={task_id}", flush=True)
        envelope = message_envelope(
            args.message,
            source,
            task_id=task_id,
            phase="send",
        )
        with target_lock(session, args.idle_timeout):
            rollouts = rollout_paths(session)
            if not rollouts:
                raise SendError(f"No live Codex rollout found for PID {session.pid}")
            rollout = rollouts[0]
            if args.wait_idle:
                wait_until_idle(rollout, args.idle_timeout)
                time.sleep(POST_IDLE_SETTLE)
                if rollout_is_busy(rollout):
                    wait_until_idle(rollout, args.idle_timeout)
                current_rollouts = rollout_paths(session)
                if not current_rollouts or current_rollouts[0] != rollout:
                    raise SendError("Target rollout changed while the message was queued")
            baseline = rollout.stat().st_size
            sent = send_message(
                session,
                args.message,
                visible=args.visible,
                source_tty=source,
                task_id=task_id,
                phase="send",
            )
            action = "typed" if args.visible else "sent"
            print(
                f"{action} -> {session.tty_path}  codex-pid={session.pid}  "
                f"bytes={sent}"
            )
            if not args.no_wait:
                reply, turn_id = wait_for_reply_turn(
                    rollout,
                    baseline,
                    envelope,
                    timeout=args.reply_timeout,
                )
                render_returned_reply(
                    session,
                    reply,
                    task_id=task_id,
                    turn_id=turn_id,
                    visible=args.visible,
                )
        return 0
    except SendError as exc:
        command = "po title" if args.set_title is not None else "po send"
        print(f"{command} stopped: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
