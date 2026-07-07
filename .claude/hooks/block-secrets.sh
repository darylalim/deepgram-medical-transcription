#!/usr/bin/env bash
# PreToolUse (Edit|Write|MultiEdit) hook.
# Refuse to modify secret-bearing files (.env and variants, secrets.toml).
# The tracked template .env.example stays editable. Enforces the project's
# non-negotiable PHI/secrets policy: secrets never flow through the assistant.
set -u
file=$(jq -r '.tool_input.file_path // empty' 2>/dev/null)
[ -n "$file" ] || exit 0

deny() {
  echo "Blocked: \"$1\" holds secrets (PHI/secrets policy). Edit .env.example (the tracked template) instead, or change the real file yourself outside Claude." >&2
  exit 2
}

case "$file" in
  *.env.example) exit 0 ;; # tracked template — allowed
  *.env | *.env.*) deny "$file" ;; # .env, .env.local, .env.production, ...
  */secrets.toml) deny "$file" ;; # e.g. .streamlit/secrets.toml
esac
exit 0
