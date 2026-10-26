"""ArenaNet patch manifest, content-addressed cache, and snapshot access."""

import gzip
import hashlib
import json
import os
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .http import Http
from .runtime import atomic_write_text, duration, human, log


PATCH_ROOT = "https://patching.1.arenanetworks.com"
SNAPSHOT = "Gw.snapshot"
PREFETCH_JOBS = 8
MAX_JOBS = 8
HASHES = {32: "md5", 40: "sha1", 64: "sha256"}


def validate_jobs(jobs):
    if jobs < 1 or jobs > MAX_JOBS:
        raise ValueError("--jobs must be between 1 and %d" % MAX_JOBS)


def patch_http(access_key, user_agent):
    return Http({"X-Access-Key": access_key, "User-Agent": user_agent,
                 "Accept-Encoding": "identity", "Connection": "keep-alive"})


class Manifest:
    def __init__(self, raw):
        if not isinstance(raw, dict):
            raise ValueError("manifest must be an object")
        if raw.get("compressionMode") not in ("none", "gzip"):
            raise ValueError("unsupported compression: %r" % raw.get("compressionMode"))
        self.compression = raw["compressionMode"]
        self.chunk_size = raw["chunkSize"]
        if (isinstance(self.chunk_size, bool) or
                not isinstance(self.chunk_size, int) or self.chunk_size <= 0):
            raise ValueError("bad chunkSize: %r" % self.chunk_size)

        dirs = raw.get("directories", [])
        if dirs is None:
            dirs = []
        if not isinstance(dirs, list):
            raise ValueError("bad directories")
        for index, directory in enumerate(dirs):
            if (not isinstance(directory, dict) or
                    not isinstance(directory.get("name"), str) or
                    not directory["name"]):
                raise ValueError("bad directory %d" % index)
            parent = directory.get("parentIndex")
            if (isinstance(parent, bool) or
                    (parent is not None and
                     (not isinstance(parent, int) or parent < 0 or
                      parent >= len(dirs)))):
                raise ValueError("bad directory parent for %s"
                                 % directory["name"])
        paths = []
        for index, directory in enumerate(dirs):
            parts = [directory["name"]]
            current = directory.get("parentIndex")
            seen = set()
            while current:
                if (isinstance(current, bool) or not isinstance(current, int) or
                        current < 0 or current >= len(dirs) or current in seen):
                    raise ValueError("bad directory parent for %s" % directory["name"])
                seen.add(current)
                parts.append(dirs[current]["name"])
                current = dirs[current].get("parentIndex")
            paths.append("/".join(reversed(parts)))

        files = raw.get("files", [])
        if files is None:
            files = []
        if not isinstance(files, list):
            raise ValueError("bad files")
        self.files = {}
        for entry in files:
            if (not isinstance(entry, dict) or
                    not isinstance(entry.get("name"), str) or
                    not entry["name"]):
                raise ValueError("bad file entry")
            size = entry.get("size")
            if isinstance(size, bool) or not isinstance(size, int) or size < 0:
                raise ValueError("bad size for %s" % entry["name"])
            parent = entry.get("parentIndex")
            if (isinstance(parent, bool) or
                    (parent is not None and
                     (not isinstance(parent, int) or parent < 0 or
                      parent >= len(paths)))):
                raise ValueError("bad file parent for %s" % entry["name"])
            path = "%s/%s" % (paths[parent], entry["name"]) if parent else entry["name"]
            expected = (size + self.chunk_size - 1) // self.chunk_size
            hashes = entry.get("chunkHashes")
            if not isinstance(hashes, list) or any(
                    not isinstance(value, str) or len(value) not in HASHES for value in hashes):
                raise ValueError("bad chunk hashes for %s" % path)
            if len(hashes) != expected:
                raise ValueError("chunk count mismatch for %s" % path)
            if path in self.files:
                raise ValueError("duplicate file path %s" % path)
            self.files[path] = entry

    def find(self, basename):
        return next((path for path in self.files
                     if path.split("/")[-1] == basename), None)


def expected_chunk_size(size, chunk_size, index):
    return min(chunk_size, size - index * chunk_size)


