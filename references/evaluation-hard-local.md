# Panda Hard-Local Evaluation

Use this workflow to test whether Panda improves Codex outcomes on tasks where Codex alone struggles. This is an overnight, laptop-friendly SWE-bench-first process.

## 1. Initialize

```bash
python3 scripts/panda_eval.py init \
  --eval-mode hard-local \
  --run-dir <panda-eval-root>/$(date +%Y%m%d)-hard-local
```

Hard-local runs use:

- variants: `codex_alone_scout`, `panda_replay`, `panda_replay_second_pass`
- Panda timeout: 600 seconds
- target candidate count: 20
- max expanded candidate count: 30
- repo cap: 3 tasks per repo

## 2. Select Hard Candidates

Prefer a local exported records file when you already have one:

```bash
python3 scripts/panda_eval.py select-hard \
  --run-dir <panda-eval-root>/YYYYMMDD-hard-local \
  --records-file /path/to/swebench-records.json \
  --target-count 20 \
  --repo-cap 3
```

To allow Hugging Face dataset loading from the network/cache:

```bash
python3 scripts/panda_eval.py select-hard \
  --run-dir <panda-eval-root>/YYYYMMDD-hard-local \
  --allow-network
```

The selector tries sources in deterministic order:

1. `ScaleAI/SWE-bench_Pro`
2. `princeton-nlp/SWE-bench`
3. `SWE-bench/SWE-bench`
4. `SWE-bench/SWE-bench_Verified`
5. `princeton-nlp/SWE-bench_Verified`

The selector writes:

- `tasks.json`
- `hard_candidates.json`
- `candidate_manifest.json`

Gold leakage policy:

- `patch` and `test_patch` content is never written to the selected task file.
- Numeric hardness metadata may include changed file counts, changed line counts, and `FAIL_TO_PASS` count.
- Exact gold test names and gold patch text are omitted.

## 3. Scout With Codex Alone

For each selected task, run Codex alone from a clean checkout with a fixed budget:

- 20 minutes per task
- max 3 test/debug loops
- no Panda
- no gold patch or test patch inspection

Record every scout result:

```bash
python3 scripts/panda_eval.py record \
  --run-dir <panda-eval-root>/YYYYMMDD-hard-local \
  --task-id TASK_ID \
  --variant codex_alone_scout \
  --tests-passed false \
  --accepted false \
  --classification failed_tests \
  --wall-seconds 1200 \
  --patch-path /path/to/patch.diff \
  --test-output-path /path/to/swebench-report.json
```

Valid scout classifications:

- `accepted`
- `failed_tests`
- `timeout`
- `no_patch`
- `environment_failure`
- `low_confidence`
- `slow_solve`
- `contaminated`

If fewer than five Codex struggles are found, select and scout ten more candidates once, up to 30 total.

## 4. Replay Struggles With Panda

Replay up to eight Codex-struggle tasks from clean checkouts:

- `failed_tests`
- `timeout`
- `no_patch`
- `low_confidence`
- `slow_solve`

Create a benchmark-safe Panda workspace first. The destination is a source copy without `.git`, nested git files, or common transient caches:

```bash
python3 scripts/panda_eval.py prepare-workspace \
  --run-dir <panda-eval-root>/YYYYMMDD-hard-local \
  --task-id TASK_ID \
  --source-workspace /path/to/base/task/repo
```

The command writes `workspace_metadata.json` under a safe task directory with commit-like task-id fragments redacted. Use the printed metadata path and its `destination_path` for the next commands. If you already have a prepared workspace, check it before exposing it to Panda:

```bash
python3 scripts/panda_eval.py check-workspace \
  --workspace <panda-eval-root>/YYYYMMDD-hard-local/tasks/SAFE_TASK_DIR/workspace \
  --strict
```

Then generate the first-pass contract review prompt:

```bash
python3 scripts/panda_eval.py prepare-first-pass \
  --run-dir <panda-eval-root>/YYYYMMDD-hard-local \
  --task-id TASK_ID \
  --workspace <panda-eval-root>/YYYYMMDD-hard-local/tasks/SAFE_TASK_DIR/workspace
```

Run the printed `consult_ai_team.py` command. It will look like:

```bash
python3 scripts/consult_ai_team.py \
  --tool all \
  --mode explore \
  --role implementation-review \
  --profile fast \
  --timeout 600 \
  --output-dir <panda-eval-root>/YYYYMMDD-hard-local/tasks/SAFE_TASK_DIR/panda_replay/panda \
  --prompt-file <panda-eval-root>/YYYYMMDD-hard-local/tasks/SAFE_TASK_DIR/panda_replay/panda_prompt.txt \
  --workspace <panda-eval-root>/YYYYMMDD-hard-local/tasks/SAFE_TASK_DIR/workspace
```

This evaluation path intentionally keeps explicit legacy tool flags so benchmark
baselines remain reproducible. Normal Codex-triggered Panda usage should use the
config-driven path or a single one-off `--agent`.

The prompt asks for a contract map, local test evidence, likely evaluator assertions, recommendation, alternative, risks, falsifiers, and verification plan. It must not include gold `patch`, `test_patch`, `FAIL_TO_PASS`, hidden test source, target commit details, raw commit SHAs, or hardness metadata. Use `--tool auto` or `--tool codex` for portable Codex-reviewer fallback runs; keep `--tool all` for the legacy full-Panda baseline.

