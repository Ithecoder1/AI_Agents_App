"""Tier 1: Deterministic zero-token heuristic filter and legacy triage helpers."""

from __future__ import annotations

import re
import time
from collections.abc import Sequence

from app.application.triage.models import DEFAULT_RESOURCE_ALLOCATION, TriageDecision

LIGHTWEIGHT_SYSTEM_INSTRUCTIONS = ""

EXACT_SLASH_COMMANDS: frozenset[str] = frozenset(
    {
        "/help",
        "/clear",
        "/reset",
        "/status",
        "/version",
        "/exit",
        "/quit",
        "/compact",
    }
)

_COMMON_GREETINGS: frozenset[str] = frozenset(
    {
        "hi",
        "hello",
        "hey",
        "hiya",
        "howdy",
        "sup",
        "yo",
        "greetings",
        "good morning",
        "good afternoon",
        "good evening",
        "how are you",
        "how are you doing",
        "who are you",
        "what are you",
        "what can you do",
        "whats up",
        "what's up",
        "help",
        "ping",
        "pong",
        "test",
        "ok",
        "okay",
        "thanks",
        "thank you",
        "bye",
        "goodbye",
        "shalom",
        "היי",
        "שלום",
        "בוקר טוב",
        "ערב טוב",
        "מה נשמע",
        "תודה",
    }
)

_MATH_PATTERN = re.compile(r"^[\d\s+\-*/%^=?.()]+$")

_MATH_QUERY_PREFIX_REGEX = re.compile(
    r"^(?:solve|calculate|compute|eval)\s*:?\s*(.+)$",
    re.IGNORECASE,
)

_GREETING_REGEX = re.compile(
    r"^(hi|hello|hey|hiya|howdy|sup|yo|greetings|good\s+(morning|afternoon|evening)|shalom)"
    r"(\s+(there|assistant|bot|friend|everyone|all))?[!?,.]*$",
    re.IGNORECASE,
)

_CODE_FILE_INDICATORS = re.compile(
    r"(\b(code|file|files|class|def|function|func|method|diff|patch|bug|fix|error|issue|"
    r"repo|repository|git|branch|commit|pr|test|tests|pytest|npm|node|python|lint|"
    r"build|run|exec|script|refactor|architecture|architect|spec|manifest|dir|directory|"
    r"tree|import|export|install|dependency|package)\b"
    r"|[/\\]|\.[a-zA-Z0-9_-]{1,6}\b)",
    re.IGNORECASE,
)

_WORKSPACE_CONTEXT_KEYWORDS = re.compile(
    r"\b("
    r"architecture|architectural|design|structure|components|diagram|layer|layers|hexagonal|"
    r"subsystem|code|refactor|refactoring|bug|fix|feature|implement|implementation|function|"
    r"func|class|method|endpoint|endpoints|api|apis|diff|patch|test|tests|pytest|unittest|"
    r"debug|debugging|module|modules|script|scripts|project|repo|repository|workspace|"
    r"directory|folder|tree|overview|what is this|explain project|guide|readme|spec|specs|"
    r"manifest|docs|documentation|how to run|how to build|how to test"
    r")\b",
    re.IGNORECASE,
)


def _matches_math(cleaned: str) -> bool:
    if _MATH_PATTERN.match(cleaned) and any(c.isdigit() for c in cleaned):
        return True
    math_prefixed = _MATH_QUERY_PREFIX_REGEX.match(cleaned)
    if math_prefixed:
        expr = math_prefixed.group(1).strip()
        if _MATH_PATTERN.match(expr) and any(c.isdigit() for c in expr):
            return True
    return False


def _matches_greeting_or_pleasantry(cleaned: str) -> bool:
    if _GREETING_REGEX.match(cleaned):
        return True
    normalized = cleaned.lower().rstrip("!?.,;:")
    return normalized in _COMMON_GREETINGS


