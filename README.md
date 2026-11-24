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

The same walkthrough is also available as a **web demo**: a single
self-contained HTML file that drives a live cluster from the browser. See
[Web demo](#web-demo).

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

The demo cluster also enables CORS (`http.cors.*` in
`elasticsearch/config/elasticsearch.yml`) so that a browser can call it. That
means *any* web page the browser visits can talk to this cluster while it is
running. That is acceptable for a disposable local demo and nothing else --
never copy those settings into a shared or production cluster.

## Run

Requirements: Git, Python 3, Docker with Compose v2, and internet access.

```bash
./run.sh
```

If your selected Docker context is unavailable, choose a running context, for
example `DOCKER_CONTEXT=default ./run.sh`.

On first run, setup clones [docker-elk](https://github.com/deviantony/docker-elk)
at commit `ae21fbe976c1392505befc0b47ae128d1a1436fb` into `.docker-elk/` and
generates a private `.env` containing fresh demo credentials. Both are ignored
by Git. Setup reuses an existing pinned, clean checkout and never overwrites an
existing `.env`.

The stack listens on port 9200. The script runs the walkthrough and stops its
Compose project when you press Enter; its data volume is retained. Pass
`--keep` to leave the stack running instead:

```bash
./run.sh --keep
```

For manual operation:

```bash
./setup.sh
./compose.sh up -d --build elasticsearch
python3 demo.py             # walkthrough; cleans up afterward
python3 demo.py --keep      # retain demo objects afterward
python3 demo.py --mint alpha
./teardown.sh               # remove the container, network and volume
```

Use `COMPOSE_PROJECT_NAME` to choose a different Compose project name. The
generated `.env` is used for both Compose and token signing. Keep it private.

Run the tests without Docker or network access:

```bash
python3 -m unittest discover -s tests
```

## Web demo

[`site/index.html`](site/index.html) is the same walkthrough as a web page.
It is a single self-contained file -- no build step, no bundler, no CDN -- so
you can open it straight from disk or drop it into any site as-is.

The page is not a mock-up: it drives a **real** Elasticsearch cluster over
the HTTP API. Every role, API key, JWT and access check it shows is created
and verified against the cluster you point it at, and the results stream
into the console panel at the bottom.

```bash
./run.sh          # start the stack and leave it running
# then open site/index.html in a browser
```

Or let the repo do it for you -- this starts the stack if it is not already
running, serves the site over http on `127.0.0.1:8000`, and prints the URL:

```bash
./web.sh
```

Then press **Try to load .env automatically**, **Connect**, **Run demo**.

Ctrl+C stops the web server **and removes the demo stack** -- the container,
its network and its data volume. Pass `--keep` to leave it running instead:

```bash
./web.sh --keep
```

You can also tear the stack down at any time:

```bash
./teardown.sh
```

The page has a **Stop cluster** button that does the same thing from the
browser. It needs the page to be served by `./web.sh`; a page opened straight
from disk cannot remove a Docker container, so it shows you the command to
run instead.

On the page, paste the contents of your `.env` file and press **Connect**:

```bash
cat .env
```

The page needs `ELASTIC_PASSWORD` (to act as the administrator) and
`JWT_HMAC_KEY` (to sign the toy JWTs). Both are used only inside the browser
-- the signing key is held in memory and applied with WebCrypto, or with a
small built-in HMAC-SHA256 implementation when WebCrypto is unavailable.
Nothing is sent anywhere except to the Elasticsearch cluster you connect to.
If you tick *Remember for this browser tab*, the values are kept in
`sessionStorage` and are discarded when the tab closes.

You can also let the page read `.env` itself. Serve the repository over http
and press **Try to load .env automatically**:

```bash
python3 -m http.server 8000
# open http://localhost:8000/site/index.html
```

`./web.sh` does this for you, bound to `127.0.0.1` only.

This is deliberately restricted to `localhost`: serving the repository on a
LAN address would make `.env` -- and therefore the demo credentials --
readable by anyone who can reach the server, so from any other origin the
page refuses to fetch it and asks you to paste the values instead.

To embed the demo in another page:

```html
<iframe src="site/index.html" width="100%" height="900" loading="lazy"></iframe>
```

The page expects the cluster to be reachable at `http://localhost:9200`; both
that address and the watcher callback URL are editable in the connect panel.
The watcher callback has to be an address the *Elasticsearch container* can
use to reach itself.

What the page gives you on top of the terminal walkthrough: a scrollable
step-by-step walkthrough with a step navigator, live JSON payloads, live
access-check results, a panel showing the cluster's current indices and team
roles, and a token minter for poking at the cluster by hand.

The console is docked across the bottom of the window, like devtools. Drag
the handle above it to resize it, press **Expand**/**Collapse** to fold it
away, and **Tall** to give it more room. It expands by itself when the demo
starts, and while it is collapsed it keeps showing the most recent line.

## Upstream attribution

The Compose stack is fetched from
[deviantony/docker-elk](https://github.com/deviantony/docker-elk); the local
Elasticsearch configuration and Dockerfile are adapted from it. Its MIT license
is included in [licenses/docker-elk-MIT.txt](licenses/docker-elk-MIT.txt).
