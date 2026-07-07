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
table form ``license = {text = "MIT"}`` — only the modern SPDX string passes.
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
_COPYRIGHT = re.compile(r"Copyright \(c\) \d{4} Daryl Lim")


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


def test_license_names_the_copyright_holder(license_text: str) -> None:
    # Year-agnostic (matches any 4-digit year) but pins holder and the standard
    # `Copyright (c) <year> <holder>` shape.
    assert _COPYRIGHT.search(license_text), (
        "LICENSE missing a `Copyright (c) <year> Daryl Lim` line"
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


def test_readme_documents_the_license() -> None:
    readme = README.read_text(encoding="utf-8")
    assert re.search(r"\bMIT\b", readme), "README should name the MIT license"
    assert "](LICENSE)" in readme, "README should link the LICENSE file"
