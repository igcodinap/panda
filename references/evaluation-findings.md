# Panda Evaluation Findings

This note preserves the early evaluation lessons for Panda so the rationale does not live only in chat history.

## 2026-05-25 Pilot Summary

Run directory:

```text
/private/tmp/panda-eval/20260525-nightly
```

Protocol:

- Reliability canary plus five SWE-bench Lite style tasks.
- Two variants per task: `codex_alone` and `panda_explore`.
- Panda ran in `--mode explore --profile fast --tool all` before Codex edited.
- Claude quota, budget, rate-limit, auth, billing, or usage exhaustion counted as a Panda failure.

Observed result:

- Reliability canary passed.
- `codex_alone`: 5/5 accepted.
- `panda_explore`: 5/5 accepted.
- Panda runner failure rate: 0%.
- Claude budget failure rate: 0%.
- Panda evidence use rate: 100%.
- No lingering Panda, Claude, OpenCode, or SWE-bench harness processes were found after completion.

Tasks:

- `django__django-11099`
- `pytest-dev__pytest-5227`
- `sympy__sympy-13480`
- `matplotlib__matplotlib-23562`
- `astropy__astropy-14995`

Important caveat:

- The Matplotlib task is not a clean blinded usefulness datapoint because dataset patch/test metadata was inspected during task exploration. Keep it for runner throughput and artifact reliability, but do not use it as strong evidence of solve-rate improvement.

## What We Learned

- Panda's runner is materially healthier after the artifact and subprocess hardening work. Five real all-core explore runs completed without malformed evidence, missing summaries, process leaks, or Claude budget failures.
- The compact evidence boundary worked. `evidence.json` and `{tool}.summary.json` were enough for the useful decision path in every Panda task; raw logs were available but did not need to become primary context.
- Panda improved confidence more than measured pass rate in this first pilot. Codex alone also solved all five tasks, so the run does not prove a solve-rate lift.
- The task set was too easy and too surgical. Several tasks were one-line or traceback-local fixes, which saturates the comparison and creates a tie.
- Panda adds time overhead. In this run, recorded mean time to green was about 131 seconds for `codex_alone` and 305 seconds for `panda_explore`. The exact wall times are approximate for some early records.
- The A/B protocol is not scientifically clean yet. Codex solved `codex_alone` first, then Panda ran on the paired task, so the second variant benefited from Codex's memory. Future runs need better blinding or task order randomization.
- Usage metadata remains incomplete. Token and cost fields are still mostly `null`, so the current evaluation measures wall time and reliability better than token efficiency.

## Where Codex Alone Is More Likely To Struggle

The first pilot did not identify concrete Codex-alone failures because Codex went 5/5. Based on benchmark literature and current agent behavior, Panda is more likely to help on these classes:

- Harder SWE-bench Pro tasks, especially long-horizon enterprise-like issues.
- Terminal-heavy tasks where setup, shell recovery, dependency conflicts, and environment state matter.
- Multi-file migrations, API changes, schema changes, and cross-module behavior.
- Ambiguous issue descriptions where multiple valid interpretations exist and hidden tests encode unstated assumptions.
- Cross-language or polyglot repositories where localization is hard.
- Frontend or browser-visible defects that require implementation plus visual or interaction verification.
- Environment failures such as broken Docker setup, generated files, Makefile quirks, or dependency resolver conflicts.

## Recommended Next Evaluation

Use a failure-finding process instead of another easy 5-task pass:

1. Select a harder pool from SWE-bench Pro public tasks, Terminal-Bench style tasks, or a curated internal set.
2. Run `codex_alone` first on a larger sample and collect failures, stalls, and timeouts.
3. Re-run only those hard or failed cases with `panda_explore`, ideally on clean checkouts and with blinded task order.
4. Track whether Panda changes the outcome, not just whether it agrees with an already-obvious fix.
5. Keep Claude quota, budget, rate-limit, auth, billing, or usage exhaustion as Panda failures.
6. Separate metrics for solve-rate lift, runner reliability, evidence usefulness, wall time, and token/cost overhead.

For the next run, prefer tasks where the expected value of consultation is high enough to justify the overhead:

- multi-file changes
- unclear bug localization
- unfamiliar repo conventions
- flaky or complex test setup
- non-Python stacks
- browser or UI verification

The local implementation runbook for this next phase is `references/evaluation-hard-local.md`.

