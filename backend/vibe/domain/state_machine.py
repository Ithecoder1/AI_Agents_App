"""Task lifecycle state machine (pure functions, table-driven).

    QUEUED   --start-->   RUNNING        QUEUED   --cancel--> CANCELLED
    RUNNING  --succeed--> SUCCEEDED      RUNNING  --fail-->   FAILED
    RUNNING  --retry-->   RETRYING       RUNNING  --cancel--> CANCELLED
    RETRYING --start-->   RUNNING        RETRYING --fail-->   FAILED
    RETRYING --cancel-->  CANCELLED

SUCCEEDED, FAILED and CANCELLED are terminal.
"""

from __future__ import annotations

from vibe.domain.enums import TaskStatus, TaskTrigger
from vibe.domain.errors import IllegalTransitionError

_S = TaskStatus
_T = TaskTrigger

TRANSITIONS: dict[tuple[TaskStatus, TaskTrigger], TaskStatus] = {
    (_S.QUEUED, _T.START): _S.RUNNING,
    (_S.QUEUED, _T.CANCEL): _S.CANCELLED,
    (_S.RUNNING, _T.SUCCEED): _S.SUCCEEDED,
    (_S.RUNNING, _T.FAIL): _S.FAILED,
    (_S.RUNNING, _T.RETRY): _S.RETRYING,
    (_S.RUNNING, _T.CANCEL): _S.CANCELLED,
    (_S.RETRYING, _T.START): _S.RUNNING,
    (_S.RETRYING, _T.FAIL): _S.FAILED,
    (_S.RETRYING, _T.CANCEL): _S.CANCELLED,
}

TERMINAL_STATUSES: frozenset[TaskStatus] = frozenset({_S.SUCCEEDED, _S.FAILED, _S.CANCELLED})


def transition(status: TaskStatus, trigger: TaskTrigger) -> TaskStatus:
    """Return the next status or raise :class:`IllegalTransitionError`."""
    try:
        return TRANSITIONS[(status, trigger)]
    except KeyError:
        raise IllegalTransitionError(
            f"Cannot apply '{trigger.value}' to a task in state '{status.value}'"
        ) from None


def is_terminal(status: TaskStatus) -> bool:
    return status in TERMINAL_STATUSES


def allowed_triggers(status: TaskStatus) -> frozenset[TaskTrigger]:
    return frozenset(t for (s, t) in TRANSITIONS if s == status)
