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

    # 1. pyproject.toml
    pyproject = read_toml(repo_root / "pyproject.toml")
    pyproject_version = pyproject["project"]["version"]

    # 2. airlock/__init__.py
    init_content = read_file(repo_root / "airlock" / "__init__.py")
    init_match = re.search(r'^__version__ = "([^"]*)"', init_content, re.MULTILINE)
    init_version = init_match.group(1) if init_match else None

    # 3. airlock/callbacks/tracing.py
    tracing_content = read_file(repo_root / "airlock" / "callbacks" / "tracing.py")
    tracing_match = re.search(
        r'trace\.get_tracer\("airlock", "([^"]*)"\)', tracing_content
    )
    tracing_version = tracing_match.group(1) if tracing_match else None

    rc = 0

    if pyproject_version != init_version:
        print(
            f"version mismatch: pyproject.toml={pyproject_version} __init__.py={init_version}",
            file=sys.stderr,
        )
        rc = 1

    if pyproject_version != tracing_version:
        print(
            f"version mismatch: pyproject.toml={pyproject_version} tracing.py={tracing_version}",
            file=sys.stderr,
        )
        rc = 1

    if args.tag:
        expected_tag = f"v{pyproject_version}"
        if args.tag != expected_tag:
            print(
                f"tag/version mismatch: tag={args.tag} expected={expected_tag}",
                file=sys.stderr,
            )
            rc = 1

    if rc == 0:
        print(f"version check passed: {pyproject_version}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
