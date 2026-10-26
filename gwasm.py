#!/usr/bin/env python3
"""gwasm runs Guild Wars in your browser. Double-click this file, or:

    python3 gwasm.py

It downloads what it needs on first run, serves it, and opens a browser.
Nothing to install -- Python 3.8+ and its standard library are enough.

Keep gwasm_core/, harness/, images/ and fonts/ next to this file.
"""

import argparse
import mimetypes
import os
import signal
import sys
import threading
import webbrowser
from functools import partial
from pathlib import Path

if not (Path(__file__).resolve().parent / "gwasm_core" / "__init__.py").is_file():
    sys.stderr.write(
        "\ngwasm cannot start: the gwasm_core folder is missing.\n\n"
        "Extract the whole release archive and keep gwasm.py beside "
        "gwasm_core/.\n")
    sys.exit(1)

from gwasm_core.http import Http, HttpError
from gwasm_core.patch import (
    HASHES,
    MAX_JOBS,
    PATCH_ROOT,
    PREFETCH_JOBS,
    SNAPSHOT,
    ChunkStore,
    Manifest,
    assemble,
    chunk_matches,
    chunk_matches_bytes,
    expected_chunk_size,
    fetch_chunk,
    file_matches,
    patch_http as _patch_http,
    store_chunk,
    update as _update,
    validate_jobs,
)
from gwasm_core.runtime import (
    Progress,
    Watchdog,
    copy_art,
    duration as _duration,
    human as _human,
    log,
    publish,
)
from gwasm_core.server import (
    CONNECT_TIMEOUT,
    DEFAULT_DOMAINS,
    DEFAULT_PORTS,
    MAX_WS_FRAME,
    WS_GUID,
    GwasmServer,
    Handler,
    RelayPolicy,
    ServerContext,
    _dns_query,
    _system_resolvers,
    allowed_name,
    bridge,
    frame,
    is_public_ip,
    lookup,
    normalize_origin,
    origin_allowed,
    policy_from_environment,
    unframe,
    websocket_origins,
)


ACCESS_KEY = "2043FE79-F32D-4FD7-8C27-0D47231C4F03"
UA = "gwasm/0.2 (interop research)"
BUILDS = {
    "jspi": ["Gw.jspi.js", "Gw.jspi.wasm"],
    "asyncify": ["Gw.js", "Gw.wasm"],
}
COMMON = ["version.json"]

# Compatibility names for callers that imported the former single-file module.
_HASHES = HASHES
ALLOWED_DOMAINS = list(DEFAULT_DOMAINS)
ALLOWED_PORTS = set(DEFAULT_PORTS)

mimetypes.add_type("application/wasm", ".wasm")


