#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "$0")" && pwd)"
cd "$ROOT"
"$ROOT/setup.sh"

down() {
    trap - EXIT INT TERM
    echo
    echo "Shutting down the demo stack..."
    "$ROOT/compose.sh" down
}
trap down EXIT INT TERM

"$ROOT/compose.sh" up -d --build elasticsearch
python3 -I "$ROOT/demo.py" --keep

cat <<'MSG'

The demo stack is still running:
  Elasticsearch: http://localhost:9200
  Mint a token:  python3 demo.py --mint alpha
  Try:           curl -H "Authorization: Bearer $(python3 demo.py --mint alpha)" localhost:9200/_security/_authenticate

MSG
read -r -p "Press Enter to shut the demo down... " _ || true
