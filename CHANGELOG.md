# Changelog

## v1.2.0 - 2026-05-30

- Require explicit approval before live Codex reviewer runs can export Panda prompt, repository context, uncommitted diff details, or test output to the Codex backend.
- Add `--privacy-mode advisory-summary` for summary-only private code review and `--privacy-mode full-context` for approved full-context review.
- Document private review lanes and Codex reviewer export handling in the Panda skill, README, security policy, and evaluation runbooks.
- Recover invalid JSON escape sequences in Panda V2 contract artifacts with explicit warnings.

