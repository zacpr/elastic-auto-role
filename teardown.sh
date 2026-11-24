#!/usr/bin/env bash
# Remove the demo stack completely: the Elasticsearch container, its network and
# its named volume. Safe to run when the stack is already down.
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "$0")" && pwd)"
cd "$ROOT"

if [[ ! -f "$ROOT/.docker-elk/docker-compose.yml" ]]; then
    echo "Nothing to tear down (no upstream checkout)."
    exit 0
fi

# --volumes drops the named elasticsearch volume, --remove-orphans clears any
# container left behind by a service that no longer exists.
"$ROOT/compose.sh" down --volumes --remove-orphans

echo
echo "Demo stack removed (container, network and volume)."
