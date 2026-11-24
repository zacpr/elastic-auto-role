#!/usr/bin/env bash
# Start the demo stack, run the walkthrough, then remove the stack again.
#
#   ./run.sh            tear the stack down (container, network and volume) on exit
#   ./run.sh --keep     leave the stack running when you stop
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "$0")" && pwd)"
cd "$ROOT"
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

down() {
    trap - EXIT INT TERM
    echo
    if [[ "$KEEP" == "1" ]]; then
        echo "Leaving the demo stack running (--keep)."
        echo "  Elasticsearch: http://localhost:9200"
        echo "  Stop it later with: ./teardown.sh"
    else
        echo "Removing the demo stack (container, network and volume)..."
        "$ROOT/teardown.sh"
    fi
}
trap down EXIT INT TERM

"$ROOT/compose.sh" up -d --build elasticsearch
python3 -I "$ROOT/demo.py" --keep

cat <<'MSG'

The demo stack is still running:
  Elasticsearch: http://localhost:9200
  Web demo:      open site/index.html in a browser (paste the contents of .env to connect)
  Mint a token:  python3 demo.py --mint alpha
  Try:           curl -H "Authorization: Bearer $(python3 demo.py --mint alpha)" localhost:9200/_security/_authenticate

MSG
read -r -p "Press Enter to shut the demo down... " _ || true
