import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
PINNED_COMMIT = "ae21fbe976c1392505befc0b47ae128d1a1436fb"

FAKE_GIT = """\
#!/bin/sh
set -eu
printf '%s\\n' "$*" >> "$FAKE_GIT_LOG"
case "$1" in
    clone)
        [ "$2" = "--filter=blob:none" ]
        [ "$3" = "--no-checkout" ]
        [ "$4" = "https://github.com/deviantony/docker-elk.git" ]
        mkdir -p "$5/.git"
        printf 'services: {}\\n' > "$5/docker-compose.yml"
        ;;
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


@unittest.skipUnless(os.name == "posix", "The demo launchers require a POSIX shell")
class PackagingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "project"
        self.bin = Path(self.tmp.name) / "bin"
        self.root.mkdir()
        self.bin.mkdir()
        for name in ("setup.sh", "compose.sh", "run.sh", "demo.py",
                     "teardown.sh", "web.sh", "serve.py"):
            shutil.copy2(ROOT / name, self.root / name)
        self.write_executable("git", FAKE_GIT)
        self.env = os.environ.copy()
        self.env["PATH"] = str(self.bin) + os.pathsep + self.env["PATH"]
        self.env["FAKE_GIT_LOG"] = str(Path(self.tmp.name) / "git.log")
        self.env.pop("COMPOSE_PROJECT_NAME", None)

    def write_executable(self, name, content):
        path = self.bin / name
        path.write_text(content)
        path.chmod(0o755)

    def run_setup(self):
        return subprocess.run(
            [str(self.root / "setup.sh")], cwd=self.tmp.name,
            env=self.env, text=True, capture_output=True,
        )

    def test_setup_pins_upstream_and_preserves_credentials(self):
        first = self.run_setup()
        self.assertEqual(first.returncode, 0, first.stderr)
        env_file = self.root / ".env"
        initial = env_file.read_text()
        values = dict(line.split("=", 1) for line in initial.splitlines())
        self.assertGreaterEqual(len(values["ELASTIC_PASSWORD"]), 24)
        self.assertGreaterEqual(len(values["JWT_HMAC_KEY"]), 32)
        self.assertEqual(env_file.stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.root / ".docker-elk/.git/test-commit").read_text().strip(), PINNED_COMMIT)

        second = self.run_setup()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(env_file.read_text(), initial)
        git_calls = Path(self.env["FAKE_GIT_LOG"]).read_text().splitlines()
        self.assertEqual(sum(call.startswith("clone ") for call in git_calls), 1)

    def test_setup_rejects_unexpected_or_modified_checkout(self):
        self.assertEqual(self.run_setup().returncode, 0)
        git_dir = self.root / ".docker-elk/.git"
        (git_dir / "test-commit").write_text("different-commit\n")
        wrong_commit = self.run_setup()
        self.assertNotEqual(wrong_commit.returncode, 0)
        self.assertIn("not at the pinned commit", wrong_commit.stderr)

        (git_dir / "test-commit").write_text(PINNED_COMMIT + "\n")
        (git_dir / "test-changes").write_text(" M docker-compose.yml\n")
        dirty = self.run_setup()
        self.assertNotEqual(dirty.returncode, 0)
        self.assertIn("has local changes", dirty.stderr)

    def test_setup_preserves_an_unrelated_existing_directory(self):
        upstream = self.root / ".docker-elk"
        upstream.mkdir()
        sentinel = upstream / "keep.txt"
        sentinel.write_text("keep")
        result = self.run_setup()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("is not an upstream checkout", result.stderr)
        self.assertEqual(sentinel.read_text(), "keep")
        self.assertFalse((self.root / ".env").exists())

    def test_compose_uses_project_env_not_inherited_credentials(self):
        self.assertEqual(self.run_setup().returncode, 0)
        self.write_executable("docker", """\
#!/bin/sh
set -eu
[ -z "${ELASTIC_VERSION+x}${ELASTIC_PASSWORD+x}${JWT_HMAC_KEY+x}" ]
printf '%s\\n' "$DEMO_ROOT" "$@"
""")
        self.env.update(
            ELASTIC_VERSION="inherited-version",
            ELASTIC_PASSWORD="inherited-password",
            JWT_HMAC_KEY="inherited-key",
            COMPOSE_PROJECT_NAME="isolated-test",
        )
        result = subprocess.run(
            [str(self.root / "compose.sh"), "config", "--quiet"],
            cwd=self.tmp.name, env=self.env, text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [
            str(self.root), "compose", "--project-name", "isolated-test",
            "--env-file", str(self.root / ".env"),
            "-f", str(self.root / ".docker-elk/docker-compose.yml"),
            "-f", str(self.root / "docker-compose.demo.yml"), "config", "--quiet",
        ])

    def test_runner_starts_demo_then_stops_its_stack(self):
        self.assertEqual(self.run_setup().returncode, 0)
        self.env["RUN_LOG"] = str(Path(self.tmp.name) / "run.log")
        self.env["TEST_ROOT"] = str(self.root)
        self.write_executable("docker", """\
#!/bin/sh
set -eu
case "$*" in
    *"up -d --build elasticsearch") echo up >> "$RUN_LOG" ;;
    *"down"*) echo down >> "$RUN_LOG" ;;
    *) exit 1 ;;
esac
""")
        self.write_executable("python3", """\
#!/bin/sh
set -eu
[ "$#" -eq 3 ] && [ "$1" = "-I" ] && [ "$2" = "$TEST_ROOT/demo.py" ] && [ "$3" = "--keep" ]
echo demo >> "$RUN_LOG"
""")
        result = subprocess.run(
            [str(self.root / "run.sh")], input="\n", cwd=self.tmp.name,
            env=self.env, text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(Path(self.env["RUN_LOG"]).read_text().splitlines(), ["up", "demo", "down"])

        Path(self.env["RUN_LOG"]).write_text("")
        self.write_executable("python3", "#!/bin/sh\necho demo >> \"$RUN_LOG\"\nexit 23\n")
        failed = subprocess.run(
            [str(self.root / "run.sh")], input="\n", cwd=self.tmp.name,
            env=self.env, text=True, capture_output=True,
        )
        self.assertEqual(failed.returncode, 23)
        self.assertEqual(Path(self.env["RUN_LOG"]).read_text().splitlines(), ["up", "demo", "down"])


if __name__ == "__main__":
    unittest.main()
