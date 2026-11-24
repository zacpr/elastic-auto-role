import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PINNED_COMMIT = "ae21fbe976c1392505befc0b47ae128d1a1436fb"

# A stand-in for git that makes setup.sh believe the upstream is already
# checked out at the pinned commit.
FAKE_GIT = """\
#!/bin/sh
set -eu
case "$1" in
    -C)
        repo="$2"
        shift 2
        case "$1" in
            checkout) [ "$2" = "--detach" ]; printf '%s\\n' "$3" > "$repo/.git/test-commit" ;;
            rev-parse) cat "$repo/.git/test-commit" ;;
            status) if [ -f "$repo/.git/test-changes" ]; then cat "$repo/.git/test-changes"; fi ;;
            *) exit 1 ;;
        esac
        ;;
    *) exit 1 ;;
esac
"""

# Stands in for docker. It records the compose arguments it was called with,
# and "starting" the stack also makes Elasticsearch answer on :9200.
FAKE_DOCKER = """\
#!/bin/sh
set -eu
printf '%s\\n' "$*" >> "$COMPOSE_LOG"
case "$*" in
    *" up "*|*" up -d"*) echo up >> "$COMPOSE_LOG"; touch "$COMPOSE_LOG.started" ;;
    *" down"*) echo down >> "$COMPOSE_LOG"; rm -f "$COMPOSE_LOG.started" ;;
esac
"""

# Stands in for curl. The cluster answers on :9200 only once the stack is up;
# the demo's own web port is free unless something else has taken it.
FAKE_CURL = """\
#!/bin/sh
for arg in "$@"; do
    case "$arg" in
        *:9200*)
            if [ -f "$COMPOSE_LOG.started" ]; then exit 0; fi
            exit 1
            ;;
        *:8000/*)
            if [ -f "$COMPOSE_LOG.busy" ]; then exit 0; fi
            exit 1
            ;;
    esac
done
exit 1
"""

# Stands in for the web server: runs until it is killed.
FAKE_SERVER = """\
#!/bin/sh
set -eu
echo served >> "$COMPOSE_LOG"
while true; do sleep 1; done
"""


class TeardownTests(unittest.TestCase):
    """The demo must not leave a container or a volume behind."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "project"
        self.bin = Path(self.tmp.name) / "bin"
        self.root.mkdir()
        self.bin.mkdir()
        for name in ("setup.sh", "compose.sh", "run.sh", "web.sh", "teardown.sh", "serve.py"):
            shutil.copy2(ROOT / name, self.root / name)
        # a fake upstream checkout, so setup.sh and compose.sh are happy
        upstream = self.root / ".docker-elk"
        (upstream / ".git").mkdir(parents=True)
        (upstream / ".git" / "test-commit").write_text(PINNED_COMMIT + "\n")
        (upstream / "docker-compose.yml").write_text("services: {}\n")
        # and the .env compose.sh insists on
        (self.root / ".env").write_text(
            "ELASTIC_VERSION=9.5.6\nELASTIC_PASSWORD=x\nJWT_HMAC_KEY=y\n"
        )
        self.env = os.environ.copy()
        self.env["PATH"] = str(self.bin) + os.pathsep + self.env["PATH"]
        self.env.pop("COMPOSE_PROJECT_NAME", None)
        self.log = Path(self.tmp.name) / "compose.log"
        self.env["COMPOSE_LOG"] = str(self.log)
        self.write_executable("git", FAKE_GIT)
        self.write_executable("docker", FAKE_DOCKER)
        self.write_executable("curl", FAKE_CURL)

    def write_executable(self, name, content):
        path = self.bin / name
        path.write_text(content)
        path.chmod(0o755)

    def calls(self):
        if not self.log.exists():
            return []
        return self.log.read_text().splitlines()

    def test_teardown_removes_the_volume_and_orphans(self):
        result = subprocess.run(
            [str(self.root / "teardown.sh")], cwd=self.tmp.name,
            env=self.env, text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        self.assertTrue(any("down" in c and "--volumes" in c for c in calls),
                        "teardown must pass --volumes: %s" % calls)
        self.assertTrue(any("down" in c and "--remove-orphans" in c for c in calls),
                        "teardown must pass --remove-orphans: %s" % calls)

    def test_teardown_is_safe_when_the_stack_is_already_gone(self):
        # no upstream checkout -> nothing to tear down, but exit 0
        shutil.rmtree(self.root / ".docker-elk")
        result = subprocess.run(
            [str(self.root / "teardown.sh")], cwd=self.tmp.name,
            env=self.env, text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def run_web_sh(self, *args):
        self.write_executable("python3", FAKE_SERVER)
        proc = subprocess.Popen(
            [str(self.root / "web.sh"), *args], cwd=self.tmp.name, env=self.env,
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        try:
            time.sleep(4)
            proc.terminate()
            proc.wait(timeout=30)
        finally:
            if proc.poll() is None:
                proc.kill()
            if proc.stdout:
                proc.stdout.close()

    def test_web_sh_tears_the_stack_down_when_it_stops(self):
        self.run_web_sh()
        calls = self.calls()
        self.assertIn("up", calls, "web.sh should have started the stack")
        self.assertIn("down", calls, "web.sh must tear the stack down on exit: %s" % calls)

    def test_web_sh_keep_leaves_the_stack_running(self):
        self.run_web_sh("--keep")
        calls = self.calls()
        self.assertIn("up", calls, "web.sh should have started the stack")
        self.assertNotIn("down", calls, "--keep must not tear the stack down: %s" % calls)

    def test_web_sh_refuses_a_port_that_is_already_in_use(self):
        Path(str(self.log) + ".busy").write_text("")
        result = subprocess.run(
            [str(self.root / "web.sh")], cwd=self.tmp.name, env=self.env,
            text=True, capture_output=True, timeout=60,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("already in use", result.stderr)


class ServeTests(unittest.TestCase):
    """The page's "Stop cluster" button talks to this server."""

    def test_serve_only_exposes_the_teardown_hook_on_the_expected_path(self):
        source = (ROOT / "serve.py").read_text(encoding="utf-8")
        self.assertIn('"/_teardown"', source)
        # the hook must not be able to run an arbitrary command
        self.assertIn('self.path != "/_teardown"', source)
        self.assertIn("[TEARDOWN]", source)
        for forbidden in ("os.system(", "shell=True", "eval("):
            self.assertNotIn(forbidden, source, "serve.py must not use %s" % forbidden)

    def test_serve_binds_to_loopback_only(self):
        source = (ROOT / "serve.py").read_text(encoding="utf-8")
        self.assertIn('("127.0.0.1", port)', source)
        self.assertNotIn('("0.0.0.0", port)', source)


if __name__ == "__main__":
    unittest.main()
