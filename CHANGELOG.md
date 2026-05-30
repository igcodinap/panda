# Changelog

## v1.2.1 - 2026-05-30

- Allow isolated Codex summary-only reviews to run by passing `--skip-git-repo-check` for advisory-mode Codex reviewer commands.
- Keep full-context code review blocked by default unless `--privacy-mode full-context` or an explicit export approval is provided.
- Clarify summary-lane approval and OpenCode auto-approval behavior in the Panda skill guide.

## v1.2.0 - 2026-05-30

- Require explicit approval before live Codex reviewer runs can export Panda prompt, repository context, uncommitted diff details, or test output to the Codex backend.
- Add `--privacy-mode advisory-summary` for summary-only private code review and `--privacy-mode full-context` for approved full-context review.
- Document private review lanes and Codex reviewer export handling in the Panda skill, README, security policy, and evaluation runbooks.
- Recover invalid JSON escape sequences in Panda V2 contract artifacts with explicit warnings.
