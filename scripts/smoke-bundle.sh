#!/usr/bin/env bash
# Start the frozen service, validate its public contract, then stop it cleanly.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SERVER="$ROOT/launcher/build/Ask Widget.app/Contents/Resources/Server/ask-widget-server"
if [ ! -x "$SERVER" ]; then
  echo "Missing bundled server. Run ./launcher/build-app.sh --no-install first." >&2
  exit 1
fi

SMOKE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/ask-widget-smoke.XXXXXX")"
PORT="$(python3 - <<'PY'
import socket
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
)"
SERVER_PID=""

cleanup() {
  if [ -n "$SERVER_PID" ] && kill -0 "$SERVER_PID" 2>/dev/null; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
  rm -rf "$SMOKE_DIR"
}
trap cleanup EXIT INT TERM

"$SERVER" \
  --folder "$ROOT" \
  --allow-root "$ROOT" \
  --data-dir "$SMOKE_DIR/data" \
  --port "$PORT" >"$SMOKE_DIR/server.log" 2>&1 &
SERVER_PID=$!

for _ in $(seq 1 80); do
  if curl --fail --silent "http://127.0.0.1:$PORT/health" >"$SMOKE_DIR/health.json"; then
    break
  fi
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    cat "$SMOKE_DIR/server.log" >&2
    exit 1
  fi
  sleep 0.25
done

python3 - "$SMOKE_DIR/health.json" <<'PY'
import json, pathlib, sys
payload = json.loads(pathlib.Path(sys.argv[1]).read_text())
assert payload["status"] == "ok", payload
assert payload["service"] == "ask-widget", payload
assert payload["protocol"] == 3, payload
assert payload["version"] == "0.5.0", payload
PY

curl --fail --silent "http://127.0.0.1:$PORT/config" >"$SMOKE_DIR/config.json"
python3 - "$SMOKE_DIR/config.json" <<'PY'
import json, pathlib, sys
payload = json.loads(pathlib.Path(sys.argv[1]).read_text())
assert payload["version"] == "0.5.0", payload
assert payload["default_folder"], payload
assert payload["provider"] in {"claude", "codex"}, payload
assert payload["model"], payload
PY

curl --fail --silent "http://127.0.0.1:$PORT/api/models" >"$SMOKE_DIR/models.json"
python3 - "$SMOKE_DIR/models.json" <<'PY'
import json, pathlib, sys
payload = json.loads(pathlib.Path(sys.argv[1]).read_text())
assert payload["ok"] is True, payload
assert [provider["id"] for provider in payload["providers"]] == ["claude", "codex"], payload
PY

curl --fail --silent "http://127.0.0.1:$PORT/ask.js" >"$SMOKE_DIR/ask.js"
if grep -q "__ASK_TOKEN__" "$SMOKE_DIR/ask.js"; then
  echo "Bundled ask.js still contains the token placeholder." >&2
  exit 1
fi

echo "✓ Bundled service smoke test passed on port $PORT"
