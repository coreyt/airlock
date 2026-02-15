"""``airlock init`` — scaffold a working Airlock configuration."""

from __future__ import annotations

import importlib.resources
import sys
from pathlib import Path


_TEMPLATES = {
    "config.yaml": "config.yaml",
    "dot_env": ".env",
}


def _load_template(name: str) -> str:
    """Read a bundled template file by name."""
    ref = importlib.resources.files("airlock.cli.templates").joinpath(name)
    return ref.read_text(encoding="utf-8")


def run(args) -> None:
    """Execute the init command."""
    target = Path(args.dir).resolve()

    if not target.is_dir():
        print(f"Error: directory does not exist: {target}", file=sys.stderr)
        sys.exit(1)

    force: bool = getattr(args, "force", False)
    results: list[tuple[str, str]] = []  # (filename, disposition)

    # --- Write template files ---
    for template_name, output_name in _TEMPLATES.items():
        dest = target / output_name
        if dest.exists() and not force:
            results.append((output_name, "skipped"))
        else:
            disposition = "overwritten" if dest.exists() else "created"
            dest.write_text(_load_template(template_name), encoding="utf-8")
            results.append((output_name, disposition))

    # --- Create logs directory ---
    logs_dir = target / "logs"
    if logs_dir.is_dir():
        results.append(("logs/", "skipped"))
    else:
        logs_dir.mkdir(parents=True, exist_ok=True)
        results.append(("logs/", "created"))

    # --- Print summary ---
    print()
    print("  Airlock initialized:")
    print()
    for name, disposition in results:
        marker = "+" if disposition in ("created", "overwritten") else "-"
        print(f"    {marker} {name:<16} {disposition}")
    print()
    print("  Next steps:")
    print("    1. Edit .env with your API keys")
    print("    2. Review config.yaml")
    print("    3. Run: airlock start")
    print()
