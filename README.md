# Collaboration Inception

This local experiment tests whether a fresh AI model works better with George when it inherits a real conversation history instead of a conventional personality prompt.

The exporter converts a Codex rollout JSONL into:

- a core transcript containing George's messages and Sol's final answers;
- a full visible transcript that also contains commentary updates;
- structured JSONL for replay or retrieval experiments;
- metadata with counts, rough token estimates, and integrity hashes.

Runtime-injected environment and `AGENTS.md` messages are omitted by default. Tool calls, hidden machinery, and token-accounting events are never exported.

## Export

```sh
python3 scripts/export_codex_session.py /path/to/rollout.jsonl \
  --output-dir exports \
  --prefix george-sol
```

## Test

```sh
python3 -m unittest discover -s tests -v
```

## Preserved-history experiment

`experiment/test-prompt.txt` is sent once to a native `codex fork` of the
source session and once to an isolated Codex home containing only temporary
account authentication and the same model/reasoning settings. The clean run
must not contain global `AGENTS.md`, canonical memory, repository context, or
prior transcript.

Randomize the completed answers before evaluation:

```sh
python3 scripts/make_blind_pair.py \
  experiment/history-answer.md experiment/baseline-answer.md \
  --output-dir experiment
```

George judges `blind-A.md` and `blind-B.md` before `blind-map.json` is opened.
The contaminated first baseline attempt is retained only as an audit artifact;
it is not part of the comparison.

## Runtime installation — 2026-07-18

The relationship trajectory is now active for fresh local coding sessions:

- **Codex:** the complete `MICROHISTORY_V1.md` is embedded byte-for-byte in
  `~/.codex/AGENTS.md`, together with the current shared financial mission.
  The installed file is 25,508 of the configured 32,768-byte startup limit.
- **Claude Code:** `~/.claude/CLAUDE.md` imports both
  `WORKING_COVENANT.md` and `MICROHISTORY_V1.md` through Claude Code's native
  `@path` mechanism.
- **Provider-neutral source:** the covenant and microhistory are ordinary
  Markdown files under `context/` so another model can
  receive the identical trajectory without rewriting it as a persona prompt.

The experiment design and scoring rules live in `context/INCEPTION_PROTOCOL.md`.

Run `python3 scripts/verify_runtime_install.py` to verify source integrity,
Codex's startup-byte budget, and both provider installations.
