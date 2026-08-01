"""Guard the Docker dependency installation path against manifest drift."""

from __future__ import annotations

import pathlib

import pytest

from scripts.check_docker_dependencies import check_litellm_version, litellm_requirement


ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_docker_installs_the_project_and_runs_dependency_check():
    dockerfile = (ROOT / "Dockerfile").read_text()

    assert "requirements.txt" not in dockerfile
    assert "pip install --no-cache-dir -e ." in dockerfile
    assert "python scripts/check_docker_dependencies.py" in dockerfile


def test_litellm_requirement_has_validated_floor_and_upper_bound():
    requirement = litellm_requirement()

    assert ">=1.94.1" in str(requirement.specifier)
    assert "<2" in str(requirement.specifier)
    assert check_litellm_version("1.94.1")[0] == "1.94.1"
    with pytest.raises(RuntimeError, match="does not satisfy"):
        check_litellm_version("1.93.0")
