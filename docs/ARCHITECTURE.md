# Architecture

Collaboration Inception keeps one operator in front of several persistent model
sessions. The terminal and Chrome side panel are two controls for the same
cockpit.

```text
Terminal input ───────────────┐
                              ├─ InputThread ─ Broker ─ Claude stream
Chrome side panel ─ LiveBridge┘                ├────── Codex app-server
                                               └────── Antigravity CLI

Browser/screen/file evidence ─ SurfaceAdapters ───────┘
Relationship evidence ─────── RelationshipLedger ────┘
Isolated builds ────────────── ProofArena ────────────┘
```

## Control plane

`scripts/live_bridge.py` is a loopback-only HTTP control plane. It owns a
bounded event queue, command injection, turn state, control ownership, and
blocking approval requests. The terminal receives side-panel commands through
the same logical input queue as typed commands.

The Chrome Manifest V3 extension long-polls events. It can dictate a prompt,
steer active work, stop a turn, choose a browser element, hand control back,
and answer an approval request. Browser activity marks the human as the current
control owner.

## Provider boundaries

- **Codex** uses `codex app-server --stdio`. Discussion turns are read-only.
  Working turns use a workspace-write sandbox and native command, file-change,
  and permission approval requests.
- **Claude** uses its streaming JSON protocol and per-turn tool callback.
  Discussion turns can read only explicitly attached evidence. Working turns
  can use project tools, with consequential actions routed to approval.
- **Antigravity** uses the native `agy` command or the compatible `gemini`
  fallback. Its CLI lacks same-turn steering, so Inception interrupts and
  resumes the conversation with the operator's guidance.

Each provider owns its native session. Inception stores only the session IDs
needed to reconnect.

## Evidence and continuity

`SurfaceAdapters` makes one private copy of each shared artifact so providers
receive the same pixels or file. Browser pointing records both an annotated
screenshot and DOM metadata.

`RelationshipLedger` stores explicit corrections, provisional observations,
promises, outcomes, confidence calibration, authority by task category,
missions, and task-matched evidence episodes. An episode preserves its source
exchange and counterevidence. Retrieval is bounded; the current request always
outranks old evidence.

At launch, Inception imports the latest local post-office message export
idempotently and connects George's canonical memory index read-only when it is
available. The index is searched first; only task-matched linked files are
opened and only bounded excerpts enter model context. Direct operator input is
learned once. Generated debate, critic, forwarding, and consensus prompts are
recorded as internal traffic and cannot become operator corrections.

## Build isolation

`ProofArena` creates detached Git worktrees from the same starting commit. Each
selected provider gets an isolated working turn and the same test command. The
arena records diffs, claims, test results, and a recommendation. Only the
operator can choose and integrate a result. Undo creates a revert commit.
