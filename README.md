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
