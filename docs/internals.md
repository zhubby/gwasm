# gwasm internals

How the client is put together and how this repo drives it. For running the
game, see the [README](../README.md).

## How the client is built

Guild Wars Reforged shipped on Android and iOS in June 2026. The mobile client
turned out not to be a native port: it is a **Capacitor WebView wrapping an
Emscripten-compiled build of the game**, with an Astro-built launcher around it.
The wasm core is platform-agnostic — every platform service is injected as a
plain JavaScript object — which means the same module that runs inside the app's
WebView runs in an ordinary browser, given a host that supplies those services.

That is what `harness/` is.

## Status

**The client boots and talks to ArenaNet.** The runtime initialises, the
snapshot opens, reads are served on demand, the twelve `File*.ArenaNetworks.com`
content servers resolve, and the client completes a short exchange with each —
21 bytes out, 32 back — which is it sampling them before choosing one.

Blocked on **authentication**: `login` and `nativeAccount` are stubs, while
browser `secureStorage` keeps credentials only in memory. There are no
credentials to present safely yet.

Rendering is unverified. Headless Chromium aborts at
`GL ES 3.0 default vertex shader compilation failed`
(`Engine/Gr/Gles3/GlShaderCache.cpp:959`) under SwiftShader, immediately after
logging "first frame presented", so the presentation path is wired but what a
real GPU does is unknown.

## Layout

| | |
|---|---|
| `gwasm.py` | CLI, installation diagnostics and thread lifecycle |
| `gwasm_core/http.py` | pooled standard-library HTTP shared by every downloader |
| `gwasm_core/patch.py` | manifests, chunk cache, updates and snapshot reads |
| `gwasm_core/runtime.py` | progress, watchdog and asset publication |
| `gwasm_core/server.py` | per-server context, HTTP/Range, proxy, DNS and relay |
| `harness/` | ordered classic scripts supplying the `Module.*` services |
| `images/`, `fonts/` | loading-screen art and typeface |
| `gwkey.py` | extracts the client's access key from an APK |
| `gwpatch.py` | the patch protocol, standalone |
| `tools/` | wasm patching, scanning and symbol recovery |

## What is committed that arguably should not be

No game binaries are in this repo; they are fetched from ArenaNet's CDN at
runtime. Three deliberate exceptions:

- **The access key**, hardcoded in `gwasm.py`. It ships in the public app bundle
  and identifies the client rather than a user. Hardcoding it is what lets
  `gwasm.py` run with nothing to configure. `gwkey.py` extracts a fresh one from
  an APK if it rotates.
