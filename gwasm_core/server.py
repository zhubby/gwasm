"""Static HTTP, virtual snapshot, DNS, HTTPS proxy, and TCP relay."""

import base64
import hashlib
import ipaddress
import json
import os
import re
import select
import socket
import struct
import sys
import time
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse, urlsplit

from .runtime import log


DEFAULT_DOMAINS = ("arenanetworks.com", "guildwars.com")
DEFAULT_PORTS = frozenset((6112, 80, 443))
DEFAULT_PROXY_ROUTES = {
    "webgate": "webgate.ncplatform.net",
    "account": "account.arena.net",
    "help": "help.guildwars.com",
    "store": "store.guildwars.com",
    "www": "www.guildwars.com",
}
FALLBACK_DNS = ("1.1.1.1", "8.8.8.8")
CONNECT_TIMEOUT = 10
WS_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_WS_FRAME = 16 * 1024 * 1024
ROUTE_RE = re.compile(r"^/([a-z0-9][a-z0-9-]{0,30})(/.*)$", re.I)
RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


@dataclass
class RelayPolicy:
    allowed_domains: tuple = DEFAULT_DOMAINS
    allowed_ports: frozenset = DEFAULT_PORTS
    allow_private: bool = False
    proxy_routes: dict = field(default_factory=lambda: dict(DEFAULT_PROXY_ROUTES))
    proxy_template: str = None


@dataclass
class ServerContext:
    progress: object
    policy: RelayPolicy = field(default_factory=RelayPolicy)
    store: object = None
    origins: set = field(default_factory=set)
    proxy_http: object = None
    watchdog: object = None
    verbose: bool = False


def policy_from_environment(env=None):
    env = os.environ if env is None else env
    domains = tuple(value.strip().lower()
                    for value in env.get("GWASM_RELAY_DOMAINS",
                                         ",".join(DEFAULT_DOMAINS)).split(",")
                    if value.strip())
    ports = frozenset(int(value) for value in
                      env.get("GWASM_RELAY_PORTS", "6112,80,443")
                         .replace(" ", "").split(",") if value)
    routes = dict(DEFAULT_PROXY_ROUTES)
    for pair in filter(None, env.get("GWASM_PROXY_ROUTES", "").split(",")):
        route, separator, host = pair.partition("=")
        if separator and route.strip() and host.strip():
            routes[route.strip()] = host.strip()
    return RelayPolicy(
        allowed_domains=domains,
        allowed_ports=ports,
        allow_private=env.get("GWASM_RELAY_ALLOW_PRIVATE") == "1",
        proxy_routes=routes,
        proxy_template=env.get("GWASM_PROXY_TEMPLATE"),
    )


def allowed_name(host, domains=DEFAULT_DOMAINS):
    normalized = host.lower().rstrip(".")
    return any(normalized == domain or normalized.endswith("." + domain)
               for domain in domains)


def _dns_query(name, server, timeout=3):
    query_id = struct.unpack("!H", os.urandom(2))[0]
    query = struct.pack("!HHHHHH", query_id, 0x0100, 1, 0, 0, 0)
    for label in name.rstrip(".").split("."):
        query += bytes([len(label)]) + label.encode()
    query += b"\0" + struct.pack("!HH", 1, 1)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as dns_socket:
        dns_socket.settimeout(timeout)
        dns_socket.sendto(query, (server, 53))
        data, _ = dns_socket.recvfrom(2048)
    if len(data) < 12 or struct.unpack("!H", data[:2])[0] != query_id:
        raise OSError("bad DNS reply")
    response_code = struct.unpack("!H", data[2:4])[0] & 0xF
    if response_code:
        raise OSError("DNS rcode %d" % response_code)
    question_count, answer_count = struct.unpack("!HH", data[4:8])

    def skip_name(offset):
        while offset < len(data):
            length = data[offset]
            if length == 0:
                return offset + 1
            if length & 0xC0 == 0xC0:
                return offset + 2
            offset += length + 1
        raise OSError("truncated DNS name")

    offset = 12
    for _ in range(question_count):
        offset = skip_name(offset) + 4
    for _ in range(answer_count):
        offset = skip_name(offset)
        record_type, _, _, length = struct.unpack("!HHIH", data[offset:offset + 10])
        offset += 10
        if record_type == 1 and length == 4:
            return ".".join(str(value) for value in data[offset:offset + 4])
        offset += length
    raise OSError("no A record")


def _system_resolvers():
    try:
        text = Path("/etc/resolv.conf").read_text()
    except OSError:
        return []
    return [match.group(1) for match in
            re.finditer(r"^nameserver\s+([0-9.]+)", text, re.M)]


