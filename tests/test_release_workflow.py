"""Guard tests for the version-bump-triggered release workflow.

``.github/workflows/release.yml`` is pure config: it runs no Python and is
invisible to ``ruff``/``ty``/``pytest`` (which only ever look at ``.py`` files),
so the rest of the suite can never catch a regression in it. These tests parse
the workflow with PyYAML and pin the invariants it exists to hold:

1. **A release is cut by bumping ``[project].version`` and landing it on main.**
   The file must parse, trigger on a push to ``main`` (plus the ``v*`` tag escape
   hatch and a no-input ``workflow_dispatch``), and never on ``pull_request`` /
   ``pull_request_target`` where a fork could publish a release.
2. **One run does both halves, and that is load-bearing.** GitHub will not start
   a workflow run from a ref created by the default ``GITHUB_TOKEN`` (the
   recursive-workflow guard), and the guard is keyed on the *token*, not the
   transport — so a ``POST /git/refs`` is suppressed exactly as a ``git push``
   would be. A split "tagger workflow → tag-triggered publisher" therefore cannot
   work without minting a long-lived credential. ``test_no_secrets_are_used`` and
   ``test_every_gh_token_is_the_default_token`` are what keep that reasoning true:
   the moment a PAT or App token appears, the tag *could* re-trigger and the
   ``v*`` escape hatch would double-release.
3. **Nothing is tagged from a red commit.** The ``main`` ruleset requires
   ``checks (3.12)`` / ``checks (3.13)`` but does *not* require a pull request,
   and it carries an always-bypass actor — so a direct push to ``main`` can skip
   CI. The ``gate`` job calling ``ci.yml`` as a local reusable workflow is the
   only thing standing between such a commit and a published Release.
4. **It stays least-privilege and injection-safe.** Top-level ``contents: read``
   with ``contents: write`` confined to the single job that tags and publishes,
   and pointedly no ``pull-requests`` scope (the workflow opens no PRs, so it can
   never hit the "GitHub Actions is not permitted to create or approve pull
   requests" policy). It never cancels a mid-release run, sets job timeouts, and
   no ``run:`` step interpolates ``github.event.*`` / ``head_ref`` / ``ref`` /
   ``inputs.*``.
5. **The version is parsed, not grepped, and agrees with pyproject.** ``version``
   appears under other tables and ``requires-python`` is close enough to catch a
   careless pattern, so the workflow uses stdlib ``tomllib``; and the version it
   would publish today must satisfy the same strict ``X.Y.Z`` shape the workflow
   enforces, so a bad bump fails in the local Stop hook rather than reddening a
   release run after the merge already landed.

Parsing (rather than substring matching over raw text) is what lets these catch
a smuggled write scope, a trigger swap, a removed CI gate, or a malformed
workflow GitHub would refuse to schedule — all of which a plain ``in`` check
would miss.
"""

import re
import tomllib
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
PYPROJECT = ROOT / "pyproject.toml"

# Attacker-controllable expression contexts that must never be interpolated raw
# into a shell `run:` step (the GitHub Actions script-injection class). A tag name
# is push-access-gated but still belongs in an env var, not the shell — so
# `github.ref`/`github.ref_name` interpolation is flagged here too.
_UNTRUSTED = re.compile(r"\$\{\{[^}]*\bgithub\.(?:event|head_ref|ref)\b")
_UNTRUSTED_INPUTS = re.compile(r"\$\{\{[^}]*\binputs\b")
# Third-party `uses:` must be pinned to a full 40-hex commit SHA. A local
# reusable-workflow path (`./.github/workflows/ci.yml`) is this repo's own file at
# this commit — there is nothing to pin.
_SHA_PINNED = re.compile(r"\S+@[0-9a-f]{40}\b")
_LOCAL_WORKFLOW = re.compile(r"^\./\.github/workflows/[\w.-]+\.ya?ml$")
# The version shape the workflow itself enforces before the value becomes a git
# ref and a `$GITHUB_OUTPUT` line. `[0-9]` not `\d`, because `\d` also matches
# non-ASCII digits; `fullmatch` not `$`, because `$` accepts a trailing newline.
_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
# Writing a git ref from the checkout's credential instead of the REST API would
# re-introduce the split-workflow trap this design exists to avoid.
_GIT_WRITE = re.compile(r"\bgit\s+(?:push|tag)\b")


@pytest.fixture(scope="module")
def workflow() -> dict:
    # A malformed workflow (bad indent, duplicate key, ...) raises here and
    # fails every test — the one failure mode that silently disables releases.
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def raw() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _triggers(workflow: dict) -> dict:
    # PyYAML parses YAML 1.1, so the bare `on:` key deserializes to the boolean
    # True, not the string "on" (the classic Actions gotcha). Accept either.
    on = workflow.get("on", workflow.get(True))
    assert isinstance(on, dict), f"unexpected `on:` shape: {on!r}"
    return on


