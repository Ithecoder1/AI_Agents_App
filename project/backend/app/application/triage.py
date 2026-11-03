"""Pre-flight triage and prompt heuristics for adaptive routing and context pruning."""

from __future__ import annotations

import re
from collections.abc import Sequence

__all__ = [
    "LIGHTWEIGHT_SYSTEM_INSTRUCTIONS",
    "is_lightweight_query",
    "should_load_workspace_context",
]

LIGHTWEIGHT_SYSTEM_INSTRUCTIONS = (
    "You are a helpful software assistant for this workspace. "
    "Respond concisely and directly (1-2 sentences). "
    "Do NOT generate architectural blueprints, plans, or diffs unless explicitly asked."
)

_COMMON_GREETINGS = frozenset(
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
    r"architecture|architectural|design|structure|components|diagram|layer|layers|hexagonal|subsystem|"
    r"code|refactor|refactoring|bug|fix|feature|implement|implementation|function|func|class|method|"
    r"endpoint|endpoints|api|apis|diff|patch|test|tests|pytest|unittest|debug|debugging|"
    r"module|modules|script|scripts|project|repo|repository|workspace|directory|folder|tree|"
    r"overview|what is this|explain project|guide|readme|spec|specs|manifest|docs|documentation|"
    r"how to run|how to build|how to test"
    r")\b",
    re.IGNORECASE,
)


def is_lightweight_query(text: str) -> bool:
    """Determine whether the prompt is a basic greeting, pleasantry, or short generic query.

    Returns True for greetings, pleasantries, simple math expressions, or queries
    under 15 characters without code/file references.
    """
    cleaned = text.strip()
    if not cleaned:
        return True
    lower = cleaned.lower()

    # 1. Match common greetings / pleasantries directly or via regex
    if lower in _COMMON_GREETINGS or bool(_GREETING_REGEX.match(cleaned)):
        return True

    # 2. Match simple math expressions (e.g. '1+1', '1+1?', '2 * 3')
    if _MATH_PATTERN.match(cleaned) and any(c.isdigit() for c in cleaned):
        return True

    # 3. Short generic query (< 15 characters) with no code/file references
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
