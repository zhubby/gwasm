"""Process lifecycle, progress reporting, and static asset publishing."""

import json
import os
import threading
import time


ART_CREDIT = ('Screenshots by <a href="https://bloogum.net/guildwars/">'
              'Snapshot Henchman</a>')


def log(*args):
    print(time.strftime("%H:%M:%S"), *args, flush=True)


def human(n):
    return "%.2f GB" % (n / 1e9) if n >= 1e9 else "%.0f MB" % (n / 1e6)


def duration(seconds):
    seconds = int(seconds)
    if seconds < 90:
        return "%ds" % seconds
    if seconds < 5400:
        return "%d min" % round(seconds / 60)
    return "%.1f hours" % (seconds / 3600)


class Watchdog:
    """Stop the server after the final browser tab has gone."""

    GRACE = 3.0
    IDLE = 150.0
    FORGET = 30.0

    def __init__(self, httpd):
        self.httpd = httpd
        self.lock = threading.Lock()
        self.live = {}
        self.gone = {}
        self.seen = False
        self.empty_since = None

    def ping(self, tab, leaving=False):
        now = time.time()
        with self.lock:
            self.seen = True
            if leaving:
                self.gone[tab] = now
                self.live.pop(tab, None)
            elif tab not in self.gone:
                self.live[tab] = now
            for tab_id, when in list(self.gone.items()):
                if now - when > self.FORGET:
                    del self.gone[tab_id]

    def run(self):
        reason = None
        while reason is None:
            time.sleep(0.5)
            now = time.time()
            with self.lock:
                if not self.seen:
                    continue
                for tab_id, when in list(self.live.items()):
                    if now - when > self.IDLE:
                        del self.live[tab_id]
                if self.live:
                    self.empty_since = None
                    continue
                if self.empty_since is None:
                    self.empty_since = now
                elif now - self.empty_since >= self.GRACE:
                    reason = "browser closed"
        log("%s -- stopping" % reason)
        self.httpd.shutdown()


class Progress:
    """Thread-safe loading state served at ``/progress.json``."""

    def __init__(self):
        self._lock = threading.Lock()
        self._s = {"phase": "starting", "label": "Starting…",
                   "received": 0, "total": 0, "done": False, "error": None,
                   "prefetch": None, "note": ""}

    def set(self, **values):
        with self._lock:
            self._s.update(values)

    def snapshot(self):
        with self._lock:
            return dict(self._s)


def publish(src, out):
    """Copy the files in ``src`` into the served output directory."""
    out.mkdir(parents=True, exist_ok=True)
    names = []
    for source in sorted(src.iterdir()) if src.is_dir() else []:
        if not source.is_file() or source.name == "index.json":
            continue
        served = out / source.name
        if not served.exists() or served.read_bytes() != source.read_bytes():
            served.write_bytes(source.read_bytes())
        names.append(source.name)
    return names


def copy_art(src, dest):
    out = dest / "images"
    names = publish(src, out)
    (out / "index.json").write_text(json.dumps({
        "logo": "images/logo.webp" if "logo.webp" in names else None,
        "backgrounds": ["images/" + name for name in names if name.startswith("bg")],
        "credit": ART_CREDIT,
    }))


def atomic_write_text(path, text):
    """Write small runtime metadata without exposing a partial file."""
    tmp = path.with_suffix(path.suffix + ".%d.tmp" % os.getpid())
    tmp.write_text(text)
    os.replace(tmp, path)
