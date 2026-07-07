#!/usr/bin/env bash
# PostToolUse (Edit|Write|MultiEdit) hook.
# For an edited Python file inside the project: auto-format, then surface any
# unresolved lint (ruff) and type (ty) problems back to Claude. Everything runs
# through `uv` so it uses the project's pinned tools and the pyproject config.
# No-ops for non-.py or out-of-project paths, so editing docs, TOML, or scratch
# files is untouched.
set -u
root="${CLAUDE_PROJECT_DIR:-$PWD}"
while [ "$root" != "/" ] && [ "${root%/}" != "$root" ]; do root="${root%/}"; done # strip trailing slash(es) so the in-project glob still matches
cd "$root" || exit 0

file=$(jq -r '.tool_input.file_path // empty' 2>/dev/null)
[ -n "$file" ] || exit 0
case "$file" in
  "$root"/*.py) ;; # only Python files under the project root
  *) exit 0 ;;
esac
[ -f "$file" ] || exit 0

# 1) Format, then lint with autofix. ruff's `--fix` exits non-zero only when
#    violations REMAIN after fixing, so its exit code is our lint signal. `--`
#    stops a file named like `-foo.py` being parsed as an option.
uv run ruff format -- "$file" >/dev/null 2>&1
lint_out=$(uv run ruff check --fix -- "$file" 2>&1)
lint_rc=$?

# 2) Type-check the edited file.
ty_out=$(uv run ty check -- "$file" 2>&1)
ty_rc=$?

# 3) Surface unresolved lint and/or type regressions back to Claude (exit 2) so
#    they are addressed in the same turn.
rc=0
if [ "$lint_rc" -ne 0 ]; then
  { echo "py-checks: unresolved ruff lint in ${file#"$root"/}:"; printf '%s\n' "$lint_out" | tail -30; } >&2
  rc=2
fi
if [ "$ty_rc" -ne 0 ]; then
  { echo "py-checks: ty type issues in ${file#"$root"/}:"; printf '%s\n' "$ty_out" | tail -30; } >&2
  rc=2
fi
exit "$rc"
