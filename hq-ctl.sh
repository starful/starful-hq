#!/bin/bash
# Starful HQ launchd helper: on | off | status | log
set -euo pipefail
LABEL=com.starful.hq
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
UID_N="$(id -u)"
DOMAIN="gui/${UID_N}"

case "${1:-status}" in
  on|start|load)
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    launchctl bootstrap "$DOMAIN" "$PLIST"
    launchctl enable "$DOMAIN/$LABEL" 2>/dev/null || true
    launchctl kickstart -k "$DOMAIN/$LABEL"
    echo "Starful HQ on → http://127.0.0.1:8091"
    ;;
  off|stop|unload)
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    # also free the port if something lingered
    pids="$(lsof -iTCP:8091 -sTCP:LISTEN -t 2>/dev/null || true)"
    if [ -n "${pids}" ]; then kill ${pids} 2>/dev/null || true; fi
    echo "Starful HQ off"
    ;;
  status)
    if launchctl print "$DOMAIN/$LABEL" 2>/dev/null | head -5; then
      :
    else
      echo "not loaded"
    fi
    lsof -iTCP:8091 -sTCP:LISTEN 2>/dev/null | head -3 || echo "8091: not listening"
    ;;
  log|logs)
    tail -n "${2:-40}" /opt/work/starful-hq/data/logs/launchd-stdout.log /opt/work/starful-hq/data/logs/launchd-stderr.log 2>/dev/null || true
    ;;
  *)
    echo "usage: $0 on|off|status|log" >&2
    exit 2
    ;;
esac
