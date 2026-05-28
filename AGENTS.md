# Panda Agent Guide

## Repo Map
- `SKILL.md` is the external skill contract and user-facing workflow.
- `scripts/consult_ai_team.py` is the stable consultation runner entrypoint.
- `scripts/panda_eval.py` is the evaluation and benchmark prompt harness.
- `src/panda_v2/` contains opt-in V2 contract artifact helpers only.
- `tests/` contains compatibility, artifact, prompt, and eval tests.
- `references/` contains runbooks, findings, and portable evaluation summaries.

## Edit Order
1. Update V2 schemas in `src/panda_v2/contracts.py`.
2. Update extractors in `src/panda_v2/extractors.py`.
3. Update prompts in `src/panda_v2/prompts.py`.
4. Update artifacts and sidecar writing in `src/panda_v2/artifacts.py`.
5. Wire script integration in `scripts/consult_ai_team.py`.
6. Wire eval integration in `scripts/panda_eval.py`.
7. Update docs and runbooks.
8. Add or update tests and fixtures.

## V1 Compatibility Rules
- V1 is the default unless a command explicitly opts into V2.
- Do not rename, remove, or change existing V1 artifact fields.
- Do not change V1 prompt text without updating the explicit compatibility tests.
- V2 must not add extra LLM calls unless an explicit V2 command requests them.

## Artifact Rules
- `evidence.json`, `{tool}.summary.json`, `manifest.json`, and session artifacts remain V1-compatible.
- `panda_contracts.v2.json` is a V2 sidecar, not a replacement for `evidence.json`.
- `panda_falsifier.v2.json` is advisory and must not be treated as ground truth.
- Missing or malformed V2 fenced JSON must record warnings and never fabricate claims.

## Prompt Version Rules
- `prepare-first-pass` defaults to `prompt_version: 1`.
- `prompt_version: 2` is opt-in and must request exact contract names and local evidence.
- `prepare-second-pass` remains a V1 debugging prompt; do not add V2 contracts there without a new explicit prompt version.
- Contract-falsifier prompts are one-pass audits, not debate invitations.
- Prompt changes must preserve Codex as the sole editor and integrator.

## Test Expectations
- Test V1 defaults before V2 behavior.
- Test schema validation, missing blocks, malformed blocks, multiple candidates, and `not_found` handling.
- Test V2 sidecars are written beside `evidence.json`.
- Test falsifier behavior is explicit and one-pass.
- Test this guide keeps the required sections and references existing top-level paths.
