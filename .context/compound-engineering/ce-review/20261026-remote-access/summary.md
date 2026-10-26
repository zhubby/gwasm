# Code Review: unrestricted remote access mode

- Scope: the `--remote` CLI mode, dynamic Host/Origin validation, relay/proxy
  integration tests, and related English and Chinese documentation
- Intent: let clients reach gwasm through any hostname or IP without
  preconfiguring Origins, while retaining same-host browser checks and all
  existing upstream destination restrictions
- Mode: autofix
- Reviewers: correctness, testing, maintainability, project-standards,
  agent-native, learnings, security, adversarial, CLI-readiness and Python
- Verdict: Ready to merge

## Findings

No unresolved findings.

## Applied Fixes

- Replaced a test-only one-line bind helper with direct launcher logic and an
  integration-style CLI binding test, matching the repository's preference for
  inline trivial functions.
- Corrected README wording so `--origin` is accurately described as additive
  when combined with `--remote`.
- Added invalid Origin and Host port coverage alongside successful and rejected
  proxy and WebSocket handshakes.

## Residual Actionable Work

None.

## Advisory Outputs

- Remote mode is intentionally unauthenticated. Host/Origin matching prevents
  ordinary cross-site use, but raw clients and DNS rebinding remain within the
  documented threat model. Public exposure requires an authenticating edge or
  firewall.
- The existing public-unicast destination check, ports `6112/80/443`, DNS
  suffix allowlist and fixed HTTPS proxy routes remain enforced.
- No `docs/solutions/` knowledge base or critical-patterns file exists in this
  repository.

## Coverage

- All 43 Python unit tests passed.
- All 49 browser host-contract assertions passed.
- Python compilation, `git diff --check`, Astro diagnostics, documentation
  parity and the production documentation build passed.
- Browser E2E reached and exercised the game host, DNS and relay using system
  Chrome; its three failures concern pre-existing footer-link expectations in
  the user's documentation-site work, not remote access.
- The standalone repository policy test has one pre-existing failure because
  it reads the committed `package.json` while the current gwasm branding change
  remains uncommitted.
