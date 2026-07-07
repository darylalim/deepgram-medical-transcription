#!/usr/bin/env bash
# Stop hook: run the (fast, fully-mocked) test suite when Claude finishes a
# turn. On failure, block the stop and hand the output back so it gets fixed
# before the turn ends. The stop_hook_active guard prevents an infinite
# re-run loop (a blocked stop that re-triggers this same hook).
set -u
root="${CLAUDE_PROJECT_DIR:-$PWD}"
cd "$root" || exit 0

if [ "$(jq -r '.stop_hook_active // false' 2>/dev/null)" = "true" ]; then
  exit 0
fi

if ! out=$(uv run pytest -q 2>&1); then
  {
    echo "pytest is failing after this turn — fix before wrapping up:"
    printf '%s\n' "$out" | tail -25
  } >&2
  exit 2
fi
exit 0
