#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

# Keep runtime data local to this folder on servers by default.
# Override if you want: export ACRFETCHER_DATA_DIR=/path
export ACRFETCHER_DATA_DIR="${ACRFETCHER_DATA_DIR:-$PWD/.acr_data}"

# Force UTF-8
export LANG="en_US.UTF-8"
export LC_ALL="en_US.UTF-8"
export PYTHONUTF8=1

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi

source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install --upgrade -r requirements.txt

# Playwright browser engine (Chromium)
# If your Linux is missing deps, run once as root:
#   sudo ./.venv/bin/python -m playwright install --with-deps chromium
python3 -m playwright install chromium || true

python3 acrFetcher.py