class Tier1HeuristicFilter:
    """Zero-token, zero-I/O deterministic heuristic filter for Tier 1 fast-path triage."""

    def __init__(self, slash_commands: frozenset[str] = EXACT_SLASH_COMMANDS) -> None:
        self._slash_commands = frozenset(cmd.strip().lower() for cmd in slash_commands)

    def evaluate(self, user_prompt: str) -> TriageDecision | None:
        """Evaluate prompt against deterministic Tier 1 rules; return None if unresolved."""
        t0 = time.perf_counter()
        cleaned = user_prompt.strip()
        alloc = DEFAULT_RESOURCE_ALLOCATION["DIRECT_QA"]

        if not cleaned:
            return TriageDecision(
                intent="DIRECT_QA",
                confidence=1.0,
                requires_workspace_manifest=alloc.requires_workspace_manifest,
                target_model_tier=alloc.target_model_tier,
                token_budget=alloc.token_budget,
                tier=1,
                latency_ms=(time.perf_counter() - t0) * 1000.0,
                matched_route="tier1_empty",
                reason="Empty input matched Tier 1 deterministic fast-path",
            )

        lower = cleaned.lower()
        if lower in self._slash_commands:
            return TriageDecision(
                intent="DIRECT_QA",
                confidence=1.0,
                requires_workspace_manifest=False,
                target_model_tier="fast",
                token_budget=256,
                tier=1,
                latency_ms=(time.perf_counter() - t0) * 1000.0,
                matched_route=f"tier1_command:{lower}",
                reason=f"Exact slash command {lower!r} matched Tier 1 fast-path",
            )

        if _matches_greeting_or_pleasantry(cleaned):
            return TriageDecision(
                intent="DIRECT_QA",
                confidence=1.0,
                requires_workspace_manifest=alloc.requires_workspace_manifest,
                target_model_tier=alloc.target_model_tier,
                token_budget=alloc.token_budget,
                tier=1,
                latency_ms=(time.perf_counter() - t0) * 1000.0,
                matched_route="tier1_greeting",
                reason="Greeting or pleasantry matched Tier 1 fast-path",
            )

        if _matches_math(cleaned):
            return TriageDecision(
                intent="DIRECT_QA",
                confidence=1.0,
                requires_workspace_manifest=alloc.requires_workspace_manifest,
                target_model_tier=alloc.target_model_tier,
                token_budget=alloc.token_budget,
                tier=1,
                latency_ms=(time.perf_counter() - t0) * 1000.0,
                matched_route="tier1_math",
                reason="Standalone arithmetic matched Tier 1 fast-path",
            )

        return None


def is_lightweight_query(text: str) -> bool:
    """Determine whether the prompt is a basic greeting, command, math, or short generic query.

    Returns True for empty inputs, exact slash commands, greetings, pleasantries,
    simple math expressions, or queries under 15 characters without code/file references.
    """
    cleaned = text.strip()
    if not cleaned:
        return True
    lower = cleaned.lower()

    if lower in EXACT_SLASH_COMMANDS:
        return True

    if _matches_greeting_or_pleasantry(cleaned):
        return True

    if _matches_math(cleaned):
        return True

    return bool(len(cleaned) < 15 and not _CODE_FILE_INDICATORS.search(cleaned))


def should_load_workspace_context(query: str | None, context_files: Sequence[str] = ()) -> bool:
    """Check if the user prompt explicitly references architecture, code changes, or structure.

    If query is None, returns True for backward-compatibility with non-conversational context calls.
    """
    if query is None:
        return True

    cleaned = query.strip()
    if not cleaned or is_lightweight_query(cleaned):
        return False

    query_lower = cleaned.lower()
    for f in context_files:
        base_name = f.split("/")[-1].split("\\")[-1].lower()
        if base_name in query_lower:
            return True

    return bool(_WORKSPACE_CONTEXT_KEYWORDS.search(cleaned))


__all__ = [
    "EXACT_SLASH_COMMANDS",
    "LIGHTWEIGHT_SYSTEM_INSTRUCTIONS",
    "Tier1HeuristicFilter",
    "is_lightweight_query",
    "should_load_workspace_context",
]