- **`images/`** — the Guild Wars logo and four screenshots. ArenaNet's artwork,
  committed so the loading screen works offline and does not hotlink a fan site
  on every run. Screenshots from
  [Snapshot Henchman](https://bloogum.net/guildwars/), credited on screen.
- **`harness/favicon.ico`** — guildwars.com's icon, so the browser tab looks
  like the game rather than a blank page. ArenaNet's mark, same category as
  `images/`.
- **`fonts/Fremont.woff`** — the loading typeface. Note this one is *not*
  ArenaNet's: Fremont is © SoftMaker Software GmbH and the name is their
  trademark, so the "interoperating with a game you own" reasoning does not
  cover it. Of the three this is the one most likely to be a real problem if
  the repo is ever opened up.

None of these is a precedent for committing anything else.

## Builds and downloads

### Which build

`--build` selects the pair to fetch, defaulting to `jspi`:

```bash
python3 gwasm.py --build jspi      # 8.2 MB, what Chrome picks
python3 gwasm.py --build asyncify  # 27.8 MB, for Safari/iOS
python3 gwasm.py --build both
```

The harness feature-detects at runtime exactly as the shipped launcher does —
`ios ? Asyncify : 'Suspending' in WebAssembly` — and falls back if the preferred
glue is absent.

**If you switch builds, clear `dist/` first.** `gwasm.py` skips files that are
already present at the right size, so a stale `Gw.wasm` lingers. Both builds were compiled
with the same output basename, so `Gw.jspi.js` also asks for `Gw.wasm`; the
harness redirects that via `locateFile`, but a leftover file is still confusing
to reason about. The log line names the pair actually in use:

```
loading Gw.jspi.js (wasm: Gw.jspi.wasm) ...
```

### The pieces individually

```bash
python3 gwkey.py base.apk        # -> access.key (mode 0600)
python3 gwpatch.py               # list the manifest
python3 gwpatch.py Gw.wasm -j 8  # fetch and reassemble one file
```

`gwpatch.py` caches chunks by content hash under `gwpatch-cache/`, so a later run
against a newer manifest only fetches hashes that actually changed. That is what
makes it an updater rather than a downloader.

### Why not `python3 -m http.server`

Two reasons, both fatal:

- It ignores `Range` and answers `200` with the whole file. Snapshot reads are
  small and random, so every read would drag the entire image.
- It does not know `application/wasm`, which `instantiateStreaming` requires.

`gwasm.py` handles both, and additionally serves `Gw.snapshot` **virtually from
`gwpatch-cache/`** — mapping each range onto the 256 KB chunks covering it. Since
reads are on demand, you can boot without ever assembling the 4.2 GB file.

## What the manifest contains

| File | Size | Notes |
|---|---|---|
| `Gw.js` | 493 KB | Emscripten glue; hosts the `EM_ASM` bodies |
| `Gw.wasm` | 27.8 MB | Asyncify build |
| `Gw.jspi.js` | 470 KB | glue for the JSPI build |
| `Gw.jspi.wasm` | 8.2 MB | JSPI build — same game, ~4× smaller code section |
| `Gw.snapshot` | 4.20 GB | game data image, read on demand |
| `version.json` | 127 B | build metadata |

`chunkSize` is 256 KB and `compressionMode` is `none`.

## Notes on the module

Both builds are stripped: no `name` section, 17.6k functions, no symbols. The
`external_debug_info` custom section names `Gw.wasm.debug`, but that file is not
in the manifest — the sidecar is built and not shipped.

The JSPI build is the better analysis target. It has essentially the same
function count as the Asyncify build (17,596 vs 17,648) in 6.5 MB of code rather
than 26.2 MB; the difference is Asyncify's instrumentation of every suspendable
call site.

`target_features` lists no `atomics` and no SIMD, so the module is
single-threaded. There is no `SharedArrayBuffer`, and therefore **no COOP/COEP
headers are needed** to host it — usually the most painful part of self-hosting
an Emscripten build.

Imports are 212 `env` plus 7 `wasi_snapshot_preview1`. EGL appears in the import
list, so GLES is translated to WebGL by Emscripten's shim.

### Host interfaces

The required browser host surface is also recorded in
[host-contract.json](host-contract.json). The harness contract test checks the
callable methods listed there; update the file when a new glue build introduces
another import or changes a return shape.

The glue reads platform services off `Module`. Implementing these is the whole
job:

| Object | Purpose |
|---|---|
| `image` | random-access reads over `Gw.snapshot` |
| `socket` | TCP; `connect(destAddr)` returns a socket object |
| `dns` | `resolve(name)` → address |
| `shop`, `login`, `nativeAccount`, `adProvider`, `secureStorage`, `browser`, `events` | commerce, accounts, telemetry |

`image` is the interesting one:

```
open(path)                                   -> handle (0 = failure)
fileSize(handle)                             -> size, SYNCHRONOUSLY
readAsync(imageId, offset, null, buf, bytes) -> Promise, writes into HEAPU8 at buf
isCached(handle, offset, size)               -> truthy
cacheAsync(handle, offset, size, progressCb) -> Promise
close(handle)
```

Random access with an explicit caching layer means the snapshot is streamed on
demand. Booting touches ~30 chunks, not all 4.2 GB.

`fileSize` is synchronous, so the size must be known before the module asks —
the harness reads it from `snapshot-chunks.json` before loading the glue.

**Nine of these are awaited or `.then()`d by the glue.** Returning `undefined`
from one throws `Cannot read properties of undefined (reading 'then')` and kills
the frame mid-connect, so a stub for any of them must be a promise, not merely
callable:

```
image.cacheAsync            adProvider.showInterstitial
dns.resolve                 ageSignals.check
secureStorage.getCredentials / storeCredentials / clearCredentials
shop.initialize / inAppPurchase
```

`socket.connect(destAddr)` returns an object on which the glue assigns
`onopen`, `onclose` and `onmessage` — and it calls `onmessage` with the payload
directly, not with an event.

## The relay is deliberately narrow

Browsers cannot open raw TCP — which is exactly why the shipped Capacitor build
carries a native socket plugin. `gwasm.py` stands in for it, on the same port it
serves the page from, so the bridge is same-origin with the harness.

A WebSocket-to-TCP bridge is an open proxy, and every page from an accepted
origin can reach it, not just this one. Without limits, any site you visit
could use it to reach hosts on your LAN it could never reach directly. By
default the server is local-only; container mode can opt into a trusted LAN
with an explicit bind address and Origin list. The relay still:

- binds `127.0.0.1` by default (`--bind 0.0.0.0` is for the private container mode)
- `/dns` resolves names under `arenanetworks.com` / `guildwars.com` and nothing else
- it dials **public unicast addresses only** — loopback, RFC1918, CGNAT and
  link-local (including cloud metadata endpoints) are all refused, so the bridge
  cannot reach anything a page could not reach directly
- destination ports are allowlisted (`6112,80,443` by default)
- the `Origin` header must be in the exact `--origin` allowlist (or one of the
  default localhost origins); configured values are full `http(s)://host:port`
  origins with no path, query, wildcard, or trailing slash

The address rule replaced an earlier one that allowed only IPs the relay had
itself resolved. That was tighter but wrong: the client takes its game server
address from the authenticated login response, not from DNS, so no lookup ever
passes through `/dns` and nothing legitimate could connect.

`GWASM_RELAY_DOMAINS` and `GWASM_RELAY_PORTS` widen the allowlists; do that only if
you have thought about the above. `GWASM_RELAY_ALLOW_PRIVATE=1` disables the
address rule outright and exists for `e2e.js`, which dials a loopback fixture —
it announces itself loudly at startup and is not for ordinary use.

### Container deployment

The repository includes a runtime `Dockerfile`, `compose.yaml` and
`.env.example` for a single-user private LAN deployment. The image contains
`gwasm.py`, its local `gwasm_core/` package and the static harness assets;
it has no Node, Playwright or game binaries. Compose runs with `--no-browser`,
binds the service to the container interface, and passes the explicit LAN URL
as `--origin`.

`/data/cache` must remain on a persistent volume because it can grow to about
4.2 GB with `--image`. The host browser still supplies the display and GPU;
putting the server in a container does not make headless rendering reliable.

## Loading-screen footer links

The gwasm repository and Guild Wars community Discord appear in the middle of
the footer. Both destinations are confirmed live.

Every link carries `rel="noopener noreferrer"`: these are external sites, and
there is no reason to hand them a window handle or the address of a server on
someone's machine. `e2e.js` asserts it.

## CI

`.github/workflows/ci.yml` runs on pull requests and pushes to `main`, and is
also callable by the release workflow. It runs Python 3.8/3.13 tests and leak
checks, the browser host contract, pinned-Chromium E2E and a container smoke
test. PR/main runs never package or publish anything.

`.github/workflows/release.yml` calls the same validation for the release tag.
Only after it passes does the workflow attach a zip of `gwasm.py`,
`gwasm_core/`, `harness/`, `images/`, `fonts/`, the README and the licence.

Three things it is doing deliberately:

- **`test_no_leaks.py` gates the release artifacts.** Uploading them is exactly
  the moment that guard matters, since an asset turns a mistake into a download.
- **No `pip install` anywhere.** Compilation and tests run on clean
  interpreters, so losing the zero-dependency property fails immediately.
- **Python 3.8 and 3.13**, because the README promises 3.8 and an untested
  floor is a guess.

The release job re-tests the *unpacked zip* rather than the checkout, so a file
that only resolves because it happens to sit in the repo is caught before it
ships.

Each release also publishes `ghcr.io/zhubby/gwasm:<release-tag>` and `latest` as
a multi-platform image for native `linux/amd64` and `linux/arm64` runtimes. The
release job checks both tags' manifests after pushing and starts each platform's
image under its matching runtime or QEMU before considering publication
successful.

## Tests

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
python3 tests/test_no_leaks.py
node tests/test_harness.js
node tests/e2e.js
```

The Python discovery suite is split into `test_tools.py`, `test_patch.py`,
`test_runtime.py` and `test_server.py`. The server tests run two differently
configured instances concurrently to catch accidental shared state.

Everything runs offline against local fixtures — a synthetic snapshot, a
stand-in TCP server, and a mock glue that drives `Module.*` the way the real
glue does. `e2e.js` is the one that matters: every harness bug so far has lived
in that wiring rather than in the parts a unit test reaches.

It needs a Chromium. It looks in `~/.cache/ms-playwright`, or set `CHROME_PATH`;
`npx playwright install chromium` if you have neither.

What it cannot check is whether the real wasm is satisfied — that still needs
artifacts and network.
