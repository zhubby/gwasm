# Code Review: Astro Starlight bilingual documentation

- Scope: Astro/Starlight configuration, 23 Chinese and 23 English pages,
  semantic architecture diagrams, Python `/docs` serving, Docker and release
  packaging, CI checks, browser lifecycle integration, and legacy-doc migration
- Intent: ship a complete bilingual technical and deployment reference at
  `/docs/` without adding Node.js or third-party packages to the Python runtime
- Mode: autofix and end-to-end verification
- Verdict: Ready to merge

## Findings

No unresolved findings.

## Applied Fixes

- Enforced Chinese/English page, sidebar, ordering, and diagram parity in the
  documentation verifier.
- Extended URL validation to generated HTML, CSS, and external document
  resources under the `/docs` base path.
- Corrected host-runtime API prose, Pagefind/game-Wasm artifact wording, and
  bilingual host-contract prefix rendering.
- Kept immutable caching to successful hashed `_astro` responses and tightened
  the documentation CSP to same-origin workers without `blob:`.
- Made Docker documentation files root-owned and read-only while retaining the
  non-root runtime user and excluding source/build dependencies.
- Updated game and documentation heartbeat scripts to rotate tab IDs after
  bfcache restoration, with browser-history regression coverage.
- Reconciled desktop/mobile E2E selectors and the game footer assertions with
  Starlight's responsive controls and the conditional Docs link.

## Residual Actionable Work

None.

## Advisory Outputs

- Node.js 22.23.4 or newer is required only for documentation builds. The local
  default Node.js 22.22.0 is below that floor; final local builds used Node.js
  26.8.1, while Docker and CI pin 22.23.4.
- Astro intentionally skips sitemap generation because the deployment Origin is
  dynamic and no public `site` value is configured.
- Real GPU rendering and supported authentication remain explicitly unverified
  or unsupported; this documentation work does not change those runtime limits.

## Coverage

- Astro diagnostics passed with zero errors, warnings, or hints; the production
  build emitted 47 HTML files and passed link, base-path, sidebar, diagram, and
  23-page-per-language parity checks.
- All 43 Python tests, the browser host-contract harness, leak policy tests,
  JavaScript syntax checks, workflow parsing, and `git diff --check` passed.
- Release-zip simulation verified `/docs` canonicalization, Chinese and English
  pages, Pagefind Wasm MIME, hashed-asset caching, Range responses, and 404
  isolation for missing and traversal paths.
- The production Dockerfile built successfully and the local AMD64 runtime was
  verified as non-root, without Node/npm, source build dependencies, credentials,
  game binaries, or persistent-volume documentation. ARM64 build and publication
  remain configured for CI and were not executed locally.
- Headless Chromium verified the desktop and mobile homepages, deep architecture
  pages, semantic diagrams, native language and theme controls, bilingual
  Pagefind search, keyboard focus, responsive overflow, local imagery, docs CSP,
  `/ping` traffic, and game-to-docs browser-history heartbeat restoration.
