"""Application layer: use-cases and orchestration. Depends on the domain only."""

from app.application.workspace_manager import WorkspaceManager

__all__ = ["WorkspaceManager"]
