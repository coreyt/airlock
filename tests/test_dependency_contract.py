"""Guard the single-source, locked dependency contract across local and CI paths."""

from __future__ import annotations

import pathlib

from scripts.check_docker_dependencies import project_requirements


ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_uv_is_the_only_checked_in_dependency_authority() -> None:
    assert (ROOT / "pyproject.toml").is_file()
    assert (ROOT / "uv.lock").is_file()
    assert not (ROOT / "requirements.txt").exists()


def test_every_runtime_manifest_requirement_is_checked_by_docker() -> None:
    requirements = project_requirements()

    assert requirements
    assert {requirement.name for requirement in requirements} >= {
        "litellm",
        "presidio-analyzer",
        "presidio-anonymizer",
        "python-dotenv",
        "pyyaml",
        "textual",
    }


def test_ci_and_local_sync_paths_are_lockfile_enforced() -> None:
    paths = [
        ".github/workflows/ci.yml",
        ".github/workflows/release.yml",
        "Dockerfile",
        "Makefile",
        "scripts/setup.sh",
        "scripts/setup-dev.sh",
        "scripts/preflight.sh",
        ".claude/hooks/session-start.sh",
    ]

    for relative_path in paths:
        assert "uv sync --locked" in (ROOT / relative_path).read_text(), relative_path


def test_ci_and_preflight_exercise_the_complete_locked_extra_set() -> None:
    for relative_path in (
        ".github/workflows/ci.yml",
        ".github/workflows/release.yml",
        "scripts/preflight.sh",
    ):
        assert "uv sync --locked --all-extras" in (ROOT / relative_path).read_text()


def test_shared_tool_and_model_pins_are_consumed_by_ci_and_local_scripts() -> None:
    pins = (ROOT / "scripts/tool-versions.sh").read_text()
    for variable in (
        "AIRLOCK_RUFF_VERSION",
        "AIRLOCK_MYPY_VERSION",
        "AIRLOCK_PIP_AUDIT_VERSION",
        "AIRLOCK_YAMLLINT_VERSION",
        "AIRLOCK_SPACY_MODEL_VERSION",
        "AIRLOCK_SPACY_MODEL_URL",
    ):
        assert variable in pins

    for relative_path in (
        ".github/workflows/ci.yml",
        "Dockerfile",
        "Makefile",
        "scripts/preflight.sh",
        "scripts/setup.sh",
        "scripts/setup-dev.sh",
    ):
        assert "tool-versions.sh" in (ROOT / relative_path).read_text(), relative_path


def test_managed_spacy_installs_use_the_pinned_wheel_not_spacys_mutable_catalog() -> (
    None
):
    for relative_path in (
        "Dockerfile",
        "Makefile",
        "scripts/setup.sh",
        "scripts/setup-dev.sh",
        ".claude/hooks/ensure-spacy-after-sync.sh",
    ):
        contents = (ROOT / relative_path).read_text()
        assert "AIRLOCK_SPACY_MODEL_URL" in contents, relative_path
        assert "python -m spacy download" not in contents, relative_path
