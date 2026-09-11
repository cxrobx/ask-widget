#!/usr/bin/env bash
# Launch the Onyx server from a self-healing project venv.
# Usage:  ./run.sh                       # ~/Projects on :8899
#         ./run.sh --folder ~/Projects/my-app --port 8899
#         ./run.sh --allow-root ~/work --folder ~/work/notes
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="${ONYX_VENV:-$PROJECT_DIR/.venv}"
STAMP="$VENV_DIR/.ask-widget-installed"
RUNTIME_LOCK="$PROJECT_DIR/requirements-runtime.lock"
cd "$PROJECT_DIR"

if [ ! -x "$VENV_DIR/bin/python" ]; then
  if ! SYSTEM_PYTHON="$(command -v python3)"; then
    echo "Onyx needs Python 3.11 or newer, but python3 was not found." >&2
    exit 1
  fi
  echo "Setting up Onyx's local runtime…" >&2
  "$SYSTEM_PYTHON" -m venv "$VENV_DIR"
fi

# Reinstall only when the environment is incomplete or the locked dependency
# manifest changed. Source is loaded directly through PYTHONPATH below.
if [ ! -f "$STAMP" ] || [ "$RUNTIME_LOCK" -nt "$STAMP" ] || \
   ! PYTHONPATH="$PROJECT_DIR/src" "$VENV_DIR/bin/python" -c \
     'import ask_widget, fastapi, pypdf, uvicorn' 2>/dev/null; then
  echo "Installing Onyx dependencies…" >&2
  if ! "$VENV_DIR/bin/python" -m pip install --disable-pip-version-check \
    --requirement "$RUNTIME_LOCK"; then
    echo "Onyx setup failed. Check your network connection and the pip error above." >&2
    exit 1
  fi
  touch "$STAMP"
fi

if [ $# -eq 0 ]; then
  set -- --folder "$HOME/Projects" --port 8899
fi

export PYTHONPATH="$PROJECT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"
exec "$VENV_DIR/bin/python" -m ask_widget "$@"
