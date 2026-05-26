# Prompt Patterns

Use these short patterns with `scripts/consult_ai_team.py`.

## Brainstorming

```text
Goal:
- ...

Constraints:
- ...

Please propose 2-3 implementation approaches. For each, include when it is best, what can go wrong, and the fastest way to validate it.
```

## Exploration

```text
Goal:
- ...

Repo/workspace:
- ...

You may run shell commands to inspect files, git state, tests, builds, logs, and relevant docs.
Avoid source edits. If any command changes files, report every changed file and why it changed.

Please return findings, evidence, risks, and recommended next steps.
```

## Implementation Review

```text
Goal:
- ...

Current plan:
- ...

Relevant code context:
- ...

Please critique this plan. Focus on hidden coupling, simpler alternatives, migration risks, and tests Codex should run.
```

## Debugging

```text
Symptom:
- ...

Evidence:
- Error output: ...
- Relevant files/functions: ...
- Things already tried: ...

Please suggest likely root causes, how to distinguish them, and the smallest next diagnostic step.
```

## Failure Recovery

```text
Goal:
- Recover from a failed verification after Codex produced a candidate patch.

First-pass advice:
- ...

Candidate patch summary:
- ...

Failure evidence:
- Failing tests: ...
- Error excerpts: ...
- Relevant artifact paths: ...

Please identify what the first pass missed, the smallest correction Codex should make, and the focused verification to rerun.
```

## API Contract Review

```text
Goal:
- Check whether the candidate patch satisfies the repo's expected public API, test, or integration contract.

Observed failure:
- ...

Relevant local files or package hints:
- ...

Please infer likely contract expectations from local evidence, call out uncertainty, and avoid relying on any gold benchmark patch or test_patch content.
```

## Code Review

```text
Change summary:
- ...

Diff or key snippets:
- ...

Please review for behavioral bugs, missing tests, edge cases, and maintainability risks. Prioritize findings over style.
```

## Test Planning

```text
Feature/change:
- ...

Risky behavior:
- ...

Please propose a focused verification plan: unit tests, integration tests, manual checks, and one failure mode that is easy to miss.
```
