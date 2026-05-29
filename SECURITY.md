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
- Review raw logs and patches before publishing them.
- Prefer compact evidence summaries over raw transcripts.
- Use no-`.git` benchmark workspaces when contamination or provenance matters.

## Supported Versions

Panda is pre-1.0. Security fixes are handled on the main development line until
a formal release policy exists.
