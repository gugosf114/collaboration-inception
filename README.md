# Collaboration Inception

One operating room where a person can direct Claude, Codex, and Google
Antigravity together from a terminal or a live Chrome side panel.

Models can answer independently, challenge each other, inspect the same live
evidence, build competing solutions, and learn from outcomes. The operator owns
the turn, can steer it while it runs, and approves consequential actions.

## Capability stack

- **One live channel:** streamed model output, interruption, steering, voice,
  browser pointing, and explicit hand-back.
- **Three collaboration modes:** independent answers, bounded model dialogue,
  and a three-model council.
- **Real working turns:** providers can inspect and change the selected
  project; Codex runs through its native app-server protocol.
- **Consequential-action gate:** push, release, deploy, send, payment, and
  destructive actions pause for an operator decision.
- **Shared evidence:** screen, browser, DOM target, image, and file evidence is
  copied once and labeled for every model.
- **Durable continuity:** corrections, promises, results, calibration,
  task-matched episodes, counterevidence, and the current mission live in a
  local SQLite ledger.
- **Proof arena:** isolated Git worktrees let models attempt the same change,
  run the same test, and present evidence before the operator chooses.

See [Architecture](docs/ARCHITECTURE.md), [Security](docs/SECURITY.md), and the
current [Verification record](docs/VERIFICATION.md).

## What the other person needs

- A Windows, Linux, macOS, or Termux computer with Python 3 and Git.
- ImageMagick for screenshot marking.
- Any **two** signed-in model commands:
  - `claude`
  - `codex`
  - `agy` (Google Antigravity)

Codex is optional. Someone with Claude plus Antigravity can use Inception
without a Codex or ChatGPT subscription. Inception uses the accounts already
connected to those command-line tools; it does not contain or share API keys.

The older `gemini` command is also accepted as Antigravity. Current individual
Google users should prefer `agy`.

Inception chooses quality over cost. It explicitly requests:

- GPT-5.6 Sol with maximum reasoning
- Claude Opus 4.8 with maximum effort
- Gemini 3.1 Pro High

The cockpit prints the exact active choices at startup and under `/show-status`.
These are deliberate pins, so another CLI's cheaper personal default cannot
silently replace them. If Antigravity cannot provide Gemini 3.1 Pro High,
Inception stops with a clear message instead of quietly using Flash.

## Install on Windows PowerShell

```powershell
git clone https://github.com/gugosf114/collaboration-inception.git
cd collaboration-inception
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

Open a new PowerShell window and run:

```powershell
inception cockpit
```

The downloaded folder is the installed application, so keep it where it is.
The installer checks the required files and tools, creates `inception.cmd`, and
adds its small launcher folder to the user PATH. If a launcher already exists,
it is preserved as a timestamped backup.

## Install on Termux, Linux, or macOS

```sh
git clone https://github.com/gugosf114/collaboration-inception.git
cd collaboration-inception
chmod +x install.sh
./install.sh
launch
```

On Termux, Claude may live inside Debian PRoot. The installer and cockpit
detect that arrangement. After installation, daily use is simply `launch`.

Each new raw Termux Codex session initially shows the terminal's secondary
`home` title and has no bold app-owned name. After George's first substantive
request, that session runs `po title "LinkedIn Applications"`, using a short
description of its actual work. The command drives Termux's native rename
dialog through the phone-local Agent Bridge, reads the exact bold drawer name
back, and only then registers it for routing. Missing, differently capitalized,
and stale names are rejected instead of guessed. Side branches do not rename
the session; only a real change in its primary work does.

Termux Codex also keeps the normal tool and build feed, then adds an **ENGLISH
READ-BACK — BLIND** block when a work turn ends. A `PostToolUse` hook records
only the real shell and file-edit events. A `Stop` hook gives those numbered
events to an isolated Luna translator that cannot see the user's request, the
coding model's plan, or its final claims. Every English sentence points back to
an event such as `[E1]`. A deterministic checker marks skipped, truncated, and
hidden-failure events as `UNTRANSLATED` or `CHECK RAW`. Exact local receipts
live under `~/.codex/code-readback/receipts/`.

## Add the Chrome side panel

The terminal cockpit works by itself. The side panel adds the live channel,
voice input, page pointing, control ownership, and approval cards.

1. Open `chrome://extensions`.
2. Turn on **Developer mode**.
3. Select **Load unpacked** and choose this repository's `extension` folder.
4. Run `inception cockpit`.
5. Select the Inception toolbar icon, enter the six-digit pairing code printed
   by the cockpit, and choose **Pair**.

