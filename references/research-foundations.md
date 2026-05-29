# Research Foundations

Panda is an experience-based tool, but its design is intentionally shaped by
recent research on agent systems, coordination failures, benchmark validity,
and cost-aware reasoning. This page records the papers and methodology notes
that currently inform the project.

Panda's interpretation is conservative: use multiple models when their
independent pressure can improve Codex's judgment, but keep one editor, one
integrator, explicit artifacts, and test-driven verification.

## Agent-System Design

### Gao et al., "Single-agent or Multi-agent Systems? Why Not Both?"

Link: https://arxiv.org/abs/2505.18286

Core claim: single-agent and multi-agent systems have complementary tradeoffs.
Multi-agent systems can help when work decomposes cleanly, but they add
orchestration, runtime, and token cost.

Panda implication: consultation should be conditional. Panda is for complex
engineering tasks where independent pressure is likely to pay for itself, not a
default wrapper around every Codex action.

Caveat: the paper supports selective use of multi-agent structure; it does not
prove Panda's exact three-advisor configuration improves coding outcomes.

### CooperBench, "Why Coding Agents Cannot be Your Teammates Yet"

Link: https://arxiv.org/abs/2601.13295

Core claim: coding agents can perform worse when asked to coordinate as peers
over shared tasks. The benchmark highlights communication and commitment
failures in collaborative coding settings.

Panda implication: Panda collaborators do not edit the shared working tree,
vote, or coordinate as teammates. They inspect independently; Codex integrates.

Caveat: CooperBench studies collaborative coding tasks, not Panda's fan-out
advisory pattern. Panda borrows the warning, not the benchmark result.

### "Rethinking the Value of Multi-Agent Workflow: A Strong Single Agent Baseline"

Link: https://arxiv.org/abs/2601.12307

Core claim: homogeneous multi-agent workflows can often be matched by a strong
single-agent baseline, with efficiency advantages from cache and context reuse.

Panda implication: Panda should not treat "more agents" as inherently better.
Its value must come from heterogeneous perspectives, local evidence, and
auditable artifacts.

Caveat: a strong single-agent baseline remains the benchmark Panda must beat or
complement; current Panda results are not yet a proof of solve-rate lift.

### Tran and Kiela, "Single-Agent LLMs Outperform Multi-Agent Systems on Multi-Hop Reasoning Under Equal Thinking Token Budgets"

Link: https://arxiv.org/abs/2604.02460

Core claim: when reasoning-token budgets are equalized, single-agent systems can
be more information-efficient than multi-agent systems on multi-hop reasoning.

Panda implication: Panda treats model cost and runtime as part of the outcome.
It records artifacts for retrospective analysis and avoids feeding verbose
sidecars back into normal model context.

Caveat: the result is about multi-hop reasoning, not software engineering
consultation with local tools. It still matters as a cost-control warning.

### Kim et al., "Towards a Science of Scaling Agent Systems"

Link: https://arxiv.org/abs/2512.08296

Core claim: agent-system scaling depends on task structure. Multi-agent
coordination can help on some decomposable tasks and harm on tasks whose shape
does not match the architecture.

Panda implication: Panda's architecture is deliberately narrow: independent
contract pressure, then single-editor integration.

Caveat: Panda still needs task-selection discipline; using it on poorly matched
tasks can waste time or amplify noise.

## Failure Modes And Coordination

### Cemri et al., "Why Do Multi-Agent LLM Systems Fail?"

Link: https://arxiv.org/abs/2503.13657

Core claim: MAST organizes multi-agent failures into system-design,
inter-agent misalignment, and task verification/termination categories.

Panda implication: V2 sidecars, parse-status fields, `not_found` and
`unverifiable` statuses, and one-pass falsifier prompts make failure modes
visible instead of hiding them in fluent prose.

Caveat: a taxonomy does not eliminate the failures. Panda still depends on
Codex reading evidence carefully and tests catching bad integrations.

### Zhang et al., "Silo-Bench: A Scalable Environment for Evaluating Distributed Coordination in Multi-Agent LLM Systems"

Link: https://arxiv.org/abs/2603.01045

Core claim: agents may exchange enough information yet fail to synthesize
distributed state correctly. The paper describes this as a
communication-reasoning gap.

Panda implication: Panda avoids asking advisors to merge state with each other.
Artifacts preserve evidence; Codex performs synthesis from local code and test
results.

Caveat: Panda's synthesis step is still a human-facing model step, so evidence
quality and verification discipline remain decisive.

## Benchmark Methodology

### "SWE-Bench Pro: Can AI Agents Solve Long-Horizon Software Engineering Tasks?"

Link: https://arxiv.org/abs/2509.16941

Core claim: SWE-bench Pro targets longer-horizon, realistic software
engineering tasks across actively maintained repositories, with greater
difficulty than saturated earlier coding benchmarks.

Panda implication: Panda's hard-local evaluation focuses on tasks with enough
complexity for consultation to matter: multi-step debugging, contract
inference, setup recovery, and benchmark-safe workspaces.

Caveat: benchmark work must be contamination-aware. Panda's docs and scripts
separate clean signals from contaminated or low-confidence observations.

### "Terminal-Bench: Benchmarking Agents on Hard, Realistic Tasks in Command Line Interfaces"

Link: https://openreview.net/forum?id=a7Qa4CcHak

Core claim: terminal-based tasks stress realistic command-line autonomy,
environment recovery, and multi-step workflows.

Panda implication: Panda should be evaluated not only on final patches, but
also on setup recovery, evidence usefulness, shell-heavy diagnosis, and
verification planning.

Caveat: Terminal-Bench is a methodology guidepost for future evaluation; this
repository's current portable results are primarily SWE-bench-style.

### OpenAI, "Why SWE-bench Verified no longer measures frontier coding capabilities"

Link: https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/

Core claim: OpenAI no longer recommends SWE-bench Verified as a frontier coding
benchmark because of saturation and contamination concerns, and recommends
SWE-bench Pro instead.

Panda implication: Panda should report benchmark claims conservatively, avoid
over-reading saturated benchmarks, and publish contamination notes with
evaluation summaries.

Caveat: this is a methodology note rather than an academic paper, but it is
important public context for coding-agent evaluation.

## Current Panda Position

The research does not say that Panda is automatically better than Codex alone.
It says the project should earn that claim through careful task selection,
bounded cost, independent evidence, explicit uncertainty, and clean evaluation.

That is why Panda V2 is built around a small operating loop:

```text
Independent advisors create pressure.
Structured artifacts preserve the pressure.
Codex integrates.
Tests decide.
Metrics teach the next prompt.
```