def _steps(workflow: dict, job: str) -> list[dict]:
    return [s for s in workflow["jobs"][job].get("steps", []) if isinstance(s, dict)]


def _run_steps(workflow: dict) -> list[str]:
    """Every `run:` body in the file, across all jobs."""
    return [
        s["run"]
        for job in workflow["jobs"]
        for s in _steps(workflow, job)
        if "run" in s
    ]


def _uncommented(run: str) -> str:
    """A `run:` body with comment-only lines dropped.

    These workflows carry long explanatory comments *inside* the shell bodies, so
    a bare substring scan for a forbidden command matches the prose describing why
    it is forbidden. Strip comment lines before scanning for commands.
    """
    return "\n".join(
        line for line in run.splitlines() if not line.lstrip().startswith("#")
    )


def test_workflow_is_valid_and_wires_resolve_gate_release(workflow: dict) -> None:
    assert isinstance(workflow, dict)
    assert set(workflow["jobs"]) == {"resolve", "gate", "release"}


def test_triggers_on_a_main_push_with_a_tag_escape_hatch(workflow: dict) -> None:
    on = _triggers(workflow)
    assert set(on) == {"push", "workflow_dispatch"}, (
        f"release must trigger only on push / workflow_dispatch, got {set(on)}"
    )
    push = on["push"]
    assert push.get("branches") == ["main"], "a bump is detected on a main push"
    assert push.get("tags") == ["v*"], "keep the hand-pushed `v*` escape hatch"
    # A `paths:` filter would make detection edge-triggered ("did this push change
    # the version?") instead of level-triggered ("is the declared version tagged
    # yet?"), losing the self-healing property after an evicted run.
    assert "paths" not in push and "paths-ignore" not in push
    # No fork-triggerable event may reach a job that can create tags and Releases.
    assert "pull_request" not in on
    assert "pull_request_target" not in on
    # No dispatch inputs, so there is no `inputs.*` to interpolate anywhere.
    dispatch = on["workflow_dispatch"]
    assert dispatch is None or "inputs" not in dispatch


def test_write_scope_is_confined_to_the_publishing_job(workflow: dict) -> None:
    # Read-only by default; exactly one job elevates, and only to `contents`.
    assert workflow.get("permissions") == {"contents": "read"}
    assert workflow["jobs"]["release"]["permissions"] == {"contents": "write"}
    for name, job in workflow["jobs"].items():
        if name == "release":
            continue
        perms = job.get("permissions")
        assert "write" not in repr(perms).lower(), (
            f"write scope on job {name!r}: {perms!r}"
        )


def test_no_pull_requests_scope_anywhere(raw: str) -> None:
    # The workflow opens no PRs, so it can never hit the "GitHub Actions is not
    # permitted to create or approve pull requests" repo/org policy.
    assert "pull-requests:" not in raw


def test_a_mid_release_run_is_never_cancelled(workflow: dict) -> None:
    # Aborting a run mid-publish could leave a tag with no Release behind it.
    concurrency = workflow.get("concurrency")
    assert isinstance(concurrency, dict), "release workflow needs a concurrency group"
    assert concurrency.get("cancel-in-progress") is False


def test_every_runner_job_has_a_timeout(workflow: dict) -> None:
    # `gate` delegates to ci.yml, which sets its own timeout.
    for name in ("resolve", "release"):
        assert isinstance(workflow["jobs"][name].get("timeout-minutes"), int)


def test_the_release_is_gated_on_ci(workflow: dict) -> None:
    """No tag is minted for a commit whose gates are red.

    `main` requires the two check contexts but not a pull request, and carries an
    always-bypass actor — so a direct push can skip CI. Calling ci.yml as a local
    reusable workflow avoids a `checks: read` scope, a race against a CI run
    starting in the same instant, and a second copy of the check names.
    """
    gate = workflow["jobs"]["gate"]
    assert gate.get("uses") == "./.github/workflows/ci.yml"
    assert gate.get("needs") == "resolve"
    release = workflow["jobs"]["release"]
    assert "gate" in release.get("needs", []), (
        "the release job must not run unless the CI gate passed"
    )
    # Both gate and release must be skipped when there is nothing to release,
    # otherwise every ordinary push to main runs the full matrix twice.
    for name in ("gate", "release"):
        assert "needs.resolve.outputs.release" in workflow["jobs"][name].get("if", "")


