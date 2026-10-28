"""Domain enumerations.

NOTE: these are ``(str, Enum)`` rather than ``StrEnum`` because the project targets
Python 3.10. Always use ``.value`` when you need the raw string (``str()`` differs
between 3.10 and 3.11).
"""

from __future__ import annotations

from enum import Enum


class TaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    RETRYING = "retrying"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskTrigger(str, Enum):
    """Things that can happen to a task; fed into the state machine."""

    START = "start"
    RETRY = "retry"
    SUCCEED = "succeed"
    FAIL = "fail"
    CANCEL = "cancel"


class EventType(str, Enum):
    STATUS = "status"
    TOKEN = "token"
    THOUGHT = "thought"
    PLAN = "plan"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    LOG = "log"
    ARTIFACT = "artifact"
    USAGE = "usage"
    ERROR = "error"
    DONE = "done"


class ArtifactKind(str, Enum):
    DIFF = "diff"
    FILE = "file"
    LOG = "log"
    JSON = "json"
