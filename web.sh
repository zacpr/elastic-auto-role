#!/usr/bin/env bash
# Serve the web demo over http so the page can read .env from localhost.
#
#   ./web.sh            start the stack, serve the site, tear everything down on exit
#   ./web.sh --keep     leave the Elasticsearch container running when you stop
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "$0")" && pwd)"
cd "$ROOT"
PORT="${WEB_PORT:-8000}"
KEEP=0
for arg in "$@"; do
    case "$arg" in
        --keep) KEEP=1 ;;
        -h|--help)
            sed -n '2,6p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) echo "Unknown option: $arg" >&2; exit 2 ;;
    esac
done

"$ROOT/setup.sh"

# Refuse to start if the port is taken: otherwise the stack would come up and
# then immediately be torn down by the trap when the server fails to bind.
if curl -sS -o /dev/null --max-time 2 "http://127.0.0.1:${PORT}/" 2>/dev/null; then
    echo "Error: port ${PORT} is already in use. Stop that server, or set WEB_PORT to another port." >&2
    exit 1
fi

# Elasticsearch answers 401 without credentials, so probe for *any* HTTP
# response rather than a 2xx one.
es_up() { curl -sS -o /dev/null --max-time 5 "http://localhost:9200" 2>/dev/null; }

STARTED_BY_US=0
if ! es_up; then
    echo "Starting the demo stack..."
    "$ROOT/compose.sh" up -d --build elasticsearch
    STARTED_BY_US=1
    echo "Waiting for Elasticsearch to accept requests..."
    for _ in $(seq 1 60); do
        es_up && break
        sleep 2
    done
fi

SERVER_PID=""
cleanup() {
    trap - EXIT INT TERM
    [[ -n "$SERVER_PID" ]] && kill "$SERVER_PID" 2>/dev/null || true
    echo
    if [[ "$KEEP" == "1" ]]; then
        echo "Leaving the demo stack running (--keep)."
        echo "  Elasticsearch: http://localhost:9200"
        echo "  Stop it later with: ./teardown.sh"
    elif [[ "$STARTED_BY_US" == "1" ]]; then
        echo "Removing the demo stack (container, network and volume)..."
        "$ROOT/teardown.sh"
    else
        echo "The demo stack was already running before this script started;"
        echo "leaving it alone. Stop it with: ./teardown.sh"
    fi
}
trap cleanup EXIT INT TERM

URL="http://localhost:${PORT}/site/index.html"

cat <<MSG

  Web demo: ${URL}

  1. Press "Try to load .env automatically"   (served from localhost only)
  2. Press "Connect"
  3. Press "Run demo"

  Ctrl+C stops the web server and removes the demo container.
  Use ./web.sh --keep to leave it running.

MSG

# Best-effort: open a browser if the desktop provides one.
if command -v xdg-open >/dev/null 2>&1; then
    (sleep 1 && xdg-open "$URL" >/dev/null 2>&1 &) || true
fi

# Run the server in the background and wait for it. With `wait`, a signal is
# handled straight away -- a foreground child would make bash defer the trap
# until that child finished. serve.py also exposes POST /_teardown so the page
# can remove the stack itself.
python3 "$ROOT/serve.py" "$PORT" &
SERVER_PID=$!
wait "$SERVER_PID"
