#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

# Snapshot every long-running Codex/Claude conversation touched recently.
# Raw rollouts are copied unchanged; readable files contain only visible user
# and assistant text. Tool payloads and hidden reasoning stay in the raw copy.

DAY="${1:-$(date +%F)}"
ROOT="${HOME}/session-post-office/${DAY}"
RAW="${ROOT}/raw"
READABLE="${ROOT}/readable"
CODEX_ROOT="${HOME}/.codex/sessions"
CLAUDE_ROOT="${PREFIX}/var/lib/proot-distro/containers/debian/rootfs/root/.claude/projects/-root"

mkdir -p "$RAW" "$READABLE"
MANIFEST="${ROOT}/manifest.tsv"
INDEX="${ROOT}/INDEX.md"

# This folder is generated output. Clear the prior snapshot so repeated runs
# refresh active sessions without leaving stale filenames behind.
rm -f "$RAW"/*.jsonl "$READABLE"/*.md

printf 'source\tsession_id\tbytes\tmodified\tsha256\traw_file\treadable_file\n' > "$MANIFEST"
printf '# Session post office — %s\n\nRaw snapshots plus visible dialogue from every main session touched in the last 48 hours. No summaries.\n\n' "$DAY" > "$INDEX"

collect_codex() {
  local source="$1" id base raw out bytes modified sha
  id="$(sed -n '1p' "$source" | jq -r '.payload.id // .payload.session_id // empty')"
  if [[ -z "$id" ]]; then
    id="$(basename "$source" .jsonl)"
  fi
  base="codex-${id}"
  raw="${RAW}/${base}.jsonl"
  out="${READABLE}/${base}.md"
  cp -p "$source" "$raw"
  {
    printf '# Codex session %s\n\nSource: `%s`\n' "$id" "$source"
    jq -r '
      select(.type == "response_item" and .payload.type == "message")
      | select(.payload.role == "user" or .payload.role == "assistant")
      | ([.payload.content[]?
          | select(.type == "input_text" or .type == "output_text")
          | .text] | join("\n")) as $body
      | select($body != "")
      | "\n\n## " + (.payload.role | ascii_upcase) + " — " + (.timestamp // "unknown") + "\n\n" + $body
    ' "$raw"
  } > "$out"
  bytes="$(stat -c %s "$raw")"
  modified="$(stat -c %y "$raw")"
  sha="$(sha256sum "$raw" | cut -d' ' -f1)"
  printf 'codex\t%s\t%s\t%s\t%s\t%s\t%s\n' "$id" "$bytes" "$modified" "$sha" "$raw" "$out" >> "$MANIFEST"
  printf -- '- Codex `%s`: [readable](readable/%s.md) · [raw](raw/%s.jsonl)\n' "$id" "$base" "$base" >> "$INDEX"
}

collect_claude() {
  local source="$1" id base raw out bytes modified sha
  id="$(basename "$source" .jsonl)"
  base="claude-${id}"
  raw="${RAW}/${base}.jsonl"
  out="${READABLE}/${base}.md"
  cp -p "$source" "$raw"
  {
    printf '# Claude session %s\n\nSource: `%s`\n' "$id" "$source"
    jq -r '
      select(.type == "user" or .type == "assistant")
      | (.message.role // .type) as $role
      | (if (.message.content | type) == "string" then .message.content
         else ([.message.content[]? | select(.type == "text") | .text] | join("\n"))
         end) as $body
      | select($body != "")
      | "\n\n## " + ($role | ascii_upcase) + " — " + (.timestamp // "unknown") + "\n\n" + $body
    ' "$raw"
  } > "$out"
  bytes="$(stat -c %s "$raw")"
  modified="$(stat -c %y "$raw")"
  sha="$(sha256sum "$raw" | cut -d' ' -f1)"
  printf 'claude\t%s\t%s\t%s\t%s\t%s\t%s\n' "$id" "$bytes" "$modified" "$sha" "$raw" "$out" >> "$MANIFEST"
  printf -- '- Claude `%s`: [readable](readable/%s.md) · [raw](raw/%s.jsonl)\n' "$id" "$base" "$base" >> "$INDEX"
}

while IFS= read -r file; do
  collect_codex "$file"
done < <(find "$CODEX_ROOT" -type f -name '*.jsonl' -mtime -2 | sort)

# Only top-level Claude conversations. Subagent/workflow rollouts are evidence
# inside their parent sessions and would add massive duplication here.
while IFS= read -r file; do
  collect_claude "$file"
done < <(find "$CLAUDE_ROOT" -maxdepth 1 -type f -name '*.jsonl' -mtime -2 | sort)

printf '\n## Inventory\n\n' >> "$INDEX"
awk -F '\t' 'NR>1 {printf "- %s %s — %.1f MB raw\n", $1, $2, $3/1048576}' "$MANIFEST" >> "$INDEX"

if [[ -f "${ROOT}/GRAND_SYNTHESIS.md" ]]; then
  printf '\n## Derived documents\n\n' >> "$INDEX"
  printf '%s\n' '- [Seven-session synthesis](GRAND_SYNTHESIS.md)' >> "$INDEX"
  printf '%s\n' '- [George–AI working covenant](WORKING_COVENANT.md)' >> "$INDEX"
  printf '%s\n' '- [Inception experiment protocol](INCEPTION_PROTOCOL.md)' >> "$INDEX"
  printf '%s\n' '- [Curated chronological microhistory v1](MICROHISTORY_V1.md)' >> "$INDEX"
  printf '%s\n' '- [Fresh-session test prompts](TEST_PROMPTS.md)' >> "$INDEX"
  printf '%s\n' '- [Keyword and source map](SEARCH_MAP.md)' >> "$INDEX"
  printf '%s\n' '- [Session loudspeaker and post-office bus](BROADCAST_BUS_DESIGN.md)' >> "$INDEX"
  printf '%s\n' '- [George’s credit note](CREDIT_NOTE.md)' >> "$INDEX"
  printf '%s\n' '- [Integration note](INTEGRATION_NOTE.md)' >> "$INDEX"
  printf '%s\n' '- [Termux recovery check](RECOVERY_CHECK_2026-07-17.md)' >> "$INDEX"
fi

printf 'Post office: %s\n' "$ROOT"
printf 'Sessions: %s\n' "$(( $(wc -l < "$MANIFEST") - 1 ))"
printf 'Raw bytes: %s\n' "$(awk -F '\t' 'NR>1 {n += $3} END {printf "%.0f", n}' "$MANIFEST")"
ln -sfn "$ROOT" "${HOME}/session-post-office/latest"
