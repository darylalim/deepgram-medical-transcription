#!/usr/bin/env bash
# PostToolUse (Edit|Write|MultiEdit) hook.
# For an edited Python file inside the project: auto-format + lint-fix with
# ruff, then type-check with ty. Everything runs through `uv` so it uses the
# pinned tool versions and the pyproject config. No-ops for non-.py or
# out-of-project paths, so editing docs, TOML, or scratch files is untouched.
set -u
root="${CLAUDE_PROJECT_DIR:-$PWD}"
cd "$root" || exit 0

file=$(jq -r '.tool_input.file_path // empty' 2>/dev/null)
[ -n "$file" ] || exit 0
case "$file" in
  "$root"/*.py) ;; # only Python files under the project root
  *) exit 0 ;;
esac
[ -f "$file" ] || exit 0

# 1) Format + autofix lint (ruff). Silent; these are deterministic rewrites.
uv run ruff format "$file" >/dev/null 2>&1
uv run ruff check --fix "$file" >/dev/null 2>&1

# 2) Type-check the edited file (ty). Surface regressions back to Claude via
#    exit 2 so they get fixed in the same turn.
if ! ty_out=$(uv run ty check "$file" 2>&1); then
  {
    echo "ty reported type issues in ${file#"$root"/}:"
    printf '%s\n' "$ty_out" | tail -20
  } >&2
  exit 2
fi
exit 0
