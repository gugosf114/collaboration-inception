# Collaboration Inception

One cockpit where George—or anyone who downloads it—can work with Claude,
Codex, and Google Antigravity together.

It is not an automatic agent swarm. The person stays in control. Models can
answer independently, challenge each other, inspect the same live evidence,
build competing solutions, and remember what proved true.

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

The cockpit prints the exact active choices at startup and under `/status`.
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
inception cockpit
```

On Termux, Claude may live inside Debian PRoot. The installer and cockpit
detect that arrangement.

## First real test

At the `george>` prompt:

```text
/status
/talk 3 We need one reversible way to let two AI models modify the same project without silently overwriting each other. Challenge each proposal, name its failure mode, and finish with one testable design.
```

The screen names both models. They first answer George independently. Then the
cockpit passes their exact completed answers back and forth for the number of
replies George granted. Each model knows which other model wrote the message.
The exchange stops and returns control to George.

Choose the pair when three models are installed:

```text
/talk claude antigravity 3 YOUR QUESTION
/talk codex antigravity 3 YOUR QUESTION
/talk claude codex 3 YOUR QUESTION
```

Use all three:

```text
/council 2 YOUR QUESTION
```

## Main controls

### Think and challenge

```text
/both QUESTION                       default pair answers independently
/all QUESTION                        every connected model answers independently
/talk [MODEL MODEL] [1-6] QUESTION   bounded two-model dialogue
/council [1-3] QUESTION              three-model challenge rounds
/pass SOURCE TARGET [NOTE]           manually forward the last complete answer
/last [MODEL]                        show a complete answer again
```

### Work with a checker

```text
/claude REQUEST
/codex REQUEST
/antigravity REQUEST
/act MODEL REQUEST
/guard on
```

A direct model turn can inspect, edit, test, commit, or push when the request
authorizes it. Only one model receives that working turn. With the default
guard enabled, it must draft first, another model objects, and the original
model then performs and verifies the checked request. Discussion turns are
read-only.

### See the same thing

```text
/file "PATH" QUESTION
/look "IMAGE" QUESTION
/point "IMAGE" X Y QUESTION
/screen QUESTION
/browser QUESTION
/browser TAB :: QUESTION
/browser-point "ELEMENT OR CSS" QUESTION
/browser-point TAB :: ELEMENT :: QUESTION
```

Both models receive the same private copy. `/screen` uses native Windows
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
/steer [MODEL] GUIDANCE
/stop
/listen
```

Codex accepts guidance inside its live turn. Claude accepts another live stream
message. Antigravity currently has no equivalent same-turn steering interface,
so Inception interrupts it and continues the same conversation with George's
guidance. `/listen` turns speech into the next full cockpit command.

### Durable relationship memory

```text
/memory
/correct MODEL CORRECTION
/promise add MODEL PROMISE
/promise done ID [NOTE]
/outcome MODEL CATEGORY success|failure|mixed [NOTE]
/recover MODEL REASON
/context [full|on|off]
```

Inception stores corrections, promises, measured outcomes, model authority by
category, and drift in a local SQLite ledger. It gives both models the same
small relevant evidence packet. Current instructions always outrank old
evidence. `/recover` starts a fresh model session while keeping the shared
relationship record.

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

## Honest limits

- Inception needs two installed model commands, but neither has to be Codex.
- Each provider's own subscription, quota, and sign-in rules still apply.
- Browser capture needs a Chrome debugging connection and Node.js 22 or newer.
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
```

GitHub Actions runs the test suite on both Ubuntu and native Windows.
