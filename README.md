# Panda

Panda is a local consultation runner for Codex. It asks independent collaborator
cores to inspect a software task, produce evidence, map likely contracts, and
surface risks before Codex edits the code.

Project site: https://igcodinap.github.io/panda/

The project goal is practical and research-driven: improve Codex performance on
hard engineering work by using efficient model pressure where it helps, while
avoiding the cost and coordination failure modes of unconstrained multi-agent
systems. Codex remains the editor, integrator, and final decision-maker.

## When To Use Panda

Use Panda when a task is ambiguous, risky, multi-file, contract-heavy, or likely
to fail from a narrow first interpretation. Good fits include API migrations,
hidden-test inference, unfamiliar repositories, flaky setup, and changes where
an early wrong assumption would be expensive.

Skip Panda for small mechanical edits, obvious one-line fixes, or questions
where a single local inspection is enough.

## Quickstart

Prerequisites:

- Python 3.9 or newer.
- Optional advisor source: `claude` on `PATH`, or set `CLAUDE_BIN`, when you
  want Panda to spawn Claude Code agents.
- Optional advisor source: `opencode` on `PATH`, or set `OPENCODE_BIN`, when
  you want Panda to spawn OpenCode-backed agents such as Kimi, GLM, or Qwen.
- Optional Codex reviewer source: `codex` on `PATH`, or set `CODEX_BIN`, when
  you explicitly approve sending Panda review context to the Codex backend.
- Local authentication configured for every CLI you ask Panda to run.
- The Panda skill available to Codex, or the Panda checkout opened directly in
  Codex for repository-local use.

Live Codex reviewer runs require `--privacy-mode advisory-summary` for
summary-only review, `--privacy-mode full-context` for approved repository
context review, `--allow-codex-reviewer`, or `PANDA_ALLOW_CODEX_REVIEWER=1`
because they can export the selected Panda prompt/context to the Codex backend.
For private or tenant-restricted workspaces, use
`--privacy-mode advisory-summary --mode advisory` with a Codex-prepared summary
or skip external consultation.

Every consultation writes `panda_export.v1.json` beside `manifest.json`. The
export contract records whether Panda is doing summary review, full-context
review, or normal advisory work; whether raw repository access or shell
exploration is allowed; the destination tools/models; and a SHA-256 hash of the
exact prompt. Use `--prepare-export-manifest --output-dir ...` to generate this
contract without launching reviewers, then pass `--export-manifest PATH` on the
matching run to fail closed if prompt, workspace, privacy mode, tool selection,
or destinations changed. The contract is for audit and policy decisions; it
does not bypass tenant policy.

See `SECURITY.md` for Panda's security model, including external context export
rules, local CLI auth boundaries, host-level Claude Code launch guidance, and
artifact handling expectations.

ROI recommendation: once Panda becomes part of regular Codex work, the first
optional paid add-on we recommend is OpenCode Go alongside Codex. As of
May 29, 2026, OpenCode lists Go at $5 for the first month and then $10/month,
with usage limits expressed as dollar value and access to open coding models
such as Kimi, GLM, Qwen, DeepSeek, MiniMax, and MiMo. That gives Panda a
separate low-cost advisor budget while Codex stays focused on editing,
integration, and final verification. Verify current pricing and limits at
https://opencode.ai/go and https://dev.opencode.ai/docs/go/.

Codex-facing Panda use has two model-selection paths:

1. **Config-driven**: run Panda without model-selection flags. Panda loads the
   user config file. If no config exists, dry-runs can preview the portable
   Codex reviewer default, but live execution stops until Codex reviewer export
   is explicitly approved.
2. **One-off single model**: when the user says to run Panda only with a
   specific model, pass exactly one `--agent name=backend:model[@effort]`.

From the repository root, the config-driven/default path is:

```bash
python3 scripts/consult_ai_team.py \
  --mode explore \
  --role implementation-review \
  --prompt "We need to implement X. Constraints: Y. What risks, alternatives, and tests should Codex consider?"
```

For a one-off single-model run:

```bash
python3 scripts/consult_ai_team.py \
  --agent claude=claude:claude-opus-4-7@medium \
  --prompt "Review this plan with Claude only."
```

For private or tenant-restricted uncommitted code review, use a summary-only
advisory lane. Codex prepares the summary locally and excludes raw code, raw
diffs, secrets, credentials, and private logs:

```bash
python3 scripts/consult_ai_team.py \
  --mode advisory \
  --role code-review \
  --privacy-mode advisory-summary \
  --prompt-file /path/to/codex-prepared-summary.txt
```

For a public or otherwise approved workspace where the Codex reviewer is
allowed:

```bash
python3 scripts/consult_ai_team.py \
  --privacy-mode full-context \
  --allow-codex-reviewer \
  --agent codex=codex:gpt-5.5@medium \
  --prompt "Review this plan with the Codex reviewer."
```

To remember a Panda default for future runs, save an explicit behavior profile.
This stores the named agents Panda should spawn outside the repo; OpenCode is
recorded as a backend, so Kimi, GLM, Qwen, or any other OpenCode model can be
separate named agents:

