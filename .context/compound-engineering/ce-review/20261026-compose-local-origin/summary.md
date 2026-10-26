# Code Review: Compose local Origin defaults

- Scope: Compose relay Origin arguments, their deployment documentation and
  the deployment-contract regression test
- Intent: make localhost and loopback access work without configuration while
  retaining an optional exact LAN Origin and the existing WebSocket Origin
  allowlist
- Mode: autofix
- Reviewers: correctness, testing, maintainability, project-standards,
  agent-native, learnings and security
- Verdict: Ready to merge

## Findings

No unresolved findings.

## Applied Fixes

- Tightened the regression test to assert the complete ordered Compose command
  fragment and exact number of `--origin` arguments.

## Residual Actionable Work

None.

## Advisory Outputs

- Compose still publishes port 8080 on `0.0.0.0`; LAN deployments must retain
  the documented host-firewall restriction.
- Exact Origin matching remains enabled. Unrelated browser Origins continue to
  receive HTTP 403 from the WebSocket relay.
- No relevant `docs/solutions/` knowledge base entry exists in this repository.

## Coverage

- All 44 Python unit tests passed.
- The repository leak/policy test and all 49 host-contract assertions passed.
- Browser E2E passed with system Chrome.
- Astro diagnostics, bilingual parity and the 47-page production docs build
  passed with Node 24.11.1.
- Two real browser runs against the recreated Compose service at
  `http://localhost:8080/` reached Ready/connected states; a cross-origin
  WebSocket probe still returned HTTP 403.
