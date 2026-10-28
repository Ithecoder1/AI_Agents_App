"""Build the LLM-facing conversation history from stored chat messages.

Token hygiene: the database keeps the rich per-message ``metadata_json`` (thoughts, logs, tool
calls, context snapshots) for the UI, but only each turn's clean ``role`` + final ``content`` is
sent back to the model. Replaying heavy artefacts of old turns would make input tokens grow
super-linearly with conversation length.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.domain.chat import STATUS_COMPLETE, ChatMessage
from app.domain.models import ChatTurn


class ConversationContextBuilder:
    def build_history(self, messages: Sequence[ChatMessage]) -> tuple[ChatTurn, ...]:
        """Ordered ``user``/``assistant`` turns suitable for ``Prompt.history``.

        Skipped: ``system``/``tool`` messages (system instructions are rebuilt per turn from the
        workspace; tool traffic is intentionally not replayed) and assistant messages that did
        not complete (in-flight, failed or cancelled partial output).
        """
        turns: list[ChatTurn] = []
        for message in sorted(messages, key=lambda m: m.seq):
            if message.role not in ("user", "assistant") or not message.content.strip():
                continue
            if message.role == "assistant" and message.metadata.status != STATUS_COMPLETE:
                continue
            turns.append(ChatTurn(message.role, message.content))
        return tuple(turns)
