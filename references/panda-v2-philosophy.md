# Panda V2 Philosophy

Panda V2 exists to make multi-agent consultation useful without turning it into
multi-agent implementation.

The project purpose is not to replace Codex with a committee. Panda's job is to
give Codex independent pressure from Claude, GLM, and Qwen before or during hard
software engineering work. Codex remains the only editor, integrator, and final
decision-maker. Panda provides evidence, alternatives, contract maps, risks, and
verification plans. Codex decides what to trust, implements the change, and runs
the final verification.

Panda also comes from a simple engineering belief: research and planning stages
have outsized influence on the lines of code eventually written. A wrong early
interpretation can create a clean-looking patch that still misses the real
contract. A better question, a better local contract map, or one well-timed
falsifier can save many lines of repair work later.

The project is therefore also about actively spotting LLM weaknesses as
engineers: overconfidence, premature convergence, shallow self-review, vague
contract reasoning, weak hidden-test inference, and poor long-horizon state
tracking. Panda's purpose is to counterweight those weaknesses with a sustained
strategy: independent model pressure, structured evidence, explicit uncertainty,
bounded verification, and Codex-owned integration.

The practical rule is:

```text
If a task is worth Panda, it is usually worth V2.
```

V2 is the formal Panda protocol for substantial engineering work. V1 remains in
git history for archaeology, but it is no longer a live protocol path.

## Why V2 Exists

Panda V1 proved that independent model pressure can be valuable, but it also
showed a recurring weakness: useful advice could remain trapped in prose.

In early hard-local evaluations, Panda often pointed Codex in the right general
direction, but the decisive hidden-test contracts were exact:

- field names such as `Player.UserName` and the later `Player.UserId`/identity
  contract in Navidrome
- method and type names around Flipt ECR credential handling
- schema and migration behavior
- local test seams and evaluator-like assertions

The original summaries preserved recommendation, alternative, risks,
verification, and confidence. That was enough for many tasks, but it was too
soft for contract-heavy bugs. V2 keeps the same prose advice and the same base
evidence artifacts, then adds a structured sidecar so exact claims can be
inspected, counted, validated, falsified, and compared across prompt versions.

V2 is therefore not a new agent architecture. It is a better communication
contract around the existing architecture.

## Scientific Rationale

The research lesson behind V2 is conservative: do not add agents casually.

Gao et al. argue that single-agent and multi-agent systems have complementary
trade-offs. Multi-agent systems can help with decomposed work, role isolation,
and error correction, but they add runtime cost, token cost, and orchestration
complexity. Panda accepts that trade only when the task is complex enough to
justify independent pressure.

CooperBench warns that coding agents are not reliable teammates when they try to
coordinate over a shared code state. Collaboration can degrade performance
because agents lose synchronization, communicate inaccurate state, and create
coordination overhead. Panda V2 responds by not letting collaborators edit,
vote, or run a debate loop. They inspect and report; Codex integrates.

OneFlow-style work suggests that many homogeneous multi-agent workflows can be
matched by a strong single-agent baseline when the same underlying model and
cache/context advantages are controlled. Panda V2 treats this as a warning
against theatrical multi-agent design. The value must come from independent
perspectives and auditable artifacts, not from pretending that more agents are
automatically smarter.

Tran and Kiela's equal-thinking-budget analysis is the clearest cost warning:
when reasoning tokens are normalized, single-agent systems can be more
information-efficient. Panda V2 therefore records parse quality and keeps V2
metrics out of normal model context. Instrumentation is for the lab unless the
current task specifically needs it.

Kim et al.'s scaling study adds the task-structure rule. Across many agent
system configurations, the value of multi-agent coordination depends on whether
the task decomposition actually matches the architecture. The paper reports
large gains on some decomposable tasks and large losses on sequential planning
tasks. Panda V2 follows the conservative interpretation: use independent
advisors for contract pressure, but keep one orchestrator and one editor.

MAST, "Why Do Multi-Agent LLM Systems Fail?", provides the failure-taxonomy
framing. It identifies failures around specification/system design,
inter-agent misalignment, and task verification/termination. Panda V2's strict
schemas, sidecar validation, explicit `not_found`/`unverifiable` statuses, and
one-pass falsifier are meant to make those failure modes visible rather than
letting them hide in prose.

Silo-Bench contributes the coordination/integration warning. Its
"Communication-Reasoning Gap" is close to the failure Panda tries to avoid:
agents can exchange or collect enough information, but still fail to synthesize
distributed state correctly. Panda V2 therefore does not ask advisors to merge
state with each other. The structured artifacts preserve their evidence, and
Codex performs integration.