def _getch():
    """Block until a key is pressed, without needing Enter where possible."""
    try:
        import msvcrt
        msvcrt.getch()
        return
    except ImportError:
        pass
    try:
        import termios
        import tty
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd, termios.TCSANOW)
            os.read(fd, 1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
    except Exception:
        try:
            input()
        except Exception:
            pass


def die(*lines):
    """Show startup failures long enough to be read after a double-click."""
    sys.stderr.write("\n" + "\n".join(lines) + "\n")
    if sys.stdin.isatty() and sys.stderr.isatty():
        sys.stderr.write("\nPress any key to close...")
        sys.stderr.flush()
        _getch()
        sys.stderr.write("\n")
    sys.exit(1)


REQUIRED = [
    "gwasm_core/__init__.py",
    "gwasm_core/http.py",
    "gwasm_core/patch.py",
    "gwasm_core/runtime.py",
    "gwasm_core/server.py",
    "harness/index.html",
    "harness/harness.js",
    "harness/harness-cache.js",
    "harness/harness-module.js",
    "harness/harness-input.js",
    "harness/harness-boot.js",
    "harness/harness.css",
    "harness/loading.js",
    "harness/loading.css",
]


def check_install(here):
    if sys.version_info < (3, 8):
        die("gwasm needs Python 3.8 or newer.", "",
            "This is Python %d.%d, at %s."
            % (sys.version_info[0], sys.version_info[1], sys.executable), "",
            "Install a current version from https://www.python.org/downloads/",
            "and, on Windows, tick \"Add python.exe to PATH\" during setup.")
    missing = [name for name in REQUIRED if not (here / name).exists()]
    if not missing:
        return
    if ".zip" in str(here).lower():
        die("gwasm cannot start: it is still inside the zip.", "",
            "Double-clicking gwasm.py inside a zip runs only that one file,",
            "without the rest of the game beside it.", "",
            "Extract the whole zip to a real folder first -- right-click it",
            "and choose \"Extract All\" -- then run gwasm.py from there.")
    die(*(["gwasm cannot start: some of its files are missing.", "",
           "Looked in: %s" % here, "", "Missing:"]
          + ["  " + name for name in missing]
          + ["", "gwasm.py needs gwasm_core and its harness folder beside it.",
             "Download the project again and keep the extracted folder together."]))


def minimise_console():
    """Minimise a console created solely for a double-clicked Windows run."""
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes
        kernel, user = ctypes.windll.kernel32, ctypes.windll.user32
        window = kernel.GetConsoleWindow()
        if not window:
            return
        processes = (ctypes.c_uint * 4)()
        if kernel.GetConsoleProcessList(processes, 4) != 1:
            return
        user.ShowWindow(window, 6)
    except Exception:
        pass


def patch_http():
    return _patch_http(ACCESS_KEY, UA)


def update(dest, cache, build, jobs=4, progress=None, stop=None):
    """Compatibility wrapper around the shared patch implementation."""
    return _update(dest, cache, build, BUILDS, COMMON, patch_http(), jobs,
                   progress, stop)


def build_parser():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-d", "--dir", default=None, type=Path,
                        help="where the client files live (default: ./dist)")
    parser.add_argument("--bind", default=None,
                        help="address to listen on (default: 127.0.0.1, or "
                             "0.0.0.0 with --remote)")
    parser.add_argument("-p", "--port", type=int, default=8080)
    parser.add_argument("-c", "--cache", default=None, type=Path)
    parser.add_argument("--origin", action="append", metavar="ORIGIN",
                        help="allowed http(s)://host:port origin; repeat for multiple origins")
    parser.add_argument("--remote", action="store_true",
                        help="accept any remote Host with same-origin checks; "
                             "no authentication")
    parser.add_argument("-j", "--jobs", type=int, default=PREFETCH_JOBS,
                        help="concurrent chunk fetches (default %d, maximum %d)"
                             % (PREFETCH_JOBS, MAX_JOBS))
    parser.add_argument("--build", choices=["jspi", "asyncify", "both"],
                        default="jspi", help="client build to download")
    parser.add_argument("--no-update", action="store_true",
                        help="skip the update check")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="log every HTTP request (thousands during play)")
    parser.add_argument("--offline", action="store_true",
                        help="never fetch; 503 on a chunk that is not cached")
    parser.add_argument("--no-prefetch", action="store_true",
                        help="do not warm the recorded boot working set")
    parser.add_argument("-i", "--image", action="store_true",
                        help="download the whole game image before starting")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    args.bind = args.bind or ("0.0.0.0" if args.remote else "127.0.0.1")
    try:
        explicit_origins = sorted(set(normalize_origin(value)
                                      for value in (args.origin or [])))
    except ValueError as exc:
        parser.error("invalid --origin: %s" % exc)
    try:
        validate_jobs(args.jobs)
    except ValueError as exc:
        parser.error(str(exc))

    here = Path(__file__).resolve().parent
    check_install(here)
    dest = args.dir or here / "dist"
    cache = args.cache or here / "gwpatch-cache"
    dest.mkdir(parents=True, exist_ok=True)
    publish(here / "harness", dest)
    copy_art(here / "images", dest)
    publish(here / "fonts", dest / "fonts")

    progress = Progress()
    stop = threading.Event()
    policy = policy_from_environment()
    docs_root = here / "docs-dist"
    if not (docs_root / "index.html").is_file():
        docs_root = None
    context = ServerContext(progress=progress, policy=policy,
                            proxy_http=Http(), verbose=args.verbose,
                            docs_root=docs_root, remote_access=args.remote)
    httpd = GwasmServer((args.bind, args.port),
                        partial(Handler, directory=str(dest)), context)
    actual_port = httpd.server_address[1]
    context.origins = set(explicit_origins)
    if not context.origins and not args.remote:
        context.origins = {
            normalize_origin("http://127.0.0.1:%d" % actual_port),
            normalize_origin("http://localhost:%d" % actual_port),
        }

    if explicit_origins:
        url = explicit_origins[0] + "/"
    else:
        browser_host = "127.0.0.1" if args.bind in ("0.0.0.0", "::") else args.bind
        if ":" in browser_host and not browser_host.startswith("["):
            browser_host = "[%s]" % browser_host
        url = "http://%s:%d/" % (browser_host, actual_port)

    def work():
        try:
            if not args.no_update and not args.offline:
                _update(dest, cache, args.build, BUILDS, COMMON,
                        patch_http(), args.jobs, progress, stop)
            if stop.is_set():
                return
        except Exception as exc:
            log("update failed: %s" % exc)
            if not any(dest.glob("Gw*")):
                progress.set(phase="error",
                             error="Could not download the game: %s" % exc)
                return
            log("carrying on with what is already downloaded")

        index = dest / "snapshot-chunks.json"
        if index.exists():
            context.store = ChunkStore(index, None if args.offline else patch_http())
            have = sum(1 for content_hash in context.store.hashes
                       if (context.store.cache / content_hash).exists())
            log("snapshot: %d/%d chunks cached%s"
                % (have, len(context.store.hashes),
                   " (offline)" if args.offline else ""))
        elif not args.offline:
            progress.set(phase="error", error="No snapshot index was downloaded.")
            return
        store = context.store
        if args.image and store and not store.download_all(progress, args.jobs, stop):
            return
        progress.set(phase="ready", label="Ready", done=True, error=None)
        if store and not args.no_prefetch and not stop.is_set():
            store.prefetch(progress, args.jobs, stop)

    def record():
        while not stop.wait(10):
            if context.store:
                try:
                    context.store.save_touched()
                except OSError:
                    pass

    log("serving %s at %s" % (dest, url))
    if docs_root:
        log("docs: %s at %sdocs/" % (docs_root, url))
    else:
        log("docs: unavailable -- run npm run docs:build; game remains available")
    origin_label = (", ".join(sorted(context.origins))
                    or "any request Host (same-origin only)")
    log("bind: %s:%d; relay origins: %s"
        % (args.bind, actual_port, origin_label))
    log("relay: same origin, domains %s, ports %s"
        % (", ".join(policy.allowed_domains),
           ", ".join(str(port) for port in sorted(policy.allowed_ports))))
    if policy.allow_private:
        log("relay: WARNING -- GWASM_RELAY_ALLOW_PRIVATE=1 allows private addresses")
    if args.remote:
        log("remote: WARNING -- no authentication; restrict exposure externally")

    if not args.no_browser:
        context.watchdog = Watchdog(httpd)
        threading.Thread(target=context.watchdog.run, daemon=True).start()

        def launch():
            webbrowser.open(url)
            minimise_console()
        threading.Timer(0.3, launch).start()
    threading.Thread(target=work, daemon=True).start()
    threading.Thread(target=record, daemon=True).start()

    def stop_on_signal(signum, _frame):
        log("received signal %d -- stopping" % signum)
        stop.set()
        threading.Thread(target=httpd.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, stop_on_signal)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        stop.set()
        print("\nstopped" +
              (" -- rerun to resume the image download" if args.image else ""))
    finally:
        stop.set()
        httpd.server_close()
        if context.store:
            context.store.save_touched()


if __name__ == "__main__":
    main()
