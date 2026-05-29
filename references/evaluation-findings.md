# Panda Evaluation Findings

This note preserves the early evaluation lessons for Panda so the rationale does not live only in chat history.

## Public Artifact Policy

Public evaluation evidence is curated by default. Commit compact summaries,
methodology notes, and redacted aggregate records; review raw logs, raw patches,
machine-local transcripts, and benchmark-specific artifacts before publishing
them. Treat solve-rate lift as an active evaluation question unless a run is
clean, blinded enough for the claim, and documented with contamination notes.

## 2026-05-25 Pilot Summary

Run directory:

```text
<panda-eval-root>/20260525-nightly
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
<panda-eval-root>/20260525-hard-local
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
- The hard-local patches and official evaluator logs remain under `<panda-eval-root>/20260525-hard-local`.
- Raw local logs under `logs/` are intentionally not part of the portable evidence set unless a future publication needs them; the committed docs and JSON summaries preserve the results without carrying large machine-local transcripts.

## Benchmark Notes

- OpenAI no longer recommends SWE-bench Verified as a frontier coding benchmark because of saturation, contamination risk, and residual test issues. They recommend SWE-bench Pro instead: https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/
- Terminal-Bench 2.0 is useful for Panda because it stresses command-line autonomy, setup, recovery from errors, and multi-step workflows rather than only producing a code diff: https://openreview.net/forum?id=a7Qa4CcHak
- SWE-bench Pro is designed for longer-horizon, more realistic software engineering issues and should be a better stress test than SWE-bench Lite for Panda's marginal value: https://arxiv.org/abs/2509.16941

## Current Interpretation

Panda is already credible as a review and confidence amplifier. The runner produced usable evidence reliably, and collaborator agreement was often valuable. We have not yet proven Panda improves solve rate over Codex alone. The next evaluation should intentionally seek harder tasks where Codex-alone has room to fail.

## 2026-05-26 Hard-Local Continuation

Additional scout tasks were run from the same hard-local directory:

```text
<panda-eval-root>/20260525-hard-local
```

New scout/replay results:

| Task | Repo | Codex-alone scout | Panda replay | Notes |
| --- | --- | --- | --- | --- |
| `instance_qutebrowser__qutebrowser-3fd8e12949b8feda401930574facf09dd4180bba` | `qutebrowser/qutebrowser` | `accepted` | not replayed | Official evaluator accepted after command rename/API compatibility fixes. Local pytest was blocked by missing qutebrowser pytest plugins, so the official Docker evaluator carried the verdict. |
| `instance_future-architect__vuls-3c1489e588dacea455ccf4c352a3b1006902e2d4` | `future-architect/vuls` | `accepted` | not replayed | Official evaluator accepted after the final allowed scout loop. A raw-sample metadata typo briefly produced false `0.0` accuracy despite all selected tests passing; corrected `pass_to_pass` restored `1.0`. |
| `instance_navidrome__navidrome-fa85e2a7816a6fe3829a4c0d8e893e982b0985da` | `navidrome/navidrome` | `failed_tests` | `failed_tests` | First-pass Panda ran cleanly and evidence was used, but it recommended only username canonicalization. Official tests revealed the deeper `Player.UserId` / `Username` / `IP` API contract. |
| same Navidrome task | `navidrome/navidrome` | already a struggle | second pass contaminated | Second-pass Panda was operationally clean but one collaborator referenced target/ground-truth commit details, so the attempt is excluded from clean rescue claims. |

Updated clean hard-local metrics:

- Clean scout tasks: 7.
- Accepted clean scout tasks: 4.
- Clean Codex scout pass rate: 57.1%.
- Codex struggle tasks found: 3.
- Panda replay tasks run: 3.
- Panda replay accepted tasks: 0.
- Failure-to-success rescue rate: 0.0%.
- Panda runner failure rate: 0.0%.
- Claude budget/rate/auth failure rate: 0.0%.
- Contaminated tasks/attempts excluded from clean claims: 2.

New lessons:

- First-pass Panda can still miss structural API contracts when the issue text points at a simpler surface fix. The Navidrome replay is a clean example: all cores agreed on canonical username handling, local base tests passed, but official tests required a deeper player identity/schema change.
- The second-pass workflow did surface the deeper contract, but this particular run is not clean because the target commit was accessible in the full local Git clone and a collaborator appears to have inspected or inferred ground-truth details.
- Future benchmark workspaces need stronger anti-leakage isolation. Prefer base-only checkouts with the target commit unavailable, or no-`.git` source copies for Panda exploration. Prompt rules alone are not enough.
- Patch artifact generation must include untracked files. For eval candidates with new files, use intent-to-add or an equivalent patch builder before `git diff`; otherwise migrations or new source files can be silently omitted.
- Raw-sample metadata matters. `fail_to_pass` and `pass_to_pass` must remain correctly stringified Python lists for the current SWE-bench Pro evaluator.

Portable proof artifacts for these continuation results were added under:

```text
references/evaluation-results/20260525-hard-local/
```

## Contract-First Rerun Protocol

The next hard-local rerun should use the additive contract-first workflow rather than the earlier thin first-pass prompt:

- Prepare Panda-visible workspaces with `prepare-workspace`, which removes `.git`, nested git files, VCS metadata traces, and common transient caches.
- Run `check-workspace --strict` before Panda sees a benchmark workspace.
- Generate first-pass Panda prompts with `prepare-first-pass` so collaborators explicitly review API contracts, public/local tests, evaluator-like assertions, falsifiers, and verification plans.
- Keep raw commit SHAs, target commit details, gold `patch`, gold `test_patch`, `FAIL_TO_PASS`, hidden test source, and hardness metadata out of Panda prompts.
- Record `workspace_metadata_path`, `workspace_isolated`, contamination status, evidence-used status, advice quality, and budget failures for each isolated rerun.
- Treat the isolated contract-first results as a new lab signal rather than an apples-to-apples replacement for the earlier full-clone attempts.

## 2026-05-26 Contract-First First-Pass Rerun

Run directory:

```text
<panda-eval-root>/20260526-contract-first-v2
```

Protocol:

- Real Panda first-pass runs only, using `prepare-workspace`, `check-workspace --strict`, and `prepare-first-pass`.
- Five previously used benchmark cases were run from `git archive` base exports with no `.git` in the Panda-visible workspace.
- No Codex re-solve and no official Docker evaluator pass were run in this phase, so these records are evidence/reliability datapoints, not solve-rate outcomes.
- Result records are intentionally marked `classification: low_confidence` and `accepted: false` to avoid overstating benchmark success.

Observed result:

- Panda first-pass runs completed for all 5 tasks.
- Panda runner failure rate: 0.0%.
- Claude budget/rate/auth failure rate: 0.0%.
- Evidence use rate: 100.0%.
- Contamination count: 0.
- Strict workspace checks passed before Panda saw each workspace.

Implementation issues found during the rerun:

- Panda review found that the workspace gold-field scan only checked root JSON files. This was fixed by scanning JSON recursively and adding a nested JSON leakage test.
- The first benchmark setup exposed a safe-path collision: two Flipt task IDs both collapsed to `instance_flipt-io__flipt-_redacted-sha`. This was fixed by appending a short deterministic hash suffix that does not expose the commit SHA.
- The internal suite passed after both fixes: 99 tests.

Early evidence-quality notes:

- Qutebrowser: contract-first Panda produced actionable command-renaming guidance, including deprecated aliases and hardcoded command-name call sites.
- Vuls: contract-first Panda produced actionable CVSS severity fallback guidance and named `MaxCvss3Score`, `Cvss3Scores`, filtering, grouping, and report propagation.
- Flipt audit task: contract-first Panda produced a broader package/config/schema/interceptor implementation map than the earlier thin prompt.
- Navidrome: the new prompt improved the advice from only surface username canonicalization toward authenticated-user/context identity flow, but it still did not clearly prove the deeper `Player.UserId`/schema contract would be solved. This remains a candidate for a full solve plus second-pass evaluation.

Portable proof artifacts:

```text
references/evaluation-results/20260526-contract-first-v2/
```

## 2026-05-27 Navidrome Diagnosis Replay

Run directory:

```text
<panda-eval-root>/20260527-diagnosis
```

Protocol:

- Replayed the prior clean Codex-struggle task `instance_navidrome__navidrome-fa85e2a7816a6fe3829a4c0d8e893e982b0985da`.
- Used the isolated contract-first Panda first-pass output from `20260526-contract-first-v2`.
- Codex edited from a clean base workspace and ran the official SWE-bench Pro local Docker evaluator.
- No Panda second pass was used for the accepted patch; official evaluator failures were enough to expose the missing API contract details.

Result:

- Official evaluator accepted the final candidate patch (`navidrome_first_pass_v5`).
- Selected tests: `TestCore`, `TestPersistence`.
- Panda runner failure rate for this replay: 0.0%.
- Claude budget/rate/auth failure: 0.
- Contamination status: clean/no `.git` Panda workspace.

What Panda helped with:

- Panda first pass correctly pushed the solution toward authenticated-user/player registration rather than treating the raw Subsonic username as authoritative.
- The first-pass evidence was actionable enough to start in the right subsystem: `core/players.go`, `server/subsonic/middlewares.go`, and `persistence/player_repository.go`.

What Panda missed:

- Panda did not identify the exact target API shape: `Player.UserId`, `Player.Username`, and `Player.IP`.
- Panda did not identify that `player.user_name` needed to stop being the ownership foreign key; persistence had to move permission and matching behavior to stable `user_id`.
- The official evaluator feedback, not Panda, exposed the field-name and persistence-mapping contract.

Interpretation:

- This is the first clean local rescue signal after the contract-first prompt change: a prior Codex-struggle task became accepted when Codex used Panda first-pass evidence plus official evaluator feedback.
- It is not a broad benchmark claim. It is one task, and the solve required multiple evaluator-feedback iterations.
- The most useful tuning signal is clear: Panda prompts should more aggressively ask collaborators to infer public model/API field names, persistence column mappings, and foreign-key/permission contracts, not only the obvious runtime bug.

Portable proof artifacts:

```text
references/evaluation-results/20260527-diagnosis/
```

## 2026-05-27 Flipt ECR Diagnosis Replay

Run directory:

```text
<panda-eval-root>/20260527-diagnosis
```

Protocol:

- Replayed the prior clean Codex-struggle task `instance_flipt-io__flipt-96820c3ad10b0b2305e8877b6b303f7fafdf815f`.
- Used the isolated contract-first Panda first-pass output from `20260526-contract-first-v2`.
- Codex edited from a clean base workspace and ran the official SWE-bench Pro local Docker evaluator.
- No Panda second pass was used.

Result:

- Accepted after one compile-contract iteration (`flipt_ecr_first_pass_v2`).
- Selected tests passed in the container: `TestFile`, `TestStore_FetchWithECR`, `TestAuthenicationTypeIsValid`, `TestDefaultClientFunc`, `TestECRCredential`, `TestCredential`, `TestPrivateClient`, and `TestPublicClient`, plus the surrounding OCI regression tests selected by the harness.
- The raw JSONL sample used uppercase `FAIL_TO_PASS`/`PASS_TO_PASS`; the local evaluator expected lowercase stringified `fail_to_pass`/`pass_to_pass`. A normalized scorer-only copy was used after confirming the same selected tests already passed.
- Panda runner failure rate for this replay: 0.0%.
- Claude budget/rate/auth failure: 0.
- Contamination status: clean/no `.git` Panda workspace.

What Panda helped with:

- Panda first pass correctly identified the core direction: split public and private ECR auth, add `ecrpublic`, use request context, cache credentials by expiry, and avoid relying only on the default ORAS auth path.
- The evidence was actionable enough to choose the right subsystem and implement a deeper credential-store design rather than only patching the old inline private ECR client.

What Panda missed:

- Panda did not provide exact hidden-test API names such as `cacheItem` and `CredentialsStore.extractCredential`.
- Panda did not settle the compatibility shape around the internal `credentialFunc`: existing tests still expected it to be directly callable, while the new workflow benefited from an `Execute` method.
- The official evaluator feedback exposed those exact type/method naming contracts.

Interpretation:

- This is the second clean local rescue signal in the diagnosis run: another prior Codex-struggle task became accepted when Codex used contract-first Panda evidence plus bounded evaluator feedback.
- The sample is still tiny, but the pattern is getting clearer: Panda is helpful at pushing Codex toward the right architectural region, while evaluator feedback remains necessary for exact hidden-test API surface.
- The next Panda prompt tweak should ask collaborators to explicitly infer unexported type names, test seam names, method names, and backward-compatibility constraints from nearby tests and local conventions.
