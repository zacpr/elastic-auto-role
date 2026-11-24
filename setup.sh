#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "$0")" && pwd)"
UPSTREAM="$ROOT/.docker-elk"
UPSTREAM_URL="https://github.com/deviantony/docker-elk.git"
UPSTREAM_COMMIT="ae21fbe976c1392505befc0b47ae128d1a1436fb"

if [[ -e "$UPSTREAM" ]]; then
    if [[ ! -d "$UPSTREAM/.git" ]]; then
        echo "Error: $UPSTREAM exists but is not an upstream checkout." >&2
        exit 1
    fi
    current_commit="$(git -C "$UPSTREAM" rev-parse --verify HEAD)"
    if [[ "$current_commit" != "$UPSTREAM_COMMIT" ]]; then
        echo "Error: upstream checkout is not at the pinned commit $UPSTREAM_COMMIT." >&2
        exit 1
    fi
    changes="$(git -C "$UPSTREAM" status --porcelain --untracked-files=normal)"
    if [[ -n "$changes" ]]; then
        echo "Error: upstream checkout has local changes; preserve or remove them before continuing." >&2
        exit 1
    fi
    echo "Using the existing pinned upstream checkout."
else
    git clone --filter=blob:none --no-checkout "$UPSTREAM_URL" "$UPSTREAM"
    git -C "$UPSTREAM" checkout --detach "$UPSTREAM_COMMIT"
fi

if [[ ! -e "$ROOT/.env" ]]; then
    (
        umask 077
        python3 - "$ROOT/.env" <<'PY'
import secrets
import sys

with open(sys.argv[1], "x", encoding="utf-8") as env:
    print("ELASTIC_VERSION=9.5.6", file=env)
    print("ELASTIC_PASSWORD=" + secrets.token_urlsafe(24), file=env)
    print("JWT_HMAC_KEY=" + secrets.token_urlsafe(48), file=env)
PY
    )
    echo "Created .env with unique local demo credentials."
else
    echo "Using the existing .env without changing it."
fi
