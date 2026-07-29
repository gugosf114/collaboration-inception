# Security model

Collaboration Inception coordinates already signed-in local model commands. It
does not ask for, copy, or transmit provider API keys.

## Local bridge

- The bridge accepts only `127.0.0.1` or `::1` as a bind address.
- A six-digit startup code performs one-time pairing.
- Pairing returns a random bearer token in the response body.
- The token is stored in a mode-`0600` local file and Chrome extension storage.
- Tokens never appear in URLs or the event journal.
- Request bodies are capped at 2 MiB and failed pairing attempts are limited.
- The event queue is bounded and approval waits expire.

Any process running as the same operating-system user may be able to reach the
loopback port or read that user's files. Protect the local account and do not
run untrusted software beside an active cockpit.

## Model permissions

Discussion turns are read-only. A direct working turn is an explicit grant to
one provider. Git push, release, deployment, outbound messages, payments, and
destructive commands trigger a blocking operator approval.

The classifier is a second gate around Claude and Antigravity; it cannot make
an irreversible action inherently safe. Review the exact action shown on the
approval card. Codex also retains its native sandbox and approval protocol.

## Browser access

The side panel observes pointer and keyboard activity on ordinary HTTP and
HTTPS pages so it can transfer control to the human immediately. Point mode
reads the exact selected element and nearby DOM. Chrome exposes these
permissions when the unpacked extension is installed. Disable or remove the
extension when browser integration is not needed.

## Private local data

The ignored `runtime/` data can contain conversation text, provider session
IDs, screenshots, browser evidence, Git diffs, the relationship ledger, and
the bridge token. Transcript archives and indexes may contain the same private
material. Review or remove local runtime data before sharing a machine or
working directory.

Report a vulnerability through a private GitHub security advisory for this
repository rather than a public issue.
