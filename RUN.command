#!/bin/zsh
set -e
cd "$(dirname "$0")"

# Force UTF-8
export LANG="en_US.UTF-8"
export LC_ALL="en_US.UTF-8"
export PYTHONUTF8=1

# Resize window to a fixed size (best-effort) so the UI looks consistent.
# Table width is fixed; this just helps avoid awkward wrapping.
printf '\e[8;32;124t' || true
osascript <<'APPLESCRIPT' >/dev/null 2>&1 || true
tell application "Terminal"
  try
    set number of columns of front window to 124
    set number of rows of front window to 32
  end try
end tell
APPLESCRIPT

xattr -dr com.apple.quarantine . 2>/dev/null || true

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi

source .venv/bin/activate
python3 -m pip install --upgrade pip >/dev/null
python3 -m pip install --upgrade -r requirements.txt >/dev/null
# Install chromium engine for playwright (first run only)
python3 -m playwright install chromium >/dev/null 2>&1 || true

python3 acrFetcher.py
