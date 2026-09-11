"""Actionable diagnostics for both subscription-backed answer providers."""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from . import __version__
from .codex_runner import build_cmd as build_codex_cmd
from .providers import claude_status, codex_status, subscription_environment
from .storage import Storage


def _probe_claude(status: dict[str, Any], model: str, effort: str) -> dict[str, Any]:
    path = status.get("path")
    started = time.monotonic()
    try:
        check = subprocess.run(
            [
                path,
                "-p",
                "Reply with exactly ONYX_OK and nothing else.",
                "--safe-mode",
                "--strict-mcp-config",
                "--no-session-persistence",
                "--model",
                model,
                "--effort",
                effort,
                "--output-format",
                "text",
                "--tools",
                "",
            ],
            capture_output=True,
            text=True,
            timeout=45,
            env=subscription_environment("claude"),
        )
        output = check.stdout.strip()
        return {
            "ok": check.returncode == 0 and "ONYX_OK" in output,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "output": output[:300],
            "error": check.stderr.strip()[-600:] if check.returncode else "",
        }
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "latency_ms": int((time.monotonic() - started) * 1000), "error": str(exc)}


def _probe_codex(status: dict[str, Any], folder: Path, model: str, effort: str) -> dict[str, Any]:
    started = time.monotonic()
    command = build_codex_cmd(folder, model, effort)
    command[0] = str(status.get("path") or command[0])
    try:
        check = subprocess.run(
            command,
            input="Reply with exactly ONYX_OK and nothing else.",
            capture_output=True,
            text=True,
            timeout=60,
            env=subscription_environment("codex"),
        )
        output = ""
        for line in check.stdout.splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if item.get("type") == "item.completed":
                value = item.get("item") or {}
                if value.get("type") == "agent_message":
                    output += str(value.get("text") or "")
        return {
            "ok": check.returncode == 0 and "ONYX_OK" in output,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "output": output[:300],
            "error": check.stderr.strip()[-600:] if check.returncode else "",
        }
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "latency_ms": int((time.monotonic() - started) * 1000), "error": str(exc)}


def build_diagnostics(
    storage: Storage,
    *,
    provider: str,
    model: str,
    effort: str,
    roots: list[dict[str, Any]],
    default_folder: Path,
    probe: bool = False,
) -> dict[str, Any]:
    folder_ok = default_folder.exists() and default_folder.is_dir() and os.access(default_folder, os.R_OK)
    db_ok = storage.path.exists() and os.access(storage.path, os.R_OK | os.W_OK)
    claude = claude_status()
    codex = codex_status()
    selected = codex if provider == "codex" else claude
    if probe and selected.get("subscription"):
        selected["probe"] = (
            _probe_codex(selected, default_folder, model, effort)
            if provider == "codex"
            else _probe_claude(selected, model, effort)
        )
    return {
        "ok": folder_ok and db_ok and bool(selected.get("ok")),
        "app": {
            "version": __version__,
            "python": sys.version.split()[0],
            "platform": platform.platform(),
        },
        "selected_provider": provider,
        "selected_model": model,
        "selected_effort": effort,
        "providers": {"claude": claude, "codex": codex},
        "claude": claude,  # compatibility with v0.3 clients
        "storage": {**storage.stats(), "ok": db_ok},
        "folder": {"path": str(default_folder), "ok": folder_ok},
        "roots": roots,
    }