The bridge listens only on `127.0.0.1`. Pairing exchanges the one-time code for
a bearer token stored in Chrome extension storage. The token is never placed in
a URL. Change the port with `inception cockpit --bridge-port PORT`, or disable
the HTTP bridge with `--no-bridge`. Terminal consequential-action approvals
remain active when the browser bridge is disabled.

## First real test

At the `TYPE HERE>` prompt:

```text
/show-status
/review We need one reversible way to let three AI models modify the same project without silently overwriting each other.
```

The screen names all three models. They answer independently, read each other's
answers, challenge them for two rounds, and Codex returns one cumulative answer.
Choose the worker with `/fix-it codex`, `/fix-it claude`, or `/fix-it agy`.
Type `/do-not-fix` to leave everything unchanged.

Choose the pair when three models are installed:

```text
/talk-two claude agy YOUR QUESTION
/talk-two codex agy YOUR QUESTION
/talk-two claude codex YOUR QUESTION
```

Use all three:

```text
/ask-all YOUR QUESTION
/debate-all YOUR QUESTION
/review YOUR QUESTION
```

## Main controls

### Think and challenge

```text
/ask-all QUESTION                     all three answer independently
/debate-all QUESTION                  all three challenge for two rounds
/review QUESTION                      all three challenge; one answer; asks to fix
/ask-two QUESTION                     Claude and Codex answer independently
/talk-two MODEL MODEL QUESTION        the chosen two exchange two replies
/ask-one MODEL QUESTION               only that model answers
/show-last [MODEL]                    show a complete answer again
```

### Work with a checker

```text
/fix-it MODEL
/do-not-fix
/work-one MODEL REQUEST
/guard on
```

A direct model turn can inspect, edit, test, commit, or push when the request
authorizes it. Only one model receives that working turn. With the default
guard enabled, it must draft first, another model objects, and the original
model then performs and verifies the checked request. Discussion turns are
read-only.

### See the same thing

```text
/show-file "PATH" QUESTION
/show-image "IMAGE" QUESTION
/point-to-image "IMAGE" X Y QUESTION
/show-screen QUESTION
/inspect-folder MODEL "FOLDER PATH" TASK
/browser QUESTION
/browser TAB :: QUESTION
/browser-point "ELEMENT OR CSS" QUESTION
/browser-point TAB :: ELEMENT :: QUESTION
```

Both models receive the same private copy. `/show-screen` uses native Windows
capture, Android/ADB where available, an existing Agent Bridge laptop, or a
configured capture command. `/browser` uses Chrome's live debugging connection.
Naming a tab removes ambiguity when several Chrome windows are open.

Useful optional settings:

```text
INCEPTION_CODEX_MODEL=gpt-5.6-sol
INCEPTION_CODEX_REASONING_EFFORT=max
INCEPTION_CLAUDE_MODEL=claude-opus-4-8
INCEPTION_CLAUDE_EFFORT=max
INCEPTION_ANTIGRAVITY_MODEL=Gemini 3.1 Pro (High)
INCEPTION_GEMINI_MODEL=gemini-3.1-pro-preview
INCEPTION_AGENT_BRIDGE_URL=https://your-private-bridge
INCEPTION_BROWSER_SSH_HOST=your-windows-host
INCEPTION_SCREEN_CAPTURE_COMMAND=your-capture-command
INCEPTION_BROWSER_CAPTURE_COMMAND=your-capture-command
INCEPTION_BROWSER_POINT_COMMAND=your-point-command
INCEPTION_SPEECH_COMMAND=your-speech-command
```

