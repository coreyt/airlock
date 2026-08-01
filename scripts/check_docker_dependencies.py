#!/usr/bin/env python3
"""Fail a container build if installed LiteLLM violates Airlock's manifest."""

from __future__ import annotations

import importlib.metadata
import pathlib
import sys
import tomllib

from packaging.requirements import Requirement


ROOT = pathlib.Path(__file__).resolve().parents[1]


def litellm_requirement(project_file: pathlib.Path = ROOT / "pyproject.toml") -> Requirement:
    """Return Airlock's declared LiteLLM requirement from its sole manifest."""
    project = tomllib.loads(project_file.read_text())["project"]
    for dependency in project["dependencies"]:
        requirement = Requirement(dependency)
        if requirement.name.lower() == "litellm":
            return requirement
    raise RuntimeError("pyproject.toml does not declare a LiteLLM dependency")


def check_litellm_version(installed_version: str | None = None) -> tuple[str, Requirement]:
    """Verify the installed distribution satisfies the project's specifier."""
    requirement = litellm_requirement()
    version = installed_version or importlib.metadata.version("litellm")
    if version not in requirement.specifier:
        raise RuntimeError(
            f"installed LiteLLM {version} does not satisfy Airlock requirement {requirement}"
        )
    return version, requirement


def main() -> int:
    version, requirement = check_litellm_version()
    print(f"LiteLLM {version} satisfies {requirement}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, importlib.metadata.PackageNotFoundError) as error:
        print(f"Docker dependency check failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
