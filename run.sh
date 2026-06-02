#!/usr/bin/env bash
# Launch the Ask Widget server from the project venv.
# Usage:  ./run.sh                       # ~/Projects on :8899
#         ./run.sh --folder ~/Projects/my-app --port 8899
#         ./run.sh --allow-root ~/work --folder ~/work/notes
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
  echo "No .venv found. Create it first (PEP 668 requires a venv):" >&2
  echo "  python3 -m venv .venv && source .venv/bin/activate && pip install -e ." >&2
  exit 1
fi

if [ $# -eq 0 ]; then
  set -- --folder "$HOME/Projects" --port 8899
fi

exec .venv/bin/python -m ask_widget "$@"
