#!/usr/bin/env python3
"""Runtime progress, publishing, atomic metadata, and watchdog tests."""

import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gwasm_core.runtime import (
    Progress,
    Watchdog,
    atomic_write_text,
    copy_art,
    duration,
    human,
    publish,
)


class RuntimeTests(unittest.TestCase):
    def test_progress_returns_isolated_snapshots(self):
        progress = Progress()
        progress.set(phase="ready", done=True)
        snapshot = progress.snapshot()
        snapshot["phase"] = "changed"
        self.assertEqual(progress.snapshot()["phase"], "ready")
        self.assertTrue(progress.snapshot()["done"])

    def test_human_and_duration_formats(self):
        self.assertEqual(human(2_000_000), "2 MB")
        self.assertEqual(human(2_000_000_000), "2.00 GB")
        self.assertEqual(duration(30), "30s")
        self.assertEqual(duration(120), "2 min")

    def test_publish_and_art_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            output = root / "output"
            source.mkdir()
            (source / "a.txt").write_text("a")
            (source / "index.json").write_text("ignored")
            self.assertEqual(publish(source, output), ["a.txt"])
            self.assertEqual((output / "a.txt").read_text(), "a")

            images = root / "images-source"
            images.mkdir()
            (images / "logo.webp").write_bytes(b"logo")
            (images / "bg1.webp").write_bytes(b"background")
            copy_art(images, output)
            manifest = json.loads(
                (output / "images" / "index.json").read_text())
            self.assertEqual(manifest["logo"], "images/logo.webp")
            self.assertEqual(manifest["backgrounds"], ["images/bg1.webp"])

    def test_atomic_write_supports_paths_without_suffix(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "metadata"
            atomic_write_text(target, "first")
            atomic_write_text(target, "second")
            self.assertEqual(target.read_text(), "second")
            self.assertEqual(list(target.parent.glob("*.tmp")), [])

    def test_watchdog_stops_after_last_tab(self):
        class Server:
            def __init__(self):
                self.stopped = threading.Event()

            def shutdown(self):
                self.stopped.set()

        server = Server()
        watchdog = Watchdog(server)
        watchdog.GRACE = 0.1
        watchdog.IDLE = 5
        threading.Thread(target=watchdog.run, daemon=True).start()
        self.assertFalse(server.stopped.wait(0.6))
        watchdog.ping("one")
        watchdog.ping("one", leaving=True)
        watchdog.ping("one")
        self.assertTrue(server.stopped.wait(1.5))

    def test_watchdog_keeps_other_tabs_alive(self):
        class Server:
            stopped = threading.Event()

            def shutdown(self):
                self.stopped.set()

        server = Server()
        watchdog = Watchdog(server)
        watchdog.GRACE = 0.1
        watchdog.IDLE = 5
        threading.Thread(target=watchdog.run, daemon=True).start()
        watchdog.ping("one")
        watchdog.ping("two")
        watchdog.ping("one", leaving=True)
        for _ in range(4):
            time.sleep(0.2)
            watchdog.ping("two")
        self.assertFalse(server.stopped.is_set())
        watchdog.ping("two", leaving=True)
        self.assertTrue(server.stopped.wait(1.5))


if __name__ == "__main__":
    unittest.main()
