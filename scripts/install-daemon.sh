#!/usr/bin/env bash
# Install (or remove) the LaunchAgent that keeps the Ask Widget service running
# in the background, so the Obsidian plugin and any browser page work whether or
# not the app is open.
#
#   ./scripts/install-daemon.sh            # install + start
#   ./scripts/install-daemon.sh --uninstall
#   ./scripts/install-daemon.sh --status
set -euo pipefail

LABEL="com.cx.ask-widget.server"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
SCRIPT="$HOME/.local/bin/ask-widget-daemon"
SOURCE="$(cd "$(dirname "$0")" && pwd)/ask-widget-daemon.sh"
LOG="$HOME/Library/Logs/ask-widget-daemon.log"
DOMAIN="gui/$(id -u)"

case "${1:-install}" in
  --status)
    launchctl print "$DOMAIN/$LABEL" 2>/dev/null | sed -n '1,12p' || echo "Not loaded."
    curl --fail --silent --max-time 2 http://127.0.0.1:8899/health || echo "Service not answering on 8899."
    exit 0
    ;;
  --uninstall)
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
    echo "Removed $LABEL. The app still starts its own service when opened."
    exit 0
    ;;
esac

mkdir -p "$(dirname "$SCRIPT")" "$(dirname "$PLIST")" "$(dirname "$LOG")"
# Copy rather than symlink: the agent must keep working while the checkout is
# being rebuilt or moved.
cp "$SOURCE" "$SCRIPT"
chmod +x "$SCRIPT"

cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$SCRIPT</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>ThrottleInterval</key>
    <integer>10</integer>
    <key>ProcessType</key>
    <string>Background</string>
    <key>StandardOutPath</key>
    <string>$LOG</string>
    <key>StandardErrorPath</key>
    <string>$LOG</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
        <key>PYTHONUNBUFFERED</key>
        <string>1</string>
    </dict>
</dict>
</plist>
PLIST_EOF

plutil -lint "$PLIST" >/dev/null
launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
launchctl bootstrap "$DOMAIN" "$PLIST"
launchctl kickstart "$DOMAIN/$LABEL"

for _ in $(seq 1 40); do
  if curl --fail --silent --max-time 1 http://127.0.0.1:8899/health >/dev/null; then
    echo "✓ Ask Widget service is running in the background (label $LABEL)."
    echo "  Log: $LOG"
    echo "  Stop it with: ./scripts/install-daemon.sh --uninstall"
    exit 0
  fi
  sleep 0.25
done

echo "The agent was installed but the service did not answer on 8899." >&2
echo "Check $LOG" >&2
exit 1
