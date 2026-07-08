"""Guard tests for the release-please automation.

``.github/workflows/release.yml`` and the two release-please control files
(``release-please-config.json`` / ``.release-please-manifest.json``) are pure
config: they run no Python and are invisible to ``ruff``/``ty``/``pytest``
(which only ever look at ``.py`` files), so the rest of the suite can never
catch a regression in them. These tests parse the workflow with PyYAML and the
control files with ``json``/``tomllib`` and pin the invariants they exist to
hold:

1. **The workflow drives release-please safely.** It parses, triggers only on
   push to ``main`` (never the write-privileged ``pull_request_target``), holds
   *exactly* the two write scopes release-please needs (``contents`` +
   ``pull-requests`` — no broader scope), never cancels a mid-release run, sets
   a job timeout, has no ``run:`` shell surface, and SHA-pins the action with a
   ``# vX.Y.Z`` comment (mirroring ``ci.yml``'s hardening).
2. **The three version sources agree.** ``release-please-config.json``'s package
   is a ``python`` package whose ``package-name`` equals ``pyproject``'s
   ``[project].name``; ``.release-please-manifest.json``'s seeded version equals
   ``pyproject``'s ``[project].version``. release-please keeps those two moving
   together after every release, so drift means a broken bootstrap or a hand
   edit. The native ``python`` updater only bumps ``[project].version`` while it
   stays *static* and no ``[tool.poetry]`` table shadows it, so both are pinned.

Parsing (rather than substring matching) is what lets these catch a smuggled
write scope, a trigger swap, an unpinned action, or a manifest/pyproject version
drift that a plain ``in`` check would miss.
"""

import json
import re
import tomllib
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
CONFIG = ROOT / "release-please-config.json"
MANIFEST = ROOT / ".release-please-manifest.json"
PYPROJECT = ROOT / "pyproject.toml"

_ACTION = "googleapis/release-please-action"
# The parsed `uses:` ref must be the action pinned to a full 40-hex commit SHA.
_SHA_PINNED = re.compile(rf"^{re.escape(_ACTION)}@[0-9a-f]{{40}}$")
# The raw `uses:` line must also carry the trailing `# vX.Y.Z` comment PyYAML
# strips out — the human-readable half of the repo's pin+comment convention that
# Dependabot bumps in lockstep with the SHA.
_PINNED_WITH_COMMENT = re.compile(
    rf"uses:\s*{re.escape(_ACTION)}@[0-9a-f]{{40}}\s+#\s*v\d+(?:\.\d+)+"
)
_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


@pytest.fixture(scope="module")
def workflow() -> dict:
    # A malformed workflow (bad indent, duplicate key, ...) raises here and
    # fails every test — the one failure mode that silently disables releases.
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def config() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def pyproject() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def _triggers(workflow: dict) -> dict:
    # PyYAML parses YAML 1.1, so the bare `on:` key deserializes to the boolean
    # True, not the string "on" (the classic Actions gotcha). Accept either.
    on = workflow.get("on", workflow.get(True))
    assert isinstance(on, dict), f"unexpected `on:` shape: {on!r}"
    return on


def _release_step(workflow: dict) -> dict:
    steps = workflow["jobs"]["release-please"]["steps"]
    for step in steps:
        if isinstance(step, dict) and str(step.get("uses", "")).startswith(_ACTION):
            return step
    raise AssertionError(f"no step uses {_ACTION}")


def test_workflow_is_valid_and_has_the_release_job(workflow: dict) -> None:
    assert isinstance(workflow, dict)
    assert "release-please" in workflow["jobs"]


def test_triggers_on_push_to_main_only(workflow: dict) -> None:
    on = _triggers(workflow)
    assert "main" in on["push"]["branches"], "release must run on push to main"
    assert "pull_request_target" not in on, (
        "release must not use the write-privileged pull_request_target trigger"
    )


