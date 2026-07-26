# Collaboration Inception

This project keeps George and Sol in one continuing Codex relationship thread.
It reopens the native thread that contains the actual work, jokes,
disagreements, mistakes, repairs, and shared references. A compact covenant and
chronological microhistory provide recovery calibration underneath that lived
thread; they are not presented as memories the receiving model personally had.

## One-person install

The person needs working, signed-in Codex and Claude Code command-line tools.
On Termux, Claude Code must be available inside the Debian PRoot.

```sh
git clone https://github.com/gugosf114/collaboration-inception.git
cd collaboration-inception
chmod +x install.sh
./install.sh
inception cockpit
```

Then type:

```text
/both Say hello and explain what you can do.
```

George's phone keeps its native Codex lineage. A downloaded copy that does not
have George's private rollout starts its own persistent Codex and Claude pair.
The installer preserves any unrelated command it would otherwise replace.

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

The cockpit is a Termux switchboard controlled entirely by George, with both
comparison and real working turns. From any ordinary Termux prompt, type:

```sh
inception cockpit
```

No `cd`, folder path, or separate Claude/Codex terminal is required. A bare
launch resumes the last project. To switch projects, put its plain name after
the command; spaces and hyphens are treated the same:

```sh
inception cockpit agent bridge
inception cockpit collaboration inception
```

The startup screen names the selected project and prints `CONNECTED` separately
for Claude and Codex. It does not show the `george>` prompt until both startup
handshakes succeed. Inside the cockpit, `/status` repeats this information and
`/projects` lists the project names available on the phone.

The cockpit starts both endpoints itself. Codex uses its JSONL app-server;
Claude uses its streaming JSON input and output. The first run forks the
canonical Codex history into a persistent cockpit thread and creates a
persistent Claude session. Later runs resume that same pair.

At the `george>` prompt:

```text
/both Is this plan sound?
/claude Critique the risky assumption.
/codex Check Claude's objection against the requirements.
/act codex Implement your recommendation, test it, commit it, and push it.
/pass claude codex Focus on the second paragraph.
/pass codex claude
/context
/stop
/quit
```

`both: ...`, `claude: ...`, and `codex: ...` are equivalent natural forms;
`claude!: ...` and `codex!: ...` are action shorthand. `/both` sends the exact
same message to each agent independently in a hard read-only turn; neither sees
the other's answer. `/pass` is the only cross-agent handoff and is also
read-only. A direct `/claude` or `/codex` turn has real working tools and may
inspect, edit, test, commit, and push when George's message requests it.
`/act AGENT TEXT` makes that execution instruction unmistakable. Only one agent
can receive a working turn at a time, every grant ends with the turn, and no
turn advances without George.

Claude stays in the same live process and session. Its programmatic permission
callback denies mutating tools during comparison turns and allows them during a
single-agent working turn. Codex explicitly resets its sandbox on every turn:
read-only for `/both` and `/pass`, full working access for direct or `/act`
turns. Press Ctrl-C or enter `/stop` during a response to interrupt it. Local
pair state, the append-only conversation journal, the process lock, and
diagnostics remain untracked under `runtime/`.

### Minimum continuity layer

Every cockpit start automatically resumes the same native Claude and Codex
sessions. It also loads the provider-neutral `context/WORKING_COVENANT.md` and
`context/MICROHISTORY_V1.md` into both endpoints' durable instruction layer.
The microhistory contains ten actual chronological demonstrations—including
the Nick and grandfather examples, mistakes, correction, repair, initiative,
and disagreement—so a provider change or compacted session retains more than a
sterile preference list. It explicitly forbids claiming those events as the
receiving model's personal memories or imitating their surface style.

Before a turn, the cockpit searches its own append-only journal for relevant
prior supervised exchanges. It injects at most two, caps the evidence packet at
2,400 characters, and gives the identical packet to Claude and Codex. No lexical
match means no episode is injected. Current words override old evidence.

`/context` shows the exact covenant, relationship-layer status, and evidence
used on the previous turn. `/context full` also prints the chronological
microhistory. `/context off` disables episode retrieval for the current run
while leaving the relationship lineage and native session continuity intact;
`/context on` restores it. This first version learns new evidence only from
cockpit exchanges. It does not silently scan every historical transcript or
synthesize permanent traits.

When a provider approaches its model context limit, its native runtime compacts
older conversation into a summary and continues the same session. That summary
is necessarily lossy. The covenant and microhistory live outside the ordinary
turn history and are attached again whenever the cockpit starts or resumes, so
the operational relationship does not depend only on what the compactor chose
to remember.

## Proof

Run:

```sh
inception status
python3 scripts/verify_runtime_install.py
```

The verifier checks the canonical rollout's native session metadata, its fork
lineage, the running app-server, and the absence of automatic microhistory
injection from global Codex and Claude startup files. The cockpit-specific
relationship layer is deliberate and does not alter ordinary sessions.

## Recovery and research artifacts

`context/MICROHISTORY_V1.md` and `context/WORKING_COVENANT.md` are the
provider-neutral disaster-recovery source and cold-model research controls.
They load automatically inside the supervised cockpit, but not into ordinary
Codex or Claude sessions.

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

- The first Inception implementation auto-loaded a curated relationship history
  into every global Codex and Claude startup. That described the relationship
  to cold models instead of preserving it, so the global hooks were removed and
  native thread continuity became primary. The later supervised cross-provider
  cockpit uses the same source only as a bounded recovery layer beneath its two
  persistent lived sessions; it does not restore the rejected global behavior.
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