```bash
python3 scripts/consult_ai_team.py \
  --agent kimi=opencode:opencode-go/kimi-k2.6 \
  --agent glm=opencode:opencode-go/glm-5.1 \
  --save-preferences
```

When using the Codex skill, the user can ask for this in natural language, for
example: "set Panda to use Kimi and GLM from now on." Codex should translate
that request into `--save-preferences`, write the user-scoped file at
`~/.config/panda/preferences.json` unless overridden, and rely on Panda's
automatic smoke test before treating the update as valid.

Plain Panda runs then spawn those configured agents. Without a saved profile,
live Panda stops before launching the Codex reviewer unless explicit export
approval is provided. A one-off single `--agent` run overrides the saved profile
for that invocation. Inspect or clear preferences with `--show-preferences` and
`--reset-preferences`; bypass them for one
invocation with `--ignore-preferences` or `PANDA_NO_PREFERENCES=1`. Older
slot-style preference files are still loaded for compatibility, but new saved
preferences are written as the single `profile.agents` behavior shape. Every
successful save automatically smoke-tests the saved profile by reloading it and
building the Panda commands it would run.

If a configured optional CLI is later removed or unavailable on `PATH`, Panda
fails that saved profile clearly instead of silently dropping the advisor.
Update the profile with `--save-preferences`, or bypass it once with
`--ignore-preferences`.

For a no-cost command preview:

```bash
python3 scripts/consult_ai_team.py --dry-run --prompt "smoke test"
```

Packaging note: Panda is currently checkout-first. The Python package builds
and installs the `panda_v2` artifact helpers, while the consultation and
evaluation runners are repo-level scripts intended to be run from a source
checkout. Console entry points are intentionally not part of the first
GitHub-first release.

Run tests with:

```bash
python3 -m pytest -q -p no:rerunfailures
```

The `-p no:rerunfailures` flag avoids local pytest plugin behavior that can try
to open a localhost socket in restricted environments.

## Workflow

1. Codex gathers task context and decides whether consultation is worth the
   overhead.
2. Panda runs the configured advisor profile. The Codex reviewer is available
   only when explicitly approved for live context export.
3. Advisors inspect and report; they do not own the working tree.
4. Panda writes compact evidence artifacts.
5. Codex reads the evidence, accepts or rejects advice, edits code, and verifies
   the result.

The operating thesis is:

```text
Independent advisors create pressure.
Structured artifacts preserve the pressure.
Codex integrates.
Tests decide.
Metrics teach the next prompt.
```

## Artifacts

Normal consultations write compact artifacts such as:

- `manifest.json`
- `panda_export.v1.json`
- `evidence.json`
- `{tool}.summary.json`
- `panda_contracts.v2.json`

Contract-falsifier runs write `panda_falsifier.v2.json`. Raw `{tool}.txt` logs
remain available for audit, but the compact JSON artifacts are the intended
first read.

For OpenCode-backed agents, Panda sets only `XDG_DATA_HOME` to a Panda-managed
runtime directory: `<output_dir>/opencode-data` for one-shot runs and
`<session_dir>/opencode-data` for session runs. It leaves `XDG_CONFIG_HOME`
unset so existing OpenCode auth and provider configuration still work. If
OpenCode fails against that managed runtime directory, Panda records a warning
instead of silently retrying with broader filesystem access.

## Evaluation Status

Panda has evidence as a review and confidence amplifier. Early runs showed that
its runner can produce usable evidence reliably, and contract-first V2 improved
the quality of advice on hard benchmark-style tasks. The project does not yet
claim a statistically proven solve-rate lift over Codex alone.

Published evaluation material is intentionally curated. Compact summaries and
methodology notes live under `references/`; raw local logs, raw patches, and
machine-specific transcripts are reviewed before publication.

Useful starting points:

- `references/evaluation-findings.md`
- `references/evaluation-nightly.md`
- `references/evaluation-hard-local.md`

## Research Foundations

Panda is grounded in recent work on single-agent versus multi-agent systems,
coordination failures, thinking-token budgets, and software-engineering
benchmarks. See `references/research-foundations.md` for an annotated review
and `references/panda-v2-philosophy.md` for the design argument.

The short version: Panda uses independent advisors for pressure and falsifiable
claims, but avoids shared-state multi-agent implementation. That design follows
the research warning that more agents are not automatically better, especially
when coordination cost, token budgets, and verification quality are controlled.

## Limitations

- Panda adds latency and model cost, so it should be reserved for tasks where
  independent pressure is likely to pay for itself.
- Collaborator outputs are advice, not ground truth.
- Live Codex reviewer execution is opt-in because it can export private
  workspace context to the Codex backend.
- Full-context `explore` code review is guarded for all external collaborators;
  use `--privacy-mode advisory-summary --mode advisory` when only a bounded
  summary should leave Codex. Summary mode uses an isolated working directory,
  but it is not an OS-level filesystem sandbox.
- `panda_export.v1.json` makes exports auditable and fail-closed, but platform
  trust rules still decide whether an escalated cloud-backed review is allowed.
- Benchmark results are still exploratory and contamination-sensitive.
- Claude/OpenCode/Codex availability, rate limits, and local CLI state can
  affect runs.

## License

Apache-2.0. See `LICENSE`.