Do not put passwords in the bridge URL.

### Interrupt and steer

```text
/steer-all GUIDANCE
/steer-one MODEL GUIDANCE
/stop-all
/listen
```

Codex accepts guidance inside its live turn. Claude accepts another live stream
message. Antigravity currently has no equivalent same-turn steering interface,
so Inception interrupts it and continues the same conversation with George's
guidance. `/listen` turns speech into the next full cockpit command.

The side panel applies the same rule automatically: speech steers active work
and asks the default pair while the operator owns the room. **Point**
captures the clicked page element's selector, text, ARIA label, role, bounds,
nearby DOM, URL, and page title. **Stop** interrupts live work. **Hand back**
returns control after human browser activity.

### Approve consequential actions

```text
/approve-once ID
/approve-for-session ID
/deny-action ID
```

Codex uses its native command and file-change approval requests. Claude and
Antigravity use Inception's consequential-tool classifier. A request appears in
the side panel and terminal; the model waits for **Allow once**, **Allow for
session**, or **Deny**. Discussion turns remain read-only.

### Durable relationship memory

```text
/memory
/mission
/mission set OUTCOME
/mission done [NOTE]
/evidence
/evidence challenge ID COUNTEREVIDENCE
/correct MODEL CORRECTION
/promise add MODEL PROMISE
/promise done ID [NOTE]
/outcome MODEL CATEGORY success|failure|mixed [NOTE]
/recover MODEL REASON
/context [full|on|off]
```

Inception stores corrections, promises, measured outcomes, recommendations,
calibration error, model authority by category, drift, missions, and
task-matched evidence episodes in a local SQLite ledger. It gives every model
the same small relevant packet. Every learned episode keeps its source
exchange, confidence, useful future behavior, and counterevidence. Current
instructions always outrank old evidence. `/recover` starts a fresh provider
session while keeping the shared relationship record.

On George's phone, launch automatically connects the read-only canonical
memory index and imports the latest post-office `messages.jsonl` into the
ledger. Only topic-matched canonical files are read for a turn. New direct
corrections preserve the preceding model answer and George's correction as a
source-backed episode. Council prompts and model criticism are marked internal
so they cannot be mislearned as George's words. No memory command or pasted
continuity prompt is required.

Import an exported Codex archive without putting the archive into model
context:

```sh
po index PATH_TO_MESSAGES_JSONL
python3 scripts/ingest_history.py PATH_TO_MESSAGES_JSONL
```

`po` also provides bounded local search over the archive. The extractor records
corrections, interruptions, approvals, and successes as fallible evidence;
re-running it is idempotent.

### Message another raw Termux Codex session

Raw Termux sessions do not need to be restarted inside tmux. List them, send to
the only other Codex, or name its terminal:

```sh
po sessions
po title "LinkedIn Applications"
po send "LinkedIn Applications" "Reply with the evidence behind your verdict."
po send-other "Check whether my fix covers the original failure."
po send pts/2 "Reply with the evidence behind your current verdict."
po send --instant pts/2 "Send this as one fast block instead."
po send --steer-now pts/2 "This deliberately changes your active task."
po crosscheck pts/2 /tmp/primary-answer.txt "Was the original request satisfied?"
```

`po title` changes the current Termux tab's bold native session name and
independently reads that drawer row back before registering the same exact name
for routing. It can briefly bring Termux forward to perform the native rename,
then restores the app that was previously visible. If the rename cannot be
verified, the routing registry is left unchanged. Before any name-routed send,
`po` prints the resolved destination name and terminal. If that exact name is
missing or belongs to a dead session, the send stops. PTY and PID selectors
remain explicit recovery routes.

