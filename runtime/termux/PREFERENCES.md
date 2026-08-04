# Global answer rules

The four human-facing rules are shared by Claude and Codex. Their loading paths differ because the products differ.

- Codex reads `~/.codex/AGENTS.md`, loads the same contract as `developer_instructions`, and receives it again from a `UserPromptSubmit` hook on every message.
- Claude reads its global `CLAUDE.md` and receives the same contract again from a `UserPromptSubmit` hook on every message.
- Both installed Claude launch paths keep two narrow Stop checks: one catches long replies and one catches words above the requested child-simple reading level. Native Termux calls the same Debian-backed scripts so the rule logic has one source.
- Old Claude reply jars, claim-limit checks, hedge scanning, outside audits, and pre-compaction reinjection are not active. They added conflicting reply shapes or duplicate work.
- Codex stores command-hook trust in the local live config. Changing the hook later requires reviewing and trusting the new hash again; the trust hash is machine state and is not committed.

`core-interaction-contract.md` is the tracked human-readable source. Tests require the Codex and Claude prompt hooks to inject the same contract text.
