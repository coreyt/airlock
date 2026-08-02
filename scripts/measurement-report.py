#!/usr/bin/env python3
"""Entrypoint for the deterministic measurement-window JSONL report."""

from airlock.measurement_report import main


if __name__ == "__main__":
    raise SystemExit(main())
