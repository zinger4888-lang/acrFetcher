#!/usr/bin/env python3
"""Backward-compatible entrypoint.

Keep this file as a thin launcher so existing RUN scripts continue working.
"""

from acrfetcher.main import run_cli


if __name__ == "__main__":
    run_cli()
