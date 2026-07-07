"""Static guard tests for the GitHub Actions CI workflow.

``.github/workflows/ci.yml`` is pure config: it runs no Python and is invisible
to ``ruff``/``ty``/``pytest`` (which only ever look at ``.py`` files), so the
rest of the suite can never catch a regression in it. These tests pin the two
invariants the workflow exists to hold:

1. **Mirror the local hook gates.** The workflow must keep running the same four
   gates the ``.claude/hooks/`` scripts enforce locally — ``ruff format --check``
   / ``ruff check`` / ``ty check`` / ``pytest`` — plus the ``uv sync --locked``
   lockfile-drift check.
2. **Stay injection-safe.** No attacker-controlled ``github.event.*`` /
   ``github.head_ref`` text may be interpolated into a shell step (the GitHub
   Actions script-injection class), and the job stays least-privilege.

Assertions are made over the file text (no YAML-parser dependency): substring /
regex checks are enough to catch a gate being dropped or an unsafe
interpolation slipping in.
"""

import re
from pathlib import Path

import pytest

WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "ci.yml"

# Attacker-controllable expression contexts that must never reach a `run:` shell.
_UNTRUSTED_INTERPOLATION = re.compile(r"\$\{\{\s*github\.(?:event|head_ref)\b")


@pytest.fixture(scope="module")
def workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_workflow_file_exists() -> None:
    assert WORKFLOW.is_file(), f"missing CI workflow at {WORKFLOW}"


@pytest.mark.parametrize(
    "gate",
    [
        "ruff format --check",
        "ruff check",
        "ty check",
        "pytest",
        "uv sync --locked",
    ],
)
def test_ci_wires_each_local_gate(workflow_text: str, gate: str) -> None:
    assert gate in workflow_text, f"CI workflow no longer runs `{gate}`"


def test_ci_job_is_least_privilege(workflow_text: str) -> None:
    # The checks job only reads the repo; no write scopes should creep in.
    assert "contents: read" in workflow_text


def test_ci_has_no_untrusted_interpolation(workflow_text: str) -> None:
    # Guards the GitHub Actions script-injection class: PR titles, branch names,
    # commit messages, etc. must never be interpolated into a shell step.
    match = _UNTRUSTED_INTERPOLATION.search(workflow_text)
    assert match is None, f"untrusted input interpolated into workflow: {match!r}"
