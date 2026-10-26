# Code Review: GHCR multi-platform release image

- Scope: `.github/workflows/release.yml`, `docs/internals.md`
- Intent: Publish and verify `linux/amd64` and `linux/arm64` images on each
  GitHub Release while keeping ordinary pushes non-publishing.
- Mode: autofix
- Reviewers: correctness, testing, maintainability, project-standards,
  agent-native, learnings, security, reliability, adversarial
- Verdict: Ready to merge

## Findings

No unresolved findings.

## Applied Fixes

No review fixes were required.

## Residual Actionable Work

None.

## Advisory Outputs

- The first new Release remains the authoritative end-to-end validation of
  GitHub-hosted QEMU, Buildx, GHCR permissions, and registry publication.
- Registry or upstream base-image outages fail the release closed; they do not
  publish a single-platform fallback image.

## Coverage

- `actionlint v1.7.7` and `git diff --check` passed.
- Both `linux/amd64` and `linux/arm64` images built locally and ran `--help`.
- The manifest predicate passed against a known multi-platform OCI index and
  failed against the existing single-platform `v2026.10.26.1` image.
- Python, leak, JavaScript contract, and browser end-to-end suites passed.
- No applicable prior solutions directory was present.
- No agent-native capability gap applies to this CI-only change.
