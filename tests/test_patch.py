#!/usr/bin/env python3
"""Patch manifest, cache, assembly, and lazy snapshot tests."""

import gzip
import hashlib
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gwasm_core.patch import (
    ChunkStore,
    Manifest,
    assemble,
    chunk_matches,
    fetch_chunk,
    file_matches,
    update,
    validate_jobs,
)
from gwasm_core.runtime import Progress
from gwpatch import prefetch


def digest(data):
    return hashlib.sha256(data).hexdigest()


class FakeHttp:
    def __init__(self, objects, delay=0):
        self.objects = objects
        self.delay = delay
        self.calls = []
        self.lock = threading.Lock()

    def get(self, url, tries=4):
        del tries
        key = url.rsplit("/", 1)[-1]
        with self.lock:
            self.calls.append(key)
        if self.delay:
            time.sleep(self.delay)
        return self.objects[key]


class ManifestTests(unittest.TestCase):
    def base(self):
        return {
            "compressionMode": "none",
            "chunkSize": 4,
            "files": [{
                "name": "a.bin", "size": 6,
                "chunkHashes": ["a" * 64, "b" * 64],
            }],
        }

    def test_valid_manifest_and_lookup(self):
        manifest = Manifest(self.base())
        self.assertEqual(manifest.find("a.bin"), "a.bin")
        self.assertIsNone(manifest.find("missing"))

    def test_rejects_invalid_shape_and_compression(self):
        invalid = [
            [],
            {**self.base(), "compressionMode": "brotli"},
            {**self.base(), "chunkSize": 0},
            {**self.base(), "chunkSize": True},
            {**self.base(), "directories": {}},
            {**self.base(), "files": {}},
        ]
        for raw in invalid:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                Manifest(raw)

    def test_rejects_parents_cycles_hashes_and_counts(self):
        cases = [
            {"directories": [{"name": "root"}, {
                "name": "loop", "parentIndex": 1}], "files": []},
            {"directories": [{"name": "child", "parentIndex": 1},
                             {"parentIndex": 0}], "files": []},
            {"directories": [{"name": "bad", "parentIndex": ""}],
             "files": []},
            {"files": [{"name": "a", "size": 0, "parentIndex": 9,
                        "chunkHashes": []}]},
            {"files": [{"name": "a", "size": 0, "parentIndex": False,
                        "chunkHashes": []}]},
            {"files": [{"name": "a", "size": -1, "chunkHashes": []}]},
            {"files": [{"name": "a", "size": 1, "chunkHashes": ["bad"]}]},
            {"files": [{"name": "a", "size": 9,
                        "chunkHashes": ["a" * 64]}]},
            {"files": [
                {"name": "a", "size": 0, "chunkHashes": []},
                {"name": "a", "size": 0, "chunkHashes": []},
            ]},
        ]
        for values in cases:
            raw = {"compressionMode": "none", "chunkSize": 4, **values}
            with self.subTest(values=values), self.assertRaises(ValueError):
                Manifest(raw)

    def test_job_limit(self):
        for value in (1, 8):
            validate_jobs(value)
        for value in (0, 9):
            with self.assertRaises(ValueError):
                validate_jobs(value)


class ChunkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.cache = self.root / "cache"
        self.cache.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def test_fetch_validates_plain_and_gzip_chunks(self):
        data = b"verified chunk"
        content_hash = digest(data)
        plain = FakeHttp({content_hash + ".bin": data})
        self.assertEqual(
            fetch_chunk(plain, self.cache, content_hash, root="http://patch"),
            data)
        (self.cache / content_hash).unlink()
        compressed = FakeHttp({content_hash + ".bin": gzip.compress(data)})
        self.assertEqual(
            fetch_chunk(compressed, self.cache, content_hash, "gzip",
                        "http://patch"), data)
        self.assertTrue(chunk_matches(
            self.cache / content_hash, content_hash, len(data)))

    def test_hash_mismatch_is_not_cached(self):
        content_hash = digest(b"expected")
        with self.assertRaises(ValueError):
            fetch_chunk(
                FakeHttp({content_hash + ".bin": b"wrong"}),
                self.cache, content_hash, root="http://patch")
        self.assertFalse((self.cache / content_hash).exists())

    def test_assemble_checks_chunks_and_replaces_atomically(self):
        chunks = [b"abcd", b"ef"]
        hashes = [digest(value) for value in chunks]
        for content_hash, data in zip(hashes, chunks):
            (self.cache / content_hash).write_bytes(data)
        manifest = Manifest({
            "compressionMode": "none", "chunkSize": 4,
            "files": [{"name": "a.bin", "size": 6, "chunkHashes": hashes}],
        })
        output = self.root / "a.bin"
        assemble(manifest, manifest.files["a.bin"], output, self.cache)
        self.assertEqual(output.read_bytes(), b"abcdef")
        self.assertTrue(file_matches(
            output, manifest.files["a.bin"], manifest.chunk_size))
        (self.cache / hashes[1]).write_bytes(b"xx")
        with self.assertRaises(ValueError):
            assemble(manifest, manifest.files["a.bin"], output, self.cache)
        self.assertEqual(output.read_bytes(), b"abcdef")
        self.assertFalse((self.root / "a.bin.part").exists())

    def test_patcher_prefetch_repairs_corrupt_cached_chunk(self):
        data = b"verified"
        content_hash = digest(data)
        entry = {"name": "a.bin", "size": len(data),
                 "chunkHashes": [content_hash]}
        manifest = Manifest({
            "compressionMode": "none", "chunkSize": len(data),
            "files": [entry],
        })
        (self.cache / content_hash).write_bytes(b"corrupt!")
        http = FakeHttp({content_hash + ".bin": data})
        prefetch(http, manifest, manifest.files["a.bin"], self.cache, 1)
        self.assertEqual((self.cache / content_hash).read_bytes(), data)
        self.assertEqual(http.calls, [content_hash + ".bin"])

    def make_store(self, cached=1, delay=0):
        chunks = [b"a" * 8, b"b" * 8, b"tail"]
        hashes = [digest(value) for value in chunks]
        for content_hash, data in list(zip(hashes, chunks))[:cached]:
            (self.cache / content_hash).write_bytes(data)
        index = self.root / "snapshot-chunks.json"
        index.write_text(json.dumps({
            "size": sum(map(len, chunks)), "chunkSize": 8,
            "cache": str(self.cache), "chunkHashes": hashes,
        }))
        objects = {content_hash + ".bin": data
                   for content_hash, data in zip(hashes, chunks)}
        return chunks, hashes, index, FakeHttp(objects, delay)

    def test_lazy_read_repairs_corruption_and_fetches_once(self):
        chunks, hashes, index, http = self.make_store(cached=1, delay=0.05)
        store = ChunkStore(index, http, root="http://patch")
        results = []
        threads = [threading.Thread(
            target=lambda: results.append(store.read(8, 8))) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(results, [chunks[1]] * 6)
        self.assertEqual(http.calls.count(hashes[1] + ".bin"), 1)

        (self.cache / hashes[0]).write_bytes(b"corrupt!")
        self.assertEqual(store.read(0, 8), chunks[0])
        self.assertEqual(http.calls.count(hashes[0] + ".bin"), 1)
        self.assertEqual(store.read(0, 8), chunks[0])
        self.assertEqual(http.calls.count(hashes[0] + ".bin"), 1)

    def test_offline_missing_chunk_and_negative_ranges_fail(self):
        _, _, index, _ = self.make_store(cached=1)
        store = ChunkStore(index)
        with self.assertRaises(FileNotFoundError):
            store.read(8, 8)
        for start, length in ((-1, 1), (0, -1)):
            with self.assertRaises(ValueError):
                store.read(start, length)

    def test_boot_working_set_merges_and_prefetches(self):
        chunks, hashes, index, http = self.make_store(cached=1)
        store = ChunkStore(index, http, root="http://patch")
        store.read(8, 8)
        store.save_touched()
        other = ChunkStore(index, http, root="http://patch")
        other.read(0, 8)
        other.save_touched()
        record = json.loads(other.boot_list.read_text())
        self.assertEqual(record["chunks"], [0, 1])

        for content_hash in hashes:
            path = self.cache / content_hash
            if path.exists():
                path.unlink()
        http.calls.clear()
        ChunkStore(index, http, root="http://patch").prefetch(
            Progress(), jobs=2)
        self.assertEqual(
            sorted(http.calls), sorted([hashes[0] + ".bin", hashes[1] + ".bin"]))

    def test_update_materializes_build_and_snapshot_index(self):
        client = b"client-js"
        snapshot = b"snapshot"
        client_hash = digest(client)
        snapshot_hash = digest(snapshot)
        raw = {
            "compressionMode": "none", "chunkSize": 64,
            "files": [
                {"name": "Gw.js", "size": len(client),
                 "chunkHashes": [client_hash]},
                {"name": "Gw.snapshot", "size": len(snapshot),
                 "chunkHashes": [snapshot_hash]},
            ],
        }
        http = FakeHttp({
            "manifest.json": json.dumps(raw).encode(),
            client_hash + ".bin": client,
            snapshot_hash + ".bin": snapshot,
        })
        destination = self.root / "dist"
        update(destination, self.cache, "asyncify",
               {"asyncify": ["Gw.js"]}, [], http, jobs=2,
               root="http://patch")
        self.assertEqual((destination / "Gw.js").read_bytes(), client)
        index = json.loads((destination / "snapshot-chunks.json").read_text())
        self.assertEqual(index["chunkHashes"], [snapshot_hash])
        self.assertEqual(index["cache"], str(self.cache))


if __name__ == "__main__":
    unittest.main()
