#!/usr/bin/env python3
"""Serve the repository over http for the web demo.

A plain static server, plus one extra endpoint: POST /_teardown removes the
demo stack (container, network and volume) so the page's own "clean up" button
can take the whole thing down instead of only the Elasticsearch objects.

The server binds to the loopback interface only, and the endpoint takes no
parameters -- it runs a fixed command. It is deliberately not a general
command runner.
"""
import http.server
import os
import socketserver
import subprocess
import sys
import threading

ROOT = os.path.dirname(os.path.abspath(__file__))
TEARDOWN = os.path.join(ROOT, "teardown.sh")


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=ROOT, **kw)

    def log_message(self, fmt, *args):
        sys.stderr.write("  %s\n" % (fmt % args))

    def _json(self, code, payload):
        body = __import__("json").dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        # Answer GET on the teardown path with 405 so the page can tell that
        # the hook exists (a 404 means this is a plain static server).
        if self.path == "/_teardown":
            self._json(405, {"error": "use POST"})
            return
        super().do_GET()

    def do_POST(self):
        if self.path != "/_teardown":
            self._json(404, {"error": "not found"})
            return
        # No parameters are honoured: this runs one fixed script.
        try:
            proc = subprocess.run(
                [TEARDOWN], cwd=ROOT, text=True, capture_output=True, timeout=180
            )
        except Exception as exc:  # noqa: BLE001 - report any failure to the page
            self._json(500, {"ok": False, "error": str(exc)})
            return
        self._json(200 if proc.returncode == 0 else 500, {
            "ok": proc.returncode == 0,
            "output": (proc.stdout or "") + (proc.stderr or ""),
        })

    def end_headers(self):
        # The page is same-origin; keep it that way.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", port), Handler) as httpd:
        print("Serving %s on http://127.0.0.1:%d" % (ROOT, port), flush=True)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
