from __future__ import annotations

import itertools

import pytest

from vibe.domain.enums import TaskStatus, TaskTrigger
from vibe.domain.errors import IllegalTransitionError
from vibe.domain.state_machine import (
    TERMINAL_STATUSES,
    TRANSITIONS,
    allowed_triggers,
    is_terminal,
    transition,
)

ALL_PAIRS = list(itertools.product(TaskStatus, TaskTrigger))


@pytest.mark.parametrize(("status", "trigger"), ALL_PAIRS)
def test_every_status_trigger_pair_is_either_allowed_or_rejected(
    status: TaskStatus, trigger: TaskTrigger
) -> None:
    """Exhaustive: all 6x5 combinations behave exactly as the table says."""
    if (status, trigger) in TRANSITIONS:
        assert transition(status, trigger) is TRANSITIONS[(status, trigger)]
    else:
        with pytest.raises(IllegalTransitionError):
            transition(status, trigger)


@pytest.mark.parametrize("status", sorted(TERMINAL_STATUSES, key=str))
def test_terminal_states_have_no_exits(status: TaskStatus) -> None:
    assert is_terminal(status)
    assert allowed_triggers(status) == frozenset()


def test_non_terminal_states_can_always_progress_or_cancel() -> None:
    for status in TaskStatus:
        if not is_terminal(status):
            assert TaskTrigger.CANCEL in allowed_triggers(status)


def test_happy_path_and_retry_path() -> None:
    s = transition(TaskStatus.QUEUED, TaskTrigger.START)
    assert s is TaskStatus.RUNNING
    s = transition(s, TaskTrigger.RETRY)
    assert s is TaskStatus.RETRYING
    s = transition(s, TaskTrigger.START)
    assert s is TaskStatus.RUNNING
    assert transition(s, TaskTrigger.SUCCEED) is TaskStatus.SUCCEEDED


def test_every_non_terminal_state_is_reachable_from_queued() -> None:
    reachable = {TaskStatus.QUEUED}
    frontier = [TaskStatus.QUEUED]
    while frontier:
        current = frontier.pop()
        for (src, _), dst in TRANSITIONS.items():
            if src is current and dst not in reachable:
                reachable.add(dst)
                frontier.append(dst)
    assert reachable == set(TaskStatus)
