"""Spawn the ``claude`` CLI and translate its stream-json output into SSE.

Spawn mechanics (create_subprocess_exec, kill-guard) are ported from
plex-agent's ``bot.py``. The parser is **new code**: plex-agent uses
``--output-format text`` + ``proc.communicate()`` and parses nothing. The shape
parsed here was locked against a live ``claude`` v2.1.160 probe:

    {"type":"system",...}                              # init / status — skip
    {"type":"stream_event","event":{"type":"content_block_delta",
        "delta":{"type":"text_delta","text":"..."}}}   # a token delta
    {"type":"stream_event","event":{"type":"content_block_start",
        "content_block":{"type":"tool_use","name":"Grep"}}}  # tool pill
    {"type":"assistant","message":{"content":[{"type":"text",...}]}}  # whole turn
    {"type":"rate_limit_event","rate_limit_info":{
        "status":"allowed",...}}                       # every run; shown only if "rejected"
    {"type":"result","is_error":false,"result":"..."}  # end

Both stdout and stderr are PIPEd; because we read stdout line-by-line instead of
``communicate()``-ing both at once, stderr is drained by a *separate* task to
avoid a PIPE-buffer deadlock.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from pathlib import Path

from .citations import extract_citations, safe_tool_trace
from .providers import find_claude, subscription_environment

logger = logging.getLogger("ask_widget.runner")

# Whole-stream wall-clock budget. First token is ~2-3s; "Prove it" with several
# Grep/Read calls can take longer, so give it headroom but bound the worst case.
STREAM_TIMEOUT = 120.0
FIRST_ACTIVITY_TIMEOUT = 35.0
HEARTBEAT_SECONDS = 8.0

# StreamReader line-length cap. The init line is ~6-8 KB; tool_result lines
# (whole files read by "Prove it") can be large, so we lift the default 64 KB
# ceiling well above anything a normal source file produces.
STREAM_LIMIT = 16 * 1024 * 1024


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


_WEB_TOOLS = ("WebSearch", "WebFetch")


def build_cmd(
    prompt: str,
    folder: Path,
    model: str,
    append_system: str,
    effort: str = "medium",
    web: bool = True,
) -> list[str]:
    return [
        find_claude() or "claude",
        "-p",
        prompt,
        "--add-dir",
        str(folder),
        "--output-format",
        "stream-json",
        "--verbose",
        "--include-partial-messages",
        # No MCP servers. The user's own settings pre-allow many MCP tools (a live
        # browser, mail, a web fetch) that would otherwise reach an answer the
        # allowlist below never names, and web lookups off would not be off.
        "--strict-mcp-config",
        "--allowedTools",
        "Read",
        "Grep",
        "Glob",
        "Skill",
        *(_WEB_TOOLS if web else ()),
        "--disallowedTools",
        "Bash",
        "Edit",
        "Write",
        "NotebookEdit",
        *(() if web else _WEB_TOOLS),
        "--model",
        model,
        "--effort",
        effort,
        "--append-system-prompt",
        append_system,
    ]


def _kill(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is None:
        try:
            proc.kill()
        except ProcessLookupError:
            pass


async def _drain_stderr(stream: asyncio.StreamReader | None, sink: list[str]) -> None:
    if stream is None:
        return
    try:
        async for raw in stream:
            sink.append(raw.decode("utf-8", errors="replace"))
            del sink[:-200]
    except Exception:  # pragma: no cover - best-effort drain
        pass


async def stream_answer(
    prompt: str,
    folder: Path,
    model: str,
    append_system: str,
    *,
    effort: str = "medium",
    web: bool = True,
    document_source: str | None = None,
    first_activity_timeout: float = FIRST_ACTIVITY_TIMEOUT,
    stream_timeout: float = STREAM_TIMEOUT,
) -> AsyncIterator[str]:
    """Yield SSE-formatted strings: ``token`` / ``tool_status`` / ``error`` / ``done``.

    Always terminates with exactly one of ``done`` or ``error`` so the widget's
    reader never hangs in "Thinking…".
    """
    cmd = build_cmd(prompt, folder, model, append_system, effort, web)
    started = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(folder),
            env=subscription_environment("claude"),
            limit=STREAM_LIMIT,
        )
    except FileNotFoundError:
        yield _sse(
            "error",
            {
                "message": "The `claude` CLI was not found on PATH. Install Claude "
                "Code and make sure `claude` is runnable by this server."
            },
        )
        return
    except Exception as exc:  # pragma: no cover - spawn failure
        yield _sse("error", {"message": f"Failed to start Claude: {exc}"})
        return

    stderr_chunks: list[str] = []
    stderr_task = asyncio.create_task(_drain_stderr(proc.stderr, stderr_chunks))

    accumulated = ""          # assistant text seen so far (for the diff fallback)
    open_tools: dict[int, dict] = {}   # content-block index -> name/input fragments
    saw_result = False
    saw_error = False
    saw_activity = False
    trace: list[dict] = []

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
                yield _sse(
                    "error",
                    {"message": f"Claude exceeded the {int(stream_timeout)}s request timeout.", "retryable": True},
                )
                saw_error = True
                break
            if not saw_activity and activity_remaining <= 0:
                _kill(proc)
                yield _sse(
                    "error",
                    {
                        "message": f"Claude produced no visible activity within {int(first_activity_timeout)}s.",
                        "retryable": True,
                    },
                )
                saw_error = True
                break
            try:
                wait_for = min(remaining, activity_remaining, HEARTBEAT_SECONDS)
                raw = await asyncio.wait_for(proc.stdout.readline(), timeout=wait_for)
            except asyncio.TimeoutError:
                now = loop.time()
                if now >= deadline:
                    _kill(proc)
                    yield _sse(
                        "error",
                        {"message": f"Claude exceeded the {int(stream_timeout)}s request timeout.", "retryable": True},
                    )
                    saw_error = True
                    break
                if not saw_activity and now >= activity_deadline:
                    _kill(proc)
                    yield _sse(
                        "error",
                        {
                            "message": f"Claude produced no visible activity within {int(first_activity_timeout)}s.",
                            "retryable": True,
                        },
                    )
                    saw_error = True
                    break
                yield _sse("heartbeat", {"elapsed_ms": int((time.monotonic() - started) * 1000)})
                continue
            except (ValueError, asyncio.LimitOverrunError):
                # A single JSONL line exceeded STREAM_LIMIT — bail rather than
                # corrupt the parse mid-line.
                _kill(proc)
                yield _sse("error", {"message": "Claude emitted an oversized line; aborted."})
                saw_error = True
                break

            if not raw:
                break  # EOF

            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            kind = obj.get("type")
            if kind == "system":
                continue
            if kind == "rate_limit_event":
                # Sent on every run to report the usage window, almost always
                # with status "allowed". Only "rejected" means Claude is
                # actually being held back, so only that reaches the reader.
                saw_activity = True
                info = obj.get("rate_limit_info") or {}
                if info.get("status") == "rejected":
                    yield _sse(
                        "status",
                        {"kind": "rate_limit", "message": "Claude is rate limited; waiting…", "retryable": True},
                    )
                continue

            if kind == "stream_event":
                event = obj.get("event") or {}
                etype = event.get("type")
                if etype == "content_block_delta":
                    delta = event.get("delta") or {}
                    if delta.get("type") == "text_delta":
                        text = delta.get("text") or ""
                        if text:
                            saw_activity = True
                            accumulated += text
                            yield _sse("token", {"text": text})
                    elif delta.get("type") == "input_json_delta":
                        state = open_tools.get(event.get("index"))
                        if state is not None:
                            state["partial"] += delta.get("partial_json") or ""
                elif etype == "content_block_start":
                    block = event.get("content_block") or {}
                    if block.get("type") == "tool_use":
                        name = block.get("name") or "tool"
                        saw_activity = True
                        if name == "ToolSearch":
                            # Plumbing: it loads WebSearch/WebFetch before their
                            # first use. It finds nothing, so no pill or trace.
                            continue
                        open_tools[event.get("index")] = {
                            "name": name,
                            "partial": "",
                            "input": block.get("input") if isinstance(block.get("input"), dict) else {},
                        }
                        yield _sse("tool_status", {"tool": name, "status": "calling"})
                elif etype == "content_block_stop":
                    state = open_tools.pop(event.get("index"), None)
                    if state:
                        payload = state["input"]
                        if state["partial"]:
                            try:
                                payload = json.loads(state["partial"])
                            except json.JSONDecodeError:
                                pass
                        item = safe_tool_trace(state["name"], payload)
                        trace.append(item)
                        yield _sse("tool_trace", item)
                        yield _sse("tool_status", {"tool": state["name"], "status": "complete"})
                continue

            if kind == "assistant":
                # Fallback for clients that send whole turns rather than deltas.
                # Diff against what the deltas already produced so we never
                # double-emit; the probe confirmed join(deltas) == assistant text.
                message = obj.get("message") or {}
                parts = [
                    b.get("text") or ""
                    for b in (message.get("content") or [])
                    if isinstance(b, dict) and b.get("type") == "text"
                ]
                full = "".join(parts)
                if full and full.startswith(accumulated) and len(full) > len(accumulated):
                    suffix = full[len(accumulated):]
                    accumulated = full
                    yield _sse("token", {"text": suffix})
                elif full and not accumulated:
                    accumulated = full
                    yield _sse("token", {"text": full})
                continue

            if kind == "result":
                saw_result = True
                if obj.get("is_error") or obj.get("subtype") == "error":
                    saw_error = True
                    msg = obj.get("result") or obj.get("error") or "Claude reported an error."
                    yield _sse("error", {"message": str(msg), "retryable": True})
                continue

        # Loop ended without a result line: surface a crash or truncated stream
        # rather than silently saving an empty/partial answer as complete.
        if not saw_result and not saw_error:
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                _kill(proc)
            rc = proc.returncode
            stderr_text = "".join(stderr_chunks).strip()
            detail = stderr_text[-500:] if stderr_text else (
                f"Claude exited with code {rc}." if rc not in (0, None)
                else "Claude ended before sending a final result."
            )
            yield _sse("error", {"message": detail, "retryable": True})
            saw_error = True

        if not saw_error:
            citations = extract_citations(
                accumulated, folder, document_source=document_source
            )
            if citations:
                yield _sse("citations", {"items": citations})
            yield _sse(
                "done",
                {
                    "elapsed_ms": int((time.monotonic() - started) * 1000),
                    "trace_count": len(trace),
                    "citation_count": len(citations),
                },
            )
    finally:
        _kill(proc)
        if not stderr_task.done():
            stderr_task.cancel()
        try:
            await proc.wait()
        except Exception:  # pragma: no cover
            pass