def test_permissions_are_exactly_the_release_scopes(workflow: dict) -> None:
    # release-please needs write (unlike read-only ci.yml) — but ONLY these two.
    # Exact equality rejects a smuggled id-token/actions/write-all scope.
    assert workflow.get("permissions") == {
        "contents": "write",
        "pull-requests": "write",
    }


def test_a_mid_release_run_is_never_cancelled(workflow: dict) -> None:
    # Deliberate deviation from ci.yml's cancel-in-progress: true — aborting a
    # run mid-tag / mid-release could leave a half-published release.
    concurrency = workflow.get("concurrency")
    assert isinstance(concurrency, dict), "release workflow needs a concurrency group"
    assert concurrency.get("cancel-in-progress") is False


def test_release_job_has_a_timeout(workflow: dict) -> None:
    job = workflow["jobs"]["release-please"]
    assert isinstance(job.get("timeout-minutes"), int)


def test_action_is_sha_pinned_with_version_comment(workflow: dict) -> None:
    ref = _release_step(workflow)["uses"]
    assert _SHA_PINNED.match(ref), f"action must be pinned to a full SHA, got {ref!r}"
    # The `# vX.Y.Z` comment lives only in the raw text (PyYAML drops comments).
    raw = WORKFLOW.read_text(encoding="utf-8")
    assert _PINNED_WITH_COMMENT.search(raw), (
        "the pinned `uses:` line must carry a `# vX.Y.Z` comment (Dependabot pin)"
    )


def test_token_is_the_default_github_token(workflow: dict) -> None:
    # No PAT/custom secret name smuggled in; the default token needs no setup.
    assert _release_step(workflow)["with"]["token"] == "${{ secrets.GITHUB_TOKEN }}"


def test_config_and_manifest_inputs_resolve_to_files(workflow: dict) -> None:
    with_ = _release_step(workflow)["with"]
    assert (ROOT / with_["config-file"]).is_file()
    assert (ROOT / with_["manifest-file"]).is_file()


def test_workflow_has_no_shell_run_surface(workflow: dict) -> None:
    # release-please talks to the API via octokit — no checkout/run step, hence
    # no github.event.*/head_ref/inputs.* script-injection surface to guard.
    steps = workflow["jobs"]["release-please"]["steps"]
    assert not [s for s in steps if isinstance(s, dict) and "run" in s]


def test_config_declares_a_python_root_package(config: dict, pyproject: dict) -> None:
    pkg = config["packages"]["."]
    assert pkg["release-type"] == "python"
    assert pkg["include-component-in-tag"] is False
    # package-name and pyproject [project].name are two copies of one fact.
    assert pkg["package-name"] == pyproject["project"]["name"]


def test_bootstrap_sha_is_a_full_sha(config: dict) -> None:
    # Optional (self-deactivates after the first release PR), but if present it
    # must be a full 40-hex SHA — release-please rejects a truncated one.
    sha = config.get("bootstrap-sha")
    if sha is not None:
        assert _FULL_SHA.match(sha), f"bootstrap-sha must be a full SHA, got {sha!r}"


def test_manifest_agrees_with_config_and_pyproject(
    config: dict, manifest: dict, pyproject: dict
) -> None:
    # The manifest tracks exactly the configured packages...
    assert set(manifest) == set(config["packages"])
    # ...and its seeded version is the current pyproject version (they move
    # together after every release, so drift means a broken bootstrap).
    assert manifest["."] == pyproject["project"]["version"]


def test_pyproject_version_stays_static(pyproject: dict) -> None:
    # If [project].version ever becomes `dynamic`, the native python updater logs
    # "dynamic version found ... Skipping update" and silently stops bumping it.
    project = pyproject["project"]
    assert "version" not in project.get("dynamic", [])
    assert isinstance(project.get("version"), str)


def test_pyproject_has_no_poetry_table(pyproject: dict) -> None:
    # A [tool.poetry] table would shadow [project] as the updater's version target.
    assert "poetry" not in pyproject.get("tool", {})
