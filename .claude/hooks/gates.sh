#!/usr/bin/env bash
# Stop hook: one whole-project gate per turn — format (applying), lint, types, tests.
#
# Replaces the former per-edit py-checks.sh PostToolUse hook, which ran ruff and
# ty against the SINGLE edited file. That was blind in both directions: a
# single-file `ty check` reports "All checks passed!" on a change that breaks a
# caller in another module (exactly the nova/ -> streamlit_app.py shape this repo
# has), and `ruff check --fix` silently rewrote files mid-turn — deleting imports
# written before the body that uses them, exiting 0 with empty stderr. Running the
# gates whole-project at the turn boundary fixes both: nothing mutates while
# Claude is still authoring, and cross-file regressions are actually seen.
set -u
root="${CLAUDE_PROJECT_DIR:-$PWD}"
cd "$root" || exit 0

# Preflight. Unlike block-secrets.sh (a security guard, which fails CLOSED), this
# is a quality gate and fails OPEN: a blocking Stop hook that cannot read its own
# stop_hook_active loop guard would wedge the session, and a missing toolchain is
# not a code defect to report as one.
command -v jq >/dev/null 2>&1 || exit 0
[ "$(jq -r '.stop_hook_active // false' 2>/dev/null)" = "true" ] && exit 0
command -v uv >/dev/null 2>&1 || exit 0

# Format APPLIES (so CI's `ruff format --check` never fires); lint only REPORTS —
# a project-wide `--fix` here would rewrite files this turn never touched.
uv run ruff format . >/dev/null 2>&1

# Each failing gate is truncated INDIVIDUALLY. Tailing the concatenated blob
# instead would drop whole sections: when pytest also fails its output is long
# enough to push the ruff/ty findings out entirely, so a real type error would
# be reported as nothing at all.
fail=""
record() {
  fail="${fail}"$'\n'"--- $1 (last ${TAIL_LINES} lines) ---"$'\n'"$(printf '%s\n' "$2" | tail -"${TAIL_LINES}")"
}
TAIL_LINES=12

out=$(uv run ruff check . 2>&1) || record "ruff check" "$out"
out=$(uv run ty check . 2>&1) || record "ty check" "$out"
out=$(uv run pytest -q 2>&1) || record "pytest" "$out"

[ -z "$fail" ] && exit 0
{
  echo "Gates failed after this turn — fix, then re-run the failing command yourself and report the result (this hook does not re-check on the retry):"
  printf '%s\n' "$fail"
} >&2
exit 2
