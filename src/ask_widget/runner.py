"""Subscription-only provider dispatcher for Claude and Codex headless runs."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

from .claude_runner import _sse, stream_answer as stream_claude
from .codex_runner import stream_answer as stream_codex
from .providers import provider_status


async def stream_answer(
    provider: str,
    prompt: str,
    folder: Path,
    model: str,
    append_system: str,
    *,
    effort: str = "medium",
    web: bool = True,
    document_source: str | None = None,
    first_activity_timeout: float,
    stream_timeout: float,
) -> AsyncIterator[str]:
    if provider not in {"claude", "codex"}:
        yield _sse("error", {"message": f"Unknown model provider: {provider}", "retryable": False})
        return
    status = await asyncio.to_thread(provider_status, provider)
    if not status.get("installed"):
        yield _sse("error", {"message": status.get("repair") or f"{provider.title()} CLI is not installed.", "retryable": False})
        return
    if not status.get("subscription"):
        yield _sse(
            "error",
            {
                "message": status.get("repair")
                or f"{provider.title()} is not signed in with subscription access. API-key execution is blocked to prevent usage charges.",
                "retryable": False,
            },
        )
        return
    implementation = stream_codex if provider == "codex" else stream_claude
    kwargs = {
        "document_source": document_source,
        "first_activity_timeout": first_activity_timeout,
        "stream_timeout": stream_timeout,
    }
    kwargs["effort"] = effort
    kwargs["web"] = web
    async for chunk in implementation(prompt, folder, model, append_system, **kwargs):
        yield chunk


__all__ = ["_sse", "stream_answer"]
