"""``airlock config`` — export or import Airlock configurations."""

from __future__ import annotations

import datetime
import platform
import shutil
import sys
import zipfile
from pathlib import Path


def _get_export_filename(out_dir: Path) -> Path:
    """Generate a unique export filename."""
    machine_name = platform.node() or "unknown"
    now = datetime.datetime.now()
    date_str = now.strftime("%Y-%m-%d")

    base_name = f"airlock-configs-{machine_name}-{date_str}"

    # Find a unique increment ID
    increment = 1
    while True:
        zip_path = out_dir / f"{base_name}-{increment}.zip"
        if not zip_path.exists():
            return zip_path
        increment += 1


def _get_backup_filename(target_file: Path) -> Path:
    """Generate a unique backup filename for a file that is about to be overwritten."""
    now = datetime.datetime.now()
    date_str = now.strftime("%Y-%m-%d")

    base_backup_name = f"{target_file.name}.backup-{date_str}"

    increment = 1
    while True:
        suffix = f"-{increment}" if increment > 1 else ""
        backup_path = target_file.parent / f"{base_backup_name}{suffix}"
        if not backup_path.exists():
            return backup_path
        increment += 1


def run_export(args) -> None:
    """Export Airlock configurations to a zip archive."""
    out_dir = Path(args.dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    zip_path = _get_export_filename(out_dir)

    # Core config files we want to export if they exist
    files_to_export = [
        Path("config.yaml"),
        Path(".env"),
        Path("logs/airlock-knobs.json"),
    ]

    found_files = []
    for f in files_to_export:
        if f.is_file():
            found_files.append(f)

    if not found_files:
        print(
            "No Airlock configuration files found to export (.env, config.yaml, logs/airlock-knobs.json).",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Creating export archive: {zip_path}")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in found_files:
            print(f"  Adding {file_path}")
            # Store them at the root of the zip or preserve directory structure?
            # It's usually better to preserve the relative path so logs/airlock-knobs.json extracts correctly.
            zf.write(file_path, arcname=str(file_path))

    print("Export complete.")


def run_import(args) -> None:
    """Import Airlock configurations from a zip archive."""
    zip_path = Path(args.file).resolve()
    target_dir = Path(args.dir).resolve()

    if not zip_path.is_file():
        print(f"Error: Archive not found: {zip_path}", file=sys.stderr)
        sys.exit(1)

    if not zipfile.is_zipfile(zip_path):
        print(f"Error: Not a valid zip file: {zip_path}", file=sys.stderr)
        sys.exit(1)

    target_dir.mkdir(parents=True, exist_ok=True)

    print(f"Importing from {zip_path} into {target_dir}")

    with zipfile.ZipFile(zip_path, "r") as zf:
        for file_info in zf.infolist():
            if file_info.is_dir():
                continue

            extracted_path = target_dir / file_info.filename

            # Create parent directories if needed (e.g. for logs/airlock-knobs.json)
            extracted_path.parent.mkdir(parents=True, exist_ok=True)

            if extracted_path.exists():
                backup_path = _get_backup_filename(extracted_path)
                print(
                    f"  Backing up existing {file_info.filename} to {backup_path.name}"
                )
                shutil.copy2(extracted_path, backup_path)

            print(f"  Extracting {file_info.filename}")
            with zf.open(file_info) as source, open(extracted_path, "wb") as target:
                shutil.copyfileobj(source, target)

    print("Import complete.")


def run(args) -> None:
    """Dispatch to export or import."""
    if args.config_action == "export":
        run_export(args)
    elif args.config_action == "import":
        run_import(args)
    else:
        # If no subcommand was provided
        print("Error: Missing subcommand 'export' or 'import'.", file=sys.stderr)
        sys.exit(1)
