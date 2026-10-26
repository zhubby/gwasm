# Code Review: Release-only packaging

- Scope: `.github/workflows/release.yml`, `docs/internals.md`
- Intent: Package and publish artifacts only after a GitHub Release is published.
- Mode: autofix
- Verdict: Ready to merge

## Findings

No unresolved findings.

## Applied Fixes

- Clarified that the test suite gates release artifacts and the container image,
  not the GitHub Release object that already exists when the workflow starts.

## Coverage

- Trigger, checkout ref, upload command, and tag-validation assertions passed.
- YAML syntax parsed locally.
- `actionlint v1.7.7` passed.
- The packaging script was exercised locally against a temporary directory.
- No applicable prior solutions were present under `docs/solutions/`.
- No agent-native capability gap applies to this CI-only change.
