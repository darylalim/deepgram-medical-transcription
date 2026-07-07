"""Integration tests for the Claude Code hook scripts in `.claude/hooks/`.

These shell out to the real scripts with the same event JSON Claude Code feeds
them on stdin, asserting exit codes and side effects. They are skipped when the
toolchain the hooks depend on (bash/jq/uv) is not on PATH, so the pure-Python
suite still runs in a bare environment.
"""

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOKS_DIR = REPO_ROOT / ".claude" / "hooks"
BASH = shutil.which("bash") or "bash"

# The hooks orchestrate these tools; without them the scripts can't run.
_MISSING = [tool for tool in ("bash", "jq", "uv") if shutil.which(tool) is None]
pytestmark = pytest.mark.skipif(
    bool(_MISSING), reason=f"hook tests require {_MISSING} on PATH"
)


def run_hook(script, event, *, env_overrides=None):
    """Run a hook script with `event` as stdin JSON; return the finished process.

    Invoked via an absolute `bash` (matching how settings.json now launches the
    hooks, and so a PATH override in env_overrides can't hide the interpreter),
    with CLAUDE_PROJECT_DIR pinned to the repo root.
    """
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(REPO_ROOT)}
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        [BASH, str(HOOKS_DIR / script)],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env=env,
    )


def _edit_event(path):
    """An Edit/Write PostToolUse/PreToolUse payload targeting `path`."""
    return {"tool_input": {"file_path": str(path)}}


@pytest.fixture
def repo_py_file():
    """Factory for uniquely-named .py files *under the repo root* (where
    py-checks.sh's in-project guard requires them), cleaned up afterwards."""
    created = []

    def _make(content):
        fd, name = tempfile.mkstemp(dir=REPO_ROOT, suffix=".py")
        os.close(fd)
        path = Path(name)
        path.write_text(content)
        created.append(path)
        return path

    yield _make
    for path in created:
        path.unlink(missing_ok=True)


class TestBlockSecrets:
    @pytest.mark.parametrize(
        "relpath",
        [
            ".env",
            ".env.local",
            ".env.production",
            ".streamlit/secrets.toml",
            ".ENV",  # case-insensitive: same inode as .env on a case-insensitive FS
            ".Env",
            ".streamlit/SECRETS.TOML",
            ".secrets.toml",  # Dynaconf secrets file
            "secrets.toml",  # bare secrets.toml under the root (nested match)
            ".envrc",  # direnv
        ],
    )
    def test_denies_secret_files(self, relpath):
        proc = run_hook("block-secrets.sh", _edit_event(REPO_ROOT / relpath))
        assert proc.returncode == 2
        assert "Blocked" in proc.stderr

    @pytest.mark.parametrize("path", ["secrets.toml", ".env", ".ENVRC"])
    def test_denies_bare_relative_secret_paths(self, path):
        # A relative path with no directory component must still be caught.
        proc = run_hook("block-secrets.sh", {"tool_input": {"file_path": path}})
        assert proc.returncode == 2

    @pytest.mark.parametrize(
        "relpath",
        [
            ".env.example",  # tracked template
            ".ENV.EXAMPLE",  # template, case-insensitively
            "nova/config.py",
            "README.md",
            ".streamlit/config.toml",  # non-secret toml must stay editable
        ],
    )
    def test_allows_non_secret_files(self, relpath):
        proc = run_hook("block-secrets.sh", _edit_event(REPO_ROOT / relpath))
        assert proc.returncode == 0

    def test_missing_file_path_is_noop(self):
        proc = run_hook("block-secrets.sh", {"tool_input": {}})
        assert proc.returncode == 0

    def test_fails_closed_when_jq_missing(self):
        # A security guard must not silently allow edits if it can't parse the
        # event. With jq off PATH the hook refuses (exit 2) rather than no-op.
        proc = run_hook(
            "block-secrets.sh",
            _edit_event(REPO_ROOT / ".env"),
            env_overrides={"PATH": "/var/empty"},
        )
        assert proc.returncode == 2
        assert "jq not found" in proc.stderr


class TestPyChecks:
    def test_reformats_edited_python(self, repo_py_file):
        # ruff format normalizes the messy spacing; the file is clean so the
        # hook exits 0.
        path = repo_py_file("x=1\ny   =   2\n")
        proc = run_hook("py-checks.sh", _edit_event(path))

        assert proc.returncode == 0
        rewritten = path.read_text()
        assert "y = 2" in rewritten
        assert "y   =   2" not in rewritten

    def test_type_error_surfaces_exit_2(self, repo_py_file):
        # Returning an int where -> str is declared: ty fails, and the hook
        # reports it back via exit 2.
        path = repo_py_file("def f(a: int) -> str:\n    return a\n")
        proc = run_hook("py-checks.sh", _edit_event(path))

        assert proc.returncode == 2
        assert "py-checks: ty type issues" in proc.stderr

    def test_unfixable_lint_surfaces_exit_2(self, repo_py_file):
        # B006 (mutable default arg) is reported by ruff, is NOT autofixable, and
        # ty ignores it — so it isolates the lint-surfacing path.
        path = repo_py_file("def f(a=[]):\n    return a\n")
        proc = run_hook("py-checks.sh", _edit_event(path))

        assert proc.returncode == 2
        assert "unresolved ruff lint" in proc.stderr

    @pytest.mark.parametrize("suffix", ["/", "//"])
    def test_trailing_slash_project_dir_still_runs(self, repo_py_file, suffix):
        # Trailing slash(es) on CLAUDE_PROJECT_DIR must not silently disable the
        # in-project glob (all are stripped before matching) — one slash or two.
        path = repo_py_file("x=1\ny   =   2\n")
        proc = run_hook(
            "py-checks.sh",
            _edit_event(path),
            env_overrides={"CLAUDE_PROJECT_DIR": str(REPO_ROOT) + suffix},
        )

        assert proc.returncode == 0
        assert "y = 2" in path.read_text()

    def test_non_python_file_is_noop(self):
        proc = run_hook("py-checks.sh", _edit_event(REPO_ROOT / "README.md"))
        assert proc.returncode == 0

    def test_out_of_project_file_is_noop(self):
        # A .py outside the project root must not be touched.
        proc = run_hook("py-checks.sh", _edit_event("/tmp/elsewhere.py"))
        assert proc.returncode == 0

    def test_deleted_file_is_noop(self, repo_py_file):
        # File removed between the edit and the hook firing -> the [ -f ] guard
        # short-circuits to exit 0.
        path = repo_py_file("x = 1\n")
        path.unlink()
        proc = run_hook("py-checks.sh", _edit_event(path))
        assert proc.returncode == 0

    def test_missing_file_path_is_noop(self):
        proc = run_hook("py-checks.sh", {"tool_input": {}})
        assert proc.returncode == 0


class TestPytestOnStop:
    def test_loop_guard_exits_without_running(self):
        # stop_hook_active=true means we already blocked once this stop cycle;
        # the hook must bail immediately rather than re-running the suite.
        # (The real-run path is intentionally not exercised: it calls
        # `uv run pytest`, which would re-collect this module -> recursion.)
        proc = run_hook("pytest-on-stop.sh", {"stop_hook_active": True})

        assert proc.returncode == 0
        assert proc.stdout == ""
        assert proc.stderr == ""
