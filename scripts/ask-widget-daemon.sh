#!/usr/bin/env bash
# Run the Ask Widget service headlessly, with no GUI app open.
#
# The native launcher ADOPTS any healthy service already on the port and only
# terminates a server it spawned itself, so this daemon and the app coexist:
# whichever starts first owns the port, and opening the app just attaches to
# this one. When the app owns the port (it started before this daemon), we wait
# rather than fight for the bind, and take over once it quits.
set -uo pipefail

PORT="${ASK_WIDGET_PORT:-8899}"
FOLDER="${ASK_WIDGET_FOLDER:-$HOME/Projects}"
ROOTS_FILE="$HOME/.config/ask-widget/allow-roots"
APP_SERVER="/Applications/Ask Widget.app/Contents/Resources/Server/ask-widget-server"
REPO="${ASK_WIDGET_REPO:-$HOME/Projects/ask-widget}"

# Same allow-roots file the launcher reads, so both routes trust the same folders.
ARGS=(--folder "$FOLDER" --port "$PORT")
if [ -r "$ROOTS_FILE" ]; then
  while IFS= read -r line; do
    value="${line#"${line%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    case "$value" in ""|\#*) continue ;; esac
    ARGS+=(--allow-root "${value/#\~/$HOME}")
  done < "$ROOTS_FILE"
fi

# Is a compatible service already answering? (Another daemon, or the GUI app.)
service_is_up() {
  curl --fail --silent --max-time 1 "http://127.0.0.1:$PORT/health" 2>/dev/null \
    | grep -q '"service": *"ask-widget"'
}

while service_is_up; do
  # The app (or another copy) owns the port. Idle until it lets go.
  sleep 5
done

if [ -x "$APP_SERVER" ]; then
  exec "$APP_SERVER" "${ARGS[@]}"
fi

# Development fallback: the checkout's venv, so a dev machine without an
# installed bundle still gets a daemon.
if [ -x "$REPO/.venv/bin/python" ]; then
  cd "$REPO"
  export PYTHONPATH="$REPO/src${PYTHONPATH:+:$PYTHONPATH}"
  exec "$REPO/.venv/bin/python" -m ask_widget "${ARGS[@]}"
fi

echo "No Ask Widget service found. Install the app or build the checkout." >&2
exit 1
