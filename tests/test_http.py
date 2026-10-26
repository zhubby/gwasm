#!/usr/bin/env python3
"""Standard-library HTTP client behavior tests."""

import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gwasm_core.http import Http, HttpError


class FixtureHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        if self.path == "/missing":
            body = b"missing"
            self.send_response(404)
        else:
            body = self.headers.get("X-Test", "").encode()
            self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
        cls.thread = threading.Thread(
            target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.root = "http://127.0.0.1:%d" % cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def test_get_returns_body_and_default_headers(self):
        self.assertEqual(Http({"X-Test": "shared"}).get(self.root + "/ok"),
                         b"shared")

    def test_request_raises_typed_error_for_fatal_status(self):
        with self.assertRaises(HttpError) as raised:
            Http().get(self.root + "/missing")
        self.assertEqual(raised.exception.status, 404)

    def test_request_can_pass_upstream_4xx_through(self):
        status, headers, body = Http().request(
            "GET", self.root + "/missing", raise_4xx=False)
        self.assertEqual((status, body), (404, b"missing"))
        self.assertEqual(headers["Content-Length"], str(len(body)))


if __name__ == "__main__":
    unittest.main()
