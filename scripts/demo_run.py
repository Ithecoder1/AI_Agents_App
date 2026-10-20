#!/usr/bin/env python3
"""Interactive demo runner: boots Vibe Forge and executes a prompt with streaming."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Ensure stdout handles UTF-8 on Windows
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Ensure backend package is in python path
repo_root = Path(__file__).resolve().parents[1]
backend_dir = repo_root / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from vibe.api.composition import default_registry
from vibe.domain.enums import EventType
from vibe.domain.models import ExecutionParams, ExecutionTask, Prompt


async def main() -> None:
    print("=" * 65)
    print("  [*] VIBE FORGE -- Multi-Provider Execution Engine")
    print("=" * 65)

    # 1. Boot composition root & provider registry
    registry, settings = default_registry()
    print(f"\n[Config] Host: {settings.server.host}:{settings.server.port}")
    print("[Providers Loaded]")
    for entry in registry.entries():
        status_tag = "[READY]   " if entry.state.value == "ready" else "[DISABLED]"
        print(f"  {status_tag} {entry.config.id:<22} [{entry.config.kind}]")

    # 2. Select ready provider
    provider_id = "fake"
    provider = registry.resolve(provider_id)
    health = await registry.health(provider_id)
    print(f"\n[Active Provider] {provider_id} (Health: {'UP' if health.ok else 'DOWN'})")

    # 3. Prompt selection
    user_prompt = "Refactor database connection pool with automatic retry backoff"
    if len(sys.argv) > 1:
        user_prompt = " ".join(sys.argv[1:])

    prompt = Prompt(
        user_prompt=user_prompt,
        system_instructions="You are an expert systems engineer. Output unified diffs where appropriate.",
    )
    task = ExecutionTask.create(
        prompt=prompt,
        params=ExecutionParams(provider_id=provider_id),
    ).start(provider_id)

    print(f"\n[Task {task.id[:8]}] Prompt: \"{prompt.user_prompt}\"")
    print("-" * 65)
    print("Streaming events:\n")

    # 4. Stream execution
    async for event in provider.stream_execute(task):
        if event.type is EventType.STATUS:
            print(f"  [STATUS] {event.data.get('state')} (attempt {event.attempt})")
        elif event.type is EventType.THOUGHT:
            print(f"  [THOUGHT] {event.data.get('text')}\n")
            print("  [OUTPUT]: ", end="", flush=True)
        elif event.type is EventType.TOKEN:
            print(event.data.get("text", ""), end="", flush=True)
        elif event.type is EventType.ARTIFACT:
            print(f"\n\n  [ARTIFACT] {event.data.get('name')} ({event.data.get('mime')})")
            if event.data.get("inline_content"):
                print("  " + "-" * 50)
                for line in event.data["inline_content"].strip().split("\n"):
                    print(f"    {line}")
                print("  " + "-" * 50)
        elif event.type is EventType.USAGE:
            print(
                f"\n  [METRICS] Tokens: {event.data.get('prompt_tokens')} prompt, "
                f"{event.data.get('completion_tokens')} completion"
            )
        elif event.type is EventType.DONE:
            print(f"  [FINISHED] finish_reason: {event.data.get('finish_reason')}")

    print("\n" + "=" * 65)
    print("  Execution completed successfully!")
    print("=" * 65)
    await registry.aclose()


if __name__ == "__main__":
    asyncio.run(main())
