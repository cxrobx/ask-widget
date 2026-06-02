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
    {"type":"rate_limit_event",...}                    # skip
    {"type":"result","is_error":false,"result":"..."}  # end

Both stdout and stderr are PIPEd; because we read stdout line-by-line instead of
``communicate()``-ing both at once, stderr is drained by a *separate* task to
avoid a PIPE-buffer deadlock.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from pathlib import Path

logger = logging.getLogger("ask_widget.runner")

# Whole-stream wall-clock budget. First token is ~2-3s; "Prove it" with several
# Grep/Read calls can take longer, so give it headroom but bound the worst case.
STREAM_TIMEOUT = 120.0

# StreamReader line-length cap. The init line is ~6-8 KB; tool_result lines
# (whole files read by "Prove it") can be large, so we lift the default 64 KB
# ceiling well above anything a normal source file produces.
STREAM_LIMIT = 16 * 1024 * 1024


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def build_cmd(prompt: str, folder: Path, model: str, append_system: str) -> list[str]:
    return [
        "claude",
        "-p",
        prompt,
        "--add-dir",
        str(folder),
        "--output-format",
        "stream-json",
        "--verbose",
        "--include-partial-messages",
        "--allowedTools",
        "Read",
        "Grep",
        "Glob",
        "--disallowedTools",
        "Bash",
        "Edit",
        "Write",
        "NotebookEdit",
        "--model",
        model,
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
    except Exception:  # pragma: no cover - best-effort drain
        pass


async def stream_answer(
    prompt: str, folder: Path, model: str, append_system: str
) -> AsyncIterator[str]:
    """Yield SSE-formatted strings: ``token`` / ``tool_status`` / ``error`` / ``done``.

    Always terminates with exactly one of ``done`` or ``error`` so the widget's
    reader never hangs in "Thinking…".
    """
    cmd = build_cmd(prompt, folder, model, append_system)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(folder),
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
    open_tools: dict[int, str] = {}   # content-block index -> tool name
    saw_result = False
    saw_error = False

    loop = asyncio.get_event_loop()
    deadline = loop.time() + STREAM_TIMEOUT

    try:
        assert proc.stdout is not None
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                _kill(proc)
                yield _sse("error", {"message": "Timed out waiting for Claude (120s)."})
                saw_error = True
                break
            try:
                raw = await asyncio.wait_for(proc.stdout.readline(), timeout=remaining)
            except asyncio.TimeoutError:
                _kill(proc)
                yield _sse("error", {"message": "Timed out waiting for Claude (120s)."})
                saw_error = True
                break
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
            if kind in ("system", "rate_limit_event"):
                continue

            if kind == "stream_event":
                event = obj.get("event") or {}
                etype = event.get("type")
                if etype == "content_block_delta":
                    delta = event.get("delta") or {}
                    if delta.get("type") == "text_delta":
                        text = delta.get("text") or ""
                        if text:
                            accumulated += text
                            yield _sse("token", {"text": text})
                elif etype == "content_block_start":
                    block = event.get("content_block") or {}
                    if block.get("type") == "tool_use":
                        name = block.get("name") or "tool"
                        open_tools[event.get("index")] = name
                        yield _sse("tool_status", {"tool": name, "status": "calling"})
                elif etype == "content_block_stop":
                    name = open_tools.pop(event.get("index"), None)
                    if name:
                        yield _sse("tool_status", {"tool": name, "status": "complete"})
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
                    yield _sse("error", {"message": str(msg)})
                continue

        # Loop ended without a result line: surface a crash rather than hang.
        if not saw_result and not saw_error:
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                _kill(proc)
            rc = proc.returncode
            if rc not in (0, None):
                stderr_text = "".join(stderr_chunks).strip()
                detail = stderr_text[-500:] if stderr_text else f"Claude exited with code {rc}."
                yield _sse("error", {"message": detail})
                saw_error = True

        if not saw_error:
            yield _sse("done", {})
    finally:
        _kill(proc)
        if not stderr_task.done():
            stderr_task.cancel()
        try:
            await proc.wait()
        except Exception:  # pragma: no cover
            pass
