# gwasm

gwasm runs the WebAssembly build of Guild Wars locally, in an ordinary browser.

```bash
python3 gwasm.py
```

That is the whole thing. It downloads what it needs, serves it, and opens your
browser.

## Read this first — what actually works

This is a work in progress, and it matters to be straight about where it stops.

| | |
|---|---|
| ✅ The client downloads, boots and runs | |
| ✅ It reaches ArenaNet's live game servers | all twelve resolve and answer |
| ✅ Game data streams on demand, or downloads up front | |
| ❌ **You cannot log in** | nothing is wired up to take your credentials |
| ❓ Whether the game *draws anything* | untested on real hardware |

**You cannot currently play Guild Wars with this.** The client starts and talks
to ArenaNet, but the parts that would hand it a username and password
(`login`, `nativeAccount`) are stubs, and browser credential storage is not
connected to a real account flow.
There is nothing to type credentials into. Authentication is the next piece of
work and it has not been started.

The browser harness deliberately does not persist passwords. Until a native
secure-storage integration exists, credentials live only in the current page
and are discarded on reload or close.

Rendering has only ever been exercised in a headless browser, where the game
aborts while compiling its shaders because there is no real GPU. On a normal
machine it may well draw fine — nobody has checked. If you try it, that is
genuinely useful information; see *If something goes wrong* below.

So this is worth running to see how far the port has got, or to work on it. It
is not yet a way to play the game.

## What you need

