# JWT-driven self-service Elasticsearch roles

This demo shows how a team can create an index and gain read access without
being able to create roles:

1. An administrator configures a JWT realm, a dynamic role-mapping template,
   and a watcher with a separate role-management API key.
2. The team's API key can create and write only `app-<team>-*` indices; it
   cannot read data or create roles.
3. The watcher detects a new index and creates `team_<team>_reader`. A JWT
   whose `team` claim matches can then read that team's indices.

The walkthrough prints the JWT, mapping, role JSON, and the results of access
checks for two teams.

## Safety

**Use only on a dedicated, disposable demo cluster.** The walkthrough deletes
all `app-*` indices, invalidates `demo-*` API keys, and removes its named
watch, mapping, and team roles before running; without `--keep`, it cleans up
again afterward. Do not point it at a shared or production cluster.

This is not a production security configuration. It uses HTTP, a shared HMAC
key, a trial license, and prints a usable bearer token. The watcher's API key
has cluster-wide role put/get privileges: its privileges are **not** limited
to the role names the watch generates. The signing key is stored in the built
Docker image; do not publish or share that image. Upstream Compose publishes
ports 9200 and 9300 on all interfaces, so run only on a trusted host/network.

## Run

Requirements: Git, Python 3, Docker with Compose v2, and internet access.

```bash
./run.sh
```

On first run, setup clones [docker-elk](https://github.com/deviantony/docker-elk)
at commit `ae21fbe976c1392505befc0b47ae128d1a1436fb` into `.docker-elk/` and
generates a private `.env` containing fresh demo credentials. Both are ignored
by Git. Setup reuses an existing pinned, clean checkout and never overwrites an
existing `.env`.

The stack listens on port 9200. The script runs the walkthrough and stops its
Compose project when you press Enter; its data volume is retained.

For manual operation:

```bash
./setup.sh
./compose.sh up -d --build elasticsearch
python3 demo.py             # walkthrough; cleans up afterward
python3 demo.py --keep      # retain demo objects afterward
python3 demo.py --mint alpha
./compose.sh down
```

Use `COMPOSE_PROJECT_NAME` to choose a different Compose project name. The
generated `.env` is used for both Compose and token signing. Keep it private.

Run the packaging tests without Docker or network access:

```bash
python3 -m unittest discover -s tests
```

## Upstream attribution

The Compose stack is fetched from
[deviantony/docker-elk](https://github.com/deviantony/docker-elk); the local
Elasticsearch configuration and Dockerfile are adapted from it. Its MIT license
is included in [licenses/docker-elk-MIT.txt](licenses/docker-elk-MIT.txt).
