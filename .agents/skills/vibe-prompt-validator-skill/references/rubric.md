# Vibe Prompt Rubric & Quality Dimensions

Vibe coding prompts must be clear, robust, and cost-effective when dispatched to automated agent runners.
The validator checks prompts against six core dimensions:

## 1. Role & Intent Framing (VP002)
- Clearly define the agent's persona, domain expertise, and operational boundaries in `system_instructions`.
- State whether the agent operates as an architect, code writer, reviewer, or debugger.

## 2. Explicit Acceptance & Success Criteria (VP003)
- Specify verifiable conditions for completion ("done when...", "must include...", "acceptance criteria").
- Provide measurable constraints so the agent does not produce endless or incomplete answers.

## 3. Strict Output Contracts & Schemas (VP004)
- Define the exact expected format: JSON schema, unified diff format (`--- a/... +++ b/...`), markdown structure, or raw code blocks.
- Clarify whether explanatory text should be included or omitted (e.g. "Output raw code only, no conversational preamble").

## 4. Guardrails & Negative Constraints (VP005)
- Explicitly declare what the agent is **forbidden** from doing:
  - Do not edit files outside the target directory.
  - Do not add unapproved external dependencies.
  - Do not introduce breaking changes to public APIs.
  - Refuse out-of-scope or destructive requests.

## 5. Reasoning Scaffolding / Chain-of-Thought (VP006)
- For non-reasoning foundation models: instruct the agent to outline its plan or step-by-step rationale before executing code edits.
- For reasoning models (e.g. o1/o3, Claude Thinking): omit manual CoT instructions to avoid redundancy and token waste.

## 6. Token Optimization & Variable Hygiene (VP007, VP008, VP009, VP010, VP011)
- Verify that all `{{placeholders}}` have corresponding parameter values (`VP007`).
- Keep stable system prompts at the beginning to maximize LLM prompt-caching hit rates.
- Isolate and delimit pasted external data (code, logs) with clear indicators that it is untrusted data, preventing prompt-injection hijack (`VP011`).
