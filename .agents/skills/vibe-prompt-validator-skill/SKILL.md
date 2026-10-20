---
name: vibe-prompt-validator-skill
description: >-
  Inspects, validates, and refines vibe-coding prompts to ensure adherence to best
  practices including Chain-of-Thought scaffolding, guardrails, schema definitions,
  variable hygiene, and token optimization. Use when authoring prompts, reviewing
  system instructions, or troubleshooting low-quality agent executions.
---

# Vibe Prompt Validator Skill

This skill enforces quality, security, and token economy across all prompts executed in Vibe Forge. It combines fast deterministic checks with semantic review guidelines.

## Quick Inspection Workflow

Run the linter CLI on any prompt text or file:

```powershell
backend\.venv\Scripts\python.exe .agents\skills\vibe-prompt-validator-skill\scripts\lint_prompt.py --prompt "Your draft prompt" --system "Role and constraints"
```

The tool returns a JSON `LintReport` with:
- `score`: integer 0–100.
- `passed`: `true` if there are no `error`-level violations.
- `token_estimate`: approximate token count.
- `issues`: list of flagged rules (`VP001` to `VP011`) with severity and concrete suggestions.

For details on the evaluation dimensions, consult [references/rubric.md](references/rubric.md).

## Rule Matrix

| Rule ID | Severity | Description | Action Required |
|---|---|---|---|
| `VP001` | ERROR | Empty user prompt | Provide a clear description of the task. |
| `VP002` | WARNING | Missing system instructions | Define the role, goals, and core operating boundaries. |
| `VP003` | WARNING | No explicit success criteria | Add measurable "done when" conditions. |
| `VP004` | WARNING | No output format contract | Specify the exact schema, markdown structure, or diff format. |
| `VP005` | WARNING | Missing negative guardrails | List forbidden actions and out-of-scope boundaries. |
| `VP006` | INFO | No reasoning scaffold | Ask for a brief step-by-step plan if the target model is non-reasoning. |
| `VP007` | ERROR | Undefined `{{variable}}` | Provide parameter values or remove unresolved placeholders. |
| `VP008` | INFO | Unused declared parameter | Remove parameters not referenced in the prompt. |
| `VP009` | ERROR/WARN | Token limit exceeded/large | Trim boilerplate, deduplicate context, and position stable prefix first. |
| `VP010` | INFO | Duplicate paragraphs | Remove redundant context blocks to conserve tokens. |
| `VP011` | INFO | Pasted data without hygiene | Delimit untrusted external code/data to prevent prompt injection. |

## Refinement Procedure

1. Run the validator CLI to obtain the baseline score and issues.
2. Address all `ERROR` issues first (`VP001`, `VP007`, `VP009`).
3. Enhance prompt specifications:
   - Append clear schema or format contracts.
   - Insert negative boundaries (e.g. "Do not modify dependencies in pyproject.toml").
4. If large inputs are pasted, encapsulate them in delimited blocks (`<context>...</context>`) and explicitly instruct the agent to treat them as passive data.
