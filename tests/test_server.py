#!/usr/bin/env python3
"""HTTP server, relay policy, range, proxy, and context-isolation tests."""

import base64
import hashlib
import json
import os
import socket
import struct
import sys
import tempfile
import threading
import unittest
from functools import partial
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gwasm_core.patch import ChunkStore
from gwasm_core.runtime import Progress
from gwasm_core.server import (
    MAX_WS_FRAME,
    GwasmServer,
    Handler,
    RelayPolicy,
    ServerContext,
    _dns_query,
    allowed_name,
    frame,
    is_public_ip,
    normalize_origin,
    policy_from_environment,
    unframe,
    websocket_origins,
)


def request(url, byte_range=None, method="GET", body=None, headers=None):
    req = Request(url, data=body, method=method, headers=headers or {})
    if byte_range:
        req.add_header("Range", byte_range)
    try:
        with urlopen(req, timeout=5) as response:
            return response.status, response.headers, response.read()
    except HTTPError as error:
        result = error.code, error.headers, error.read()
        error.close()
        return result


def make_store(root, content, chunk_size=4):
    cache = root / "cache"
    cache.mkdir(parents=True)
    chunks = [content[index:index + chunk_size]
              for index in range(0, len(content), chunk_size)]
    hashes = []
    for chunk in chunks:
        content_hash = hashlib.sha256(chunk).hexdigest()
        hashes.append(content_hash)
        (cache / content_hash).write_bytes(chunk)
    index = root / "snapshot-chunks.json"
    index.write_text(json.dumps({
        "size": len(content), "chunkSize": chunk_size,
        "cache": str(cache), "chunkHashes": hashes,
    }))
    return ChunkStore(index)


class FakeProxy:
    def __init__(self, name):
        self.name = name
        self.urls = []

    def request(self, method, url, **_kwargs):
        self.urls.append(url)
        body = json.dumps({
            "name": self.name, "method": method, "url": url,
        }).encode()
        return 200, {"Content-Type": "application/json"}, body


class ServerFixture:
    def __init__(self, directory, context):
        self.server = GwasmServer(
            ("127.0.0.1", 0),
            partial(Handler, directory=str(directory)),
            context)
        self.context = context
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def port(self):
        return self.server.server_address[1]

    @property
    def url(self):
        return "http://127.0.0.1:%d" % self.port

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


