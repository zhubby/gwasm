"""Small pooled HTTP client shared by the launcher and patch tools."""

import http.client
import threading
import time
from urllib.parse import urlsplit


FATAL_STATUSES = (401, 403, 404)


class HttpError(Exception):
    def __init__(self, status, what):
        super().__init__("HTTP %d for %s" % (status, what))
        self.status = status


class Http:
    """Reuse one HTTP connection per thread and destination."""

    def __init__(self, headers=None):
        self.headers = headers or {}
        self._local = threading.local()

    def _conn(self, scheme, host, fresh=False):
        conns = getattr(self._local, "conns", None)
        if conns is None:
            conns = self._local.conns = {}
        key = (scheme, host)
        if fresh and key in conns:
            conns.pop(key).close()
        if key not in conns:
            cls = (http.client.HTTPSConnection if scheme == "https"
                   else http.client.HTTPConnection)
            conns[key] = cls(host, timeout=60)
        return conns[key]

    def request(self, method, url, body=None, headers=None, tries=4, raise_4xx=True):
        """Return ``(status, headers, body)`` for an absolute URL."""
        parsed = urlsplit(url)
        path = parsed.path + ("?" + parsed.query if parsed.query else "")
        request_headers = {**self.headers, **(headers or {})}
        for attempt in range(tries):
            conn = self._conn(parsed.scheme, parsed.netloc, fresh=attempt > 0)
            try:
                conn.request(method, path, body=body, headers=request_headers)
                response = conn.getresponse()
                data = response.read()
                if raise_4xx and response.status >= 400:
                    raise HttpError(response.status, url)
                return response.status, response.headers, data
            except HttpError as exc:
                if exc.status in FATAL_STATUSES or attempt == tries - 1:
                    raise
                time.sleep(2 ** attempt)
            except (OSError, http.client.HTTPException):
                self._conn(parsed.scheme, parsed.netloc, fresh=True)
                if attempt == tries - 1:
                    raise
                time.sleep(2 ** attempt)

    def get(self, url, tries=4):
        return self.request("GET", url, tries=tries)[2]
