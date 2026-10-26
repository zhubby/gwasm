#!/usr/bin/env python3
"""Standalone patcher for the Guild Wars Reforged web client VFS.

  python3 gwpatch.py                 # list every file in the manifest
  python3 gwpatch.py Gw.wasm         # reassemble and write Gw.wasm
  python3 gwpatch.py Gw.wasm -j 8    # ...with 8 concurrent fetches

Python 3.8+ and the standard library are sufficient.
"""

import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from gwasm_core.http import Http
from gwasm_core.patch import (
    HASHES,
    MAX_JOBS,
    PATCH_ROOT,
    Manifest,
    assemble,
    chunk_matches,
    expected_chunk_size,
    fetch_chunk,
    validate_jobs,
)


ROOT = PATCH_ROOT
UA = "gwpatch/0.2 (interop research)"
_HASHES = HASHES


def read_key(path):
    try:
        key = Path(path).read_text().strip()
    except OSError as exc:
        sys.exit("could not read key file %s: %s\n"
                 "run gwkey.py against a base.apk first" % (path, exc))
    if not key:
        sys.exit("key file %s is empty" % path)
    return key


def make_session(key, jobs=None):
    """Return the shared standard-library HTTP client.

    jobs remains accepted for compatibility; the client keeps one pooled
    connection per worker thread and needs no pool-size setting.
    """
    del jobs
    return Http({"X-Access-Key": key, "User-Agent": UA,
                 "Accept-Encoding": "identity", "Connection": "keep-alive"})


def get(session, url, tries=4):
    return session.get(url, tries=tries)


def prefetch(session, manifest, entry, cache, jobs, algo=None):
    """Fetch each missing or invalid content-addressed chunk once."""
    del algo
    hashes = entry["chunkHashes"]
    sizes = {content_hash: expected_chunk_size(
                entry["size"], manifest.chunk_size, index)
             for index, content_hash in enumerate(hashes)}
    missing = sorted({content_hash for content_hash in hashes
                      if not chunk_matches(cache / content_hash, content_hash,
                                           sizes[content_hash])})
    if not missing:
        print("  all %d chunks already cached" % len(set(hashes)), file=sys.stderr)
        return
    done = 0
    lock = threading.Lock()

    def fetch(content_hash):
        fetch_chunk(session, cache, content_hash, manifest.compression, ROOT)

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = {pool.submit(fetch, content_hash): content_hash
                   for content_hash in missing}
        try:
            for future in as_completed(futures):
                future.result()
                with lock:
                    done += 1
                    print("\r  fetched %d/%d chunks" % (done, len(missing)),
                          end="", file=sys.stderr, flush=True)
        except BaseException:
            for future in futures:
                future.cancel()
            raise
    print(file=sys.stderr)


def build_parser():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("target", nargs="?",
                        help="file to fetch, matched on suffix (omit to list)")
    parser.add_argument("-k", "--key-file", default="access.key",
                        help="file holding the access key (default: access.key)")
    parser.add_argument("-c", "--cache", default="gwpatch-cache", type=Path,
                        help="chunk cache directory (default: gwpatch-cache)")
    parser.add_argument("-o", "--out",
                        help="output path (default: the file's basename)")
    parser.add_argument("-j", "--jobs", type=int, default=4,
                        help="concurrent chunk fetches (default: 4, maximum: %d)"
                             % MAX_JOBS)
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
    key = read_key(args.key_file)
    session = make_session(key, args.jobs)
    manifest = Manifest(json.loads(get(session, ROOT + "/manifest.json")))
    print("compression=%s chunkSize=%d files=%d"
          % (manifest.compression, manifest.chunk_size, len(manifest.files)))
    if not args.target:
        for path, entry in sorted(manifest.files.items()):
            print("  %12d  %5d chunks  %s"
                  % (entry["size"], len(entry["chunkHashes"]), path))
        return 0

    match = next((path for path in manifest.files
                  if path.lower().endswith(args.target.lower())), None)
    if not match:
        sys.exit("not in manifest: %s" % args.target)
    entry = manifest.files[match]
    hashes = entry["chunkHashes"]
    algorithm = HASHES.get(len(hashes[0])) if hashes else None
    print("%s: %d bytes, %d chunks (%d unique), %s"
          % (match, entry["size"], len(hashes), len(set(hashes)),
             algorithm or "unknown hash"))
    args.cache.mkdir(parents=True, exist_ok=True)
    out = Path(args.out) if args.out else Path(Path(match).name)
    started = time.time()
    prefetch(session, manifest, entry, args.cache, args.jobs, algorithm)
    assemble(manifest, entry, out, args.cache)
    print("wrote %s (%d bytes) in %.1fs"
          % (out, out.stat().st_size, time.time() - started))
    return 0


if __name__ == "__main__":
    sys.exit(main())
