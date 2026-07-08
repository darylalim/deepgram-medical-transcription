"""Guard tests for the tag-triggered release workflow.

``.github/workflows/release.yml`` is pure config: it runs no Python and is
invisible to ``ruff``/``ty``/``pytest`` (which only ever look at ``.py`` files),
so the rest of the suite can never catch a regression in it. These tests parse
the workflow with PyYAML and pin the invariants it exists to hold:

1. **Releases are cut by pushing a ``v*`` tag — nothing else.** The file must
   parse, trigger *only* on ``push`` of a ``v*`` tag (never a branch push, and
   never the write-privileged ``pull_request_target``), and expose a ``release``
   job that publishes the GitHub Release for that tag.
2. **It stays least-privilege and injection-safe.** It holds *exactly*
   ``{contents: write}`` — the one scope needed to create a Release, and pointedly
   *not* ``pull-requests`` (the workflow opens no PRs, so it cannot hit the
   "GitHub Actions is not permitted to create or approve pull requests" policy).
   It never cancels a mid-release run, sets a job timeout, passes the tag through
   the ``GITHUB_REF_NAME`` env var rather than a raw ``${{ github.ref* }}`` shell
   interpolation, and — should a ``uses:`` step ever be added — SHA-pins it.

Parsing (rather than substring matching over raw text) is what lets these catch
a smuggled write scope, a trigger swap, a tag-name interpolated straight into the
shell, or a malformed workflow GitHub would refuse to schedule — all of which a
plain ``in`` check would miss.
"""

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"

# Attacker-controllable expression contexts that must never be interpolated raw
# into a shell `run:` step (the GitHub Actions script-injection class). A tag
# name is push-access-gated but still belongs in an env var, not the shell — so
# `github.ref`/`github.ref_name` interpolation is flagged here too.
_UNTRUSTED = re.compile(r"\$\{\{[^}]*\bgithub\.(?:event|head_ref|ref)\b")
_UNTRUSTED_INPUTS = re.compile(r"\$\{\{[^}]*\binputs\b")
# Any `uses:` (there are none today — the workflow shells out to the preinstalled
# `gh` CLI) must be pinned to a full 40-hex commit SHA if one is ever added.
_SHA_PINNED = re.compile(r"\S+@[0-9a-f]{40}\b")


@pytest.fixture(scope="module")
def workflow() -> dict:
    # A malformed workflow (bad indent, duplicate key, ...) raises here and
    # fails every test — the one failure mode that silently disables releases.
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _triggers(workflow: dict) -> dict:
    # PyYAML parses YAML 1.1, so the bare `on:` key deserializes to the boolean
    # True, not the string "on" (the classic Actions gotcha). Accept either.
    on = workflow.get("on", workflow.get(True))
    assert isinstance(on, dict), f"unexpected `on:` shape: {on!r}"
    return on


def _steps(workflow: dict) -> list[dict]:
    return [s for s in workflow["jobs"]["release"]["steps"] if isinstance(s, dict)]


def _run_steps(workflow: dict) -> list[str]:
    return [s["run"] for s in _steps(workflow) if "run" in s]


def test_workflow_is_valid_and_has_the_release_job(workflow: dict) -> None:
    assert isinstance(workflow, dict)
    assert "release" in workflow["jobs"]


def test_triggers_only_on_a_version_tag_push(workflow: dict) -> None:
    on = _triggers(workflow)
    # A tag push and nothing else: no branch push (that would double-fire with a
    # merge), and no pull_request(_target) where a fork could publish a release.
    assert set(on) == {"push"}, f"release must trigger only on push, got {set(on)}"
    push = on["push"]
    assert "branches" not in push, "release must trigger on tags, not branches"
    assert push.get("tags") == ["v*"], "release must trigger on `v*` tags"
    assert "pull_request" not in on
    assert "pull_request_target" not in on


def test_permissions_are_exactly_contents_write(workflow: dict) -> None:
    # Exactly the one scope needed to create the Release — and pointedly NOT
    # `pull-requests` (no PRs are opened), so the workflow can never trip the
    # "Actions is not permitted to create or approve pull requests" policy.
    assert workflow.get("permissions") == {"contents": "write"}


def test_a_mid_release_run_is_never_cancelled(workflow: dict) -> None:
    # Aborting a run mid-publish could leave a half-created release.
    concurrency = workflow.get("concurrency")
    assert isinstance(concurrency, dict), "release workflow needs a concurrency group"
    assert concurrency.get("cancel-in-progress") is False


def test_release_job_has_a_timeout(workflow: dict) -> None:
    assert isinstance(workflow["jobs"]["release"].get("timeout-minutes"), int)


def test_release_step_publishes_from_the_pushed_tag(workflow: dict) -> None:
    runs = _run_steps(workflow)
    assert runs, "the release job has no run: step"
    joined = "\n".join(runs)
    assert "gh release create" in joined, "release must create the GitHub Release"
    # Auto-generated notes from merged PRs/commits, and refuse to publish a tag
    # that was never pushed.
    assert "--generate-notes" in joined
    assert "--verify-tag" in joined
    # The tag flows through the env var, not a shell interpolation of the ref.
    assert "$GITHUB_REF_NAME" in joined


def test_run_steps_have_no_untrusted_interpolation(workflow: dict) -> None:
    for run in _run_steps(workflow):
        assert not _UNTRUSTED.search(run), f"untrusted github context in: {run!r}"
        assert not _UNTRUSTED_INPUTS.search(run), f"raw inputs.* in run step: {run!r}"


def test_any_action_is_sha_pinned(workflow: dict) -> None:
    # None today (the job shells out to the preinstalled `gh` CLI), but if a
    # `uses:` step is ever added it must be pinned to a full SHA, matching ci.yml.
    for step in _steps(workflow):
        uses = step.get("uses")
        if uses is not None:
            assert _SHA_PINNED.match(uses), f"action must be SHA-pinned, got {uses!r}"
