"""Run Codex CLI headlessly with ChatGPT subscription auth and emit widget SSE."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from pathlib import Path

from .citations import extract_citations, safe_tool_trace
from .claude_runner import (
    FIRST_ACTIVITY_TIMEOUT,
    HEARTBEAT_SECONDS,
    STREAM_LIMIT,
    STREAM_TIMEOUT,
    _drain_stderr,
    _kill,
    _sse,
)
from .providers import find_codex, subscription_environment


def build_cmd(folder: Path, model: str, effort: str) -> list[str]:
    codex = find_codex() or "codex"
    return [
        codex,
        "-a",
        "never",
        "--search",
        "exec",
        "--json",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--disable",
        "plugins",
        "--disable",
        "apps",
        "--disable",
        "browser_use",
        "--disable",
        "computer_use",
        "--disable",
        "image_generation",
        "--disable",
        "multi_agent",
        "--sandbox",
        "read-only",
        "--skip-git-repo-check",
        "--model",
        model,
        "-c",
        f'model_reasoning_effort="{effort}"',
        "-C",
        str(folder),
        "-",
    ]


def _error_message(obj: dict) -> str:
    error = obj.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error.get("code") or error)
    return str(error or obj.get("message") or "Codex reported an error.")


async def stream_answer(
    prompt: str,
    folder: Path,
    model: str,
    append_system: str,
    *,
    effort: str = "medium",
    document_source: str | None = None,
    first_activity_timeout: float = FIRST_ACTIVITY_TIMEOUT,
    stream_timeout: float = STREAM_TIMEOUT,
) -> AsyncIterator[str]:
    cmd = build_cmd(folder, model, effort)
    started = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(folder),
            env=subscription_environment("codex"),
            limit=STREAM_LIMIT,
        )
    except FileNotFoundError:
        yield _sse(
            "error",
            {"message": "The `codex` CLI was not found. Install Codex and run `codex login`.", "retryable": False},
        )
        return
    except Exception as exc:  # pragma: no cover - spawn failure
        yield _sse("error", {"message": f"Failed to start Codex: {exc}"})
        return

    full_prompt = (
        "Follow these task-specific instructions for this read-only answer:\n"
        f"{append_system}\n\n"
        "Reader request:\n"
        f"{prompt}"
    )
    assert proc.stdin is not None
    proc.stdin.write(full_prompt.encode("utf-8"))
    await proc.stdin.drain()
    proc.stdin.close()

    stderr_chunks: list[str] = []
    stderr_task = asyncio.create_task(_drain_stderr(proc.stderr, stderr_chunks))
    accumulated = ""
    trace: list[dict] = []
    open_tools: dict[str, str] = {}
    nonfatal_errors: list[str] = []
    usage: dict = {}
    saw_result = False
    saw_error = False
    saw_activity = False

    loop = asyncio.get_event_loop()
    deadline = loop.time() + stream_timeout
    activity_deadline = loop.time() + first_activity_timeout

    try:
        assert proc.stdout is not None
        while True:
            now = loop.time()
            remaining = deadline - now
            activity_remaining = activity_deadline - now if not saw_activity else remaining
            if remaining <= 0:
                _kill(proc)
                yield _sse("error", {"message": f"Codex exceeded the {int(stream_timeout)}s request timeout.", "retryable": True})
                saw_error = True
                break
            if not saw_activity and activity_remaining <= 0:
                _kill(proc)
                yield _sse("error", {"message": f"Codex produced no visible activity within {int(first_activity_timeout)}s.", "retryable": True})
                saw_error = True
                break
            try:
                raw = await asyncio.wait_for(
                    proc.stdout.readline(),
                    timeout=min(remaining, activity_remaining, HEARTBEAT_SECONDS),
                )
            except asyncio.TimeoutError:
                now = loop.time()
                if now >= deadline:
                    _kill(proc)
                    yield _sse("error", {"message": f"Codex exceeded the {int(stream_timeout)}s request timeout.", "retryable": True})
                    saw_error = True
                    break
                if not saw_activity and now >= activity_deadline:
                    _kill(proc)
                    yield _sse("error", {"message": f"Codex produced no visible activity within {int(first_activity_timeout)}s.", "retryable": True})
                    saw_error = True
                    break
                yield _sse("heartbeat", {"elapsed_ms": int((time.monotonic() - started) * 1000)})
                continue
            except (ValueError, asyncio.LimitOverrunError):
                _kill(proc)
                yield _sse("error", {"message": "Codex emitted an oversized line; aborted."})
                saw_error = True
                break

            if not raw:
                break
            try:
                obj = json.loads(raw.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                continue
            kind = obj.get("type")
            if kind == "turn.started":
                saw_activity = True
                yield _sse("status", {"kind": "working", "message": "Codex is reasoning…"})
                continue
            if kind == "item.started":
                item = obj.get("item") or {}
                if item.get("type") == "command_execution":
                    item_id = str(item.get("id") or len(open_tools))
                    command = str(item.get("command") or "")
                    open_tools[item_id] = command
                    saw_activity = True
                    yield _sse("tool_status", {"tool": "Shell", "status": "calling"})
                elif item.get("type") == "web_search":
                    saw_activity = True
                    yield _sse("tool_status", {"tool": "WebSearch", "status": "calling"})
                continue
            if kind == "item.completed":
                item = obj.get("item") or {}
                item_type = item.get("type")
                if item_type == "agent_message":
                    text = str(item.get("text") or "")
                    if text:
                        saw_activity = True
                        accumulated += text
                        yield _sse("token", {"text": text})
                elif item_type == "command_execution":
                    item_id = str(item.get("id") or "")
                    command = str(item.get("command") or open_tools.pop(item_id, ""))
                    trace_item = safe_tool_trace("Shell", {"command": command})
                    trace.append(trace_item)
                    yield _sse("tool_trace", trace_item)
                    yield _sse("tool_status", {"tool": "Shell", "status": "complete"})
                elif item_type == "web_search":
                    # The query is only filled in once the search completes.
                    trace_item = safe_tool_trace("WebSearch", {"query": item.get("query")})
                    trace.append(trace_item)
                    yield _sse("tool_trace", trace_item)
                    yield _sse("tool_status", {"tool": "WebSearch", "status": "complete"})
                elif item_type == "error":
                    nonfatal_errors.append(str(item.get("message") or "Codex item error."))
                continue
            if kind == "turn.completed":
                saw_result = True
                usage = obj.get("usage") if isinstance(obj.get("usage"), dict) else {}
                continue
            if kind in {"turn.failed", "error"}:
                saw_result = True
                saw_error = True
                yield _sse("error", {"message": _error_message(obj), "retryable": True})

        if not saw_result and not saw_error:
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                _kill(proc)
            stderr_text = "".join(stderr_chunks).strip()
            detail = stderr_text[-700:] if stderr_text else (
                nonfatal_errors[-1] if nonfatal_errors else f"Codex exited with code {proc.returncode}."
            )
            yield _sse("error", {"message": detail, "retryable": True})
            saw_error = True

        if not saw_error:
            citations = extract_citations(accumulated, folder, document_source=document_source)
            if citations:
                yield _sse("citations", {"items": citations})
            yield _sse(
                "done",
                {
                    "elapsed_ms": int((time.monotonic() - started) * 1000),
                    "trace_count": len(trace),
                    "citation_count": len(citations),
                    "usage": usage,
                },
            )
    finally:
        _kill(proc)
        if not stderr_task.done():
            stderr_task.cancel()
        try:
            await proc.wait()
        except Exception:  # pragma: no cover - best-effort cleanup
            pass
