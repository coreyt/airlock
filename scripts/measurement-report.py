#!/usr/bin/env python3
"""Entrypoint for the deterministic measurement-window JSONL report."""

from pathlib import Path
import sys

# Running ``python scripts/measurement-report.py`` otherwise puts only
# ``scripts/`` on sys.path and may silently import an older editable install.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from airlock.measurement_report import main


if __name__ == "__main__":
    raise SystemExit(main())
