# Code Review: gwasm modularization

- Scope: working tree changes for the Python core, server context, browser host,
  tools, tests, packaging, CI and maintained documentation
- Intent: split the monolithic runtime without changing public behavior, remove
  shared server state and duplicated patch logic, and move complete validation
  to pull request and main CI
- Mode: autofix
- Reviewers: correctness, testing, maintainability, project-standards,
  agent-native, learnings, security, performance, API-contract, reliability,
  adversarial, CLI-readiness, Python and frontend-races
- Verdict: Ready to merge

## Findings

No unresolved findings.

## Applied Fixes

- Revalidated cached chunks in `gwpatch.py` before skipping downloads, so a
  corrupt existing cache entry is repaired instead of failing at assembly.
- Prevalidated directory records and all parent-index types in `Manifest`, so
  malformed forward references raise `ValueError` rather than leaking
  `KeyError` or being treated as root entries.
- Removed failed assembly `.part` files while preserving an existing output.
- Preserved the full glue candidate list across browser-host fallback attempts.
- Restored non-root, Origin-policy, persistent-volume and artifact-exclusion
  assertions when the container smoke test moved into reusable CI.
- Restored protocol coverage for standard-library HTTP, DNS wire replies and
  snapshot ranges that cross chunk boundaries.

## Residual Actionable Work

None.

## Advisory Outputs

- Local Chromium E2E remains environment-dependent. The reusable CI installs
  the pinned Playwright Chromium before running `tests/e2e.js`.
- `docs/deployment.md` was present as a user-owned untracked file and was
  intentionally excluded from edits and review fixes.

## Coverage

- 36 Python `unittest` cases passed, including concurrent server isolation.
- Release policy, browser host contract, Python compilation, workflow YAML and
  `git diff --check` passed.
- The runtime container built and passed help, offline serving, security,
  non-root, packaging, persistence and artifact-exclusion smoke checks.
- The local browser E2E reached its environment guard and stopped because no
  Playwright Chromium binary is installed; CI installs it explicitly.
- No applicable prior solutions directory was present.
- No agent-native capability gap applies to this local launcher refactor.
