#!/usr/bin/env bash
# Run Obscura natively as an optional local CDP render backend for ungated
# JS-only pages (Issue #26).
#
# Explicit non-goal: we NEVER pass `--stealth` or anti-detection flags.
# The browser tier's ethical boundary stands: headless is supposed to be
# detectable, `--headed` plus a human is the path through a live challenge,
# and `ALLOWED_HOSTS` governs every challenge-bearing page.
#
# Installs into deploy/.obscura (gitignored).
#
# Usage: deploy/obscura.sh [up|down|status|logs|install]
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$DIR/.obscura"
BIN="$ROOT/bin/obscura"
PORT="${OBSCURA_PORT:-9222}"
PIDFILE="$ROOT/obscura.pid"
LOG="$ROOT/obscura.log"

asset_name() {
  local os arch
  os="$(uname -s)"
  arch="$(uname -m)"
  case "${os}:${arch}" in
    Darwin:arm64|Darwin:aarch64) echo "obscura-aarch64-macos.tar.gz" ;;
    Darwin:x86_64)               echo "obscura-x86_64-macos.tar.gz" ;;
    Linux:x86_64)                echo "obscura-x86_64-linux.tar.gz" ;;
    Linux:aarch64|Linux:arm64)   echo "obscura-aarch64-linux.tar.gz" ;;
    *)
      echo "Unsupported platform ${os}/${arch} for precompiled Obscura release." >&2
      return 1
      ;;
  esac
}

install_bin() {
  local asset url tmp
  asset="$(asset_name)"
  url="https://github.com/h4ckf0r0day/obscura/releases/latest/download/${asset}"
  mkdir -p "$ROOT/bin"
  tmp="$ROOT/${asset}"
  echo "downloading: $url"
  curl -fsSL "$url" -o "$tmp"
  tar -xzf "$tmp" -C "$ROOT/bin"
  rm -f "$tmp"
  if [ ! -f "$BIN" ]; then
    local found
    found="$(find "$ROOT/bin" -name obscura -type f | head -n 1 || true)"
    if [ -n "$found" ] && [ "$found" != "$BIN" ]; then
      mv "$found" "$BIN"
    fi
  fi
  chmod +x "$BIN"
  echo "installed:   $BIN"
}

probe_cdp() {
  curl -sf -m 2 "http://127.0.0.1:${PORT}/json/version" >/dev/null 2>&1 || \
  curl -sf -m 2 "http://127.0.0.1:${PORT}/" >/dev/null 2>&1 || \
  nc -z 127.0.0.1 "$PORT" >/dev/null 2>&1
}

up() {
  if probe_cdp; then
    echo "already up: ws://127.0.0.1:${PORT}"
    echo "  export EVIDENCE_BROWSER_CDP=ws://127.0.0.1:${PORT}"
    return 0
  fi
  [ -x "$BIN" ] || install_bin

  # Intentionally launched WITHOUT --stealth (see Issue #26 explicit non-goal).
  ( cd "$ROOT" && exec nohup "$BIN" serve --port "$PORT" >"$LOG" 2>&1 ) &
  echo $! >"$PIDFILE"

  printf 'starting obscura'
  for _ in $(seq 1 25); do
    if probe_cdp; then
      echo
      echo "ready:   ws://127.0.0.1:${PORT}  (pid $(cat "$PIDFILE"))"
      echo "  export EVIDENCE_BROWSER_CDP=ws://127.0.0.1:${PORT}"
      return 0
    fi
    printf '.'; sleep 0.3
  done
  echo; echo "did not start. Last lines of $LOG:" >&2; tail -20 "$LOG" >&2; exit 1
}

down() {
  local killed=0
  if [ -f "$PIDFILE" ]; then
    kill "$(cat "$PIDFILE")" 2>/dev/null && killed=1 || true
    rm -f "$PIDFILE"
  fi
  local pids; pids="$(pgrep -f "obscura serve" 2>/dev/null || true)"
  for pid in $pids; do kill "$pid" 2>/dev/null && killed=1 || true; done
  [ "$killed" = 1 ] && echo "stopped" || echo "not running"
}

case "${1:-up}" in
  up)      up ;;
  down)    down ;;
  status)  probe_cdp && echo "up on ws://127.0.0.1:${PORT}" || echo "down" ;;
  logs)    tail -f "$LOG" ;;
  install) install_bin ;;
  *)       echo "usage: $0 [up|down|status|logs|install]" >&2; exit 2 ;;
esac