class FramingAndPolicyTests(unittest.TestCase):
    def test_frame_sizes_and_masked_round_trip(self):
        for size in (5, 200, 70_000):
            payload = os.urandom(size)
            server_frame = frame(payload)
            self.assertEqual(server_frame[0], 0x82)
            header = bytearray(server_frame[:-size])
            header[1] |= 0x80
            mask = os.urandom(4)
            masked = bytes(value ^ mask[index & 3]
                           for index, value in enumerate(payload))
            parsed = unframe(bytes(header) + mask + masked)
            self.assertEqual(parsed[0:2], (2, payload))

    def test_unframe_rejects_unsafe_frames(self):
        cases = [
            frame(b"x"),
            bytes([0x02, 0x80, 0, 0, 0, 0]),
            bytes([0xC2, 0x80, 0, 0, 0, 0]),
            bytes([0x82, 0xFF]) + struct.pack("!Q", MAX_WS_FRAME + 1),
            bytes([0x89, 0xFE, 0, 126, 0, 0, 0, 0]),
        ]
        for value in cases:
            with self.subTest(value=value[:2]), self.assertRaises(ValueError):
                unframe(value)

    def test_domain_ip_and_origin_rules(self):
        self.assertTrue(allowed_name("File1.ArenaNetworks.com"))
        self.assertFalse(allowed_name("arenanetworks.com.evil.test"))
        self.assertTrue(is_public_ip("8.8.8.8"))
        for value in ("127.0.0.1", "10.0.0.1", "169.254.169.254",
                      "::1", "::ffff:127.0.0.1", "not-an-ip"):
            self.assertFalse(is_public_ip(value), value)
        self.assertEqual(
            normalize_origin("HTTP://Example.COM:8080"),
            "http://example.com:8080")
        self.assertEqual(normalize_origin("https://Example.COM:443"),
                         "https://example.com")
        self.assertEqual(
            websocket_origins({"https://example.com",
                               "http://example.com:8080"}),
            ["ws://example.com:8080", "wss://example.com"])
        for value in ("http://example.com", "ftp://example.com:80",
                      "http://user@example.com:80",
                      "http://example.com:80/path",
                      "http://*.example.com:80"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_origin(value)

    def test_policy_is_parsed_once_from_supplied_environment(self):
        policy = policy_from_environment({
            "GWASM_RELAY_DOMAINS": "one.test, two.test",
            "GWASM_RELAY_PORTS": "123,456",
            "GWASM_RELAY_ALLOW_PRIVATE": "1",
            "GWASM_PROXY_ROUTES": "alpha=a.test",
            "GWASM_PROXY_TEMPLATE": "http://fixture/{route}",
        })
        self.assertEqual(policy.allowed_domains, ("one.test", "two.test"))
        self.assertEqual(policy.allowed_ports, frozenset((123, 456)))
        self.assertTrue(policy.allow_private)
        self.assertEqual(policy.proxy_routes["alpha"], "a.test")
        self.assertEqual(policy.proxy_template, "http://fixture/{route}")

    def test_dns_wire_parser_checks_id_rcode_and_compression(self):
        question = b"\x04File\x02gw\x03com\x00" + struct.pack("!HH", 1, 1)
        answer = (b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, 60, 4) +
                  bytes((9, 8, 7, 6)))
        state = {"query": b"", "id_xor": 0, "rcode": 0}

        class FakeSocket:
            def __init__(self, *_args):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                pass

            def settimeout(self, _timeout):
                pass

            def sendto(self, data, _address):
                state["query"] = data

            def recvfrom(self, _size):
                query_id = struct.unpack("!H", state["query"][:2])[0]
                query_id ^= state["id_xor"]
                header = struct.pack(
                    "!HHHHHH", query_id, 0x8180 | state["rcode"], 1, 1, 0, 0)
                return header + question + answer, None

        with mock.patch("gwasm_core.server.socket.socket", FakeSocket):
            self.assertEqual(_dns_query("File.gw.com", "1.1.1.1"), "9.8.7.6")
            self.assertEqual(state["query"][2:4], b"\x01\x00")
            self.assertIn(b"\x04File", state["query"])

            state["id_xor"] = 0xFFFF
            with self.assertRaisesRegex(OSError, "bad DNS reply"):
                _dns_query("File.gw.com", "1.1.1.1")
            state["id_xor"] = 0

            state["rcode"] = 3
            with self.assertRaisesRegex(OSError, "DNS rcode 3"):
                _dns_query("File.gw.com", "1.1.1.1")


class ServerIsolationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.one_dir = root / "one"
        self.two_dir = root / "two"
        self.one_dir.mkdir()
        self.two_dir.mkdir()
        (self.one_dir / "index.html").write_text("one")
        (self.two_dir / "index.html").write_text("two")

        self.tcp = socket.socket()
        self.tcp.bind(("127.0.0.1", 0))
        self.tcp.listen()
        self.tcp_port = self.tcp.getsockname()[1]
        self.tcp_stop = threading.Event()

        def accept_loop():
            while not self.tcp_stop.is_set():
                try:
                    connection, _ = self.tcp.accept()
                except OSError:
                    return
                threading.Thread(
                    target=self.drain, args=(connection,), daemon=True).start()
        threading.Thread(target=accept_loop, daemon=True).start()

        self.proxy_one = FakeProxy("one")
        self.proxy_two = FakeProxy("two")
        self.context_one = ServerContext(
            progress=Progress(),
            policy=RelayPolicy(
                allowed_domains=("one.test",),
                allowed_ports=frozenset((6112,)),
                allow_private=False,
                proxy_routes={"alpha": "alpha.test"}),
            store=make_store(self.one_dir / "snapshot", b"one-snapshot"),
            origins={"http://one.example:1111"},
            proxy_http=self.proxy_one)
        self.context_two = ServerContext(
            progress=Progress(),
            policy=RelayPolicy(
                allowed_domains=("two.test",),
                allowed_ports=frozenset((self.tcp_port,)),
                allow_private=True,
                proxy_routes={"beta": "beta.test"}),
            store=make_store(self.two_dir / "snapshot", b"two-snapshot"),
            origins={"http://two.example:2222"},
            proxy_http=self.proxy_two)
        self.one = ServerFixture(self.one_dir, self.context_one)
        self.two = ServerFixture(self.two_dir, self.context_two)

    @staticmethod
    def drain(connection):
        with connection:
            try:
                while connection.recv(1024):
                    pass
            except OSError:
                pass

    def tearDown(self):
        self.one.close()
        self.two.close()
        self.tcp_stop.set()
        self.tcp.close()
        self.temp.cleanup()

    def websocket_status(self, fixture, destination, origin):
        connection = socket.create_connection(("127.0.0.1", fixture.port), 5)
        try:
            key = base64.b64encode(os.urandom(16)).decode()
            wire = (
                "GET /?dest=%s HTTP/1.1\r\n"
                "Host: 127.0.0.1:%d\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                "Sec-WebSocket-Key: %s\r\n"
                "Sec-WebSocket-Version: 13\r\n"
                "Origin: %s\r\n\r\n"
                % (destination, fixture.port, key, origin))
            connection.sendall(wire.encode())
            first = b""
            while b"\r\n" not in first:
                chunk = connection.recv(1)
                if not chunk:
                    break
                first += chunk
            return int(first.split()[1])
        finally:
            connection.close()

    def test_stores_origins_routes_and_private_policy_are_isolated(self):
        status, headers, body = request(
            self.one.url + "/Gw.snapshot", "bytes=0-11")
        self.assertEqual((status, body), (206, b"one-snapshot"))
        self.assertIn("ws://one.example:1111",
                      headers["Content-Security-Policy"])
        self.assertNotIn("two.example", headers["Content-Security-Policy"])

        status, headers, body = request(
            self.two.url + "/Gw.snapshot", "bytes=0-11")
        self.assertEqual((status, body), (206, b"two-snapshot"))
        self.assertIn("ws://two.example:2222",
                      headers["Content-Security-Policy"])
        self.assertNotIn("one.example", headers["Content-Security-Policy"])

        status, _, body = request(self.one.url + "/alpha/path")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["name"], "one")
        self.assertIn("https://alpha.test/path", self.proxy_one.urls)
        self.assertEqual(request(self.two.url + "/alpha/path")[0], 502)

        status, _, body = request(self.two.url + "/beta/path")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["name"], "two")
        self.assertIn("https://beta.test/path", self.proxy_two.urls)
        self.assertEqual(request(self.one.url + "/beta/path")[0], 502)

        destination = "127.0.0.1:%d" % self.tcp_port
        self.assertEqual(self.websocket_status(
            self.one, destination, "http://one.example:1111"), 403)
        self.assertEqual(self.websocket_status(
            self.two, destination, "http://two.example:2222"), 101)

    def test_progress_range_and_origin_rejection(self):
        self.context_one.progress.set(phase="one")
        self.context_two.progress.set(phase="two")
        self.assertEqual(
            json.loads(request(self.one.url + "/progress.json")[2])["phase"],
            "one")
        self.assertEqual(
            json.loads(request(self.two.url + "/progress.json")[2])["phase"],
            "two")
        self.assertEqual(
            request(self.one.url + "/Gw.snapshot", "bytes=2-6")[2],
            b"e-sna")
        self.assertEqual(
            request(self.one.url + "/Gw.snapshot", "invalid")[0], 416)
        self.assertEqual(
            request(self.one.url + "/alpha/path",
                    headers={"Origin": "http://evil.test"})[0], 403)
        self.assertEqual(
            request(self.one.url + "/dns?name=two.test")[0], 403)


if __name__ == "__main__":
    unittest.main()
