#!/usr/bin/env python3

from __future__ import annotations

import argparse
import pathlib
import sys
import re

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib


def read_toml(path: pathlib.Path) -> dict:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def read_file(path: pathlib.Path) -> str:
    with path.open("r", encoding="utf-8") as handle:
        return handle.read()


def editable_lock_version(repo_root: pathlib.Path) -> str | None:
    """Return the version of Airlock's editable package entry in ``uv.lock``."""
    lock = read_toml(repo_root / "uv.lock")
    matches = [
        package
        for package in lock.get("package", [])
        if package.get("name") == "airlock-llm"
        and package.get("source") == {"editable": "."}
    ]
    if len(matches) != 1:
        return None
    return matches[0].get("version")


def version_mismatches(repo_root: pathlib.Path, tag: str | None = None) -> list[str]:
    """Return every canonical version or optional tag disagreement."""
    pyproject = read_toml(repo_root / "pyproject.toml")
    pyproject_version = pyproject["project"]["version"]

    init_content = read_file(repo_root / "airlock" / "__init__.py")
    init_match = re.search(r'^__version__ = "([^"]*)"', init_content, re.MULTILINE)
    init_version = init_match.group(1) if init_match else None

    tracing_content = read_file(repo_root / "airlock" / "callbacks" / "tracing.py")
    tracing_match = re.search(
        r'trace\.get_tracer\("airlock", "([^"]*)"\)', tracing_content
    )
    tracing_version = tracing_match.group(1) if tracing_match else None
    lock_version = editable_lock_version(repo_root)

    mismatches: list[str] = []
    for source, version in (
        ("__init__.py", init_version),
        ("tracing.py", tracing_version),
        ("uv.lock editable airlock-llm", lock_version),
    ):
        if pyproject_version != version:
            mismatches.append(
                f"version mismatch: pyproject.toml={pyproject_version} "
                f"{source}={version}"
            )

    if tag and tag != f"v{pyproject_version}":
        mismatches.append(
            f"tag/version mismatch: tag={tag} expected=v{pyproject_version}"
        )
    return mismatches


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify that Airlock package versions stay aligned across files."
    )
    parser.add_argument(
        "--tag",
        help="Optional release tag to validate against, e.g. v0.1.0",
    )
    args = parser.parse_args()

    repo_root = pathlib.Path(__file__).resolve().parent.parent
    mismatches = version_mismatches(repo_root, args.tag)
    if mismatches:
        print("\n".join(mismatches), file=sys.stderr)
        return 1

    pyproject_version = read_toml(repo_root / "pyproject.toml")["project"]["version"]
    print(f"version check passed: {pyproject_version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
