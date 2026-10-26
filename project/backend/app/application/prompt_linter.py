"""Deterministic prompt linter (the code behind ``vibe-prompt-validator-skill``).

Heuristic, fast and offline: it flags *structural* weaknesses (no output contract, no
guardrails, undefined variables, token bloat). It does not judge semantic quality -- the
skill pairs this report with a human/LLM review. Rule ids are stable; keep them that way.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum

from app.domain.models import Prompt


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


_PENALTY = {Severity.ERROR: 30, Severity.WARNING: 10, Severity.INFO: 3}


@dataclass(frozen=True, slots=True)
class LintIssue:
    rule_id: str
    severity: Severity
    message: str
    suggestion: str


@dataclass(frozen=True, slots=True)
class LintReport:
    score: int
    token_estimate: int
    issues: tuple[LintIssue, ...] = field(default_factory=tuple)

    @property
    def passed(self) -> bool:
        return not any(i.severity is Severity.ERROR for i in self.issues)

    def to_dict(self) -> dict[str, object]:
        return {
            "score": self.score,
            "passed": self.passed,
            "token_estimate": self.token_estimate,
            "issues": [
                {
                    "rule_id": i.rule_id,
                    "severity": i.severity.value,
                    "message": i.message,
                    "suggestion": i.suggestion,
                }
                for i in self.issues
            ],
        }


def estimate_tokens(text: str) -> int:
    """Rough estimate (~4 chars/token for English). Good enough for budgeting warnings."""
    return (len(text) + 3) // 4


def _has(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


_SUCCESS = (
    r"\bsuccess\b",
    r"\bacceptance\b",
    r"\bdone when\b",
    r"\bmust\b",
    r"\bshould\b",
    r"\bensure\b",
    r"\bcriteria\b",
)
_OUTPUT = (
    r"\bjson\b",
    r"\bschema\b",
    r"\bformat\b",
    r"\bmarkdown\b",
    r"\brespond with\b",
    r"\breturn\b",
    r"\boutput\b",
    r"\bunified diff\b",
    r"```",
)
_GUARD = (
    r"\bdo not\b",
    r"\bdon't\b",
    r"\bnever\b",
    r"\bmust not\b",
    r"\bonly\b",
    r"\bforbidden\b",
    r"\brefuse\b",
    r"\bout of scope\b",
    r"\bwithout\b",
)
_COT = (r"step[- ]by[- ]step", r"\bthink\b", r"\bplan\b", r"\breason\b", r"\bfirst,")
_UNTRUSTED = (
    r"untrusted",
    r"treat .* as data",
    r"do not follow instructions",
    r"ignore instructions",
)
_PASTED = (r"```", r"<document", r"<file", r"<context", r"BEGIN [A-Z ]+")


def lint_prompt(
    prompt: Prompt, *, max_tokens: int | None = None, token_warn_threshold: int = 8000
) -> LintReport:
    system, user = prompt.system_instructions, prompt.user_prompt
    combined = f"{system}\n{user}"
    issues: list[LintIssue] = []

    def add(rule: str, sev: Severity, msg: str, fix: str) -> None:
        issues.append(LintIssue(rule, sev, msg, fix))

    if not user.strip():
        add("VP001", Severity.ERROR, "User prompt is empty.", "Describe the task to perform.")

    if not system.strip():
        add(
            "VP002",
            Severity.WARNING,
            "No system instructions (role/goal not set).",
            "State the role, the goal and the constraints in system instructions.",
        )

    if not _has(combined, _SUCCESS):
        add(
            "VP003",
            Severity.WARNING,
            "No explicit success/acceptance criteria.",
            "Add measurable 'done when' criteria so the agent knows when to stop.",
        )

    if not _has(combined, _OUTPUT):
        add(
            "VP004",
            Severity.WARNING,
            "No output format or schema contract.",
            "Specify the exact output shape (e.g. JSON schema, unified diff, markdown sections).",
        )

    if not _has(combined, _GUARD):
        add(
            "VP005",
            Severity.WARNING,
            "No guardrails (scope, forbidden actions, refusals).",
            "List what the agent must NOT do and how to behave when the task is out of scope.",
        )

    if not _has(combined, _COT):
        add(
            "VP006",
            Severity.INFO,
            "No reasoning scaffold (plan / step-by-step).",
            "For non-reasoning models, ask for a short plan before the answer. "
            "Skip this for models with native reasoning.",
        )

    used, declared = prompt.variables_used(), set(prompt.parameters)
    if undefined := sorted(used - declared):
        add(
            "VP007",
            Severity.ERROR,
            f"Undefined variable(s): {', '.join(undefined)}.",
            "Define a value for each {{variable}} or remove the placeholder.",
        )
    if unused := sorted(declared - used):
        add(
            "VP008",
            Severity.INFO,
            f"Declared but unused parameter(s): {', '.join(unused)}.",
            "Remove unused parameters to keep the prompt tidy.",
        )

    tokens = estimate_tokens(combined)
    if max_tokens is not None and tokens > max_tokens:
        add(
            "VP009",
            Severity.ERROR,
            f"Prompt (~{tokens} tokens) exceeds max_tokens ({max_tokens}).",
            "Shorten the prompt or raise max_tokens.",
        )
    elif tokens > token_warn_threshold:
        add(
            "VP009",
            Severity.WARNING,
            f"Prompt is large (~{tokens} tokens).",
            "Trim boilerplate, dedupe context, and keep the stable prefix first for caching.",
        )

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", combined) if len(p.strip()) > 40]
    if any(n > 1 for n in Counter(paragraphs).values()):
        add(
            "VP010",
            Severity.INFO,
            "Repeated paragraphs detected.",
            "Remove duplicated blocks to save tokens.",
        )

    if _has(user, _PASTED) and not _has(combined, _UNTRUSTED):
        add(
            "VP011",
            Severity.INFO,
            "Pasted content without prompt-injection hygiene.",
            "Delimit pasted content and state that it is untrusted data, not instructions.",
        )

    score = max(0, 100 - sum(_PENALTY[i.severity] for i in issues))
    return LintReport(score=score, token_estimate=tokens, issues=tuple(issues))
