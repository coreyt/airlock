#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Iterable


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_CI_WORKFLOW = "ci.yml"
GH_RUN_FIELDS = "conclusion,headBranch,headSha,status,updatedAt,url"


class ReleaseGateError(RuntimeError):
    pass


@dataclass(frozen=True)
class WorkflowRun:
    conclusion: str
    head_branch: str
    head_sha: str
    status: str
    updated_at: datetime
    url: str


def run_command(
    args: list[str],
    *,
    cwd: pathlib.Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def parse_rfc3339(timestamp: str) -> datetime:
    normalized = timestamp.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def load_runs(payload: str) -> list[WorkflowRun]:
    try:
        raw_runs = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ReleaseGateError(f"failed to parse gh JSON output: {exc}") from exc

    runs: list[WorkflowRun] = []
    for raw in raw_runs:
        try:
            runs.append(
                WorkflowRun(
                    conclusion=raw["conclusion"],
                    head_branch=raw["headBranch"],
                    head_sha=raw["headSha"],
                    status=raw["status"],
                    updated_at=parse_rfc3339(raw["updatedAt"]),
                    url=raw["url"],
                )
            )
        except KeyError as exc:
            raise ReleaseGateError(
                f"gh JSON output missing field: {exc.args[0]}"
            ) from exc
    return runs


def gh_run_list(
    repo: str,
    workflow: str,
    *,
    commit: str | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = run_command,
) -> list[WorkflowRun]:
    args = [
        "gh",
        "run",
        "list",
        "--repo",
        repo,
        "--workflow",
        workflow,
        "--limit",
        "20",
        "--json",
        GH_RUN_FIELDS,
    ]

    try:
        completed = runner(args, cwd=REPO_ROOT)
    except FileNotFoundError as exc:
        raise ReleaseGateError("gh command not found") from exc
    if completed.returncode != 0:
        raise ReleaseGateError(
            f"gh run list failed for workflow {workflow!r}: {completed.stderr.strip()}"
        )

    runs = load_runs(completed.stdout)
    if commit:
        if len(commit) < 7:
            raise ValueError(
                f"commit SHA must be at least 7 characters: got {len(commit)}"
            )
        runs = [r for r in runs if r.head_sha.startswith(commit)]

    return runs


def verify_ci_success(
    repo: str,
    commit: str,
    workflow: str,
    runner: Callable[..., subprocess.CompletedProcess[str]] = run_command,
) -> WorkflowRun:
    runs = gh_run_list(repo, workflow, commit=commit, runner=runner)
    if not runs:
        raise ReleaseGateError(
            f"no runs found for workflow {workflow!r} on commit {commit}"
        )

    # Sort by updated_at descending to get the most recent run
    runs.sort(key=lambda r: r.updated_at, reverse=True)
    latest = runs[0]

    if latest.status != "completed":
        raise ReleaseGateError(
            f"workflow {workflow!r} is still {latest.status} on commit {commit} ({latest.url})"
        )

    if latest.conclusion != "success":
        raise ReleaseGateError(
            f"workflow {workflow!r} completed with status {latest.conclusion!r} on commit {commit} ({latest.url})"
        )

    return latest


def verify_release_gates(
    *,
    repo: str,
    commit: str,
    tag: str,
    ci_workflow: str = DEFAULT_CI_WORKFLOW,
) -> None:
    print(f"verifying release gates for {repo} tag {tag} at commit {commit}")

    ci_run = verify_ci_success(repo, commit, ci_workflow)
    print(f"✓ CI workflow ({ci_workflow}) passed for {commit}: {ci_run.url}")


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify release gates before publishing."
    )
    parser.add_argument(
        "--repo", default=os.environ.get("GITHUB_REPOSITORY"), help="owner/repo"
    )
    parser.add_argument(
        "--commit", default=os.environ.get("GITHUB_SHA"), help="release commit SHA"
    )
    parser.add_argument(
        "--tag", default=os.environ.get("GITHUB_REF_NAME"), help="release tag"
    )
    parser.add_argument(
        "--ci-workflow",
        default=DEFAULT_CI_WORKFLOW,
        help="CI workflow name to validate",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    missing = [
        name
        for name, value in (
            ("repo", args.repo),
            ("commit", args.commit),
            ("tag", args.tag),
        )
        if not value
    ]
    if missing:
        print(
            f"missing required release gate input(s): {', '.join(missing)}",
            file=sys.stderr,
        )
        return 2

    try:
        verify_release_gates(
            repo=args.repo,
            commit=args.commit,
            tag=args.tag,
            ci_workflow=args.ci_workflow,
        )
    except ReleaseGateError as exc:
        print(f"release gate verification failed: {exc}", file=sys.stderr)
        return 1

    print("release gates verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
