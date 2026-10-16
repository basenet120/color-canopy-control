#!/usr/bin/env bash
# Keep a Claude Code Remote Control session running on the Mac mini, so you can drive it from any device
# (claude.ai/code, the Claude phone app, or the Claude desktop app elsewhere) while everything runs on this Mac.
#
#   bash scripts/macmini_remote_control.sh install     # start now, and again at every login; restart if it exits
#   bash scripts/macmini_remote_control.sh status
#   bash scripts/macmini_remote_control.sh uninstall
#
# It installs a per-user LaunchAgent that runs `claude remote-control` (server mode) in this repo, restarting it
# when it exits (e.g. after a long network outage). It never changes permissions: approval prompts still come to
# whichever device you're using. Docs: https://code.claude.com/docs/en/remote-control
set -euo pipefail

LABEL="com.colorcanopy.claude-remote-control"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/claude-remote-control.log"
REPO="$(cd "$(dirname "$0")/.." && pwd)"

case "${1:-status}" in
install)
  CLAUDE="$(command -v claude || true)"
  [ -n "$CLAUDE" ] || { echo "Install Claude Code first (https://code.claude.com), then run 'claude' once and sign in."; exit 1; }
  mkdir -p "$(dirname "$PLIST")" "$(dirname "$LOG")"
  cat >"$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>$CLAUDE</string><string>remote-control</string></array>
  <key>WorkingDirectory</key><string>$REPO</string>
  <key>EnvironmentVariables</key>
  <dict><key>PATH</key><string>$(dirname "$CLAUDE"):/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
EOF
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$PLIST"
  echo "Installed. Remote Control runs from $REPO and restarts at login. Log: $LOG"
  echo "Open claude.ai/code (or the Claude app → Code) and look for this Mac's session (green dot = online)."
  ;;
status)
  launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null | grep -E "state =|pid =|last exit" || echo "Not installed."
  [ -f "$LOG" ] && tail -n 5 "$LOG"
  ;;
uninstall)
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"
  echo "Removed."
  ;;
*) echo "usage: $0 install|status|uninstall"; exit 2 ;;
esac
