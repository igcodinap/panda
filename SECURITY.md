# Security Policy

Panda runs local model CLIs and can pass repository context to external model
providers through those CLIs. Treat prompts and artifacts as potentially
sensitive.

## Reporting Vulnerabilities

Please report security issues privately to the project maintainers. For a
GitHub-hosted release, prefer GitHub's private vulnerability reporting flow
under the repository's Security tab. If that is unavailable, use the repository
owner's preferred private contact channel rather than opening a public issue.

Include:

- A short description of the issue.
- Steps to reproduce, if safe.
- Affected files, commands, or artifact types.
- Whether secrets, credentials, private source, or customer data may be exposed.

## Handling Sensitive Data

- Do not paste secrets, tokens, private credentials, customer data, or
  unnecessary proprietary context into Panda prompts.
- Treat the Codex reviewer as an external export path. Live Codex reviewer
  execution requires `--privacy-mode advisory-summary` for summary-only review,
  `--privacy-mode full-context` for approved repository-context review,
  `--allow-codex-reviewer`, or `PANDA_ALLOW_CODEX_REVIEWER=1`; use full-context
  only when the workspace is approved for repository context, diff, and
  test-output export to the Codex backend.
- Treat `panda_export.v1.json` as the machine-readable export contract for a
  run. `--prepare-export-manifest` writes the contract without launching
  reviewers; `--export-manifest` validates that the prompt, workspace, privacy
  mode, tool selection, and destinations still match before any reviewer starts.
  The contract supports policy review and audit; it does not override tenant
  policy.
- For private code review, prefer `--privacy-mode advisory-summary --mode
  advisory` with a Codex-prepared summary. Full-context `explore` code review
  with external collaborators requires `--privacy-mode full-context` or
  `PANDA_ALLOW_PRIVATE_CONTEXT_EXPORT=1`.
- OpenCode-backed runs receive a Panda-managed `XDG_DATA_HOME` under the run or
  session directory for runtime DB/log state. Panda leaves `XDG_CONFIG_HOME`
  unset so existing OpenCode auth and provider configuration remain available.
- Review raw logs and patches before publishing them.
- Prefer compact evidence summaries over raw transcripts.
- Use no-`.git` benchmark workspaces when contamination or provenance matters.

## Supported Versions

Panda is pre-1.0. Security fixes are handled on the main development line until
a formal release policy exists.