## 2026-05-25 Hard-Local Scout Progress

Run directory:

```text
/private/tmp/panda-eval/20260525-hard-local
```

Protocol:

- SWE-bench Pro was available locally and selected first.
- The selector saw 731 records, selected 20 candidates, and capped each repo at 3 tasks.
- Candidate manifests omit gold `patch` and `test_patch` content; only numeric or coarse metadata is written.
- The reliability canary passed: unit tests, Panda dry-run artifacts, one short real Panda explore run, and process cleanup check.
- Claude had no quota, budget, rate-limit, auth, billing, or usage exhaustion in the canary. One Qwen best-effort budget flag was recorded during the real canary, but the run completed and does not trigger the phase's Claude-core failure rule.

Scout and replay results so far:

| Task | Repo | Codex-alone scout | Panda replay | Notes |
| --- | --- | --- | --- | --- |
| `instance_ansible__ansible-e40889e7112ae00a21a2c74312b330e67a766cc0-v1055803c3a812189a1133297f7f5468579283f86` | `ansible/ansible` | `accepted` | not replayed | Wall time includes first-run harness setup; final official evaluator pass took about 19 seconds. |
| `instance_element-hq__element-web-4fec436883b601a3cac2d4a58067e597f737b817-vnan` | `element-hq/element-web` | `accepted` | not replayed | Public model outcome metadata showed 9/9 failures; Codex solved it after focused repair. |
| `instance_navidrome__navidrome-d0dceae0943b8df16e579c2d9437e11760a0626a` | `navidrome/navidrome` | `contaminated` | excluded | Excluded before solving after accidental exposure of gold patch/test metadata from a local helper JSON. |
| `instance_flipt-io__flipt-e50808c03e4b9d25a6a78af9c61a3b1616ea356b` | `flipt-io/flipt` | `timeout` | `timeout` | Panda evidence was used and all cores succeeded, but official hidden `TestSinkSpanExporter` still timed out after 10 minutes. |
| `instance_flipt-io__flipt-96820c3ad10b0b2305e8877b6b303f7fafdf815f` | `flipt-io/flipt` | `failed_tests` | `failed_tests` | Panda evidence correctly pushed toward a credential-store design, but the final official run still failed `TestStore_FetchWithECR` and `TestPrivateClient`. |

Current clean hard-local metrics:

- Clean scout tasks: 4.
- Accepted clean scout tasks: 2.
- Clean Codex scout pass rate: 50.0%.
- Codex struggle tasks found: 2.
- Contaminated tasks excluded from clean claims: 1.
- Panda replay tasks run: 2.
- Panda replay accepted tasks: 0.
- Failure-to-success rescue rate: 0.0%.
- Panda runner failure rate: 0.0%.
- Claude budget/rate/auth failure rate: 0.0%.
- Evidence use rate on Panda replays: 100.0%.

Portable proof artifacts:

- The 5-task pilot result JSON files are committed under `references/evaluation-results/20260525-nightly/` as compact per-task summaries.
- The hard-local run manifest, canary result, scout/replay result JSON files, and summary are committed under `references/evaluation-results/20260525-hard-local/`.
- The hard-local patches and official evaluator logs remain under `/private/tmp/panda-eval/20260525-hard-local`.
- Raw local logs under `logs/` are intentionally not part of the portable evidence set unless a future publication needs them; the committed docs and JSON summaries preserve the results without carrying large machine-local transcripts.

## Benchmark Notes

- OpenAI no longer recommends SWE-bench Verified as a frontier coding benchmark because of saturation, contamination risk, and residual test issues. They recommend SWE-bench Pro instead: https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/
- Terminal-Bench 2.0 is useful for Panda because it stresses command-line autonomy, setup, recovery from errors, and multi-step workflows rather than only producing a code diff: https://openreview.net/forum?id=a7Qa4CcHak
- SWE-bench Pro is designed for longer-horizon, more realistic software engineering issues and should be a better stress test than SWE-bench Lite for Panda's marginal value: https://arxiv.org/abs/2509.16941

## Current Interpretation

Panda is already credible as a review and confidence amplifier. The runner produced usable evidence reliably, and collaborator agreement was often valuable. We have not yet proven Panda improves solve rate over Codex alone. The next evaluation should intentionally seek harder tasks where Codex-alone has room to fail.