References:

- Gao et al. (2025), "Single-agent or Multi-agent Systems? Why Not Both?",
  https://arxiv.org/abs/2505.18286
- CooperBench (2026), "Why Coding Agents Cannot be Your Teammates Yet",
  https://arxiv.org/abs/2601.13295
- "Rethinking the Value of Multi-Agent Workflow: A Strong Single Agent
  Baseline" (2026), https://arxiv.org/abs/2601.12307
- Tran and Kiela (2026), "Single-Agent LLMs Outperform Multi-Agent Systems on
  Multi-Hop Reasoning Under Equal Thinking Token Budgets",
  https://arxiv.org/abs/2604.02460
- Kim et al. (2025/2026), "Towards a Science of Scaling Agent Systems",
  https://arxiv.org/abs/2512.08296
- Cemri et al. (2025), "Why Do Multi-Agent LLM Systems Fail?",
  https://arxiv.org/abs/2503.13657
- Zhang et al. (2026), "Silo-Bench: A Scalable Environment for Evaluating
  Distributed Coordination in Multi-Agent LLM Systems",
  https://arxiv.org/abs/2603.01045

## Architecture

The V2 architecture is intentionally additive:

```text
Codex
  owns task framing, implementation, verification, and final judgment

Panda collaborators
  Claude, GLM, Qwen inspect independently and return prose advice

Base evidence artifacts
  evidence.json
  {tool}.summary.json
  manifest.json

V2 sidecars
  panda_contracts.v2.json
  panda_falsifier.v2.json
```

V2 does not replace the compact evidence layer. `panda_contracts.v2.json` is a
sidecar, not a replacement for `evidence.json`.

The contract sidecar records per-tool reports:

- `parse_status`
- warnings
- contract claims
- files inspected
- parse-quality metrics

Claim statuses are deliberately small:

- `confirmed`: directly supported by local evidence
- `inferred`: follows from evidence but is not directly stated
- `candidate`: plausible alternative or competing contract
- `not_found`: explicitly searched for and absent
- `unverifiable`: cannot be checked with available evidence

Malformed or missing JSON never creates claims. The parser records the failure
and leaves the claim list empty. This rule matters more than recovery rate.

## Codex And Panda

Codex is the orchestrator.

That means Codex:

- decides whether Panda is worth using
- uses Panda early enough that research and planning can shape the eventual
  patch
- gathers enough context to ask a focused question
- chooses the prompt version and protocol
- reads the evidence and local code directly
- accepts or rejects collaborator advice
- edits files
- runs tests and evaluators
- explains the final decision to the user

Panda collaborators are advisors.

They may inspect the repository and run safe local commands in explore mode, but
they do not own the working tree. They do not commit, push, publish, deploy,
delete, rewrite history, or intentionally edit source files. They do not vote.
When they disagree, Codex resolves the disagreement from local evidence.

This is the role that best matches the literature: Panda is not an autonomous
team of coding agents. It is a controlled independent-review system with a
single human-facing integrator.

## Contract Artifacts

The V2 contract artifact turns fuzzy advice into testable claims.

Good V2 claims name exact local surfaces:

- field names
- method names
- unexported type names
- endpoint names
- schema columns
- migrations
- foreign keys
- permission boundaries
- cache behavior
- backward compatibility seams
- nearby test naming conventions

If multiple contracts are plausible, V2 should list candidates. If the contract
is not present, V2 should say `not_found` or `unverifiable`. It should not fill
gaps from memory or confidence.

The optional falsifier path is a one-pass claim audit. It is not a debate loop.
Its job is to ask: "Which concrete claims are contradicted, unverifiable, or
not found in the local evidence?" The falsifier output is advisory only.

## Benchmark Experience

Panda V1 first proved runner viability on five easier SWE-bench Lite style
tasks:

- `astropy__astropy-14995`
- `django__django-11099`
- `matplotlib__matplotlib-23562`
- `pytest-dev__pytest-5227`
- `sympy__sympy-13480`

Both Codex-alone and Panda variants solved the early five-task pilot, so that
run did not prove solve-rate lift. It did prove that the runner, summaries, and
subprocess lifecycle could work on real tasks.