The Termux-only sender visibly types each message and presses Enter by default,
so George can watch the handoff in the receiving session. `--instant` keeps the
same delivery and reply tracking but injects the message as one fast block. When
the matching answer returns, the sending session displays the peer as "typing
back," renders the complete answer character by character, and ends with a
receipt containing the task ID and exact Codex turn ID. The
sender queues behind the target's active turn by default,
locks each target so two senders cannot interleave, assigns every message a
task ID, and accepts only the matching Codex turn's completion. It duplicates
the terminal master's existing file
descriptor and writes through the same input path as the phone keyboard. It
verifies the PTY again immediately before sending and rejects multiline or
control-character input. `--steer-now` is the explicit override for changing
an active target turn. `po blast` remains the route for registered tmux panes.

`po crosscheck` turns the pipe into a bounded two-Codex review. It waits for
the target to become idle, assigns a task ID, asks for a blind independent
answer before revealing or journaling the primary answer, permits one final
challenge, validates the required disagreement sections, and saves both
replies plus hashes and unresolved disagreement under
`~/postoffice/crosschecks/`. The sending Codex remains responsible for the
final result.

### Isolated build arena

```text
/arena REQUEST
/arena MODEL MODEL --test "COMMAND" :: REQUEST
/choose [ARENA_ID] MODEL
/undo [ARENA_ID]
/replay [ARENA_ID]
```

The arena requires a clean Git project. It creates one detached worktree per
model, gives both the same request, runs the same test command, records their
diffs and claims, and recommends a winner from evidence. George chooses the
winner. Inception cherry-picks that attempt into the real project. `/undo`
creates a recoverable revert commit; `/replay` shows what happened.

## Persistence and privacy

Each provider keeps its own native session or conversation. Inception also
keeps its project selection, event journal, surface copies, arena records, and
relationship ledger under `runtime/`, which Git ignores.

George's own installation can reopen his private canonical Codex history. A
downloaded copy cannot access that history and starts its own persistent
cockpit. The repository contains no account credentials or model sessions.

The local bridge token, event stream, screenshots, transcripts, model session
IDs, archive index, and ledger stay under ignored runtime or user-data paths.
Review that local data before sharing a working directory.

## Measure whether continuity helps

The repository includes a blind three-condition evaluation:

1. **clean** — no relationship context;
2. **profile** — static covenant/profile only;
3. **episodes** — Inception's task-matched evidence packet.

```sh
python3 scripts/continuity_eval.py prepare \
  --clean clean.md --profile profile.md --episodes episodes.md \
  --output-dir runtime/eval/blind --map runtime/eval/private-map.json

# Fill runtime/eval/blind/judgments.json before opening the map.
python3 scripts/continuity_eval.py score \
  --map runtime/eval/private-map.json \
  --judgments runtime/eval/blind/judgments.json \
  --report runtime/eval/report.json
```

The rubric scores task success, directness, continuity, correction use, and
calibration, then penalizes contradictions. Episode retrieval earns promotion
only when it beats both simpler conditions.

## Honest limits

- Inception needs two installed model commands, but neither has to be Codex.
- Each provider's own subscription, quota, and sign-in rules still apply.
- Browser capture needs a Chrome debugging connection and Node.js 22 or newer.
- Voice input uses Chrome's Web Speech support and therefore depends on the
  browser and operating system.
- A non-interactive SSH session cannot capture the visible Windows desktop;
  the native Windows process or Agent Bridge route handles that case.
- The arena can isolate file changes, but it cannot make an inherently
  irreversible external action safe.

## Development proof

```sh
python3 -m unittest discover -s tests -v
python3 scripts/cockpit.py --help
node --check scripts/capture_browser.cjs
node --check scripts/point_browser.cjs
node --check scripts/review_extension_live.cjs
node --check extension/background.js
node --check extension/activity.js
node --check extension/sidepanel.js
```

GitHub Actions runs the test suite on both Ubuntu and native Windows.

## License

[MIT](LICENSE)
