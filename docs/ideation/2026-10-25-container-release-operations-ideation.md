---
date: 2026-10-25
topic: container-release-operations
focus: OrbStack deployment, GHCR publishing, persistence, security, and operations
---

# Ideation: Container, Release, and Operations Improvements

## Codebase Context

gwasm is intentionally a zero-dependency Python application with a small static browser harness. Its cache path is already designed carefully: chunks are content-addressed, hash-verified, atomically replaced, resumable, and protected against duplicate in-process fetches. Container deployment uses a non-root user, an explicit Origin, a named `/data` volume, and a relay that rejects private destinations by default.

The local OrbStack deployment exposed several concrete gaps:

- The host and OrbStack VM are ARM64, but `ghcr.io/zhubby/gwasm:latest` only publishes `linux/amd64`; the running Python process reports `x86_64`.
- The published image still uses the pre-rename launcher entrypoint and `gw` user, while the working tree uses `gwasm.py` and `gwasm`.
- Compose reports the container healthy while `/progress.json` is still assembling the client, so liveness and readiness are currently conflated.
- Compose combines a mutable `latest` tag with `pull_policy: always`, making every restart eligible for an unreviewed runtime change.
- CI smoke-tests one locally built image, then the publish job rebuilds another image from a mutable base before pushing it.
- Anonymous GHCR pull, named-volume persistence, restart recovery, LAN HTTP access, CSP generation, and non-root execution all worked in the local deployment.

No prior `docs/solutions/` or `docs/ideation/` material was present.

## Ranked Ideas

### 1. Publish the exact image that passed container tests

**Description:** Restructure the release workflow so it builds the runtime image once, tests that exact digest, then promotes the same digest to the immutable release tag and `latest`. Add OCI source, revision, version, and license labels, and assert the expected entrypoint and non-root user in the smoke test.

**Rationale:** The current container job tests `gwasm:ci`, while the release job performs a second build. A mutable base tag or build-time drift can make the published artifact differ from the tested one. This also gives a deterministic gate that would have caught the currently published image's pre-rename launcher entrypoint.

**Downsides:** Requires passing image artifacts or registry digests between jobs and carefully handling an image before the publish gate completes.

**Confidence:** 97%

**Complexity:** Medium

**Status:** Unexplored

### 2. Publish native AMD64 and ARM64 images

**Description:** Use Buildx to publish a manifest list for `linux/amd64` and `linux/arm64`, with at least an architecture check and startup smoke test for each platform. Keep the Python application and static assets identical across architectures.

**Rationale:** The actual deployment host is ARM64 but the current GHCR manifest contains only AMD64. OrbStack is transparently emulating x86, which adds CPU overhead and makes an otherwise portable Python service less predictable on Apple Silicon and ARM servers.

**Downsides:** Multi-platform builds take longer. ARM smoke tests on an AMD64 GitHub runner require QEMU unless a native ARM runner is added.

**Confidence:** 99%

**Complexity:** Medium

**Status:** Unexplored

### 3. Separate stable deployment versions from `latest`

**Description:** Make the Compose image tag configurable, for example `ghcr.io/zhubby/gwasm:${GWASM_IMAGE_TAG:-<stable-version>}`, and document explicit upgrade and rollback commands. Keep `latest` as an opt-in fast-moving channel rather than combining it with unconditional pulls for every deployment.

**Rationale:** Version tags already exist, but Compose ignores them. Pinning a known release makes a container restart reproducible and gives rollback a single environment-variable change, while still allowing users to choose `latest` deliberately.

**Downsides:** Stable deployments no longer receive fixes automatically. The release process or documentation must announce and update the recommended tag.

**Confidence:** 94%

**Complexity:** Low

**Status:** Unexplored

### 4. Expose separate liveness, readiness, and build status

**Description:** Add small `/healthz`, `/readyz`, and `/status.json` endpoints. Liveness should prove the HTTP loop responds; readiness should reflect `Progress.phase == "ready"`; status should expose the application version, commit, architecture, cache usage, client phase, and configured build without exposing credentials. Keep `/progress.json` for the browser UI.

**Rationale:** During the measured first run, Docker marked the service healthy before client assembly completed. Separate signals let Compose, operators, and future automation distinguish "process is alive" from "game client can boot" and diagnose stale images or emulation immediately.

**Downsides:** Readiness semantics need to account for the optional 20-minute `--image` flow. Status fields become a small compatibility surface and should remain intentionally limited.

**Confidence:** 93%

**Complexity:** Low to Medium

**Status:** Unexplored

### 5. Harden the runtime container after a compatibility smoke test

**Description:** Validate and then add a read-only root filesystem, `cap_drop: [ALL]`, `security_opt: [no-new-privileges:true]`, an init process, and a conservative PID limit. Leave `/data` as the only writable persistent location and add a temporary filesystem only if Python or TLS needs it.

**Rationale:** The service exposes an HTTP server and TCP relay to the LAN. It already runs non-root and constrains relay targets, so removing ambient Linux capabilities and filesystem writes is a natural defense-in-depth step with limited application impact.

