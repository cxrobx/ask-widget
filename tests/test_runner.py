from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ask_widget.claude_runner import build_cmd as build_claude_cmd, stream_answer
from ask_widget.codex_runner import stream_answer as stream_codex_answer


def json_line(payload: dict) -> bytes:
    return (json.dumps(payload) + "\n").encode()


class FakeProcess:
    def __init__(self, stdout: asyncio.StreamReader) -> None:
        self.stdout = stdout
        self.stderr = None
        self.stdin = FakeStdin()
        self.returncode: int | None = None
        self.killed = False

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def wait(self) -> int:
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


class FakeStdin:
    def __init__(self) -> None:
        self.data = b""

    def write(self, data: bytes) -> None:
        self.data += data

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        return None


class RunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_claude_command_uses_selected_effort(self) -> None:
        command = build_claude_cmd("prompt", Path("/tmp/context"), "sonnet", "system", "xhigh")
        self.assertEqual(command[command.index("--model") + 1], "sonnet")
        self.assertEqual(command[command.index("--effort") + 1], "xhigh")

    async def test_stream_parses_tokens_tool_traces_citations_and_done(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "app.py"
            source.write_text("first\nevidence\n", encoding="utf-8")
            stdout = asyncio.StreamReader()
            for payload in (
                {
                    "type": "stream_event",
                    "event": {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {"type": "tool_use", "name": "Read", "input": {}},
                    },
                },
                {
                    "type": "stream_event",
                    "event": {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {
                            "type": "input_json_delta",
                            "partial_json": json.dumps({"file_path": str(source)}),
                        },
                    },
                },
                {"type": "stream_event", "event": {"type": "content_block_stop", "index": 0}},
                {
                    "type": "stream_event",
                    "event": {
                        "type": "content_block_delta",
                        "delta": {"type": "text_delta", "text": "See `app.py:2`."},
                    },
                },
                {"type": "result", "is_error": False, "result": "See `app.py:2`."},
            ):
                stdout.feed_data(json_line(payload))
            stdout.feed_eof()
            process = FakeProcess(stdout)

            with patch("asyncio.create_subprocess_exec", return_value=process):
                chunks = [
                    chunk
                    async for chunk in stream_answer(
                        "prompt", root, "sonnet", "system", document_source=str(source)
                    )
                ]

            stream = "".join(chunks)
            self.assertIn("event: tool_trace", stream)
            self.assertIn("event: token", stream)
            self.assertIn("event: citations", stream)
            self.assertIn('"line": 2', stream)
            self.assertIn("event: done", stream)

    async def test_no_activity_timeout_kills_the_child(self) -> None:
        stdout = asyncio.StreamReader()
        process = FakeProcess(stdout)
        with tempfile.TemporaryDirectory() as raw:
            with patch("asyncio.create_subprocess_exec", return_value=process):
                chunks = [
                    chunk
                    async for chunk in stream_answer(
                        "prompt",
                        Path(raw),
                        "sonnet",
                        "system",
                        first_activity_timeout=0.01,
                        stream_timeout=1,
                    )
                ]
        self.assertTrue(process.killed)
        self.assertIn("no visible activity", "".join(chunks))
        self.assertNotIn("event: done", "".join(chunks))

    async def test_eof_without_result_is_an_error(self) -> None:
        stdout = asyncio.StreamReader()
        stdout.feed_eof()
        process = FakeProcess(stdout)
        with tempfile.TemporaryDirectory() as raw:
            with patch("asyncio.create_subprocess_exec", return_value=process):
                chunks = [
                    chunk
                    async for chunk in stream_answer(
                        "prompt", Path(raw), "sonnet", "system"
                    )
                ]
        self.assertIn("before sending a final result", "".join(chunks))
        self.assertNotIn("event: done", "".join(chunks))

    async def test_codex_jsonl_is_translated_to_widget_events(self) -> None:
        stdout = asyncio.StreamReader()
        for payload in (
            {"type": "thread.started", "thread_id": "thread"},
            {"type": "turn.started"},
            {
                "type": "item.started",
                "item": {"id": "tool-1", "type": "command_execution", "command": "rg evidence"},
            },
            {
                "type": "item.completed",
                "item": {"id": "tool-1", "type": "command_execution", "command": "rg evidence"},
            },
            {
                "type": "item.completed",
                "item": {"id": "answer", "type": "agent_message", "text": "Found `app.py:2`."},
            },
            {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 4}},
        ):
            stdout.feed_data(json_line(payload))
        stdout.feed_eof()
        process = FakeProcess(stdout)
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "app.py").write_text("one\ntwo\n", encoding="utf-8")
            with patch("asyncio.create_subprocess_exec", return_value=process):
                chunks = [
                    chunk
                    async for chunk in stream_codex_answer(
                        "prompt", root, "gpt-5.6-sol", "system", effort="low"
                    )
                ]
        stream = "".join(chunks)
        self.assertIn("event: tool_trace", stream)
        self.assertIn("event: token", stream)
        self.assertIn("event: citations", stream)
        self.assertIn("event: done", stream)
        self.assertIn(b"Reader request", process.stdin.data)


if __name__ == "__main__":
    unittest.main()
