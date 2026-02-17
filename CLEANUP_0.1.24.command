#!/bin/zsh
set -e
APP_NAME="acrFetcher"
SUPPORT="$HOME/Library/Application Support/$APP_NAME"
STAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP="$HOME/Desktop/${APP_NAME}_backup_${STAMP}"
mkdir -p "$BACKUP"

echo "Cleanup for ${APP_NAME} 0.1.24"

echo "Support dir: $SUPPORT"

if [ -d "$SUPPORT" ]; then
  echo "Backing up support dir to: $BACKUP"
  cp -R "$SUPPORT" "$BACKUP/" || true

  # Legacy single-session from 0.0.45: ~/Library/Application Support/acrFetcher/acrFetcher.session
  rm -f "$SUPPORT/$APP_NAME.session" || true

  # Legacy browser profile (shared). New version uses per-account profiles/.
  # Uncomment if you want to reset browser login state:
  # rm -rf "$SUPPORT/browser_profile" || true

  echo "Done. (Backup kept on Desktop.)"
else
  echo "Nothing to clean (support dir not found)."
fi

read "?Press Enter to close..."
