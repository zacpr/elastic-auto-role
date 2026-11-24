#!/usr/bin/env python3
"""
Demo: JWT-driven, self-service Elasticsearch access with *no* human in the loop.

  admin (one time)  : restricted "role-creator" API key + Watcher + JWT role mapping
  app team          : holds ONLY a create-index/write API key scoped to app-<team>-*
  watcher           : spots new app-<team>-* indices, creates role team_<team>_reader
  role mapping      : JWT claim `team` -> role team_<team>_reader, no per-user setup
  app team's JWT    : can now read its own indices, and nothing else

Stdlib only. Run after `./setup.sh` and `./compose.sh up -d --build elasticsearch`:

    python3 demo.py            # full walkthrough (idempotent, cleans up first)
    python3 demo.py --keep     # leave everything in place at the end
    python3 demo.py --mint alpha   # just print a toy JWT for team alpha
"""
import argparse, base64, hashlib, hmac, json, os, ssl, sys, time, urllib.error, urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
ES = os.environ.get("ES_URL", "http://localhost:9200")
WATCH_INTERVAL = os.environ.get("WATCH_INTERVAL", "10s")  # "1m" in real life
ISSUER, AUDIENCE = "local-issuer", "elasticsearch"       # must match elasticsearch.yml
TEAMS = ["alpha", "beta"]
WATCH_ID, MAPPING_ID = "role-per-team", "jwt_team_readers"

# ---------------------------------------------------------------- tiny helpers
def load_env():
    env = {}
    with open(os.path.join(ROOT, ".env")) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k] = v.strip().strip("'\"")
    return env

ENV = load_env()
ELASTIC_AUTH = "Basic " + base64.b64encode(f"elastic:{ENV['ELASTIC_PASSWORD']}".encode()).decode()

def call(method, path, body=None, auth=ELASTIC_AUTH):
    """Returns (status, parsed_json)."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(ES + path, data=data, method=method)
    req.add_header("Authorization", auth)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raw = e.read()
        try: return e.code, json.loads(raw)
        except ValueError: return e.code, {"raw": raw.decode(errors="replace")}

def b64u(b): return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

def mint_jwt(sub, team, ttl=300, key=None, issuer=ISSUER):
    """A toy identity provider: HS256 JWT signed with the shared demo secret."""
    key = (key or ENV["JWT_HMAC_KEY"]).encode()
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    claims = {"iss": issuer, "aud": AUDIENCE, "sub": sub, "team": team,
              "iat": now, "auth_time": now, "exp": now + ttl}
    signing = b64u(json.dumps(header).encode()) + "." + b64u(json.dumps(claims).encode())
    sig = hmac.new(key, signing.encode(), hashlib.sha256).digest()
    return signing + "." + b64u(sig)

def jwt_parts(tok):
    """(header, claims) of a JWT -- for display only."""
    def dec(seg): return json.loads(base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4)))
    head, pay, _ = tok.split(".")
    return dec(head), dec(pay)

def bearer(tok): return "Bearer " + tok
def apikey(encoded): return "ApiKey " + encoded

def show(label, obj, indent=6):
    """Pretty-print a JSON object under the current step."""
    print("   " + label)
    for line in json.dumps(obj, indent=2).splitlines():
        print(" " * indent + line)

def role_template(team):
    """The role the watcher creates for app-<team>-* -- mirrors find_missing below."""
    return {"indices": [{"names": [f"app-{team}-*"],
                         "privileges": ["read", "view_index_metadata"]}],
            "metadata": {"managed_by": "role-per-team-watch"}}

def step(n, title): print(f"\n\033[1;36m== {n}. {title}\033[0m")
def info(msg): print("   " + msg)
def note(msg):
    """A guide line: what this step is simulating and what it proves."""
    print("   \033[2m→ " + msg + "\033[0m")

def expect(label, got, want):
    ok = got == want
    print(f"   {'✔' if ok else '✘'} {label:<62} -> HTTP {got}" + ("" if ok else f"  (expected {want})"))
    if not ok: expect.failed += 1
expect.failed = 0

def check(label, ok, detail=""):
    """Same as expect(), for assertions that aren't HTTP status codes."""
    print(f"   {'✔' if ok else '✘'} {label:<62} {detail}")
    if not ok: expect.failed += 1

