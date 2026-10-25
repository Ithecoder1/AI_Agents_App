"""Ports (interfaces) the application depends on; adapters in infrastructure implement them."""

from vibe.domain.ports.agent_provider import IAgentProvider, ProviderDeps, ProviderFactory
from vibe.domain.ports.artifacts import ArtifactStore
from vibe.domain.ports.events import EventBus, EventStore
from vibe.domain.ports.queue import TaskQueue
from vibe.domain.ports.repositories import PromptRepository, TaskRepository
from vibe.domain.ports.secrets import SecretResolver
from vibe.domain.ports.workspace import IManifestParser

__all__ = [
    "ArtifactStore",
    "EventBus",
    "EventStore",
    "IAgentProvider",
    "IManifestParser",
    "PromptRepository",
    "ProviderDeps",
    "ProviderFactory",
    "SecretResolver",
    "TaskQueue",
    "TaskRepository",
]
