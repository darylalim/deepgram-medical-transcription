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

# The hooks orchestrate these tools; without them the scripts can't run.
_MISSING = [tool for tool in ("bash", "jq", "uv") if shutil.which(tool) is None]
pytestmark = pytest.mark.skipif(
    bool(_MISSING), reason=f"hook tests require {_MISSING} on PATH"
)


def run_hook(script, event):
    """Run a hook script with `event` as stdin JSON; return the finished process.

    Invoked via `bash` (not the exec bit) and with CLAUDE_PROJECT_DIR pinned to
    the repo root, mirroring how Claude Code launches hooks.
    """
    return subprocess.run(
        ["bash", str(HOOKS_DIR / script)],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(REPO_ROOT)},
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
        "relpath", [".env", ".env.local", ".env.production", ".streamlit/secrets.toml"]
    )
    def test_denies_secret_files(self, relpath):
        proc = run_hook("block-secrets.sh", _edit_event(REPO_ROOT / relpath))
        assert proc.returncode == 2
        assert "Blocked" in proc.stderr

    @pytest.mark.parametrize("relpath", [".env.example", "nova/config.py", "README.md"])
    def test_allows_non_secret_files(self, relpath):
        # .env.example is the tracked template; ordinary source/docs are fine.
        proc = run_hook("block-secrets.sh", _edit_event(REPO_ROOT / relpath))
        assert proc.returncode == 0

    def test_missing_file_path_is_noop(self):
        proc = run_hook("block-secrets.sh", {"tool_input": {}})
        assert proc.returncode == 0


class TestPyChecks:
    def test_reformats_edited_python(self, repo_py_file):
        # ruff format normalizes the messy spacing; the file is type-correct so
        # ty passes and the hook exits 0.
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
        assert "ty reported type issues" in proc.stderr

    def test_non_python_file_is_noop(self):
        proc = run_hook("py-checks.sh", _edit_event(REPO_ROOT / "README.md"))
        assert proc.returncode == 0

    def test_out_of_project_file_is_noop(self):
        # A .py outside the project root must not be touched.
        proc = run_hook("py-checks.sh", _edit_event("/tmp/elsewhere.py"))
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
