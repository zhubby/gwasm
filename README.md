# gwasm

Run and study the Guild Wars WebAssembly client in a browser with a Python
standard-library host. This is an independent interoperability research project,
not an ArenaNet or NCSoft product.

## Project status

- Verified: client update, content-addressed snapshot streaming, browser host
  wiring, DNS, HTTPS proxy, and constrained WebSocket-to-TCP relay.
- Not implemented: authentication. Do not enter real account credentials.
- Unverified: final rendering on a real GPU. Headless SwiftShader reaches first
  frame presentation and then fails GLES shader compilation.
- Available with an explicit warning: unrestricted remote listener mode.
- Unsupported: unauthenticated public or untrusted multi-user deployment.

## Start

Requires Python 3.8+ and no installed packages. Download and fully extract a
release zip, then run:

```bash
python3 gwasm.py
```

The game opens at `http://127.0.0.1:8080/`. The complete bilingual technical
and deployment manual is served at `http://127.0.0.1:8080/docs/`.

To accept connections through any hostname or IP on every IPv4 interface:

```bash
python3 gwasm.py --remote --no-browser
```

`--remote` removes the need to enumerate Origins in advance, but still requires
browser Origins to match the requested Host. It does not add authentication or
TLS; expose it only behind access controls you operate.

To cache the complete 4.2 GB snapshot before launch:

```bash
python3 gwasm.py --image
```

For a trusted private LAN, see `/docs/deployment/docker/` after startup. The
relay is intentionally limited to public addresses and ports, and download
concurrency is capped at eight to protect the shared upstream access key.

## Develop

Documentation sources live in `docs/src/`; Node 22.23.4+ is build-time only.

```bash
npm ci
npm run docs:check
npm run docs:build
python3 -m unittest discover -s tests -p 'test_*.py'
node tests/test_harness.js
node tests/e2e.js
```

See `/docs/contributing/` after starting the service. `CLAUDE.md` contains
repository maintenance constraints for contributors and coding agents.

## Legal

Guild Wars and associated content are © 2005–2026 ArenaNet, Inc. All rights
reserved. All trademarks belong to their respective owners. Loading-screen
photography is by [Snapshot Henchman](https://bloogum.net/guildwars/).

The code is GPL-3.0. This repository contains no game binaries; the runtime
obtains required client content from ArenaNet services.