**Downsides:** Overly tight PID limits can break the thread-per-request server, and a read-only root may reveal hidden temporary-file assumptions. These controls need an actual boot, proxy, relay, and download smoke test before becoming defaults.

**Confidence:** 88%

**Complexity:** Low to Medium

**Status:** Unexplored

### 6. Add a Compose-level deployment test and release canary

**Description:** Test `compose.yaml` itself with a generated `.env`, not only an equivalent `docker run` command. Verify Origin/CSP, rejected WebSocket Origins, volume recovery, image architecture metadata, health/readiness behavior, and anonymous pull of the published digest. After publishing, start the registry image by digest as a final canary.

**Rationale:** The current smoke test is strong but bypasses the actual Compose contract. The recent switch from local build to GHCR, environment interpolation, `pull_policy`, and volume naming can all regress without the existing container test noticing.

**Downsides:** A post-publish canary cannot prevent the first push of a bad tag unless promotion is staged. Compose tests add CI time and require careful port isolation.

**Confidence:** 95%

**Complexity:** Medium

**Status:** Unexplored

### 7. Make the cache volume portable and observable

**Description:** Document tested backup, restore, and migration commands for `gwasm_gwasm-data`; optionally support a host bind path through `.env`. Report cache bytes, cached snapshot chunks, required free space, and last successful client update in the status endpoint. Warn that exported data contains copyrighted game assets and is for personal recovery only.

**Rationale:** The complete cache is about 4.2 GB and currently lives inside the OrbStack or Docker VM. It is persistent across container rebuilds but awkward to inspect, migrate, or recover when moving machines or resetting the VM.

**Downsides:** The data is reproducible, so backup may not be worth the disk space. Bind mounts have platform-specific ownership and performance behavior, and exported game data must not become a release artifact.

**Confidence:** 82%

**Complexity:** Low

**Status:** Unexplored

## Rejection Summary

| # | Idea | Reason Rejected |
|---|------|-----------------|
| 1 | Add `uv` to the runtime image | The application has no Python dependencies; this would add machinery without solving a current problem. |
| 2 | Replace the chunk cache with SQLite | The content-addressed, hash-verified file cache already provides simpler recovery and concurrency behavior. |
| 3 | Compress cached chunks locally | Most value would be offset by CPU cost, random-range access complexity, and limited compression of already packaged data. |
| 4 | Split `/data/cache` and `/data/dist` into separate named volumes | Both are derived, persistent data with the same lifecycle; two volumes add operator friction without a clear recovery benefit. |
| 5 | Move immediately to Alpine or distroless | The current image is modest and debuggable; libc and certificate differences add risk before architecture and release correctness are fixed. |
| 6 | Bundle game binaries into the image | This violates the project's legal and leak-prevention boundary and would make the image several gigabytes. |
| 7 | Run Chromium and GPU drivers inside the container | The established architecture keeps rendering in the user's browser; containerizing the GPU path does not answer real hardware compatibility. |
| 8 | Add Kubernetes manifests | The product is explicitly a single-user private LAN service, so orchestration objects would add maintenance without solving a target use case. |
| 9 | Add built-in public TLS termination | Public deployment remains out of scope; a reverse proxy is the appropriate separate boundary if that scope changes. |
| 10 | Add account authentication to the server | This is a separate product/security project and does not fix game authentication or multi-tenant isolation. |
| 11 | Allow wildcard Origins for easier LAN access | It would weaken the exact-Origin protection around the WebSocket relay. |
| 12 | Auto-detect the LAN IP and silently rewrite Origin | Multi-interface, VPN, DHCP, IPv6, and reverse-proxy hosts make automatic selection unreliable and surprising. |
| 13 | Raise download concurrency above eight | The cap protects ArenaNet's production CDN and shared access key; local speed is not worth ecosystem risk. |
| 14 | Add automatic Watchtower-style upgrades | It makes the existing mutable-tag risk worse and removes a deliberate rollback point. |
| 15 | Scale the Compose service to multiple replicas | The server is single-user, bound to one host port, and intentionally owns one browser session and cache lifecycle. |
| 16 | Add Prometheus, Grafana, and log aggregation | A compact status endpoint and clear logs provide proportionate observability for a private single-container service. |
| 17 | Log every snapshot request by default | A boot issues thousands of range reads; default verbose logging would obscure actionable failures. |
| 18 | Require a database for runtime state | Current state is derived files and a small JSON working-set record; a database adds failure modes without useful queries. |
| 19 | Make private relay targets configurable in Compose | `GWASM_RELAY_ALLOW_PRIVATE=1` is intentionally test-only and exposing it as a normal deployment option invites SSRF and LAN scanning. |
| 20 | Add Windows container images | Linux containers already run through Docker Desktop and OrbStack; a second kernel target offers little benefit. |
| 21 | Claim rendering support from headless CI | Headless shader behavior cannot substitute for the missing real-GPU acceptance matrix. |

## Session Log

- 2026-10-25: Initial ideation after a successful OrbStack deployment. Generated 28 candidates; 7 survived deduplication and adversarial filtering.