def lookup(name):
    tried = []
    try:
        return socket.getaddrinfo(name, None, socket.AF_INET)[0][4][0], "getaddrinfo"
    except OSError as exc:
        tried.append("getaddrinfo:%s" % exc)
    for server in _system_resolvers() + list(FALLBACK_DNS):
        try:
            return _dns_query(name, server), "dns %s (%s)" % (server, ", ".join(tried))
        except OSError as exc:
            tried.append("%s:%s" % (server, exc))
    raise OSError("; ".join(tried))


def normalize_origin(value):
    raw = value.strip()
    try:
        parsed = urlsplit(raw)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        raise ValueError("origin has an invalid host or port")
    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        raise ValueError("origin must use http:// or https://")
    if (not hostname or "@" in parsed.netloc or parsed.username is not None
            or parsed.password is not None):
        raise ValueError("origin must contain only a scheme, host and port")
    if parsed.port is None:
        raise ValueError("origin must include an explicit port")
    if "*" in hostname:
        raise ValueError("origin must not contain wildcards")
    if parsed.path or parsed.query or parsed.fragment or "?" in raw or "#" in raw:
        raise ValueError("origin must not contain a path, query or fragment")
    host = hostname.lower()
    if ":" in host:
        host = "[%s]" % host
    default = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    netloc = host if default else "%s:%d" % (host, port)
    return "%s://%s" % (scheme, netloc)


def websocket_origins(origins):
    output = []
    for origin in origins:
        # Origins are normalized when the server starts. A normalized default
        # port intentionally has no :80/:443, so re-running normalize_origin()
        # here would reject a value that was already accepted.
        parsed = urlsplit(origin)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname or
                parsed.path or parsed.query or parsed.fragment or
                "\r" in origin or "\n" in origin):
            raise ValueError("invalid relay origin %r" % origin)
        output.append("%s://%s" % ("wss" if parsed.scheme == "https" else "ws",
                                   parsed.netloc))
    return sorted(set(output))


def is_public_ip(value):
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    if address.version == 6 and address.ipv4_mapped:
        return is_public_ip(str(address.ipv4_mapped))
    if address.version == 4 and address in ipaddress.ip_network("192.0.0.0/24"):
        return False
    return address.is_global and not address.is_multicast


def frame(payload, opcode=0x2):
    length = len(payload)
    if length < 126:
        header = bytes([0x80 | opcode, length])
    elif length < 65536:
        header = bytes([0x80 | opcode, 126]) + struct.pack("!H", length)
    else:
        header = bytes([0x80 | opcode, 127]) + struct.pack("!Q", length)
    return header + payload


def unframe(buffer):
    if len(buffer) < 2:
        return None
    first = buffer[0]
    if first & 0x70:
        raise ValueError("WebSocket RSV bits are not supported")
    if not first & 0x80:
        raise ValueError("fragmented WebSocket messages are not supported")
    opcode = first & 0x0F
    if opcode not in (0x1, 0x2, 0x8, 0x9, 0xA):
        raise ValueError("unsupported WebSocket opcode %d" % opcode)
    if not buffer[1] & 0x80:
        raise ValueError("client WebSocket frames must be masked")
    length = buffer[1] & 0x7F
    offset = 2
    if length == 126:
        if len(buffer) < offset + 2:
            return None
        length = struct.unpack("!H", buffer[offset:offset + 2])[0]
        offset += 2
    elif length == 127:
        if len(buffer) < offset + 8:
            return None
        length = struct.unpack("!Q", buffer[offset:offset + 8])[0]
        offset += 8
        if length & (1 << 63):
            raise ValueError("invalid WebSocket frame length")
    if length > MAX_WS_FRAME:
        raise ValueError("WebSocket frame exceeds limit")
    if opcode >= 0x8 and length > 125:
        raise ValueError("invalid WebSocket control frame")
    if len(buffer) < offset + 4:
        return None
    mask = buffer[offset:offset + 4]
    offset += 4
    if len(buffer) < offset + length:
        return None
    payload = bytearray(buffer[offset:offset + length])
    for index in range(length):
        payload[index] ^= mask[index & 3]
    return opcode, bytes(payload), buffer[offset + length:]


