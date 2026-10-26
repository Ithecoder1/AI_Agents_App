"""Ports (interfaces) the application depends on; adapters in infrastructure implement them."""

from app.domain.ports.agent_provider import IAgentProvider, ProviderDeps, ProviderFactory
from app.domain.ports.artifacts import ArtifactStore
from app.domain.ports.events import EventBus, EventStore
from app.domain.ports.queue import TaskQueue
from app.domain.ports.repositories import PromptRepository, TaskRepository
from app.domain.ports.secrets import SecretResolver
from app.domain.ports.workspace import IManifestParser

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
