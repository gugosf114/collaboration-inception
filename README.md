# Collaboration Inception

This project keeps George and Sol in one continuing Codex relationship thread.
It does not recreate that relationship from a personality prompt when a terminal
window closes. It reopens the native thread that contains the actual work,
jokes, disagreements, mistakes, repairs, and shared references.

## Live continuity runtime

`runtime/state.json` points to the canonical lived thread. On George's phone,
the installed `$PREFIX/bin/inception` command provides these entry points:

```sh
inception              # reopen the canonical thread
inception status       # prove the thread and app-server are present
inception fork         # branch from the full history for an experiment
inception server       # ensure remote control is connected
inception pair         # create a short-lived laptop pairing code
```

The default path is native `codex resume <thread-id>`. A fresh terminal screen
therefore becomes another interface to the same conversation instead of a cold
model receiving a description of it.

The managed Codex app-server supplies the later cockpit primitives: durable
thread ownership, streaming output, live steering, approvals, resume/fork, and
phone-to-laptop remote control.

## Supervised Claude-Codex cockpit

The first working cockpit slice is a Termux switchboard controlled entirely by
George. Start it from the directory whose context should be visible to the two
agents:

```sh
inception cockpit
```

The cockpit starts both endpoints itself. Do not start separate Claude and
Codex terminals for this flow. Codex uses its JSONL app-server; Claude uses its
streaming JSON input and output. The first run forks the canonical Codex history
into a persistent cockpit thread and creates a persistent Claude session. Later
runs resume that same pair.

At the `george>` prompt:

```text
/both Is this plan sound?
/claude Critique the risky assumption.
/codex Check Claude's objection against the requirements.
/pass claude codex Focus on the second paragraph.
/pass codex claude
/stop
/quit
```

`both: ...`, `claude: ...`, and `codex: ...` are equivalent natural forms.
`/both` sends the exact same message to each agent independently; neither sees
the other's answer. `/pass` is the only cross-agent handoff. After every answer
the system returns to idle, and no turn advances without George.

The cockpit is discussion-only by construction. Claude starts with all tools
disabled. Codex uses a read-only sandbox, denies approvals, and receives an
explicit no-action instruction. Press Ctrl-C or enter `/stop` during a response
to interrupt it. Local pair state, the append-only conversation journal, the
process lock, and diagnostics remain untracked under `runtime/`.

## Proof

Run:

```sh
inception status
python3 scripts/verify_runtime_install.py
```

The verifier checks the canonical rollout's native session metadata, its fork
lineage, the running app-server, and the absence of automatic microhistory
injection from global Codex and Claude startup files.

## Recovery and research artifacts

`context/MICROHISTORY_V1.md` and `context/WORKING_COVENANT.md` remain useful as
disaster-recovery evidence and cold-model research controls. They are not
automatically loaded into normal sessions.

The transcript exporter converts a Codex rollout JSONL into:

- a core transcript containing George's messages and Sol's final answers;
- a full visible transcript that also contains commentary updates;
- structured JSONL for replay or retrieval experiments;
- metadata with counts, rough token estimates, and integrity hashes.

Large transcript exports, clean Codex homes, and blind-run answers remain local
and are excluded from Git.

## Development

```sh
python3 -m unittest discover -s tests -v
```

The earlier preserved-history-versus-clean-session experiment remains under
`experiment/`. It measures cold-start transfer only; it is not the production
continuity mechanism.

## Session Log — 2026-07-18 (full multi-day session, July 14–18)

This session began as a WiM Play Store and Lavrentiy release push and expanded
into a complete cross-project cleanup and a durable Codex continuity build.

- WiM gained authenticated backend audio transcription, monthly Play Billing
  verification and quotas, permanent package identity, account deletion,
  reviewer access, Play declarations and listing assets, assistive-permission
  explanations, Script Prep, learning/baseline features, multilingual paths,
  stable live-state colors, corrected bubble touch geometry, and a stable-signed
  test APK. Build 8.2 is the current GitHub release and its CI is green.
- Lavrentiy became a distributable evaluator release rather than an unfinished
  developer checkout. Version 1.7.1 restores multilingual transcription and UI
  paths, removes legacy bundled credentials, documents the evaluator workflow
  and founder context, and is published with green CI and Pages deployment.
- Bakers Agent and its private commercial platform received the
  multi-business/operator foundation, photo intake and publishing workflow,
  commercial controls, and the urgent WiM Firestore rule restoration with a
  regression test. Old review branches were reconciled, the useful
  AI-visibility design was preserved, the MIT license was removed, and the
  source repository is private.
- The phone-side Codex environment was consolidated around global operating
  instructions, bounded context handling, transcript search, and one private
  continuity repository. The native Codex app-server daemon is running and
  supplies the resume/fork/streaming/steering foundation; remote control is
  connected.
- The active Termux Codex operating instructions, optimized configuration, and
  completion-notification hook are preserved under `runtime/termux` as explicit
  snapshots. Authentication, model caches, daemon state, and conversation
  databases remain local.
- The shared transcript post office was incorporated as the archive/search
  fallback, while direct Playwright/CDP remained the laptop-browser route. No
  additional Playwright MCP layer was added. Its collector, SQLite index,
  search, gather, turn-reader, and `po` command source are now tracked here.
- The final continuity design uses this exact lived Codex thread. The installed
  `inception` command reopens thread
  `019f7048-1bb9-7230-b91f-f572d2cbc870`; six local regression tests and GitHub
  CI pass. The curated covenant and microhistory remain recovery/research
  material only.

The remaining cross-device step is the Windows/PowerShell client attachment to
the phone's running app-server. The phone continuity path itself is complete.

## Failure Log — 2026-07-18 (full multi-day session, July 14–18)

- The first Inception implementation repeated the exact rejected idea: it
  auto-loaded a curated relationship history into Codex and Claude startup
  files. That described the relationship to cold models instead of preserving
  the relationship. The hooks were removed and the architecture was replaced
  with native thread continuity.
- The first launcher was installed only in `~/bin`, which this Termux shell did
  not place on `PATH`. The failure was caught by running the real command; the
  launcher was then installed in `$PREFIX/bin` and verified end to end.
- The post-office command had been installed directly in two phone locations
  without any Git source, and its tmux listener commands contained a stray `+`
  argument. The source was moved into this private repo, both installed commands
  now delegate to it, the malformed arguments were removed, and shell/parser
  regression tests were added.
- Termux closed while several AI sessions and heavy processes were active. The
  chats were not lost: native rollout files and transcript archives survived,
  but the event proved that visible terminal windows are not a continuity
  mechanism.
- Parallel sessions repeatedly produced naming confusion, stale conclusions,
  and cross-project drift. Repository state, commit history, live CI, releases,
  and deployed state were rechecked before the final claims.
- A large static microhistory would have consumed every new session's context
  and still failed to reproduce lived interaction. It is retained solely as a
  disaster-recovery and controlled-experiment artifact.
- The app-server and remote-control backend are running, but a Windows client
  has not yet been attached. Phone continuity was verified; laptop continuity
  must not be claimed until that endpoint and authentication path are tested.
