#!/usr/bin/env bash
set -euo pipefail

UUID="ai-energy-bar@example.com"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE_DIR="$ROOT_DIR/extension"
TARGET_DIR="$HOME/.local/share/gnome-shell/extensions/$UUID"
MARKER_FILE=".ai-energy-bar-managed"
REPO_ROOT_HINT_FILE=".repo-root"

mkdir -p "$(dirname "$TARGET_DIR")"

if [[ -L "$TARGET_DIR" ]]; then
  rm "$TARGET_DIR"
elif [[ -e "$TARGET_DIR" && ! -f "$TARGET_DIR/$MARKER_FILE" ]]; then
  echo "Refusing to replace unmanaged extension directory: $TARGET_DIR" >&2
  exit 1
fi

rm -rf "$TARGET_DIR"
mkdir -p "$TARGET_DIR"

cp "$SOURCE_DIR/extension.js" "$TARGET_DIR/extension.js"
cp "$SOURCE_DIR/metadata.json" "$TARGET_DIR/metadata.json"
cp "$SOURCE_DIR/probe.py" "$TARGET_DIR/probe.py"
cp "$SOURCE_DIR/stylesheet.css" "$TARGET_DIR/stylesheet.css"
touch "$TARGET_DIR/$MARKER_FILE"
printf '%s\n' "$ROOT_DIR" > "$TARGET_DIR/$REPO_ROOT_HINT_FILE"

if command -v gsettings >/dev/null 2>&1; then
  CURRENT_EXTENSIONS="$(gsettings get org.gnome.shell enabled-extensions)"
  if [[ "$CURRENT_EXTENSIONS" != *"'$UUID'"* ]]; then
    if [[ "$CURRENT_EXTENSIONS" == "@as []" || "$CURRENT_EXTENSIONS" == "[]" ]]; then
      UPDATED_EXTENSIONS="['$UUID']"
    else
      UPDATED_EXTENSIONS="${CURRENT_EXTENSIONS%]}"
      UPDATED_EXTENSIONS="$UPDATED_EXTENSIONS, '$UUID']"
    fi
    gsettings set org.gnome.shell enabled-extensions "$UPDATED_EXTENSIONS"
  fi
fi

if command -v gnome-extensions >/dev/null 2>&1; then
  gnome-extensions enable "$UUID" >/dev/null 2>&1 || true
fi

cat <<EOF
Installed extension to:
  $TARGET_DIR

If the widget does not appear immediately:
  1. Log out and log back in on Wayland, or
  2. Restart GNOME Shell with Alt+F2, then r, on X11.
EOF
