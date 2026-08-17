"""Versioned safety contracts for the repository's GitHub Actions workflows."""

from __future__ import annotations

from pathlib import Path
import re

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"

PINS = {
    "actions/checkout": ("d23441a48e516b6c34aea4fa41551a30e30af803", "v6"),
    "actions/setup-python": ("ece7cb06caefa5fff74198d8649806c4678c61a1", "v6"),
    "astral-sh/setup-uv": ("cec208311dfd045dd5311c1add060b2062131d57", "v8.0.0"),
    "actions/upload-artifact": ("043fb46d1a93c77aae656e7c1c64a875d1fc6a0a", "v7"),
    "actions/configure-pages": ("983d7736d9b0ae728b81ab479565c72886d7745b", "v5"),
    "actions/upload-pages-artifact": ("56afc609e74202658d3ffba0e8f6dda462b719fa", "v3"),
    "actions/deploy-pages": ("d6db90164ac5ed86f2b6aed7e0febac5b3c0c03e", "v4"),
    "actions/download-artifact": ("3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c", "v8"),
    "pypa/gh-action-pypi-publish": (
        "dc37677b2e1c63e2034f94d8a5b11f265b73ba33",
        "v1.14.2",
    ),
    "softprops/action-gh-release": (
        "3d0d9888cb7fd7b750713d6e236d1fcb99157228",
        "v3.0.2",
    ),
}


def _text(name: str) -> str:
    return (WORKFLOWS / name).read_text()


def _workflow(name: str) -> dict[str, object]:
    # BaseLoader retains YAML 1.2's ``on`` spelling instead of coercing it to
    # Python's legacy YAML-1.1 boolean key.
    return yaml.load(_text(name), Loader=yaml.BaseLoader)


def _assert_immutable_actions(workflow_text: str) -> None:
    """Require every action reference to be immutable and human-auditable."""
    uses_lines = [
        line
        for line in workflow_text.splitlines()
        if re.match(r"\s*(?:-\s+)?uses:", line)
    ]
    assert uses_lines, "workflow must contain at least one action reference"

    for line in uses_lines:
        assert re.fullmatch(
            r"\s*(?:-\s+)?uses:\s+[^\s@]+@[0-9a-f]{40}\s+#\s+\S(?:.*\S)?\s*",
            line,
        ), f"action reference must use a full SHA with a version comment: {line}"


def test_all_actions_are_sha_pinned_with_human_version_comments() -> None:
    workflow_text = "\n".join(
        _text(name) for name in ("ci.yml", "docs.yml", "release.yml")
    )

    for action, (sha, version) in PINS.items():
        assert f"uses: {action}@{sha} # {version}" in workflow_text
    _assert_immutable_actions(workflow_text)


def test_immutable_action_guard_rejects_a_mutable_reference() -> None:
    valid_direct_reference = (
        "steps:\n"
        "  - uses: actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6\n"
    )
    _assert_immutable_actions(valid_direct_reference)

    with pytest.raises(AssertionError):
        _assert_immutable_actions(
            valid_direct_reference + "  - uses: actions/checkout@v6\n"
        )


def test_ci_jobs_are_bounded_independent_and_keep_generated_material_local() -> None:
    ci = _workflow("ci.yml")
    jobs = ci["jobs"]
    assert ci["permissions"] == {"contents": "read"}
    assert ci["concurrency"] == {
        "group": "${{ github.workflow }}-${{ github.ref }}",
        "cancel-in-progress": "true",
    }

    expected_timeouts = {
        "docs": "15",
        "test": "35",
        "lint": "15",
        "docker": "30",
        "security": "20",
    }
    for name, timeout in expected_timeouts.items():
        assert jobs[name]["timeout-minutes"] == timeout
    for name in ("lint", "docker", "security"):
        assert "needs" not in jobs[name]

    text = _text("ci.yml")
    assert 'pytest -m "not live and not docker"' in text
    assert "make test-docker" in text
    for prohibited in (
        "docker push",
        "podman push",
        "pypa/gh-action-pypi-publish",
        "softprops/action-gh-release",
        "workflow_dispatch",
    ):
        assert prohibited not in text
    assert "--junitxml=test-results/junit.xml" in text
    assert "junit_logging=no" in text

    test_steps = jobs["test"]["steps"]
    assert (
        sum("--junitxml=test-results/junit.xml" in str(step) for step in test_steps)
        == 1
    )
    for job_name, job in jobs.items():
        if job_name == "test":
            continue
        job_steps = job["steps"]
        assert all("--junitxml" not in str(step) for step in job_steps)
        assert "test-results/junit.xml" not in str(job_steps)

    safety_step = next(step for step in test_steps if step.get("id") == "junit")
    assert safety_step["if"] == "failure()"
    assert "5242880" in safety_step["run"]
    assert 'echo "upload=true"' in safety_step["run"]
    assert 'echo "upload=false"' in safety_step["run"]

    upload_step = next(
        step
        for step in test_steps
        if step.get("name") == "Upload bounded failure JUnit report"
    )
    assert upload_step["if"] == "failure() && steps.junit.outputs.upload == 'true'"
    assert upload_step["with"] == {
        "name": "test-failure-junit",
        "path": "test-results/junit.xml",
        "retention-days": "1",
    }


def test_docs_permissions_are_deploy_job_local_and_bounded() -> None:
    docs = _workflow("docs.yml")
    jobs = docs["jobs"]
    assert docs["permissions"] == {"contents": "read"}
    assert docs["concurrency"] == {
        "group": "${{ github.workflow }}-${{ github.ref }}",
        "cancel-in-progress": "true",
    }
    assert jobs["build"]["timeout-minutes"] == "20"
    assert jobs["deploy"]["timeout-minutes"] == "10"
    assert "permissions" not in jobs["build"]
    assert jobs["deploy"]["permissions"] == {"pages": "write", "id-token": "write"}


def test_release_permissions_are_least_privilege_and_tests_exclude_docker() -> None:
    release = _workflow("release.yml")
    jobs = release["jobs"]
    assert release["permissions"] == {"contents": "read"}
    assert release["concurrency"] == {
        "group": "${{ github.workflow }}-${{ github.ref }}",
        "cancel-in-progress": "false",
    }
    assert jobs["test"]["timeout-minutes"] == "40"
    assert jobs["check-version"]["timeout-minutes"] == "10"
    assert jobs["build"]["timeout-minutes"] == "15"
    assert jobs["publish"]["timeout-minutes"] == "15"
    assert jobs["github-release"]["timeout-minutes"] == "15"
    assert jobs["publish"]["permissions"] == {"id-token": "write"}
    assert jobs["github-release"]["permissions"] == {"contents": "write"}
    assert 'pytest --tb=short -q -m "not live and not docker"' in _text("release.yml")
