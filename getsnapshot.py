#!/usr/bin/env python3
"""Download Gw.snapshot, with progress.

Chunks land in the content-addressed cache, so this is resumable: interrupt it
and run it again, and it picks up where it stopped. Nothing valid in the cache
is re-fetched.

  python3 getsnapshot.py                    # fill the chunk cache
  python3 getsnapshot.py --out Gw.snapshot  # also assemble the full file
  python3 getsnapshot.py -j 4               # gentler on the CDN

By default it does not write the full image. gwasm.py serves the snapshot
straight from the chunk cache.
"""

import argparse
import json
import shutil
import signal
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import gwpatch
from gwasm_core.patch import (
    MAX_JOBS,
    SNAPSHOT,
    chunk_matches,
    expected_chunk_size,
    fetch_chunk,
    validate_jobs,
)


stop = threading.Event()


def human(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return "%.1f%s" % (n, unit) if unit != "B" else "%dB" % n
        n /= 1024.0


def duration(seconds):
    if seconds is None or seconds != seconds or seconds == float("inf"):
        return "--:--"
    seconds = int(seconds)
    if seconds >= 3600:
        return "%d:%02d:%02d" % (
            seconds // 3600, (seconds % 3600) // 60, seconds % 60)
    return "%02d:%02d" % (seconds // 60, seconds % 60)


class Progress:
    WINDOW = 30.0

    def __init__(self, total_bytes, already):
        self.total = total_bytes
        self.done = already
        self.start = time.time()
        self.samples = [(self.start, already)]
        self.lock = threading.Lock()
        self.last_draw = 0.0

    def advance(self, n):
        with self.lock:
            self.done += n
            now = time.time()
            self.samples.append((now, self.done))
            cutoff = now - self.WINDOW
            while len(self.samples) > 2 and self.samples[0][0] < cutoff:
                self.samples.pop(0)
            if now - self.last_draw >= 0.2:
                self.last_draw = now
                self.draw()

    def rate(self):
        if len(self.samples) < 2:
            return 0.0
        (start_time, start_bytes), (end_time, end_bytes) = (
            self.samples[0], self.samples[-1])
        return ((end_bytes - start_bytes) / (end_time - start_time)
                if end_time > start_time else 0.0)

    def draw(self, final=False):
        fraction = self.done / self.total if self.total else 1.0
        width = 32
        filled = int(width * fraction)
        rate = self.rate()
        eta = (self.total - self.done) / rate if rate > 0 else None
        sys.stderr.write("\r  [%s] %5.1f%%  %s/%s  %s/s  ETA %s " % (
            "#" * filled + "-" * (width - filled), fraction * 100,
            human(self.done), human(self.total), human(rate), duration(eta)))
        if final:
            sys.stderr.write("\n")
        sys.stderr.flush()


def check_space(path, needed):
    try:
        free = shutil.disk_usage(path).free
    except OSError:
        return
    if free < needed:
        sys.exit("not enough space in %s: need %s, have %s"
                 % (path, human(needed), human(free)))
    if free < needed * 2:
        print("warning: %s free, this needs %s" % (human(free), human(needed)),
              file=sys.stderr)


def build_parser():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-k", "--key-file", default="access.key")
    parser.add_argument("-c", "--cache", default="gwpatch-cache", type=Path)
    parser.add_argument("-j", "--jobs", type=int, default=MAX_JOBS,
                        help="concurrent chunk fetches (default: %d, maximum: %d)"
                             % (MAX_JOBS, MAX_JOBS))
    parser.add_argument("-o", "--out", type=Path,
                        help="also assemble the full file here (needs another 4.2GB)")
    return parser


def parse_args(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        validate_jobs(args.jobs)
    except ValueError as exc:
        parser.error(str(exc))
    return args


def main(argv=None):
    args = parse_args(argv)
    stop.clear()
    key = gwpatch.read_key(args.key_file)
    session = gwpatch.make_session(key, args.jobs)
    print("fetching manifest ...")
    manifest = gwpatch.Manifest(json.loads(
        gwpatch.get(session, gwpatch.ROOT + "/manifest.json")))
    match = manifest.find(SNAPSHOT)
    if not match:
        sys.exit("%s is not in the manifest" % SNAPSHOT)
    entry = manifest.files[match]
    hashes = entry["chunkHashes"]
    args.cache.mkdir(parents=True, exist_ok=True)

    unique = list(dict.fromkeys(hashes))
    sizes = {}
    for index, content_hash in enumerate(hashes):
        sizes[content_hash] = expected_chunk_size(
            entry["size"], manifest.chunk_size, index)
    missing = [content_hash for content_hash in unique
               if not chunk_matches(args.cache / content_hash, content_hash,
                                    sizes[content_hash])]
    have_bytes = entry["size"] - sum(sizes[value] for value in missing)
    have_bytes = max(0, min(have_bytes, entry["size"]))
    print("%s: %s in %d chunks (%d unique)"
          % (SNAPSHOT, human(entry["size"]), len(hashes), len(unique)))
    print("cached: %d/%d chunks -- %d to fetch"
          % (len(unique) - len(missing), len(unique), len(missing)))

    if not missing:
        print("nothing to download")
    else:
        check_space(args.cache, sum(sizes[value] for value in missing))
        print("downloading with %d workers (ctrl-c to stop; rerun to resume)"
              % args.jobs)
        progress = Progress(entry["size"], have_bytes)
        failures = []
        failures_lock = threading.Lock()

        def fetch(content_hash):
            if stop.is_set():
                return
            try:
                data = fetch_chunk(session, args.cache, content_hash,
                                   manifest.compression, gwpatch.ROOT)
                progress.advance(len(data))
            except Exception as exc:
                with failures_lock:
                    failures.append((content_hash, str(exc)))
                stop.set()

        def on_sigint(*_):
            if not stop.is_set():
                stop.set()
                sys.stderr.write(
                    "\n  stopping -- cached chunks are kept, rerun to resume\n")
        signal.signal(signal.SIGINT, on_sigint)
        started = time.time()
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            list(pool.map(fetch, missing))
        progress.draw(final=True)
        if failures:
            print("stopped after %d failure(s); first: %s -- %s"
                  % (len(failures), failures[0][0][:12], failures[0][1]),
                  file=sys.stderr)
            return 1
        if stop.is_set():
            return 1
        print("fetched %d chunks in %s"
              % (len(missing), duration(time.time() - started)))

    if args.out:
        parent = args.out.parent if args.out.parent.name else Path(".")
        check_space(parent, entry["size"])
        print("assembling %s ..." % args.out)
        gwpatch.assemble(manifest, entry, args.out, args.cache)
        print("wrote %s (%s)" % (args.out, human(args.out.stat().st_size)))
    else:
        print("chunk cache is complete; gwasm.py serves the snapshot directly.\n"
              "pass --out to write the full file as well.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
