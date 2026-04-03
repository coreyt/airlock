"""``airlock install-service`` — install Airlock as a systemd user service."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def run(args) -> None:  # noqa: ANN001
    """Install Airlock as a systemd user service."""
    project_root = Path(__file__).resolve().parent.parent.parent
    unit_src = project_root / "deploy" / "airlock.service"
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    unit_dest = unit_dir / "airlock.service"
    dry_run = getattr(args, "dry_run", False)
    service = "airlock"

    def _run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if dry_run:
            print(f"  [dry-run] {' '.join(cmd)}")
            return subprocess.CompletedProcess(cmd, 0)
        return subprocess.run(cmd, check=True, text=True, **kwargs)  # noqa: S603

    # -- sanity checks --------------------------------------------------------
    if not unit_src.exists():
        print(f"ERROR: unit file not found: {unit_src}", file=sys.stderr)
        raise SystemExit(1)

    env_file = project_root / ".env"
    if not env_file.exists():
        print(
            f"ERROR: {env_file} not found — service needs it for API keys",
            file=sys.stderr,
        )
        print("       Run `airlock init` first.", file=sys.stderr)
        raise SystemExit(1)

    if not shutil.which("systemctl"):
        print("ERROR: systemctl not found — systemd is required", file=sys.stderr)
        raise SystemExit(1)

    # -- stop if already running ----------------------------------------------
    active = subprocess.run(
        ["systemctl", "--user", "is-active", "--quiet", service],  # noqa: S603, S607
        capture_output=True,
    )
    if active.returncode == 0:
        print(f"  Stopping running {service}...")
        _run(["systemctl", "--user", "stop", service])

    # -- install unit file ----------------------------------------------------
    if not dry_run:
        unit_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(unit_src, unit_dest)
    print(f"  Installed unit file to {unit_dest}")

    # -- reload, enable, start ------------------------------------------------
    _run(["systemctl", "--user", "daemon-reload"])
    _run(["systemctl", "--user", "enable", service])
    _run(["systemctl", "--user", "start", service])

    if not dry_run:
        import time

        time.sleep(2)
        print()
        subprocess.run(  # noqa: S603, S607
            ["systemctl", "--user", "status", service, "--no-pager", "--lines=5"],
        )

    # -- linger check ---------------------------------------------------------
    try:
        result = subprocess.run(  # noqa: S603, S607
            ["loginctl", "show-user", os.environ.get("USER", ""), "--property=Linger"],
            capture_output=True,
            text=True,
        )
        if "Linger=yes" not in result.stdout:
            print()
            print("  NOTE: Linger is disabled — service only runs while you are logged in.")
            print(f"        To start at boot: loginctl enable-linger {os.environ.get('USER', '')}")
    except FileNotFoundError:
        pass

    print()
    print("  Done. Manage with:")
    print("    systemctl --user status airlock")
    print("    systemctl --user stop airlock")
    print("    systemctl --user restart airlock")
    print("    journalctl --user -u airlock -f")
