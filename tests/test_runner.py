from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from onyx.claude_runner import build_cmd as build_claude_cmd, stream_answer
from onyx.codex_runner import build_cmd as build_codex_cmd, stream_answer as stream_codex_answer


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

    async def test_claude_command_gates_web_tools_and_never_allows_writes(self) -> None:
        web_tools = {"WebSearch", "WebFetch"}
        for web in (True, False):
            with self.subTest(web=web):
                command = build_claude_cmd("prompt", Path("/tmp/context"), "sonnet", "system", web=web)
                allowed = set(command[command.index("--allowedTools") + 1 : command.index("--disallowedTools")])
                denied = set(command[command.index("--disallowedTools") + 1 : command.index("--model")])
                self.assertLessEqual({"Read", "Grep", "Glob", "Skill"}, allowed)
                self.assertLessEqual({"Bash", "Edit", "Write", "NotebookEdit"}, denied)
                self.assertEqual(web_tools <= allowed, web)
                self.assertEqual(web_tools <= denied, not web)
                # Without it, MCP tools the user's settings pre-allow reach the answer.
                self.assertIn("--strict-mcp-config", command)

    async def test_codex_command_sets_web_search_both_ways(self) -> None:
        # Unset, Codex still searches a cached index, so off has to be explicit.
        for web, mode in ((True, "live"), (False, "disabled")):
            with self.subTest(web=web):
                command = build_codex_cmd(Path("/tmp/context"), "gpt-5.6-sol", "low", web=web)
                self.assertIn(f'web_search="{mode}"', command)
                self.assertNotIn("--search", command)

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

    async def _claude_stream(self, payloads: list[dict]) -> str:
        stdout = asyncio.StreamReader()
        for payload in payloads:
            stdout.feed_data(json_line(payload))
        stdout.feed_eof()
        with tempfile.TemporaryDirectory() as raw:
            with patch("asyncio.create_subprocess_exec", return_value=FakeProcess(stdout)):
                chunks = [
                    chunk
                    async for chunk in stream_answer("prompt", Path(raw), "sonnet", "system")
                ]
        return "".join(chunks)

    async def test_routine_rate_limit_event_is_not_reported_as_a_limit(self) -> None:
        # The CLI sends this on every run; "allowed" is the normal case.
        for status in ("allowed", "allowed_warning"):
            with self.subTest(status=status):
                stream = await self._claude_stream(
                    [
                        {
                            "type": "rate_limit_event",
                            "rate_limit_info": {"status": status, "rateLimitType": "five_hour"},
                        },
                        {
                            "type": "stream_event",
                            "event": {
                                "type": "content_block_delta",
                                "delta": {"type": "text_delta", "text": "Yes."},
                            },
                        },
                        {"type": "result", "is_error": False, "result": "Yes."},
                    ]
                )
                self.assertNotIn("event: status", stream)
                self.assertNotIn("rate limited", stream)
                self.assertIn("event: done", stream)

    async def test_rejected_rate_limit_event_is_reported(self) -> None:
        stream = await self._claude_stream(
            [
                {
                    "type": "rate_limit_event",
                    "rate_limit_info": {"status": "rejected", "rateLimitType": "five_hour"},
                },
                {"type": "result", "is_error": False, "result": "Yes."},
            ]
        )
        self.assertIn("event: status", stream)
        self.assertIn("rate limited", stream)

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

    async def test_tool_search_is_hidden_and_web_fetch_keeps_its_url(self) -> None:
        stream = await self._claude_stream(
            [
                {
                    "type": "stream_event",
                    "event": {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {"type": "tool_use", "name": "ToolSearch", "input": {}},
                    },
                },
                {"type": "stream_event", "event": {"type": "content_block_stop", "index": 0}},
                {
                    "type": "stream_event",
                    "event": {
                        "type": "content_block_start",
                        "index": 1,
                        "content_block": {"type": "tool_use", "name": "WebFetch", "input": {}},
                    },
                },
                {
                    "type": "stream_event",
                    "event": {
                        "type": "content_block_delta",
                        "index": 1,
                        "delta": {
                            "type": "input_json_delta",
                            "partial_json": json.dumps(
                                {"url": "https://pypi.org/project/httpx/", "prompt": "latest?"}
                            ),
                        },
                    },
                },
                {"type": "stream_event", "event": {"type": "content_block_stop", "index": 1}},
                {"type": "result", "is_error": False, "result": "0.28.1"},
            ]
        )
        self.assertNotIn("ToolSearch", stream)
        self.assertIn('"tool": "WebFetch"', stream)
        self.assertIn("https://pypi.org/project/httpx/", stream)
        self.assertIn("event: done", stream)

    async def test_codex_web_search_gets_a_pill_and_a_trace(self) -> None:
        stdout = asyncio.StreamReader()
        for payload in (
            {"type": "turn.started"},
            {"type": "item.started", "item": {"id": "ws", "type": "web_search", "query": ""}},
            {
                "type": "item.completed",
                "item": {"id": "ws", "type": "web_search", "query": "httpx latest version"},
            },
            {"type": "item.completed", "item": {"id": "answer", "type": "agent_message", "text": "0.28.1"}},
            {"type": "turn.completed", "usage": {}},
        ):
            stdout.feed_data(json_line(payload))
        stdout.feed_eof()
        with tempfile.TemporaryDirectory() as raw:
            with patch("asyncio.create_subprocess_exec", return_value=FakeProcess(stdout)):
                chunks = [
                    chunk
                    async for chunk in stream_codex_answer("prompt", Path(raw), "gpt-5.6-sol", "system")
                ]
        stream = "".join(chunks)
        self.assertIn('{"tool": "WebSearch", "status": "calling"}', stream)
        self.assertIn('"query": "httpx latest version"', stream)
        self.assertIn("event: done", stream)


if __name__ == "__main__":
    unittest.main()
