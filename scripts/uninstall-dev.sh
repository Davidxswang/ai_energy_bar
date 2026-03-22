#!/usr/bin/env bash
set -euo pipefail

UUID="ai-energy-bar@example.com"
TARGET_DIR="$HOME/.local/share/gnome-shell/extensions/$UUID"
MARKER_FILE=".ai-energy-bar-managed"

if command -v gnome-extensions >/dev/null 2>&1; then
  gnome-extensions disable "$UUID" >/dev/null 2>&1 || true
fi

if command -v gsettings >/dev/null 2>&1; then
  CURRENT_EXTENSIONS="$(gsettings get org.gnome.shell enabled-extensions)"
  if [[ "$CURRENT_EXTENSIONS" == *"'$UUID'"* ]]; then
    UPDATED_EXTENSIONS="${CURRENT_EXTENSIONS//, '$UUID'/}"
    UPDATED_EXTENSIONS="${UPDATED_EXTENSIONS//'$UUID', /}"
    UPDATED_EXTENSIONS="${UPDATED_EXTENSIONS//'$UUID'/}"
    gsettings set org.gnome.shell enabled-extensions "$UPDATED_EXTENSIONS"
  fi
fi

if [[ -L "$TARGET_DIR" ]]; then
  rm "$TARGET_DIR"
  echo "Removed extension symlink:"
  echo "  $TARGET_DIR"
elif [[ -f "$TARGET_DIR/$MARKER_FILE" ]]; then
  rm -rf "$TARGET_DIR"
  echo "Removed managed extension directory:"
  echo "  $TARGET_DIR"
elif [[ -e "$TARGET_DIR" ]]; then
  echo "Refusing to remove unmanaged extension path:"
  echo "  $TARGET_DIR" >&2
  exit 1
else
  echo "Nothing to remove:"
  echo "  $TARGET_DIR"
fi

cat <<EOF

If GNOME still shows the widget, reload the shell session:
  1. Log out and back in on Wayland, or
  2. Restart GNOME Shell with Alt+F2, then r, on X11.
EOF