def chunk_matches_bytes(data, content_hash):
    algorithm = HASHES.get(len(content_hash))
    return bool(algorithm and
                hashlib.new(algorithm, data).hexdigest() == content_hash.lower())


def chunk_matches(path, content_hash, expected_size):
    algorithm = HASHES.get(len(content_hash))
    if not path.is_file() or not algorithm:
        return False
    try:
        if path.stat().st_size != expected_size:
            return False
        digest = hashlib.new(algorithm)
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest() == content_hash.lower()
    except OSError:
        return False


def file_matches(path, entry, chunk_size):
    if not path.is_file() or path.stat().st_size != entry["size"]:
        return False
    try:
        with path.open("rb") as handle:
            for index, content_hash in enumerate(entry["chunkHashes"]):
                expected = expected_chunk_size(entry["size"], chunk_size, index)
                data = handle.read(expected)
                if len(data) != expected or not chunk_matches_bytes(data, content_hash):
                    return False
            return handle.read(1) == b""
    except OSError:
        return False


def store_chunk(cache, content_hash, data):
    tmp = cache / ("%s.%d.tmp" % (content_hash, os.getpid()))
    tmp.write_bytes(data)
    os.replace(tmp, cache / content_hash)


def fetch_chunk(http, cache, content_hash, compression="none", root=PATCH_ROOT):
    data = http.get("%s/%s.bin" % (root.rstrip("/"), content_hash))
    if compression == "gzip":
        data = gzip.decompress(data)
    if not chunk_matches_bytes(data, content_hash):
        raise ValueError("hash mismatch on chunk %s" % content_hash)
    cache.mkdir(parents=True, exist_ok=True)
    store_chunk(cache, content_hash, data)
    return data


def assemble(manifest, entry, out, cache):
    tmp = out.with_suffix(out.suffix + ".part")
    try:
        with tmp.open("wb") as handle:
            for index, content_hash in enumerate(entry["chunkHashes"]):
                data = (cache / content_hash).read_bytes()
                expected = expected_chunk_size(
                    entry["size"], manifest.chunk_size, index)
                if (len(data) != expected or
                        not chunk_matches_bytes(data, content_hash)):
                    raise ValueError("invalid cached chunk %d (%s)"
                                     % (index, content_hash))
                handle.write(data)
        if tmp.stat().st_size != entry["size"]:
            raise ValueError("assembled size %d != manifest size %d"
                             % (tmp.stat().st_size, entry["size"]))
        os.replace(tmp, out)
    except BaseException:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise


