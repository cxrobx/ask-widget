#!/usr/bin/env bash
# Build the Obsidian plugin and copy it into a vault's plugin folder.
#   VAULT=~/Documents/CX ./scripts/install-obsidian-plugin.sh
set -euo pipefail

VAULT="${VAULT:-$HOME/Documents/CX}"
PLUGIN_DIR="$(cd "$(dirname "$0")/../integrations/obsidian" && pwd)"
TARGET="$VAULT/.obsidian/plugins/ask-widget"

if [ ! -d "$VAULT/.obsidian" ]; then
  echo "No Obsidian vault at $VAULT (set VAULT=/path/to/vault)." >&2
  exit 1
fi

cd "$PLUGIN_DIR"
if [ ! -d node_modules ]; then
  echo "Installing plugin dependencies…" >&2
  npm ci
fi
npm run build

mkdir -p "$TARGET"
cp manifest.json main.js styles.css "$TARGET/"
echo "Installed to $TARGET"
echo
echo "Next: Obsidian ▸ Settings ▸ Community plugins ▸ enable “Ask Widget”, then"
echo "QUIT AND RELAUNCH Obsidian — Cmd+R does not load a newly enabled plugin."
echo "Then press “Allow vault folder” once in the plugin's settings."
