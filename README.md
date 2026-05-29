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
- Minimum: `codex` on `PATH`, or set `CODEX_BIN`. With only Codex available,
  Panda runs the portable Codex reviewer on `gpt-5.5` with `medium` reasoning.
- Optional advisor source: `claude` on `PATH`, or set `CLAUDE_BIN`, when you
  want Panda to spawn Claude Code agents.
- Optional advisor source: `opencode` on `PATH`, or set `OPENCODE_BIN`, when
  you want Panda to spawn OpenCode-backed agents such as Kimi, GLM, or Qwen.
- Local authentication configured for every CLI you ask Panda to run.
- The Panda skill available to Codex, or the Panda checkout opened directly in
  Codex for repository-local use.

Claude Code and OpenCode are not required for the base install. They add more
independent review pressure when configured, but the default no-config path is
intentionally Codex-only so a fresh checkout can still use Panda.

Codex-facing Panda use has two model-selection paths:

1. **Config-driven**: run Panda without model-selection flags. Panda loads the
   user config file, and if no config exists it runs Codex `gpt-5.5` with
   `medium` reasoning.
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

Plain Panda runs then spawn those configured agents. A one-off single `--agent`
run overrides the saved profile for that invocation. Inspect or clear preferences
with `--show-preferences` and `--reset-preferences`; bypass them for one
invocation with `--ignore-preferences` or `PANDA_NO_PREFERENCES=1`. Older
slot-style preference files are still loaded for compatibility, but new saved
preferences are written as the single `profile.agents` behavior shape. Every
successful save automatically smoke-tests the saved profile by reloading it and
building the Panda commands it would run.

If a configured optional CLI is later removed or unauthenticated, Panda fails
that saved profile clearly instead of silently dropping the advisor. Update the
profile with `--save-preferences`, or bypass it once with `--ignore-preferences`.

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
2. Panda runs the Codex reviewer by default, and can add Claude Code,
   OpenCode GLM, OpenCode Qwen, or other named agents as independent advisors.
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
- `evidence.json`
- `{tool}.summary.json`
- `panda_contracts.v2.json`

Contract-falsifier runs write `panda_falsifier.v2.json`. Raw `{tool}.txt` logs
remain available for audit, but the compact JSON artifacts are the intended
first read.

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
- Benchmark results are still exploratory and contamination-sensitive.
- Claude/OpenCode/Codex availability, rate limits, and local CLI state can
  affect runs.

## License

Apache-2.0. See `LICENSE`.