def make_key(name, descriptor, expiration="1d"):
    st, r = call("POST", "/_security/api_key",
                 {"name": name, "expiration": expiration, "role_descriptors": {name: descriptor}})
    assert st == 200, r
    return r["encoded"]

# ---------------------------------------------------------------- the demo
def cleanup():
    call("DELETE", f"/_watcher/watch/{WATCH_ID}")
    call("DELETE", f"/_security/role_mapping/{MAPPING_ID}")
    st, r = call("DELETE", "/_security/api_key", {"name": "demo-*"})
    for t in TEAMS + ["gamma"]:
        call("DELETE", f"/_security/role/team_{t}_reader")
    st, r = call("GET", "/_resolve/index/app-*")   # wildcard DELETE is blocked by default
    for i in r.get("indices", []): call("DELETE", "/" + i["name"])

def wait_for_es():
    for _ in range(60):
        try:
            st, r = call("GET", "/_cluster/health?wait_for_status=yellow&timeout=5s")
            if st == 200: return
        except Exception: pass
        time.sleep(2)
    sys.exit("Elasticsearch not reachable at " + ES)

def run(keep):
    wait_for_es()
    cleanup()

    print("\n\033[1mWHAT THIS DEMO SIMULATES\033[0m")
    print("   A team must get read access to its own indices without anyone editing")
    print("   a role by hand. The team cannot create roles; a privileged watcher")
    print("   performs that operation. Three moving parts do it:")
    print("     (a) a JWT   - carries a 'team' claim; the realm turns it into metadata")
    print("     (b) a role mapping with a TEMPLATE - claim 'team' -> role name, resolved at login")
    print("     (c) a watcher - sees new app-<team>-* indices and creates the role body")
    print("   The app team itself only ever holds create_index+write on app-<team>-*.")
    print("   It can create its own index, and that act alone causes it to gain read")
    print("   access - it can never create a role, and never needs an admin.")
    note("watch the two privilege hand-offs: admin sets up (a)(b)(c) once, then the")
    note("team drives everything itself.")

    # ---------------------------------------------------------------- admin
    step(1, "ADMIN: mint a *minimal* API key for the role-creator watcher")
    rc_key = make_key("demo-role-creator", {
        # exactly: create/update/read roles. NOT manage_security, NOT users, NOT api keys.
        "cluster": ["cluster:admin/xpack/security/role/put",
                    "cluster:admin/xpack/security/role/bulk_put",
                    "cluster:admin/xpack/security/role/get"],
        # can only *see* indices matching the app pattern
        "indices": [{"names": ["app-*"], "privileges": ["view_index_metadata"]}]})
    info("cluster: role put/get only | indices: view_index_metadata on app-*")
    show("the key's role descriptor:", {"cluster": ["cluster:admin/xpack/security/role/put",
                                                     "cluster:admin/xpack/security/role/bulk_put",
                                                     "cluster:admin/xpack/security/role/get"],
                                        "indices": [{"names": ["app-*"],
                                                     "privileges": ["view_index_metadata"]}]})
    note("this key can write roles but read no data and manage no users.")
    note("role put/get privileges are cluster-wide, not restricted to team roles.")

    step(2, "ADMIN: JWT role mapping  (claim team=X  ->  role team_X_reader)")
    st, r = call("PUT", f"/_security/role_mapping/{MAPPING_ID}", {
        "enabled": True, "roles": [],
        "role_templates": [{"template": {"source": "team_{{metadata.jwt_claim_team}}_reader"},
                            "format": "string"}],
        "rules": {"all": [{"field": {"realm.name": "jwt1"}},
                          {"field": {"metadata.jwt_claim_team": "*"}}]}})
    info(f"role mapping stored: {st} {r}")
    show("the mapping that turns a claim into a role NAME at login time:", {
        "role_templates": [{"template": {"source": "team_{{metadata.jwt_claim_team}}_reader"},
                            "format": "string"}],
        "rules": {"all": [{"field": {"realm.name": "jwt1"}},
                          {"field": {"metadata.jwt_claim_team": "*"}}]}})
    note("the template is what makes this zero-touch: no user is listed anywhere.")
    note("login resolves the name on the fly, so a new team needs no admin edit.")

    # Show the token the mapping is about to consume: the `team` claim is the whole trick.
    sample = mint_jwt("alice@alpha.example", "alpha", ttl=3600)
    hdr, claims = jwt_parts(sample)
    info("a token as the app team's IdP would issue it (HS256, shared demo secret):")
    print("      " + sample)
    show("decoded header:", hdr)
    show("decoded claims:", claims)
    info(f"mapping reads claims['team'] = {claims['team']!r}  ->  role team_{claims['team']}_reader")
    info("copy it and try:  curl -H \"Authorization: Bearer <token>\" localhost:9200/_security/_authenticate")

    step(3, f"ADMIN: install the role-per-team watch (every {WATCH_INTERVAL})")
    roles_req = lambda: {"scheme": "http", "host": "localhost", "port": 9200, "path": "/_security/role",
                         "headers": {"Authorization": apikey(rc_key)}}
    idx_req = lambda: {"scheme": "http", "host": "localhost", "port": 9200, "path": "/_resolve/index/app-*",
                       "params": {"expand_wildcards": "open"}, "headers": {"Authorization": apikey(rc_key)}}
    # Index names must look like  app-<team>-<anything>.  <team> must be [a-z0-9]+
    find_missing = """
      def out = [:];
      for (def i : ctx.payload.indices.indices) {
        def p = i.name.splitOnToken('-');
        if (p.length >= 3 && p[0] == 'app' && p[1] ==~ /[a-z0-9]+/) {
          def rn = 'team_' + p[1] + '_reader';
          if (!ctx.payload.roles.containsKey(rn)) {
            out[rn] = ['indices': [['names': ['app-' + p[1] + '-*'],
                                    'privileges': ['read', 'view_index_metadata']]],
                       'metadata': ['managed_by': 'role-per-team-watch']];
          }
        }
      }
      return out;"""
    watch = {
        "trigger": {"schedule": {"interval": WATCH_INTERVAL}},
        "input": {"chain": {"inputs": [{"roles": {"http": {"request": roles_req()}}},
                                       {"indices": {"http": {"request": idx_req()}}}]}},
        "condition": {"script": {"lang": "painless",
                                 "source": find_missing.replace("return out;", "return out.size() > 0;")}},
        "transform": {"script": {"lang": "painless",
                                 "source": find_missing.replace("return out;", "return ['body': ['roles': out]];")}},
        "actions": {"bulk_create_roles": {"webhook": {
            "scheme": "http", "host": "localhost", "port": 9200, "method": "post",
            "path": "/_security/role",
            "headers": {"Authorization": apikey(rc_key), "Content-Type": "application/json"},
            "body": "{{#toJson}}ctx.payload.body{{/toJson}}"}}}}
    st, r = call("PUT", f"/_watcher/watch/{WATCH_ID}", watch)
    info(f"watch stored: {st} created={r.get('created')}")
    if st >= 300: sys.exit(json.dumps(r, indent=1))
    info("the role JSON the watcher will POST to /_security/role for each team it finds:")
    for t in TEAMS:
        show(f"team_{t}_reader:", role_template(t))
    note("the watcher is the only thing that ever creates a role, and it does it")
    note("from index NAMES - app-alpha-orders => team_alpha_reader. The team never")
    note("names a role; the index name alone decides the role body.")

    step(4, "ADMIN: onboard teams -> each gets a create-index key scoped to app-<team>-*")
    team_keys = {}
    for t in TEAMS:
        team_keys[t] = make_key(f"demo-bootstrap-{t}", {
            "cluster": [],
            "indices": [{"names": [f"app-{t}-*"], "privileges": ["create_index", "write"]}]})
        info(f"team {t}: create_index+write on app-{t}-*   (no read, no roles, no cluster)")
    show("what the team actually holds (team alpha):", {
        "cluster": [],
        "indices": [{"names": ["app-alpha-*"], "privileges": ["create_index", "write"]}]})
    note("this is the whole trust model: the team can CREATE and WRITE its own")
    note("indices, and that is ALL. No read, no role verbs, no cluster verbs.")

    # ---------------------------------------------------------------- app team
    step(5, "APP TEAM alpha: bootstrap its own index with ONLY the limited key")
    A = apikey(team_keys["alpha"])
    st, _ = call("PUT", "/app-alpha-orders", {}, A);                          expect("create app-alpha-orders", st, 200)
    st, _ = call("POST", "/app-alpha-orders/_doc?refresh=true", {"order": 1, "item": "widget"}, A)
    expect("write a document", st, 201)
    st, _ = call("PUT", "/app-beta-sneaky", {}, A);                           expect("create app-beta-sneaky (someone else's pattern)", st, 403)
    st, _ = call("PUT", "/random-index", {}, A);                              expect("create random-index (outside pattern)", st, 403)
    st, _ = call("GET", "/app-alpha-orders/_search", None, A);                expect("READ own index with the create key", st, 403)
    st, _ = call("PUT", "/_security/role/evil", {"cluster": ["all"]}, A);     expect("create a role with the create key", st, 403)
    note("the 403s are the point: the team is contained to its own namespace and")
    note("can never mint itself a role. Its only lever is CREATING an index -")
    note("which is exactly the trigger the watcher is waiting for.")

    step(6, "Before the watcher has run, a team-alpha JWT can authenticate but sees nothing")
    tok = bearer(mint_jwt("alice@alpha.example", "alpha"))
    st, _ = call("GET", "/app-alpha-orders/_search", None, tok)
    expect("alpha JWT reads app-alpha-orders (role not created yet)", st, 403)
    note("the token is valid and the realm accepts it - the team claim already")
    note("resolves to team_alpha_reader. The role just doesn't exist yet, so the")
    note("mapping grants nothing. Authentication and authorisation are separate.")

    step(7, f"Wait for the watcher to notice the new index and create the role (≤ {WATCH_INTERVAL})")
    t0 = time.time()
    while time.time() - t0 < 120:
        st, r = call("GET", "/_security/role/team_alpha_reader")
        if st == 200: break
        time.sleep(2)
    else:
        sys.exit("watcher never created team_alpha_reader — check .watcher-history*")
    info(f"role appeared after {time.time()-t0:.0f}s: " +
         json.dumps(r["team_alpha_reader"]["indices"]))
    # the JSON printed in step 3 must match what the watcher actually created
    live = r["team_alpha_reader"]
    tmpl = role_template("alpha")
    got = {"indices": [{k: v for k, v in i.items() if k in ("names", "privileges")}
                       for i in live.get("indices", [])],
           "metadata": live.get("metadata", {})}
    check("watcher created the role JSON shown in step 3", got == tmpl,
          "" if got == tmpl else f"  got {json.dumps(got)}")
    note("the team never asked for this and never had the privilege to do it.")
    note("Creating app-alpha-orders was enough: watcher saw the name, built the")
    note("role from the name pattern, and the step-2 mapping now has something")
    note("to point at. This is the self-service loop, fully closed.")

    # ---------------------------------------------------------------- JWT users
    step(8, "JWT access: alpha token reads alpha data only")
    st, r = call("GET", "/app-alpha-orders/_search", None, tok)
    expect("alpha JWT search app-alpha-orders", st, 200)
    if st == 200: info(f"hits: {r['hits']['total']['value']}  -> {r['hits']['hits'][0]['_source']}")
    st, _ = call("POST", "/app-alpha-orders/_doc", {"x": 1}, tok);            expect("alpha JWT WRITE (read-only role)", st, 403)
    st, _ = call("GET", "/app-beta-logs/_search", None, tok)
    expect("alpha JWT search app-beta-logs  (other team)", st, 403)
    st, r = call("GET", "/app-*/_search", None, tok)   # wildcards silently drop unauthorised indices
    expect("alpha JWT search app-*  (200 but only alpha's data)", st, 200)
    info("indices actually searched: " + ", ".join(sorted({h["_index"] for h in r["hits"]["hits"]})))
    st, _ = call("GET", "/_security/role", None, tok);                        expect("alpha JWT list roles", st, 403)
    st, r = call("GET", "/_security/_authenticate", None, tok)
    info(f"_authenticate -> user={r.get('username')} realm={r.get('authentication_realm',{}).get('name')} roles={r.get('roles')}")
    note("read its own data: YES. read another team's data: NO. write: NO.")
    note("the same claim that was refused in step 6 now grants access - the only")
    note("thing that changed is that the watcher filled in the role body.")

    step(9, "A second team shows it is fully self-service (no admin touch)")
    B = apikey(team_keys["beta"])
    call("PUT", "/app-beta-logs", {}, B)
    call("POST", "/app-beta-logs/_doc?refresh=true", {"msg": "hello from beta"}, B)
    t0 = time.time()
    while time.time() - t0 < 120 and call("GET", "/_security/role/team_beta_reader")[0] != 200: time.sleep(2)
    btok = bearer(mint_jwt("bob@beta.example", "beta"))
    st, _ = call("GET", "/app-beta-logs/_search", None, btok);               expect("beta JWT search app-beta-logs", st, 200)
    st, _ = call("GET", "/app-alpha-orders/_search", None, btok);            expect("beta JWT search app-alpha-orders", st, 403)
    note("beta onboarded itself: own key -> own index -> watcher -> own role.")
    note("No admin action between alpha and beta. That is what 'self-service' means.")

    step(10, "Token hygiene: bad tokens are rejected by the realm")
    st, _ = call("GET", "/app-alpha-orders/_search", None, bearer(mint_jwt("eve", "alpha", key="not-the-real-secret-not-the-real-secret")))
    expect("wrong signature", st, 401)
    st, _ = call("GET", "/app-alpha-orders/_search", None, bearer(mint_jwt("eve", "alpha", ttl=-60)))
    expect("expired token", st, 401)
    st, _ = call("GET", "/app-alpha-orders/_search", None, bearer(mint_jwt("eve", "alpha", issuer="evil-issuer")))
    expect("wrong issuer", st, 401)
    st, _ = call("GET", "/app-alpha-orders/_search", None, bearer(mint_jwt("eve", "gamma")))
    expect("valid token for team with no indices/role (gamma)", st, 403)
    note("the claim is not a capability: a valid signature alone grants nothing.")
    note("gamma has no index, so no watcher-generated role exists, so the mapping")
    note("resolves to a role that grants nothing.")

    print()
    if expect.failed:
        print(f"\033[1;31m{expect.failed} check(s) failed\033[0m")
    else:
        print("\033[1;32mAll checks passed.\033[0m")
    print("\n\033[1mWHAT JUST HAPPENED\033[0m")
    print("   1. The admin did three things ONCE: a role-mapping template, a JWT")
    print("      realm, and a watcher. No user, no role, and no mapping per team.")
    print("   2. The team held one API key: create_index + write on app-<team>-*.")
    print("   3. Creating app-alpha-orders triggered the watcher, which built")
    print("      team_alpha_reader from the index NAME - a privilege the team")
    print("      did not have and never asked for.")
    print("   4. A JWT with team=alpha then resolved to that role at login, so the")
    print("      team got read access it was never granted directly, while being")
    print("      locked out of every other team's data and of role creation.")
    print("   Effective access without effective authority: the team CAN reach its")
    print("   data, but it can never reach the role API that made that possible.")
    if keep:
        print("Left everything in place. Mint a token:  python3 demo.py --mint alpha")
    else:
        cleanup(); print("Cleaned up demo objects (use --keep to retain).")
    return 1 if expect.failed else 0

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--keep", action="store_true", help="don't clean up at the end")
    ap.add_argument("--mint", metavar="TEAM", help="print a toy JWT for TEAM and exit")
    a = ap.parse_args()
    if a.mint:
        print(mint_jwt(f"user@{a.mint}.example", a.mint)); sys.exit(0)
    sys.exit(run(a.keep))