def bridge(browser_socket, tcp_socket, label):
    started = time.time()
    sent = received = 0
    buffer = b""
    try:
        while True:
            ready, _, _ = select.select([browser_socket, tcp_socket], [], [], 60)
            if not ready:
                continue
            if tcp_socket in ready:
                data = tcp_socket.recv(65536)
                if not data:
                    break
                received += len(data)
                browser_socket.sendall(frame(data))
            if browser_socket in ready:
                data = browser_socket.recv(65536)
                if not data:
                    break
                buffer += data
                while True:
                    parsed = unframe(buffer)
                    if not parsed:
                        break
                    opcode, payload, buffer = parsed
                    if opcode == 0x8:
                        return
                    if opcode == 0x9:
                        browser_socket.sendall(frame(payload, 0xA))
                    elif opcode in (0x1, 0x2):
                        sent += len(payload)
                        tcp_socket.sendall(payload)
    except (OSError, ValueError) as exc:
        if isinstance(exc, ValueError):
            log("relay protocol error %s: %s" % (label, exc))
    finally:
        log("close %s -- %dB sent, %dB received, %dms"
            % (label, sent, received, (time.time() - started) * 1000))
        try:
            tcp_socket.close()
        except OSError:
            pass


class Handler(SimpleHTTPRequestHandler):
    @property
    def context(self):
        return self.server.context

    def log_message(self, fmt, *args):
        sys.stderr.write("  %s\n" % (fmt % args))

    def log_request(self, code="-", size="-"):
        if self.context.verbose or (isinstance(code, int) and code >= 400):
            super().log_request(code, size)

    def end_headers(self):
        self.send_header("Accept-Ranges", "bytes")
        connect = " ".join(["'self'"] + websocket_origins(self.context.origins))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; connect-src %s; " % connect +
                         "img-src 'self' data:; font-src 'self'; "
                         "script-src 'self' 'wasm-unsafe-eval'; style-src 'self'; "
                         "object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        super().end_headers()

    def _bail(self, code, why):
        log("DENY ws", why)
        self.wfile.write(b"HTTP/1.1 %d\r\n\r\n" % code)
        self.close_connection = True

    def _websocket(self, query):
        origin = self.headers.get("Origin")
        if origin not in self.context.origins:
            return self._bail(403, "origin %s" % origin)
        destination = query.get("dest", [""])[0]
        match = re.match(r"^\[?([0-9a-fA-F:.]+)\]?:(\d+)$", destination)
        if not match:
            return self._bail(400, "malformed dest %r" % destination)
        host, port = match.group(1), int(match.group(2))
        policy = self.context.policy
        if not is_public_ip(host) and not policy.allow_private:
            return self._bail(403, "%s is not a public address -- loopback, LAN "
                              "and link-local are refused so this bridge cannot "
                              "reach hosts a page could not reach itself" % host)
        if port not in policy.allowed_ports:
            return self._bail(403, "port %d not allowed" % port)
        key = self.headers.get("Sec-WebSocket-Key")
        if not key:
            return self._bail(400, "no Sec-WebSocket-Key")
        if self.headers.get("Sec-WebSocket-Version") != "13":
            return self._bail(400, "unsupported WebSocket version")
        try:
            if len(base64.b64decode(key, validate=True)) != 16:
                raise ValueError
        except (ValueError, base64.binascii.Error):
            return self._bail(400, "invalid Sec-WebSocket-Key")
        label = "%s:%d" % (host, port)
        try:
            tcp_socket = socket.create_connection((host, port), CONNECT_TIMEOUT)
        except OSError as exc:
            log("connect FAILED", label, exc)
            return self._bail(502, "connect %s: %s" % (label, exc))
        tcp_socket.settimeout(None)
        log("open", label)
        accept = base64.b64encode(
            hashlib.sha1(key.encode() + WS_GUID).digest()).decode()
        self.wfile.write(
            b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
            b"Connection: Upgrade\r\nSec-WebSocket-Accept: " +
            accept.encode() + b"\r\n\r\n")
        self.wfile.flush()
        self.close_connection = True
        bridge(self.connection, tcp_socket, label)

    def _dns(self, query):
        name = query.get("name", [""])[0]
        if not allowed_name(name, self.context.policy.allowed_domains):
            log("DENY dns", name, "(not in allowlist)")
            return self._text(HTTPStatus.FORBIDDEN, "domain not allowed\n")
        try:
            address, method = lookup(name)
        except OSError as exc:
            log("dns", name, "FAILED:", exc)
            return self._text(HTTPStatus.BAD_GATEWAY, "lookup failed: %s\n" % exc)
        log("dns", name, "->", address, "(%s)" % method)
        return self._text(HTTPStatus.OK, address)

    def _ping(self, query):
        if self.context.watchdog:
            self.context.watchdog.ping(query.get("id", [""])[0],
                                       leaving="bye" in query)
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _text(self, status, body):
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _parse_range(self, total):
        header = self.headers.get("Range")
        if not header:
            return None
        match = RANGE_RE.match(header.strip())
        if not match:
            self.send_error(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            return "invalid"
        first, last = match.group(1), match.group(2)
        try:
            if first:
                start = int(first)
                end = int(last) if last else total - 1
            else:
                if not last:
                    return None
                start = max(0, total - int(last))
                end = total - 1
        except ValueError:
            self.send_error(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            return "invalid"
        if start >= total or start > end:
            self.send_error(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            return "invalid"
        return start, min(end, total - 1)

    def _send_range(self, data, start, end, total, content_type):
        self.send_response(HTTPStatus.PARTIAL_CONTENT)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, total))
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _try_proxy(self):
        local = Path(self.translate_path(self.path.split("?")[0]))
        if local.exists():
            return False
        match = ROUTE_RE.match(self.path)
        if not match:
            return False
        route, rest = match.group(1), match.group(2)
        origin = self.headers.get("Origin")
        if origin is not None and origin not in self.context.origins:
            log("DENY proxy origin", origin)
            self.send_error(HTTPStatus.FORBIDDEN, "origin not allowed")
            return True
        policy = self.context.policy
        if policy.proxy_template:
            url = policy.proxy_template.format(route=route) + rest
        else:
            host = policy.proxy_routes.get(route.lower())
            if not host:
                self.send_error(HTTPStatus.BAD_GATEWAY,
                                "unknown proxy route %r -- known: %s"
                                % (route, ", ".join(sorted(policy.proxy_routes))))
                return True
            url = "https://%s%s" % (host, rest)
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self.send_error(HTTPStatus.BAD_REQUEST, "invalid Content-Length")
            return True
        if length < 0:
            self.send_error(HTTPStatus.BAD_REQUEST, "invalid Content-Length")
            return True
        if length > 8 * 1024 * 1024:
            self.send_error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                            "proxy request body is too large")
            return True
        body = self.rfile.read(length) if length else None
        skipped = {"host", "connection", "content-length", "accept-encoding",
                   "origin", "referer"}
        headers = {key: value for key, value in self.headers.items()
                   if key.lower() not in skipped}
        try:
            status, response_headers, data = self.context.proxy_http.request(
                self.command, url, body=body, headers=headers, tries=1,
                raise_4xx=False)
        except Exception as exc:
            self.log_message("proxy %s %s FAILED: %s", self.command, url, exc)
            self.send_error(HTTPStatus.BAD_GATEWAY, "proxy failed: %s" % exc)
            return True
        self.log_message("proxy %s %s -> %d", self.command, url, status)
        self.send_response(status)
        for key, value in response_headers.items():
            if key.lower() not in ("transfer-encoding", "content-encoding",
                                   "content-length", "connection"):
                self.send_header(key, value)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
        return True

    def do_POST(self):
        parts = urlparse(self.path)
        if parts.path == "/ping":
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return self.send_error(HTTPStatus.BAD_REQUEST,
                                       "invalid Content-Length")
            if length < 0:
                return self.send_error(HTTPStatus.BAD_REQUEST,
                                       "invalid Content-Length")
            if length:
                self.rfile.read(length)
            return self._ping(parse_qs(parts.query))
        if not self._try_proxy():
            self.send_error(HTTPStatus.NOT_FOUND)

    do_PUT = do_POST

    def do_GET(self):
        parts = urlparse(self.path)
        name = parts.path.lstrip("/")
        query = parse_qs(parts.query)
        if self.headers.get("Upgrade", "").lower() == "websocket":
            return self._websocket(query)
        if parts.path == "/dns":
            return self._dns(query)
        if parts.path == "/ping":
            return self._ping(query)
        if name == "progress.json":
            body = json.dumps(self.context.progress.snapshot()).encode()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return self.wfile.write(body)
        store = self.context.store
        if name == "Gw.snapshot" and store and not (
                Path(self.directory) / "Gw.snapshot").exists():
            byte_range = self._parse_range(store.size)
            if byte_range == "invalid":
                return
            if byte_range is None:
                return self.send_error(
                    HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE,
                    "Gw.snapshot is served from cached chunks; range requests only")
            start, end = byte_range
            try:
                data = store.read(start, end - start + 1)
            except FileNotFoundError as exc:
                return self.send_error(HTTPStatus.SERVICE_UNAVAILABLE, str(exc))
            return self._send_range(data, start, end, store.size,
                                    "application/octet-stream")
        if self._try_proxy():
            return
        path = Path(self.translate_path(self.path))
        if path.is_file():
            byte_range = self._parse_range(path.stat().st_size)
            if byte_range == "invalid":
                return
            if byte_range is not None:
                start, end = byte_range
                with path.open("rb") as handle:
                    handle.seek(start)
                    data = handle.read(end - start + 1)
                return self._send_range(data, start, end, path.stat().st_size,
                                        self.guess_type(str(path)))
        super().do_GET()


class GwasmServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False

    def __init__(self, server_address, handler, context):
        self.context = context
        super().__init__(server_address, handler)
