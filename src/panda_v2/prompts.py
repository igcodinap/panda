"""Opt-in Panda V2 prompt addenda."""

from __future__ import annotations


CONTRACTS_JSON_TEMPLATE = """{
  "claims": [
    {
      "claim": "Exact API, field, method, type, schema, test seam, or compatibility contract.",
      "status": "confirmed | inferred | candidate | not_found | unverifiable",
      "evidence_refs": ["path/to/file.ext:line-or-summary"],
      "confidence": "low | medium | high"
    }
  ],
  "files_inspected": ["path/to/file.ext"]
}"""

FALSIFIER_JSON_TEMPLATE = """{
  "claims_audited": 0,
  "contradictions": [],
  "unverifiable": [],
  "not_found": []
}"""


def protocol_v2_return_addendum(role: str) -> str:
    if role == "contract-falsifier":
        return f"""
Panda V2 falsifier artifact:
- After the normal response, include exactly one fenced `panda_falsifier_v2` JSON block.
- The opening fence line must be exactly three backticks followed by `panda_falsifier_v2`; do not use `json` as the fence label.
- Put only the JSON object inside the fence; do not wrap it in a `panda_falsifier_v2` key.
- Double-escape regex or path backslashes as `\\\\` inside JSON strings.
- Before finalizing, mentally validate that the fenced block is strict JSON: double quotes only, no comments, no trailing commas, and escaped backslashes.
- Audit only concrete contract claims; do not propose a new implementation plan.
- Do not ask advisors to rebut your findings and do not create a debate loop.
- Use contradictions only when evidence directly conflicts with a claim.
- Use unverifiable or not_found when available evidence cannot confirm the claim.

Use this JSON shape inside the final fence:
{FALSIFIER_JSON_TEMPLATE}
"""
    return f"""
Panda V2 contract artifact:
- After the normal response, include exactly one fenced `panda_contracts_v2` JSON block.
- The opening fence line must be exactly three backticks followed by `panda_contracts_v2`; do not use `json` as the fence label.
- Put only the JSON object inside the fence; do not wrap it in a `panda_contracts_v2` key.
- Double-escape regex or path backslashes as `\\\\` inside JSON strings.
- Before finalizing, mentally validate that the fenced block is strict JSON: double quotes only, no comments, no trailing commas, and escaped backslashes.
- Capture concrete API, field, method, type, endpoint, schema, migration, test seam, foreign-key, and backward-compatibility contracts.
- Preserve multiple plausible contracts as separate `candidate` claims.
- If a requested contract is absent, use `not_found` or `unverifiable`; never invent the exact name.
- Use status values only from: confirmed, inferred, candidate, not_found, unverifiable.

Use this JSON shape inside the final fence:
{CONTRACTS_JSON_TEMPLATE}
"""


def contract_first_v2_addendum() -> str:
    return """Additional Panda V2 contract focus:
- Explicitly infer exact field names, method names, unexported type names, endpoint names, schema columns, migrations, foreign keys, permission boundaries, and backward-compatibility seams.
- Look for nearby tests and naming conventions that imply the public or hidden contract shape.
- If there are multiple plausible contracts, list each as a separate candidate and state what local evidence would distinguish them.
- If a contract is not present in the local workspace, mark it not_found or unverifiable instead of filling the gap from memory.
- Include the files inspected and the local evidence behind each claim.
"""


def falsifier_user_prompt() -> str:
    return """Please perform a one-pass Panda contract falsifier review.

Audit the concrete claims in the V2 contract artifact against the provided local evidence and optional failure output. Focus on exact API, field, method, type, schema, test seam, synchronization, or backward-compatibility assumptions that could break the candidate implementation.

Do not edit files. Do not start a debate. Do not ask the original advisors to respond. Codex remains the only integrator.
"""