def update(dest, cache, build, builds, common, http, jobs=4, progress=None,
           stop=None, root=PATCH_ROOT):
    """Download and assemble the selected client build."""
    stop = stop or threading.Event()
    log("checking for updates ...")
    manifest = Manifest(json.loads(http.get(root.rstrip("/") + "/manifest.json")))
    dest.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)

    selected = list(builds) if build == "both" else [build]
    wanted = []
    for name in [name for selected_build in selected for name in builds[selected_build]] + common:
        path = manifest.find(name)
        if not path:
            log("  ! %s is not in the manifest, skipping" % name)
            continue
        entry = manifest.files[path]
        out = dest / name
        if not file_matches(out, entry, manifest.chunk_size):
            wanted.append((name, entry, out))

    todo = {}
    for _, entry, _ in wanted:
        for index, content_hash in enumerate(entry["chunkHashes"]):
            size = expected_chunk_size(entry["size"], manifest.chunk_size, index)
            if not chunk_matches(cache / content_hash, content_hash, size):
                todo.setdefault(content_hash, size)
    total = sum(todo.values())
    if progress and total:
        progress.set(phase="client", label="Downloading Guild Wars…",
                     received=0, total=total)

    received = [0]
    received_lock = threading.Lock()
    for name, entry, out in wanted:
        sizes = {content_hash: expected_chunk_size(
                    entry["size"], manifest.chunk_size, index)
                 for index, content_hash in enumerate(entry["chunkHashes"])}
        missing = sorted({content_hash for content_hash in entry["chunkHashes"]
                          if not chunk_matches(cache / content_hash, content_hash,
                                               sizes[content_hash])})
        log("  %s: %.1f MB, %d chunks (%d to fetch)"
            % (name, entry["size"] / 1e6, len(entry["chunkHashes"]), len(missing)))

        def grab(content_hash):
            if stop.is_set():
                return
            fetch_chunk(http, cache, content_hash, manifest.compression, root)
            with received_lock:
                received[0] += sizes[content_hash]
                current = received[0]
            if progress:
                progress.set(received=current)
            if total:
                print("\r    %d%%" % (100 * current // total), end="", flush=True)

        if missing:
            with ThreadPoolExecutor(max_workers=jobs) as pool:
                list(pool.map(grab, missing))
            print()
            if stop.is_set():
                return
        if progress:
            progress.set(label="Assembling %s…" % name)
        assemble(manifest, entry, out, cache)
        log("  %s: ready" % name)

    path = manifest.find(SNAPSHOT)
    if path:
        entry = manifest.files[path]
        atomic_write_text(dest / "snapshot-chunks.json", json.dumps({
            "size": entry["size"], "chunkSize": manifest.chunk_size,
            "cache": str(cache), "chunkHashes": entry["chunkHashes"],
        }))
    log("up to date")


class ChunkStore:
    """Random access over a snapshot stored as content-addressed chunks."""

    def __init__(self, index_path, http=None, root=PATCH_ROOT):
        index = json.loads(Path(index_path).read_text())
        self.size = index["size"]
        self.chunk_size = index["chunkSize"]
        self.hashes = index["chunkHashes"]
        self.cache = Path(index["cache"])
        self.http = http
        self.root = root
        self.fetched = 0
        self._locks = {}
        self._guard = threading.Lock()
        self._verified = {}
        self.touched = set()
        self._touched_dirty = False
        self.quiet = False

    @staticmethod
    def _signature(path):
        try:
            stat = path.stat()
            return stat.st_size, stat.st_mtime_ns
        except OSError:
            return None

    def _cached_chunk_ok(self, path, content_hash, expected):
        signature = self._signature(path)
        if signature is None or signature[0] != expected:
            self._verified.pop(content_hash, None)
            return False
        if self._verified.get(content_hash) == signature:
            return True
        if chunk_matches(path, content_hash, expected):
            self._verified[content_hash] = signature
            return True
        self._verified.pop(content_hash, None)
        return False

    def _ensure(self, index):
        content_hash = self.hashes[index]
        path = self.cache / content_hash
        expected = expected_chunk_size(self.size, self.chunk_size, index)
        if self._cached_chunk_ok(path, content_hash, expected):
            return path
        if not self.http:
            raise FileNotFoundError("chunk %d (%s) not cached, and offline"
                                    % (index, content_hash))
        with self._guard:
            lock = self._locks.setdefault(content_hash, threading.Lock())
        with lock:
            if self._cached_chunk_ok(path, content_hash, expected):
                return path
            fetch_chunk(self.http, self.cache, content_hash, root=self.root)
            self.fetched += 1
            self._verified[content_hash] = self._signature(path)
            if not self.quiet:
                log("  snapshot: fetched chunk %d (%d this session)"
                    % (index, self.fetched))
        return path

    @property
    def boot_list(self):
        return self.cache / "boot-chunks.json"

    def save_touched(self):
        if not self._touched_dirty:
            return
        known = set()
        try:
            known = set(json.loads(self.boot_list.read_text())["chunks"])
        except Exception:
            pass
        merged = sorted(known | self.touched)
        if merged != sorted(known):
            atomic_write_text(self.boot_list, json.dumps({
                "chunkSize": self.chunk_size, "count": len(self.hashes),
                "chunks": merged,
            }))
        self._touched_dirty = False

    def prefetch(self, progress, jobs=PREFETCH_JOBS, stop=None):
        if not self.http:
            return
        stop = stop or threading.Event()
        try:
            wanted = json.loads(self.boot_list.read_text())["chunks"]
        except Exception:
            return
        todo = [index for index in wanted if index < len(self.hashes)
                and not chunk_matches(
                    self.cache / self.hashes[index], self.hashes[index],
                    expected_chunk_size(self.size, self.chunk_size, index))]
        if not todo:
            return
        total = len(todo)
        done = [0]
        done_lock = threading.Lock()
        log("prefetch: warming %d chunks (%.0f MB) with %d workers"
            % (total, total * self.chunk_size / 1e6, jobs))
        progress.set(prefetch={"done": 0, "total": total})

        def grab(index):
            if stop.is_set():
                return
            try:
                self._ensure(index)
            except Exception:
                pass
            with done_lock:
                done[0] += 1
                current = done[0]
            if current % 8 == 0 or current == total:
                progress.set(prefetch={"done": current, "total": total})

        self.quiet = True
        try:
            with ThreadPoolExecutor(max_workers=jobs) as pool:
                list(pool.map(grab, todo))
        finally:
            self.quiet = False
        log("prefetch: done")
        progress.set(prefetch={"done": total, "total": total})

    def download_all(self, progress, jobs, stop):
        todo = [index for index, content_hash in enumerate(self.hashes)
                if not chunk_matches(
                    self.cache / content_hash, content_hash,
                    expected_chunk_size(self.size, self.chunk_size, index))]
        if not todo:
            log("image: already complete (%d chunks)" % len(self.hashes))
            return True
        need = sum(expected_chunk_size(self.size, self.chunk_size, index)
                   for index in todo)
        free = shutil.disk_usage(self.cache).free
        log("image: %d of %d chunks missing (%s), %s free"
            % (len(todo), len(self.hashes), human(need), human(free)))
        if free < need + 512 * 1024 * 1024:
            progress.set(phase="error",
                         error="Not enough disk space: %s needed, %s free."
                               % (human(need), human(free)))
            return False

        initial = self.size - need
        received = [initial]
        progress.set(phase="image", label="Downloading game data…",
                     received=initial, total=self.size, done=False)
        started = time.time()
        lock = threading.Lock()
        failed = []
        last = [started]
        self.quiet = True

        def grab(index):
            if stop.is_set():
                return
            size = expected_chunk_size(self.size, self.chunk_size, index)
            try:
                self._ensure(index)
            except Exception as exc:
                with lock:
                    failed.append((index, str(exc)))
                return
            with lock:
                received[0] += size
                current = received[0]
                elapsed = time.time() - started
                due = time.time() - last[0] >= 10
                if due:
                    last[0] = time.time()
            rate = (current - initial) / elapsed if elapsed > 0.5 else 0
            eta = duration((self.size - current) / rate) if rate > 0 else ""
            progress.set(received=min(current, self.size), total=self.size,
                         note="%.1f MB/s · %s remaining" % (rate / 1e6, eta)
                              if rate else "")
            if due:
                log("image: %s of %s (%.0f%%) · %.1f MB/s · %s remaining"
                    % (human(current), human(self.size),
                       100.0 * current / self.size, rate / 1e6, eta))

        try:
            with ThreadPoolExecutor(max_workers=jobs) as pool:
                list(pool.map(grab, todo))
        finally:
            self.quiet = False
        if stop.is_set():
            return False
        if failed:
            log("image: %d chunks failed, e.g. %s" % (len(failed), failed[0][1]))
            progress.set(phase="error",
                         error="%d chunks could not be downloaded. Restart to retry."
                               % len(failed))
            return False
        log("image: complete in %s" % duration(time.time() - started))
        progress.set(note="")
        return True

    def read(self, start, length):
        if start < 0 or length < 0:
            raise ValueError("negative snapshot range")
        output = bytearray()
        position = start
        remaining = length
        while remaining > 0:
            index = position // self.chunk_size
            if index >= len(self.hashes):
                break
            if index not in self.touched:
                self.touched.add(index)
                self._touched_dirty = True
            data = self._ensure(index).read_bytes()
            offset = position - index * self.chunk_size
            take = min(remaining, len(data) - offset)
            output += data[offset:offset + take]
            position += take
            remaining -= take
        return bytes(output)
