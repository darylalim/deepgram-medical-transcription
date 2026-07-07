"""Guard tests for the project's MIT license declaration.

The license lives in three static, non-Python places that ``ruff``/``ty``/
``pytest`` never inspect, so the rest of the suite can never catch a regression
in them:

1. the ``LICENSE`` file — the text GitHub's license scanner reads to show the
   repo's MIT badge;
2. the ``[project]`` license metadata in ``pyproject.toml`` — the PEP 639 SPDX
   id (``license = "MIT"``) plus the ``license-files`` pointer;
3. the README's License section.

These tests pin that all three exist and agree the project is MIT: the individual
checks assert the ``LICENSE`` body is MIT *and* that ``pyproject`` declares the
``MIT`` SPDX id, so swapping one without the other (an Apache ``LICENSE`` under a
``MIT`` SPDX id, or vice versa) trips a check. Parsing ``pyproject`` with
``tomllib`` (rather than substring-matching the raw text) also rejects the legacy
table form ``license = {text = "MIT"}`` — only the modern SPDX string passes. The
``LICENSE`` copyright holder is likewise tied to ``[project].authors`` so the two
copies of that name can't drift.
"""

import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LICENSE = ROOT / "LICENSE"
PYPROJECT = ROOT / "pyproject.toml"
README = ROOT / "README.md"

# Stable MIT fingerprints: the SPDX title, the permission grant, and the all-caps
# warranty disclaimer. All three are verbatim in every MIT text, so requiring
# them together rejects a different license (or a mangled MIT) while tolerating
# incidental whitespace/wrapping differences.
_MIT_MARKERS = (
    "MIT License",
    "Permission is hereby granted, free of charge",
    'THE SOFTWARE IS PROVIDED "AS IS"',
)
# Captures the holder so `test_copyright_holder_matches_authors` can tie it to
# pyproject; the optional `-YYYY` tolerates a future year range (e.g. 2026-2027)
# rather than hard-failing on it.
_COPYRIGHT = re.compile(r"Copyright \(c\) \d{4}(?:-\d{4})? (?P<holder>.+)")


@pytest.fixture(scope="module")
def license_text() -> str:
    return LICENSE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def pyproject() -> dict:
    # tomllib parses TOML, so the SPDX string vs. legacy table distinction is
    # structural here, not a substring guess.
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def test_license_file_exists() -> None:
    # GitHub's license detector keys off this file at the repo root.
    assert LICENSE.is_file(), "LICENSE file is missing"


@pytest.mark.parametrize("marker", _MIT_MARKERS)
def test_license_body_is_mit(license_text: str, marker: str) -> None:
    assert marker in license_text, f"LICENSE missing MIT marker: {marker!r}"


def test_license_has_a_copyright_line(license_text: str) -> None:
    # Validates the `Copyright (c) <year|range> <holder>` shape; the holder name
    # itself is tied to pyproject by test_copyright_holder_matches_authors.
    assert _COPYRIGHT.search(license_text), (
        "LICENSE missing a `Copyright (c) <year> <holder>` line"
    )


def test_copyright_holder_matches_authors(license_text: str, pyproject: dict) -> None:
    # The LICENSE copyright holder and pyproject [project].authors are two copies
    # of one fact — tie them so they can't silently drift apart.
    name = pyproject["project"]["authors"][0]["name"]
    match = _COPYRIGHT.search(license_text)
    assert match is not None, "LICENSE has no copyright line"
    assert name in match.group("holder"), (
        f"pyproject author {name!r} is not the LICENSE copyright holder "
        f"({match.group('holder')!r})"
    )


def test_pyproject_declares_mit_spdx(pyproject: dict) -> None:
    project = pyproject["project"]
    # Equality with the plain string rejects the legacy `{text = "MIT"}` table.
    assert project.get("license") == "MIT", (
        f"pyproject [project].license must be the SPDX string 'MIT', "
        f"got {project.get('license')!r}"
    )
    assert "LICENSE" in project.get("license-files", []), (
        "pyproject [project].license-files must point at LICENSE"
    )


def test_license_files_all_resolve(pyproject: dict) -> None:
    # Every path pyproject points at must exist — catches a typo'd pointer
    # (LICENCE, LICENSE.txt) that the membership check above would miss.
    license_files = pyproject["project"].get("license-files", [])
    assert license_files, "pyproject [project].license-files is empty"
    for rel in license_files:
        assert (ROOT / rel).is_file(), (
            f"license-files entry {rel!r} does not resolve to a file"
        )


def test_readme_documents_the_license() -> None:
    readme = README.read_text(encoding="utf-8")
    assert re.search(r"\bMIT\b", readme), "README should name the MIT license"
    assert "](LICENSE)" in readme, "README should link the LICENSE file"
