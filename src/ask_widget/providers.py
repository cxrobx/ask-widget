"""Discover local subscription-backed model providers and their model catalogs."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any


_CLAUDE_FALLBACKS = (
    "/opt/homebrew/bin/claude",
    "/usr/local/bin/claude",
    str(Path.home() / ".local/bin/claude"),
    str(Path.home() / ".claude/local/claude"),
    str(Path.home() / ".bun/bin/claude"),
)
_CODEX_FALLBACKS = (
    "/opt/homebrew/bin/codex",
    "/usr/local/bin/codex",
    str(Path.home() / ".local/bin/codex"),
    str(Path.home() / ".bun/bin/codex"),
)
_CLAUDE_LABELS = {
    "sonnet": "Claude Sonnet (latest)",
    "opus": "Claude Opus (latest)",
    "haiku": "Claude Haiku (latest)",
    "fable": "Claude Fable (latest)",
}
_CLAUDE_ORDER = {"sonnet": 0, "opus": 1, "fable": 2, "haiku": 3}


def _find_cli(name: str, fallbacks: tuple[str, ...]) -> str | None:
    direct = shutil.which(name)
    if direct and Path(direct).is_file():
        return direct
    # A native app does not inherit the interactive shell's NVM/PATH setup.
    try:
        result = subprocess.run(
            ["/bin/zsh", "-lc", f"command -v {name}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        path = result.stdout.strip()
        if result.returncode == 0 and path and Path(path).is_file():
            return path
    except (OSError, subprocess.SubprocessError):
        pass
    for candidate in fallbacks:
        if Path(candidate).is_file():
            return candidate
    if name == "codex":
        candidates = sorted(
            (Path.home() / ".nvm/versions/node").glob("*/bin/codex"),
            key=lambda path: path.stat().st_mtime if path.exists() else 0,
            reverse=True,
        )
        if candidates:
            return str(candidates[0])
    return None


def find_claude() -> str | None:
    return _find_cli("claude", _CLAUDE_FALLBACKS)


def find_codex() -> str | None:
    return _find_cli("codex", _CODEX_FALLBACKS)


def subscription_environment(provider: str) -> dict[str, str]:
    """Remove API-key/provider overrides so cached subscription auth wins."""
    environment = dict(os.environ)
    if provider == "claude":
        for key in (
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_AUTH_TOKEN",
            "CLAUDE_CODE_USE_BEDROCK",
            "CLAUDE_CODE_USE_VERTEX",
            "CLAUDE_CODE_USE_FOUNDRY",
        ):
            environment.pop(key, None)
    elif provider == "codex":
        for key in ("OPENAI_API_KEY", "CODEX_API_KEY"):
            environment.pop(key, None)
        codex = find_codex()
        if codex:
            binary_dir = str(Path(codex).parent)
            inherited = environment.get("PATH", "")
            environment["PATH"] = binary_dir + (f":{inherited}" if inherited else "")
    return environment


def _run(command: list[str], *, provider: str, timeout: float = 10, input_text: str | None = None):
    return subprocess.run(
        command,
        input=input_text,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=subscription_environment(provider),
    )


def claude_status() -> dict[str, Any]:
    path = find_claude()
    if not path:
        return {
            "id": "claude",
            "label": "Claude",
            "installed": False,
            "logged_in": False,
            "subscription": False,
            "repair": "Install Claude Code, then run `claude auth login`.",
        }
    result: dict[str, Any] = {
        "id": "claude",
        "label": "Claude",
        "installed": True,
        "path": path,
        "subscription": False,
    }
    try:
        version = _run([path, "--version"], provider="claude", timeout=5)
        result["version"] = (version.stdout or version.stderr).strip()[:300]
        auth = _run([path, "auth", "status", "--json"], provider="claude", timeout=8)
        payload = json.loads(auth.stdout) if auth.returncode == 0 else {}
        result.update(
            logged_in=bool(payload.get("loggedIn")),
            auth_method=payload.get("authMethod"),
            api_provider=payload.get("apiProvider"),
            plan=payload.get("subscriptionType"),
        )
        result["subscription"] = bool(
            result["logged_in"]
            and payload.get("authMethod") == "claude.ai"
            and payload.get("subscriptionType")
        )
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        result.update(logged_in=False, error=str(exc))
    result["ok"] = bool(result.get("subscription"))
    if result.get("logged_in") and not result["subscription"]:
        result["repair"] = "Sign into Claude Code with claude.ai; API-key sessions are intentionally blocked."
    elif not result.get("logged_in"):
        result["repair"] = "Run `claude auth login` and choose your Claude subscription."
    return result


def codex_status() -> dict[str, Any]:
    path = find_codex()
    if not path:
        return {
            "id": "codex",
            "label": "Codex",
            "installed": False,
            "logged_in": False,
            "subscription": False,
            "repair": "Install Codex CLI, then run `codex login`.",
        }
    result: dict[str, Any] = {
        "id": "codex",
        "label": "Codex",
        "installed": True,
        "path": path,
        "subscription": False,
    }
    try:
        version = _run([path, "--version"], provider="codex", timeout=5)
        result["version"] = (version.stdout or version.stderr).strip()[:300]
        auth = _run([path, "login", "status"], provider="codex", timeout=8)
        detail = (auth.stdout or auth.stderr).strip()[:500]
        result["auth_detail"] = detail
        result["logged_in"] = auth.returncode == 0 and "logged in" in detail.lower()
        result["subscription"] = result["logged_in"] and "chatgpt" in detail.lower()
        result["auth_method"] = "chatgpt" if result["subscription"] else "api-key-or-unknown"
    except (OSError, subprocess.SubprocessError) as exc:
        result.update(logged_in=False, error=str(exc))
    result["ok"] = bool(result.get("subscription"))
    if result.get("logged_in") and not result["subscription"]:
        result["repair"] = "Run `codex logout`, then `codex login` and choose ChatGPT subscription access."
    elif not result.get("logged_in"):
        result["repair"] = "Run `codex login` and choose Sign in with ChatGPT."
    return result


def claude_models() -> list[dict[str, Any]]:
    path = find_claude()
    if not path:
        return []
    try:
        result = _run([path, "--help"], provider="claude", timeout=8)
    except (OSError, subprocess.SubprocessError):
        return []
    help_text = result.stdout or result.stderr
    section = re.search(
        r"alias for the latest model(?P<aliases>.*?)(?:or a\s+model(?:'s)? full name)",
        help_text,
        re.IGNORECASE | re.DOTALL,
    )
    aliases = re.findall(r"['\"]([a-z][a-z0-9._-]*)['\"]", section.group("aliases")) if section else []
    aliases = sorted(set(aliases), key=lambda item: (_CLAUDE_ORDER.get(item, 99), item))
    return [
        {
            "id": alias,
            "label": _CLAUDE_LABELS.get(alias, f"Claude {alias.title()} (latest)"),
            "description": "Current subscription alias reported by the installed Claude CLI.",
            "efforts": ["low", "medium", "high", "xhigh", "max"],
            "default_effort": "medium",
        }
        for alias in aliases
    ]


def codex_models() -> list[dict[str, Any]]:
    path = find_codex()
    if not path:
        return []
    try:
        result = _run([path, "debug", "models"], provider="codex", timeout=15)
        payload = json.loads(result.stdout) if result.returncode == 0 else {}
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return []
    models: list[dict[str, Any]] = []
    for item in payload.get("models", []):
        if not isinstance(item, dict) or item.get("visibility") != "list" or not item.get("slug"):
            continue
        efforts = [
            level.get("effort")
            for level in item.get("supported_reasoning_levels", [])
            if isinstance(level, dict) and level.get("effort")
        ]
        models.append(
            {
                "id": str(item["slug"]),
                "label": str(item.get("display_name") or item["slug"]),
                "description": str(item.get("description") or ""),
                "efforts": efforts,
                "default_effort": str(item.get("default_reasoning_level") or "medium"),
            }
        )
    return models


def provider_catalogs() -> list[dict[str, Any]]:
    claude = claude_status()
    codex = codex_status()
    claude["models"] = claude_models()
    codex["models"] = codex_models()
    return [claude, codex]


def provider_status(provider: str) -> dict[str, Any]:
    return codex_status() if provider == "codex" else claude_status()
