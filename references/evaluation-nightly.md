# Panda Nightly Evaluation

Use this workflow when testing whether Panda is reliable and whether Panda explore improves Codex on a small SWE-bench-style pilot.

## 1. Initialize

```bash
python3 scripts/panda_eval.py init --run-dir <panda-eval-root>/$(date +%Y%m%d)-nightly
```

This creates:

- `run_manifest.json`
- `tasks.json`
- `results.json`

The default task list contains five pinned SWE-bench Lite instance IDs for the first pilot. Replace it with `--tasks-file` if you want a different pinned set.

## 2. Reliability Canary

```bash
python3 scripts/panda_eval.py canary --run-dir <panda-eval-root>/$(date +%Y%m%d)-nightly
```

For a local dry rehearsal that avoids real Claude/OpenCode calls:

```bash
python3 scripts/panda_eval.py canary \
  --run-dir <panda-eval-root>/$(date +%Y%m%d)-nightly \
  --skip-real-panda
```

The canary is failed if:

- unit tests fail with `ResourceWarning` promoted to errors
- Panda artifacts are missing or malformed
- a required Panda core is missing or non-success
- Claude reports quota, budget, rate-limit, auth, billing, or usage exhaustion
- lingering marker processes are detected after completion

## 3. SWE-bench-Style A/B Pilot

For each pinned task, run two clean variants:

- `codex_alone`
- `panda_explore`

For `panda_explore`, run Panda before editing:

```bash
python3 scripts/consult_ai_team.py \
  --tool all \
  --mode explore \
  --profile fast \
  --timeout 420 \
  --output-dir <panda-eval-root>/YYYYMMDD-nightly/tasks/TASK_ID/panda_explore/panda \
  --prompt "SWE-bench task TASK_ID. Inspect the clean checkout, identify likely fix areas, risks, and verification plan. Do not edit files."
```

Read `evidence.json` and `{tool}.summary.json` first. Inspect raw logs only when needed.

## 4. Record Results

After each variant:

```bash
python3 scripts/panda_eval.py record \
  --run-dir <panda-eval-root>/YYYYMMDD-nightly \
  --task-id TASK_ID \
  --variant codex_alone \
  --tests-passed true \
  --regression false \
  --wall-seconds 123.4 \
  --patch-path /path/to/patch.diff \
  --test-output-path /path/to/test-output.txt
```

For Panda:

```bash
python3 scripts/panda_eval.py record \
  --run-dir <panda-eval-root>/YYYYMMDD-nightly \
  --task-id TASK_ID \
  --variant panda_explore \
  --tests-passed true \
  --regression false \
  --wall-seconds 123.4 \
  --panda-output-dir <panda-eval-root>/YYYYMMDD-nightly/tasks/TASK_ID/panda_explore/panda \
  --evidence-used
```

Any Claude quota, budget, rate-limit, auth, billing, or usage exhaustion in the Panda output is recorded as:

- `claude_budget_failure: true`
- `panda_run_failed: true`

## 5. Summarize

```bash
python3 scripts/panda_eval.py summarize --run-dir <panda-eval-root>/YYYYMMDD-nightly
```

This writes `summary.json` with:

- pass rate
- Panda runner failure rate
- Claude budget failure rate
- evidence use rate
- mean time to green

Treat the first 5-task run as a signal-finding pilot, not a statistically meaningful benchmark.

For lessons from completed runs and recommended harder benchmark directions, see `references/evaluation-findings.md`.

For the next harder local scout/replay workflow, see `references/evaluation-hard-local.md`.
