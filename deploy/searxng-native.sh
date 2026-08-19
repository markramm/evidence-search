#!/usr/bin/env bash
# Run SearXNG natively — no container runtime at all.
#
# Apple's `container` needs macOS 26+, and Docker Desktop runs a Linux VM plus
# an Electron app to proxy search queries. SearXNG is a Flask app whose
# dependencies are pure-Python or ship arm64 wheels, so on a Mac that already
# has Python 3.10+ the honest answer is: just run it.
#
# Installs into deploy/.searxng (source checkout + venv), both gitignored.
#
# Usage: deploy/searxng-native.sh [up|down|status|logs|update]
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$DIR/.searxng"
SRC="$ROOT/src"
VENV="$ROOT/venv"
PORT="${SEARXNG_PORT:-8888}"
PIDFILE="$ROOT/searxng.pid"
LOG="$ROOT/searxng.log"

pick_python() {
  for c in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$c" >/dev/null 2>&1 &&
       "$c" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3,10) else 1)' 2>/dev/null; then
      command -v "$c"; return 0
    fi
  done
  return 1
}

ensure_local_cfg() {
  # A per-install secret, written to a GITIGNORED local override. Generating
  # into the tracked template would commit the key and share it with every
  # clone, which is no secret at all. Lives outside install() so it is repaired
  # even when the venv already exists.
  local local_cfg="$DIR/searxng/settings.local.yml"
  [ -f "$local_cfg" ] && return 0
  local key
  if [ -x "$VENV/bin/python" ]; then
    key="$("$VENV/bin/python" -c 'import secrets;print(secrets.token_hex(32))')"
  else
    key="$(od -An -tx1 -N32 /dev/urandom | tr -d ' \n')"
  fi
  /usr/bin/sed "s|^  secret_key: .*|  secret_key: \"$key\"|" \
    "$DIR/searxng/settings.yml" > "$local_cfg"
  echo "secret:  generated -> ${local_cfg##*/}"
}

install() {
  local py; py="$(pick_python)" || {
    echo "Need Python 3.10+. Try: brew install python@3.13" >&2; exit 1; }
  echo "python:  $py"
  mkdir -p "$ROOT"
  if [ -d "$SRC/.git" ]; then
    echo "source:  updating"; git -C "$SRC" pull --ff-only -q
  else
    echo "source:  cloning searxng"
    git clone --depth 1 -q https://github.com/searxng/searxng.git "$SRC"
  fi
  [ -d "$VENV" ] || "$py" -m venv "$VENV"
  echo "deps:    installing (arm64 wheels; no compiler needed)"
  "$VENV/bin/pip" install -q --upgrade pip
  "$VENV/bin/pip" install -q -r "$SRC/requirements.txt"

}

up() {
  if curl -sf -m 3 "http://127.0.0.1:${PORT}/config" >/dev/null 2>&1; then
    echo "already up: http://127.0.0.1:${PORT}"; return 0
  fi
  [ -d "$VENV" ] && [ -d "$SRC" ] || install
  ensure_local_cfg

  export SEARXNG_SETTINGS_PATH="$DIR/searxng/settings.local.yml"
  export SEARXNG_PORT="$PORT"
  export SEARXNG_BIND_ADDRESS="127.0.0.1"   # localhost only, by design
  # `cd` in a subshell then `$!` captures the subshell, not the server, so a
  # later kill orphans the real process. Launch directly and record ITS pid.
  ( cd "$SRC" && exec nohup "$VENV/bin/python" -m searx.webapp >"$LOG" 2>&1 ) &
  echo $! >"$PIDFILE"

  printf 'starting'
  for _ in $(seq 1 40); do
    if curl -sf -m 3 "http://127.0.0.1:${PORT}/config" >/dev/null 2>&1; then
      echo; echo "ready:   http://127.0.0.1:${PORT}  (pid $(cat "$PIDFILE"))"
      if curl -sf -m 20 "http://127.0.0.1:${PORT}/search?q=test&format=json" >/dev/null 2>&1; then
        echo "json:    enabled"
      else
        echo "json:    DISABLED — add 'json' to search.formats in deploy/searxng/settings.yml" >&2
      fi
      echo; echo "  cascade-search web 'your query'"
      return 0
    fi
    printf '.'; sleep 1
  done
  echo; echo "did not start. Last lines of $LOG:" >&2; tail -15 "$LOG" >&2; exit 1
}

down() {
  local killed=0
  if [ -f "$PIDFILE" ]; then
    kill "$(cat "$PIDFILE")" 2>/dev/null && killed=1
    rm -f "$PIDFILE"
  fi
  # Kill whatever is actually serving the port. A stale or wrong pidfile must
  # not leave a live instance behind reporting itself as stopped.
  local pids; pids="$(pgrep -f "searx.webapp" 2>/dev/null || true)"
  for pid in $pids; do kill "$pid" 2>/dev/null && killed=1; done

  for _ in $(seq 1 10); do
    curl -sf -m 2 "http://127.0.0.1:${PORT}/config" >/dev/null 2>&1 || { 
      [ "$killed" = 1 ] && echo stopped || echo "not running"; return 0; }
    sleep 1
  done
  echo "still responding on ${PORT} — a foreign instance?" >&2; return 1
}

case "${1:-up}" in
  up)     up ;;
  down)   down ;;
  status) curl -sf -m 3 "http://127.0.0.1:${PORT}/config" >/dev/null 2>&1 \
            && echo "up on ${PORT}" || echo "down" ;;
  logs)   tail -f "$LOG" ;;
  update) install; echo "updated — restart with: $0 down && $0 up" ;;
  *)      echo "usage: $0 [up|down|status|logs|update]" >&2; exit 2 ;;
esac
