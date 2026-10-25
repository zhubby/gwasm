# gwasm brand consistency review

- Mode: autofix
- Base: `origin/main`
- Scope: current working tree
- Intent: unify the project name across runtime, browser, container, tests,
  documentation, release assets, and repository metadata.
- Reviewers: correctness, testing, maintainability, project standards,
  agent-native, learnings, API contract, reliability, adversarial, CLI
  readiness, and Python maintainability.

## Findings

No unresolved findings remain.

## Applied fixes

- Added positive assertions for the browser title and the key release,
  container, package, and documentation brand markers.
- Added a repository-wide guard against reintroducing legacy project names.

## Verification

- Python runtime tests passed.
- Repository leakage and brand checks passed.
- Harness contract tests passed.
- Chromium end-to-end tests passed.
- Compose configuration rendered successfully.
- Docker image built and passed an offline startup smoke test as a non-root
  user.

## Residual risk

This is an intentional breaking rename. Existing installations must adopt the
new executable, environment-variable, browser-storage, and Compose resource
names. Cached game data remains reproducible if an old volume is not migrated.

## Excluded untracked file

- `docs/gwasm-notion-technical.md` was present before this task and was not
  modified or included in the review diff.

## Verdict

Ready to merge.