def test_the_version_is_parsed_not_grepped(workflow: dict) -> None:
    resolve = "\n".join(s["run"] for s in _steps(workflow, "resolve") if "run" in s)
    assert "tomllib" in resolve, "read [project].version with stdlib tomllib"
    # `version` also appears under other tables and `requires-python` is close
    # enough to catch a careless pattern.
    for tool in ("grep", "sed ", "awk"):
        assert tool not in _uncommented(resolve), (
            f"{tool!r} must not be used to extract the version"
        )
    assert _VERSION.pattern in resolve, (
        "the workflow must reject a version that is not a plain X.Y.Z — the value "
        "becomes a git ref and a $GITHUB_OUTPUT line"
    )


def test_the_tag_is_created_through_the_rest_api(workflow: dict) -> None:
    steps = "\n".join(s["run"] for s in _steps(workflow, "release") if "run" in s)
    # An atomic create-if-absent: a second attempt returns 422, so the
    # `create || confirm` pair can neither double-tag nor be raced.
    assert "--method POST" in steps and "git/refs" in steps
    # Never a PATCH: this can create a tag, never move one.
    assert "--method PATCH" not in steps


def test_the_release_is_published_from_the_resolved_tag(workflow: dict) -> None:
    steps = "\n".join(s["run"] for s in _steps(workflow, "release") if "run" in s)
    assert "gh release create" in steps, "release must create the GitHub Release"
    # Notes auto-generated from the merged PRs since the previous release, and a
    # refusal to publish a tag the previous step failed to create.
    assert "--generate-notes" in steps
    assert "--verify-tag" in steps
    # The tag flows from the resolve job through an env var, never a raw
    # interpolation in the shell.
    assert '"$TAG"' in steps
    assert workflow["jobs"]["release"]["env"]["TAG"] == (
        "${{ needs.resolve.outputs.tag }}"
    )
    # `gh release view` exits 0 for a draft too, so a bare existence check would
    # report a never-published draft as done forever.
    assert "isDraft" in steps


def test_no_run_step_writes_a_git_ref_directly(workflow: dict) -> None:
    # `persist-credentials: false` on checkout only means something if nothing
    # tries to push; and a token-written ref is what the REST API path replaces.
    for run in _run_steps(workflow):
        assert not _GIT_WRITE.search(_uncommented(run)), (
            f"tags are created through the REST API, not git: {run!r}"
        )


def test_no_secrets_are_used(raw: str) -> None:
    """The whole design rests on there being no second identity.

    A PAT or App token would make the tag this run creates able to start another
    run — which is exactly what the `v*` escape hatch assumes cannot happen.
    """
    assert "secrets." not in raw, "release automation must need no repo secrets"


def test_every_gh_token_is_the_default_token(workflow: dict) -> None:
    found = False
    scopes = [workflow, *workflow["jobs"].values()]
    scopes += [s for job in workflow["jobs"] for s in _steps(workflow, job)]
    for scope in scopes:
        token = (scope.get("env") or {}).get("GH_TOKEN")
        if token is not None:
            found = True
            assert token == "${{ github.token }}", f"unexpected GH_TOKEN: {token!r}"
    assert found, "no GH_TOKEN wired — how does `gh` authenticate?"


def test_run_steps_have_no_untrusted_interpolation(workflow: dict) -> None:
    for run in _run_steps(workflow):
        assert not _UNTRUSTED.search(run), f"untrusted github context in: {run!r}"
        assert not _UNTRUSTED_INPUTS.search(run), f"raw inputs.* in run step: {run!r}"


def test_any_action_is_sha_pinned(workflow: dict) -> None:
    for name, job in workflow["jobs"].items():
        for uses in [job.get("uses"), *(s.get("uses") for s in _steps(workflow, name))]:
            if uses is None:
                continue
            assert _SHA_PINNED.match(uses) or _LOCAL_WORKFLOW.match(uses), (
                f"action must be SHA-pinned or a local workflow, got {uses!r}"
            )


def test_pyproject_version_is_releasable() -> None:
    """Cross-source agreement, in the spirit of tests/test_license.py.

    The workflow refuses anything that is not a static, plain ``X.Y.Z``. Pinning
    the same shape here means a bad bump fails in the local Stop hook, not in a
    release run that only starts after the merge has already landed on main.
    """
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]
    assert "version" in project, (
        "release.yml reads a static [project].version; a dynamic version would "
        "drift from the tag it publishes"
    )
    version = project["version"]
    assert isinstance(version, str) and _VERSION.fullmatch(version), (
        f"[project].version must be a plain X.Y.Z string, got {version!r}"
    )