- **Python 3.8 or newer.** Nothing else — no `pip install`, no Node.js.
  - macOS and Linux already have it. Run `python3 --version` to check.
  - Windows: install from [python.org](https://www.python.org/downloads/) and
    tick **"Add python.exe to PATH"** during setup.
- **About 1 GB of free disk space**, or **5 GB** if you use `--image`.
- A recent **Chrome, Edge or Firefox**. Chrome is what this is tested against.

You do not need to own Guild Wars to get this far, because it never reaches a
login. You would need an account to actually play, once that exists.

## Running it

1. Download the latest **[release zip](https://github.com/zhubby/gwasm/releases/latest)**.
2. **Unzip it properly**, to a real folder — do not run `gwasm.py` from inside the
   zip. Windows lets you open a zip like a folder, but double-clicking `gwasm.py`
   there runs it on its own without the rest of the game. (If you do, it will
   tell you so rather than failing silently.)
3. Start it:
   - **Windows**: double-click `gwasm.py`.
   - **macOS / Linux**: open a terminal in the folder and run `python3 gwasm.py`.

The release contains `gwasm.py` plus a local `gwasm_core/` folder. There is
still nothing to install: keep the extracted folder together and run the entry
file directly.

Your browser opens by itself, and on Windows the black command window tucks
itself into the taskbar once it has. **Leave it running** — closing it stops
the server, and the game with it.

**Closing the browser stops it.** A few seconds after the last tab pointing at
the game is gone, `gwasm.py` shuts itself down — there is nothing left running and
nothing to remember to kill. Reloading the page, or having a second tab open,
does not trigger it.

You can also bring the window back up and press **Ctrl+C**, or just close it.

The window is deliberately minimised rather than hidden: it is where errors
appear, and it is the manual way to stop a server that is also holding a
network connection open.

### The first run takes a while

It downloads the game client first (about 8 MB), then the game starts pulling
its own data as it needs it — roughly 360 MB over a few minutes. The loading
screen shows both, with a speed and a time estimate.

Later runs are much quicker, because everything downloaded is kept.

### Downloading everything up front

To wait once instead of every time:

```bash
python3 gwasm.py --image
```

This fetches the complete 4.2 GB game image before starting — expect **around
20 minutes**. Afterwards the game needs no network at all for game data.

Stop it whenever you like with Ctrl+C and run it again later: it resumes exactly
where it left off and never re-downloads anything it already has.

## Options

You only need these to change a default or work around a problem.

| Option | What it does |
|---|---|
| `--image` | download all 4.2 GB before starting, instead of streaming |
| `--bind HOST` | listen on a specific address (default `127.0.0.1`) |
| `--origin ORIGIN` | allow a browser origin; repeat for multiple origins |
| `-p 9000` | serve on a different port, if 8080 is taken |
| `--no-browser` | do not open a browser automatically |
| `--offline` | never download; use only what is already on disk |
| `--no-update` | skip the check for a newer game version |
| `--build asyncify` | the build for Safari and iOS (27.8 MB rather than 8.2 MB) |
| `-j 1..8` | choose download concurrency; 8 is the enforced maximum |
| `-v` | log every request, for diagnosing a problem |

Run `python3 gwasm.py --help` for the full list.

### Running in Docker on a private LAN

The container image is for one trusted user or a small trusted LAN, not a
public multi-user service. The browser stays on the host; the container runs
the file server and relay without trying to open a desktop browser.

1. Copy the environment template and set the exact URL that the browser will
   open (including the port):

   ```bash
   cp .env.example .env
   # edit GWASM_ORIGIN=http://192.168.1.20:8080
   ```

2. Start the service:

   ```bash
   docker compose up -d
   ```

3. Open `http://192.168.1.20:8080` from a trusted device on the LAN. The
   value in `GWASM_ORIGIN` must match that browser URL exactly. It must be a full
   `http://host:port` or `https://host:port` origin with no path, query,
   wildcard, or trailing slash.

The `gwasm-data` Docker volume stores `/data/cache` (the content-addressed game
chunks) and `/data/dist` (the client files and snapshot index). The first run
downloads the client and then streams game data on demand. Add `--image` to the
service command in `compose.yaml` if the complete 4.2 GB image should be
cached before booting.
The volume survives `docker compose down`; `docker compose down -v` removes it
and forces the data to be downloaded again.

Keep the host firewall limited to the intended LAN. The relay remains limited
to public game addresses and ports; do not set `GWASM_RELAY_ALLOW_PRIVATE=1` in a
normal deployment. This mode does not add authentication or make the project a
multi-user service, and it does not provide a GPU to the browser.

### Downloading faster

Downloads are limited by round-trip time, not bandwidth. The launcher uses
eight workers by default, and also enforces eight as the maximum:

| `-j` | speed | 4.2 GB takes |
|---|---|---|
| 8 (default and maximum) | ~3 MB/s | ~20 min |

There is no supported setting above 8. These are ArenaNet's real servers, and
everyone using this shares one access key — so a per-user burst can get the key
rate-limited for actual paying players, not just for us.

## Where it puts things

Everything lands next to `gwasm.py`. Nothing is installed system-wide and nothing
is written elsewhere:

- `dist/` — the game client and the page it is served from
- `gwpatch-cache/` — downloaded game data, kept in 256 KB pieces

Deleting either is safe; both are rebuilt on the next run. Deleting
`gwpatch-cache/` means downloading the game data again.

## If something goes wrong

**Double-clicking does nothing, or "python3: command not found"**
Python is missing or not on your PATH. See *What you need*. On Windows,
reinstall it with "Add python.exe to PATH" ticked.

**"gwasm cannot start: it is still inside the zip"**
Exactly what it says — extract the zip to a real folder first, then run `gwasm.py`
from there.

**"Address already in use"**
Something else has port 8080. Use `python3 gwasm.py -p 9000`.

**The page says it could not download the game**
Check your connection, then read the terminal window — the actual error is
printed there. Run it again; nothing already downloaded is lost.

**"Not enough disk space"**
`--image` needs about 5 GB free. Free some up, or run without `--image`.

**The loading screen finishes and the window is blank**
That is the open rendering question above. Please
[open an issue](https://github.com/zhubby/gwasm/issues) saying which
operating system, browser and graphics card you have — that is exactly the
information this project is missing.

**It seems stuck**
Ctrl+C in the terminal and start it again. Progress is kept.

## For developers

The technical write-up — how the client is put together, the patch protocol,
the host interfaces the module expects, the WebSocket relay, snapshot read
performance and the tests — is in **[docs/internals.md](docs/internals.md)**.

`CLAUDE.md` records how the project got here and which mistakes not to repeat.

The runtime is split by responsibility:

- `gwasm.py` is the CLI and process-lifecycle entry point.
- `gwasm_core/{http,patch,runtime,server}.py` contains the standard-library
  implementation shared by the launcher and download tools.
- `harness/harness*.js` is loaded as ordered classic scripts: foundation,
  cache, `Module.*`, input, then boot. There is no Node build step.

Run the maintained checks with:

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
python3 tests/test_no_leaks.py
node tests/test_harness.js
node tests/e2e.js
```

The first three need only Python and Node respectively. The E2E suite also
needs the pinned Playwright Chromium installed by CI.

## Credits and legal

An independent fan project, **in no way affiliated with or endorsed by ArenaNet
or NCSoft**.

Guild Wars and all associated game content are © 2005–2026 ArenaNet, Inc. All
rights reserved. NCsoft, the interlocking NC logo, ArenaNet, Arena.net, Guild
Wars and all associated logos and designs are trademarks or registered
trademarks of NCsoft Corporation. All other trademarks are the property of
their respective owners.

Loading screen photography by
[Snapshot Henchman](https://bloogum.net/guildwars/).

This repository contains no game binaries. They are downloaded from ArenaNet's
own servers when you run it.

### Licence

GPL-3.0, matching `gwlauncher` and `gMod`. See `LICENSE`.

Copyleft is the deliberate choice here: the value of this repo is the recovered
knowledge of how the client is put together, and forks of it should stay open
for the same reason the original work was worth doing.

### Please be considerate

This talks to ArenaNet's real servers. Download concurrency is capped on
purpose, and the same access key is shared by everyone using this — so pushing
it harder risks getting that key rate-limited, which would break patching for
actual players. Please leave the limits where they are.
