# Contributing To Panda

Thanks for improving Panda. The project is intentionally conservative: Panda
adds independent model pressure around Codex, but Codex remains the only editor
and integrator.

## Development Setup

Use Python 3.9 or newer. From the repository root:

```bash
python3 -m pytest -q -p no:rerunfailures
```

For runner smoke checks that do not call external models:

```bash
python3 scripts/consult_ai_team.py --dry-run --prompt "smoke test"
```

## Change Guidelines

- Keep Panda V2 as the main engineering consultation flow.
- Do not reintroduce a live Panda V1 protocol path.
- Preserve the compact base evidence layer: `evidence.json`,
  `{tool}.summary.json`, `manifest.json`, and session artifacts.
- Treat `panda_contracts.v2.json` and `panda_falsifier.v2.json` as sidecars,
  not replacements for the base evidence.
- Do not fabricate claims from malformed or missing model output.
- Keep prompt changes compatible with Codex as the sole editor and integrator.

## Evaluation And Artifacts

Public evaluation evidence should be curated before it is committed. Prefer
compact summaries, methodology notes, and redacted aggregate records. Do not
commit raw local logs, raw benchmark patches, secrets, private customer data, or
machine-specific transcripts.

## Pull Request Checklist

- Tests pass with `python3 -m pytest -q -p no:rerunfailures`.
- Documentation reflects any user-visible behavior change.
- New artifacts are compact, intentional, and safe to publish.
- Prompt/schema changes include focused tests for defaults, malformed output,
  missing blocks, `not_found`, and sidecar writing when relevant.
