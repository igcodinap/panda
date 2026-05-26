# Panda Hard-Local Evaluation

Use this workflow to test whether Panda improves Codex outcomes on tasks where Codex alone struggles. This is an overnight, laptop-friendly SWE-bench-first process.

## 1. Initialize

```bash
python3 scripts/panda_eval.py init \
  --eval-mode hard-local \
  --run-dir /private/tmp/panda-eval/$(date +%Y%m%d)-hard-local
```

Hard-local runs use:

- variants: `codex_alone_scout`, `panda_replay`
- Panda timeout: 600 seconds
- target candidate count: 20
- max expanded candidate count: 30
- repo cap: 3 tasks per repo

## 2. Select Hard Candidates

Prefer a local exported records file when you already have one:

```bash
python3 scripts/panda_eval.py select-hard \
  --run-dir /private/tmp/panda-eval/YYYYMMDD-hard-local \
  --records-file /path/to/swebench-records.json \
  --target-count 20 \
  --repo-cap 3
```

To allow Hugging Face dataset loading from the network/cache:

```bash
python3 scripts/panda_eval.py select-hard \
  --run-dir /private/tmp/panda-eval/YYYYMMDD-hard-local \
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
  --run-dir /private/tmp/panda-eval/YYYYMMDD-hard-local \
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

Run Panda first:

```bash
python3 scripts/consult_ai_team.py \
  --tool all \
  --mode explore \
  --profile fast \
  --timeout 600 \
  --output-dir /private/tmp/panda-eval/YYYYMMDD-hard-local/tasks/TASK_ID/panda_replay/panda \
  --prompt "SWE-bench task TASK_ID. Inspect the clean checkout and advise Codex. Do not edit files."
```

Read `evidence.json` and `{tool}.summary.json` first. Inspect raw logs only when necessary. Codex remains the only editor.

Record replay results:

```bash
python3 scripts/panda_eval.py record \
  --run-dir /private/tmp/panda-eval/YYYYMMDD-hard-local \
  --task-id TASK_ID \
  --variant panda_replay \
  --tests-passed true \
  --classification accepted \
  --wall-seconds 900 \
  --panda-output-dir /private/tmp/panda-eval/YYYYMMDD-hard-local/tasks/TASK_ID/panda_replay/panda \
  --evidence-used \
  --patch-path /path/to/patch.diff \
  --test-output-path /path/to/swebench-report.json
```

Claude quota, budget, rate-limit, auth, billing, or usage exhaustion is a Panda failure. Stop replay after the first clear budget exhaustion and summarize the completed work.

## 5. Summarize

```bash
python3 scripts/panda_eval.py summarize \
  --run-dir /private/tmp/panda-eval/YYYYMMDD-hard-local
```

Hard-local metrics include:

- `codex_scout_pass_rate`
- `codex_struggle_count`
- `panda_replay_pass_rate`
- `failure_to_success_rescue_rate`
- `panda_runner_failure_rate`
- `claude_budget_failure_rate`
- `evidence_use_rate`
- `mean_time_to_green`
- `contaminated_task_count`

Interpretation:

- Panda is promising if it rescues at least 25% of clean Codex-struggle tasks.
- Panda is not proven better if Codex finds too few struggles, Panda only ties, or evidence is unused.
- Runner reliability target is zero malformed/missing artifacts and under 10% runner failure rate.
