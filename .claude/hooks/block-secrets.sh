#!/usr/bin/env bash
# PreToolUse (Edit|Write|MultiEdit) hook.
# Refuse to modify secret-bearing files (.env and variants, secrets.toml /
# .secrets.toml, .envrc). The tracked template .env.example stays editable.
# Enforces the project's non-negotiable PHI/secrets policy: secrets never flow
# through the assistant.
#
# This is a security guard, so it FAILS CLOSED: if jq (used to parse the event)
# is unavailable, or the event itself is unparseable, we refuse the edit rather
# than silently allowing it. Matching is case-insensitive because on a
# case-insensitive filesystem (the macOS default) a write addressed to ".ENV"
# lands in the real ".env".
set -u

command -v jq >/dev/null 2>&1 || {
  echo "block-secrets: jq not found — refusing edits until it is installed (fail-closed)." >&2
  exit 2
}

file=$(jq -r '.tool_input.file_path // empty') || {
  echo "block-secrets: unparseable hook event -- refusing the edit (fail-closed)." >&2
  exit 2
}
[ -n "$file" ] || exit 0

shopt -s nocasematch

deny() {
  echo "Blocked: \"$1\" holds secrets (PHI/secrets policy). Edit .env.example (the tracked template) instead, or change the real file yourself outside Claude." >&2
  exit 2
}

case "$file" in
  *.env.example) exit 0 ;;                                        # tracked template — allowed
  *.env | *.env.*) deny "$file" ;;                               # .env, .env.local, .env.production, .ENV, ...
  secrets.toml | */secrets.toml | *.secrets.toml) deny "$file" ;; # secrets.toml (bare/nested) + Dynaconf .secrets.toml
  *.envrc | *.envrc.*) deny "$file" ;;                          # direnv .envrc + .envrc.local etc (often carry exported secrets)
esac
exit 0