Read `evidence.json` and `{tool}.summary.json` first. Inspect raw logs only when necessary. Codex remains the only editor.

Record replay results:

```bash
python3 scripts/panda_eval.py record \
  --run-dir <panda-eval-root>/YYYYMMDD-hard-local \
  --task-id TASK_ID \
  --variant panda_replay \
  --tests-passed true \
  --classification accepted \
  --wall-seconds 900 \
  --panda-output-dir <panda-eval-root>/YYYYMMDD-hard-local/tasks/SAFE_TASK_DIR/panda_replay/panda \
  --workspace-metadata-path <panda-eval-root>/YYYYMMDD-hard-local/tasks/SAFE_TASK_DIR/workspace_metadata.json \
  --workspace-isolated true \
  --evidence-used \
  --patch-path /path/to/patch.diff \
  --test-output-path /path/to/swebench-report.json
```

Claude quota, budget, rate-limit, auth, billing, or usage exhaustion is a Panda failure. Stop replay after the first clear budget exhaustion and summarize the completed work.

## 5. Second-Pass Recovery

Use second pass only when the first Panda replay succeeded, Codex produced a patch, and verification failed. Prepare a bounded recovery prompt:

```bash
python3 scripts/panda_eval.py prepare-second-pass \
  --run-dir <panda-eval-root>/YYYYMMDD-hard-local \
  --task-id TASK_ID \
  --first-pass-panda-output-dir <panda-eval-root>/YYYYMMDD-hard-local/tasks/SAFE_TASK_DIR/panda_replay/panda \
  --patch-path /path/to/patch.diff \
  --test-output-path /path/to/swebench-report-or-stdout.log \
  --workspace <panda-eval-root>/YYYYMMDD-hard-local/tasks/SAFE_TASK_DIR/workspace
```

The command writes `panda_prompt.txt`, records prompt metadata, and prints the exact `consult_ai_team.py --mode explore --role debugging` command to run. The prompt includes capped task context, first-pass evidence summaries, a capped patch excerpt, failing test names, and nearby error lines. It references artifact paths instead of embedding raw logs, and it must not include gold `patch` or `test_patch` content, raw commit SHAs, target commit details, or hidden-test-derived hardness stats.

Record second-pass results separately:

```bash
python3 scripts/panda_eval.py record \
  --run-dir <panda-eval-root>/YYYYMMDD-hard-local \
  --task-id TASK_ID \
  --variant panda_replay_second_pass \
  --tests-passed false \
  --accepted false \
  --classification failed_tests \
  --wall-seconds 900 \
  --panda-output-dir <panda-eval-root>/YYYYMMDD-hard-local/tasks/SAFE_TASK_DIR/panda_replay_second_pass/panda \
  --second-pass-prompt-path <panda-eval-root>/YYYYMMDD-hard-local/tasks/SAFE_TASK_DIR/panda_replay_second_pass/panda_prompt.txt \
  --workspace-metadata-path <panda-eval-root>/YYYYMMDD-hard-local/tasks/SAFE_TASK_DIR/workspace_metadata.json \
  --workspace-isolated true \
  --evidence-used \
  --patch-path /path/to/patch.diff \
  --test-output-path /path/to/swebench-report-or-stdout.log
```

Optional scoring fields can be added when the run is reviewed: `--panda-direction-correct`, `--panda-missed-contract`, `--codex-implementation-error`, `--evidence-was-actionable`, and `--advice-quality-notes`.

## 6. Summarize

```bash
python3 scripts/panda_eval.py summarize \
  --run-dir <panda-eval-root>/YYYYMMDD-hard-local
```

Hard-local metrics include:

- `codex_scout_pass_rate`
- `codex_struggle_count`
- `panda_replay_pass_rate`
- `failure_to_success_rescue_rate`
- `panda_replay_second_pass_pass_rate`
- `second_pass_rescue_rate`
- `incremental_second_pass_rescue_count`
- `panda_runner_failure_rate` across first-pass replay and second-pass Panda runs
- `claude_budget_failure_rate` across first-pass replay and second-pass Panda runs
- `evidence_use_rate` across first-pass replay and second-pass Panda runs
- `panda_replay_runner_failure_rate`
- `panda_replay_claude_budget_failure_rate`
- `panda_replay_evidence_use_rate`
- `second_pass_runner_failure_rate`
- `second_pass_claude_budget_failure_rate`
- `second_pass_evidence_use_rate`
- `mean_time_to_green`
- `contaminated_task_count`

Interpretation:

- Panda is promising if it rescues at least 25% of clean Codex-struggle tasks.
- Treat second-pass rescue metrics as exploratory until at least five clean Codex-struggle tasks have second-pass attempts after a matching first Panda replay failed to pass.
- Do not count a second-pass success as an incremental rescue unless the same task has a matching first-pass `panda_replay` record that did not pass.
- Treat isolated contract-first reruns as a new lab signal. Do not overwrite earlier full-clone results or compare them as a strict apples-to-apples run.
- Panda is not proven better if Codex finds too few struggles, Panda only ties, or evidence is unused.
- Runner reliability target is zero malformed/missing artifacts and under 10% runner failure rate.
