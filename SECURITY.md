# Security Policy

Panda runs local model CLIs and can pass repository context to external model
providers through those CLIs. Treat prompts and artifacts as potentially
sensitive.

## Security Model

Panda is a local runner, not a security sandbox. It coordinates local CLIs such
as Claude Code, OpenCode, and Codex reviewer, then writes compact artifacts for
Codex to inspect. The main security boundary is explicit user, Codex, or tenant
approval for what context may be sent to which external model provider.

Security-sensitive surfaces:

- Prompts, prompt files, and session memory can contain private context.
- Full-context `explore` runs can expose repository files, diffs, shell output,
  dependency metadata, and test logs to selected collaborator CLIs.
- `advisory-summary` mode limits the reviewer prompt to a Codex-prepared
  summary, but the isolated working directory is only a working-directory
  boundary. It is not an OS-level filesystem sandbox.
- `panda_export.v1.json`, `manifest.json`, `evidence.json`,
  `{tool}.summary.json`, sidecars, and raw `{tool}.txt` logs may contain
  sensitive prompt or tool-output material.
- Host-level approval for running a local CLI lets that CLI access its normal
  user-scoped auth and runtime state. It does not by itself approve exporting
  private repository context.

## Known Security Findings

### Claude Code OAuth And Codex Sandboxing

Claude Code stores first-party login state in user-scoped local auth/keychain
state. In Codex, a direct `claude -p` command can be authenticated while a
sandboxed Python child process running Panda reports `Not logged in` and asks
for `/login`. This is an auth-visibility problem caused by the Codex filesystem
sandbox around the Panda runner, not proof that the user's Claude account is
logged out.

Operational rule:

- Claude-backed Panda runs launched by Codex should run the Panda runner outside
  the Codex filesystem sandbox so Claude Code can access its existing
  OAuth/keychain login state.
- Do not ask users to paste Claude tokens into Panda prompts, config files, or
  environment variables to work around this.
- If direct `claude -p` works but Panda records
  `claude_auth_unavailable_to_subprocess`, rerun the same Panda command through
  the approved host-level runner path.
- This host-level launch decision is separate from context-export approval. For
  private or tenant-restricted workspaces, still use `advisory-summary` or
  explicit full-context approval before sending repository context to external
  CLIs.

### OpenCode Runtime State

Panda gives OpenCode-backed tools a managed `XDG_DATA_HOME` under the one-shot
output directory or session directory. This isolates runtime DB/log state for a
run, while leaving `XDG_CONFIG_HOME` unset so normal OpenCode auth and provider
configuration remain available.

If OpenCode fails with SQLite, PRAGMA, or WAL errors under the managed data
directory, Panda records `opencode_managed_data_dir_failure`. Do not silently
retry with broader filesystem access; inspect or clear the managed runtime state
first.

### Auto-Approval And Tool Permissions

In unsupervised mode, Panda lets collaborator CLIs proceed through their own
local permission prompts so consultations do not block. This is useful for
review and exploration, but it also means the selected collaborator can run the
allowed local tool surface for that Panda mode.

Guardrails:

- Collaborators must not edit source files; Codex remains the only editor and
  integrator.
- Avoid destructive, publishing, deployment, history-rewriting, or production
  commands unless the user explicitly requested that class of action and the
  workspace policy allows it.
- Use `--execution sequential` when repository commands or local tool state are
  likely to conflict.

### Export Contracts And Private Context

Every normal one-shot run writes `panda_export.v1.json` before launching
reviewers. The export contract records the workspace, run directory, privacy
mode, requested tools, destinations, approval sources, prompt hash, and whether
raw repository access or shell exploration is allowed.

Use the export contract as an audit and fail-closed mechanism:

- `--prepare-export-manifest` writes the contract without launching reviewers.
- `--export-manifest PATH` validates that the prompt, workspace, privacy mode,
  tool selection, and destinations still match before reviewer launch.
- The export contract supports policy review; it does not override tenant
  policy or replace human approval for private context export.

### Artifacts And Retention

Artifacts are intentionally compact, but they are not automatically redacted.
Raw logs remain the authority for exact tool output and may contain sensitive
repository context, command output, paths, or model responses.

Before committing or publishing artifacts:

- Prefer compact evidence summaries over raw transcripts.
- Review raw `{tool}.txt`, stdout, stderr, patch, and benchmark files.
- Redact secrets, tokens, customer data, private logs, and machine-specific
  paths when they are not required for the public record.
- Use no-`.git` benchmark workspaces when provenance or contamination matters.

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
