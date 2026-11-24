import hashlib
import hmac
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site" / "index.html"
DEMO = ROOT / "demo.py"
ES_CONFIG = ROOT / "elasticsearch" / "config" / "elasticsearch.yml"


def grab_block(text, header):
    """Return the python function that starts at `header`, up to the next top-level def."""
    idx = text.index(header)
    lines = text[idx:].split("\n")
    block = [lines[0]]
    for line in lines[1:]:
        if line and not line[0].isspace():
            break
        block.append(line)
    return "\n".join(block)


def grab_js_function(text, header):
    """Return the javascript function that starts at `header`, up to its closing brace."""
    start = text.index(header)
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    raise AssertionError("unbalanced braces after %s" % header)


class SiteTests(unittest.TestCase):
    """The browser demo must stay in step with demo.py and stay self-contained."""

    def setUp(self):
        self.assertTrue(SITE.is_file(), "site/index.html is missing")
        self.html = SITE.read_text(encoding="utf-8")
        self.demo = DEMO.read_text(encoding="utf-8")

    def contains(self, haystack, needle, why):
        # assertIn() would dump the whole page into the failure message.
        self.assertTrue(needle in haystack, why)

    def test_site_is_a_single_self_contained_file(self):
        # No external scripts, stylesheets, fonts or images: the page has to
        # work from a file:// URL with nothing but a browser.
        for pattern in (
            r'<script[^>]+\ssrc\s*=',
            r'<link[^>]+\srel\s*=\s*["\']?stylesheet',
            r'<img[^>]+\ssrc\s*=\s*["\']?https?:',
            r'@import\s+url\(\s*["\']?https?:',
        ):
            self.assertIsNone(
                re.search(pattern, self.html, re.IGNORECASE),
                "site/index.html must not load anything from the network: %s" % pattern,
            )

    def test_site_does_not_reach_the_network_outside_the_cluster(self):
        hosts = set(re.findall(r"https?://([A-Za-z0-9._:-]+)", self.html))
        allowed = {"localhost:9200", "localhost", "127.0.0.1", "www.w3.org"}
        self.assertTrue(
            hosts <= allowed,
            "unexpected external hosts in site/index.html: %s" % (hosts - allowed),
        )

    def test_site_and_demo_agree_on_the_demo_objects(self):
        for value in ("role-per-team", "jwt_team_readers", "local-issuer", "elasticsearch", "alpha"):
            self.contains(self.demo, value, "demo.py no longer contains %s" % value)
            self.contains(self.html, value, "site/index.html no longer contains %s" % value)

    def test_site_covers_every_step_of_the_demo(self):
        demo_steps = re.findall(r"step\((\d+), ", self.demo)
        self.assertTrue(demo_steps, "could not find the steps in demo.py")
        for n in demo_steps:
            self.contains(self.html, 'id="step-%s"' % n,
                          "site/index.html has no section for step %s" % n)

    def test_site_mints_jwts_with_the_same_claims_as_demo(self):
        demo_block = re.search(r"claims = \{(.*?)\}", self.demo, re.DOTALL)
        site_block = re.search(r"const claims = \{(.*?)\};", self.html, re.DOTALL)
        self.assertIsNotNone(demo_block, "could not find the claims in demo.py")
        self.assertIsNotNone(site_block, "could not find the claims in the page")
        demo_claims = set(re.findall(r'"(\w+)"\s*:', demo_block.group(1)))
        site_claims = set(re.findall(r"\b(\w+)\s*:", site_block.group(1)))
        self.assertEqual(
            demo_claims, site_claims,
            "the page and demo.py disagree on the JWT claims: %s vs %s"
            % (sorted(demo_claims), sorted(site_claims)),
        )

    def test_site_creates_the_same_role_body_as_the_watcher(self):
        """The page advertises the role the watcher creates; keep them identical."""
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        js_fn = grab_js_function(self.html, "function roleTemplate(team) {")
        py_fn = grab_block(self.demo, "def role_template(team):")
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "role.mjs"
            script.write_text(
                js_fn + "\nprocess.stdout.write(JSON.stringify(roleTemplate('alpha')));\n",
                encoding="utf-8",
            )
            got = subprocess.run([node, str(script)], capture_output=True, text=True, check=True)
        namespace = {}
        exec(py_fn, namespace)  # noqa: S102 - the block is taken from this repo
        want = namespace["role_template"]("alpha")
        self.assertEqual(json.loads(got.stdout), want)

    def test_site_uses_a_full_width_console_dock(self):
        """The console must be a docked row, not a small overlay in a corner."""
        self.contains(self.html, 'class="console collapsed" id="console"',
                      "the console dock is missing")
        self.contains(self.html, 'id="console-grip"',
                      "the console needs a grip so it can be resized")
        self.contains(self.html, "height:var(--console-h",
                      "the console height must be driven by a CSS variable")
        self.contains(self.html, "flex:0 0 auto",
                      "the console must be a fixed row of the app layout")
        # it must span the full width, not sit in a corner
        self.assertNotIn(".terminal{", self.html, "the old floating console is still here")

    def test_console_peek_shows_the_last_line_when_collapsed(self):
        self.contains(self.html, 'id="console-peek"', "the collapsed peek is missing")
        self.contains(self.html, "function updateConsolePeek()",
                      "the peek is never updated")

    def test_elasticsearch_enables_cors_for_the_browser_demo(self):
        config = ES_CONFIG.read_text(encoding="utf-8")
        self.contains(config, "http.cors.enabled: true", "CORS is not enabled")
        self.contains(config, "http.cors.allow-origin:", "no CORS origin configured")
        for header in ("Authorization", "Content-Type"):
            self.contains(config, header, "CORS must allow the %s header" % header)
        for method in ("GET", "POST", "PUT", "DELETE", "OPTIONS"):
            self.contains(config, method, "CORS must allow the %s method" % method)

    def test_no_live_credentials_are_committed(self):
        """The demo's .env holds a real signing key; it must never be committed."""
        env = ROOT / ".env"
        tracked = [
            ROOT / "README.md",
            ROOT / "elasticsearch" / "config" / "elasticsearch.yml",
            ROOT / "run.sh",
            ROOT / "web.sh",
            ROOT / "teardown.sh",
            ROOT / "serve.py",
            SITE,
            ROOT / "tests" / "test_site.py",
            ROOT / "tests" / "test_teardown.py",
            ROOT / "tests" / "test_packaging.py",
        ]
        if not env.is_file():
            self.skipTest("no .env to compare against")
        secrets = []
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() in ("JWT_HMAC_KEY", "ELASTIC_PASSWORD") and value.strip():
                secrets.append(value.strip())
        self.assertTrue(secrets, "the .env should hold the demo credentials")
        for path in tracked:
            if not path.is_file():
                continue
            body = path.read_text(encoding="utf-8", errors="replace")
            for secret in secrets:
                self.assertNotIn(
                    secret, body,
                    "%s contains a live demo credential" % path.relative_to(ROOT),
                )

    def test_site_carries_a_browser_hmac_sha256_that_matches_python(self):
        """The page signs JWTs itself, so its HMAC must be byte-for-byte correct."""
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        m = re.search(r"function sha256Bytes\(msg\) \{.*?\n\}\n", self.html, re.DOTALL)
        h = re.search(r"function hmacSha256Bytes\(keyBytes, msgBytes\) \{.*?\n\}\n", self.html, re.DOTALL)
        self.assertIsNotNone(m, "could not find sha256Bytes in the page")
        self.assertIsNotNone(h, "could not find hmacSha256Bytes in the page")
        driver = (
            m.group(0) + "\n" + h.group(0) + "\n" +
            "const enc = new TextEncoder();\n"
            "const hex = b => Array.from(b).map(x => x.toString(16).padStart(2, '0')).join('');\n"
            "process.stdout.write(JSON.stringify({\n"
            "  sha_abc: hex(sha256Bytes(enc.encode('abc'))),\n"
            "  hmac_jefe: hex(hmacSha256Bytes(enc.encode('Jefe'), enc.encode('what do ya want for nothing?'))),\n"
            "  hmac_jwt: hex(hmacSha256Bytes(enc.encode(process.argv[2]), enc.encode(process.argv[3]))),\n"
            "}));\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "hmac.mjs"
            script.write_text(driver, encoding="utf-8")
            # A synthetic key: the test only needs a fixed input to compare
            # against Python, never the real demo signing key.
            key = "unit-test-key-not-a-real-secret"
            msg = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJsb2NhbC1pc3N1ZXIifQ"
            result = subprocess.run(
                [node, str(script), key, msg], capture_output=True, text=True, check=True
            )
            got = json.loads(result.stdout)
        self.assertEqual(got["sha_abc"], hashlib.sha256(b"abc").hexdigest())
        self.assertEqual(
            got["hmac_jefe"],
            hmac.new(b"Jefe", b"what do ya want for nothing?", hashlib.sha256).hexdigest(),
        )
        self.assertEqual(
            got["hmac_jwt"],
            hmac.new(key.encode(), msg.encode(), hashlib.sha256).hexdigest(),
        )


if __name__ == "__main__":
    unittest.main()
