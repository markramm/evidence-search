#!/usr/bin/env bash
# Start/stop the local SearXNG instance on whatever container runtime is here,
# preferring the lightest one available.
#
# Docker Desktop runs a Linux VM plus an Electron UI in the background. That is
# a lot of machine to proxy search queries, so it is the LAST choice, not the
# first. Preference order:
#
#   container  Apple's native runtime. Lightest by far, but macOS 26+ only.
#   podman     Daemonless; `podman machine` is a plain VM with no desktop app.
#   colima     Lima-backed Docker-compatible VM, no Electron.
#   docker     Works, and is heaviest. Used only if nothing else is present.
#
# Usage: deploy/searxng.sh [up|down|status|logs]
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NAME=cascade-searxng
IMAGE=docker.io/searxng/searxng:latest
PORT=8888

runtime() {
  if command -v container >/dev/null 2>&1 && container system status >/dev/null 2>&1; then
    echo container
  elif command -v podman >/dev/null 2>&1 && podman info >/dev/null 2>&1; then
    echo podman
  elif command -v colima >/dev/null 2>&1 && colima status >/dev/null 2>&1; then
    echo docker
  elif command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    echo docker
  else
    echo none
  fi
}

up() {
  local rt; rt="$(runtime)"
  if [ "$rt" = none ]; then
    cat >&2 <<'MSG'
No container runtime is running. Pick the lightest one your Mac supports:

  macOS 26+     brew install container && container system start
  any macOS     brew install podman && podman machine init && podman machine start
  any macOS     brew install colima && colima start
  last resort   open -a "Docker Desktop"

Then: deploy/searxng.sh up
MSG
    exit 1
  fi

  echo "runtime: $rt"
  "$rt" rm -f "$NAME" >/dev/null 2>&1 || true
  "$rt" run -d --name "$NAME" --restart unless-stopped \
    -p "127.0.0.1:${PORT}:8080" \
    -v "${DIR}/searxng:/etc/searxng:rw" \
    -e "SEARXNG_BASE_URL=http://127.0.0.1:${PORT}/" \
    -e "SEARXNG_LIMITER=false" \
    "$IMAGE" >/dev/null

  printf 'waiting for the instance'
  for _ in $(seq 1 45); do
    if curl -sf "http://127.0.0.1:${PORT}/config" >/dev/null 2>&1; then
      echo; echo "ready: http://127.0.0.1:${PORT}"
      if curl -sf "http://127.0.0.1:${PORT}/search?q=test&format=json" >/dev/null 2>&1; then
        echo "json format: enabled"
      else
        echo "json format: DISABLED — add 'json' to search.formats in deploy/searxng/settings.yml" >&2
      fi
      echo; echo "  cascade-search web 'your query'"
      return 0
    fi
    printf '.'; sleep 1
  done
  echo; echo "did not come up; check: deploy/searxng.sh logs" >&2; exit 1
}

case "${1:-up}" in
  up)     up ;;
  down)   rt="$(runtime)"; [ "$rt" = none ] || "$rt" rm -f "$NAME" >/dev/null 2>&1 || true; echo stopped ;;
  status) rt="$(runtime)"; echo "runtime: $rt"
          curl -sf "http://127.0.0.1:${PORT}/config" >/dev/null 2>&1 \
            && echo "instance: up on ${PORT}" || echo "instance: down" ;;
  logs)   rt="$(runtime)"; "$rt" logs -f "$NAME" ;;
  *)      echo "usage: $0 [up|down|status|logs]" >&2; exit 2 ;;
esac