The benchmark choice also came from the research discussion. SWE-bench Lite and
Verified are useful historical references, but increasingly saturated and
contamination-sensitive. SWE-bench Pro and Terminal-Bench 2.0 better match
Panda's purpose because they stress longer-horizon work, shell recovery,
realistic repository state, and evaluation harness discipline. Panda's local
hard-run workflow uses no-`.git` workspaces and strict leakage checks because
benchmark validity is part of the experiment, not an afterthought.

Hard-local work exposed Panda's real problem space. Navidrome and Flipt showed
that first-pass advice could be directionally useful but miss exact evaluator
contracts. In Navidrome, early Panda advice centered on username
canonicalization while official evaluation later exposed a deeper player
identity/API contract. In Flipt ECR, Panda pushed toward credential-store and
ECR authentication contracts, but later iterations were needed to satisfy exact
private/public ECR behavior.

The contract-first reruns produced the first strong signal that this was the
right direction. With better prompts, no-`.git` workspaces, and bounded
evidence, Codex later rescued prior struggle tasks:

- Navidrome accepted after Codex used contract-first evidence plus evaluator
  feedback.
- Flipt ECR accepted after one compile-contract iteration.

V2 then made the contract layer explicit. Post-implementation smoke results:

- Five easy V2 behavior cases all wrote `panda_contracts.v2.json`.
- The previous malformed-regex JSON issue did not recur.
- Parse-quality metrics separated clean parses, fallback parses, missing blocks,
  malformed JSON, and timeouts.
- A later hard-two V2 behavior run produced:
  - Flipt ECR: 3/3 parsed, 67 claims, no malformed output.
  - Navidrome: 2/3 parsed, 36 claims, GLM timed out at 600 seconds.

The Navidrome GLM timeout is also a useful lesson. The raw log showed GLM
continued broad exploration until the runner terminated it. That is not a JSON
or schema failure. It is a run-control problem: broad repositories need bounded
exploration instructions so collaborators reserve time to synthesize.

Benchmark-methodology references:

- "SWE-Bench Pro: Can AI Agents Solve Long-Horizon Software Engineering Tasks?",
  https://arxiv.org/abs/2509.16941
- "Terminal-Bench: Benchmarking Agents on Hard, Realistic Tasks in Command Line
  Interfaces", https://openreview.net/forum?id=a7Qa4CcHak
- OpenAI, "Why SWE-bench Verified no longer measures frontier coding
  capabilities",
  https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/

## Token And Runtime Cost

Measured from the implementation:

- ordinary `--protocol v2` adds about 340 input-token-equivalents per agent
- `prepare-first-pass --prompt-version 2` plus V2 adds about 500
  input-token-equivalents per agent
- with three collaborators, the extra input cost is roughly 1k to 1.5k
  token-equivalents per Panda call

The sidecar output is larger:

- easy V2 runs produced roughly 2.5k to 3.6k token-equivalents in the full
  sidecar across all tools
- the Flipt hard case produced roughly 6.3k token-equivalents because it
  recorded 67 claims

These are approximate `chars / 4` measurements, not authoritative billing
numbers.

The design choice is that parse-quality metrics and full sidecars are stored as
artifacts, not fed back into model context by default. This keeps the operating
prompt focused on the software task. Metrics are for retrospective lab analysis
unless a specific follow-up, such as `prepare-falsifier`, needs a compact claim
summary.

Because Panda is meant for complex tasks, this cost is acceptable. For tiny
questions, Codex should usually skip Panda entirely. Once a task is complex
enough to justify multiple external perspectives, the V2 structure usually pays
for itself by making the consultation auditable.

## Operating Principles

1. Use V2 by default when Panda is invoked for engineering work.
2. Keep old V1 behavior in git history, not as a live default.
3. Do not turn Panda into a multi-agent editing team.
4. Prefer independent evidence over agent consensus.
5. Preserve exact local contracts in sidecars.
6. Record malformed, missing, fallback, and timeout states separately.
7. Never fabricate claims from malformed or missing JSON.
8. Keep parse-quality metrics out of model context unless debugging Panda.
9. Use falsifier reviews as one-pass audits, not debates.
10. Treat token and runtime cost as part of the result, not bookkeeping noise.

## What V2 Means For The Project

Panda's role is not to maximize the number of agents. Its role is to improve
Codex's judgment on tasks where one model's first interpretation may be too
narrow, overconfident, or weakly verified.

The formal Panda V2 protocol says:

```text
Independent advisors create pressure.
Structured artifacts preserve the pressure.
Codex integrates.
Tests decide.
Metrics teach the next prompt.
```

That is the project philosophy.
