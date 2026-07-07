"""Guard tests for the GitHub Actions CI workflow.

``.github/workflows/ci.yml`` is pure config: it runs no Python and is invisible
to ``ruff``/``ty``/``pytest`` (which only ever look at ``.py`` files), so the
rest of the suite can never catch a regression in it. These tests parse the
workflow with PyYAML and pin the invariants it exists to hold:

1. **It is a valid workflow that wires the local hook gates.** The file must
   parse, expose a ``checks`` job, and run — in real ``run:`` step bodies, not a
   comment — the same four gates the ``.claude/hooks/`` scripts enforce locally
   (``ruff format --check`` / ``ruff check`` / ``ty check`` / ``pytest``) plus
   the ``uv sync --locked`` lockfile-drift check.
2. **It stays least-privilege and injection-safe.** No ``write`` permission
   scope anywhere; it triggers on ``pull_request`` (never the write-privileged
   ``pull_request_target``); and no ``run:`` step interpolates attacker-
   controllable ``github.event.*`` / ``github.head_ref`` / ``inputs.*`` text
   (the GitHub Actions script-injection class).

Parsing (rather than substring matching over raw text) is what lets these
catch a gate demoted to a comment, a smuggled ``write`` scope, a trigger swap,
or a malformed workflow GitHub would refuse to schedule — all of which a plain
``in`` check would miss.
"""

import re
from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "ci.yml"

# Attacker-controllable expression contexts that must never be interpolated raw
# into a shell `run:` step. `inputs.*` covers the `workflow_dispatch` surface.
_UNTRUSTED_GITHUB = re.compile(r"\$\{\{[^}]*\bgithub\.(?:event|head_ref)\b")
_UNTRUSTED_INPUTS = re.compile(r"\$\{\{[^}]*\binputs\b")


@pytest.fixture(scope="module")
def workflow() -> dict:
    # A malformed workflow (bad indent, duplicate key, ...) raises here and
    # fails every test — the one failure mode that silently disables CI.
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _triggers(workflow: dict) -> set:
    # PyYAML parses YAML 1.1, so the bare `on:` key deserializes to the boolean
    # True, not the string "on" (the classic Actions gotcha). Accept either.
    on = workflow.get("on", workflow.get(True))
    return set(on) if isinstance(on, dict | list) else {on}


def _run_steps(workflow: dict) -> list[str]:
    steps = workflow["jobs"]["checks"]["steps"]
    return [s["run"] for s in steps if isinstance(s, dict) and "run" in s]


def test_workflow_is_valid_and_has_a_checks_job(workflow: dict) -> None:
    assert isinstance(workflow, dict)
    assert "checks" in workflow["jobs"]
    assert _run_steps(workflow), "the checks job has no run: steps"


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
def test_ci_runs_each_local_gate(workflow: dict, gate: str) -> None:
    runs = _run_steps(workflow)
    assert any(gate in r for r in runs), f"no run: step runs `{gate}`"


def test_ci_is_least_privilege(workflow: dict) -> None:
    # Exactly read-only at the top level, and no write scope leaks into any job.
    assert workflow.get("permissions") == {"contents": "read"}
    for job in workflow["jobs"].values():
        perms = job.get("permissions") if isinstance(job, dict) else None
        assert "write" not in repr(perms).lower(), (
            f"write scope in job perms: {perms!r}"
        )


def test_ci_triggers_on_pull_request_not_target(workflow: dict) -> None:
    triggers = _triggers(workflow)
    assert "pull_request" in triggers, "CI must run on pull_request"
    assert "pull_request_target" not in triggers, (
        "CI must not use the write-privileged pull_request_target trigger"
    )


def test_run_steps_have_no_untrusted_interpolation(workflow: dict) -> None:
    for run in _run_steps(workflow):
        assert not _UNTRUSTED_GITHUB.search(run), (
            f"untrusted github context in: {run!r}"
        )
        assert not _UNTRUSTED_INPUTS.search(run), f"raw inputs.* in run step: {run!r}"
