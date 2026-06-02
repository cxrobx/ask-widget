"""Open a dedicated `claude` session in a terminal, scoped to a folder.

Mirrors cxmail's "Open in Claude": write a seed prompt + a launch script, then
open a terminal running ``claude "$(cat prompt.txt)"`` cd'd into the context
folder (so the session gets that folder's CLAUDE.md + file access). Ghostty is
preferred (same as cxmail); iTerm / Terminal.app are fallbacks.
"""

from __future__ import annotations

import subprocess
import uuid
from pathlib import Path

_TERMINALS = [
    ("/Applications/Ghostty.app", "ghostty"),
    (str(Path.home() / "Applications/Ghostty.app"), "ghostty"),
    ("/Applications/iTerm.app", "open"),
    ("/System/Applications/Utilities/Terminal.app", "open"),
    ("/Applications/Utilities/Terminal.app", "open"),
]

_CLAUDE_FALLBACKS = [
    "/opt/homebrew/bin/claude",
    "/usr/local/bin/claude",
    str(Path.home() / ".local/bin/claude"),
    str(Path.home() / ".claude/local/claude"),
    str(Path.home() / ".bun/bin/claude"),
]


def find_claude() -> str | None:
    # GUI apps don't inherit the shell PATH; ask a login shell first.
    try:
        out = subprocess.run(
            ["/bin/zsh", "-lc", "command -v claude"], capture_output=True, text=True, timeout=5
        )
        if out.returncode == 0:
            p = out.stdout.strip()
            if p and Path(p).exists():
                return p
    except Exception:
        pass
    for c in _CLAUDE_FALLBACKS:
        if Path(c).exists():
            return c
    return None


def _find_terminal() -> tuple[str, str] | None:
    for path, kind in _TERMINALS:
        if Path(path).exists():
            return path, kind
    return None


def _shq(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


def open_in_claude(folder: Path, prompt: str) -> None:
    """Launch a terminal running `claude` in `folder` with `prompt`. Raises on failure."""
    claude = find_claude()
    if not claude:
        raise RuntimeError(
            "The `claude` CLI wasn't found. Install Claude Code "
            "(npm install -g @anthropic-ai/claude-code)."
        )
    term = _find_terminal()
    if not term:
        raise RuntimeError("No terminal found (Ghostty / iTerm / Terminal).")
    term_path, kind = term

    scratch = Path.home() / ".ask-widget" / "sessions" / uuid.uuid4().hex[:8]
    scratch.mkdir(parents=True, exist_ok=True)
    prompt_file = scratch / "prompt.txt"
    prompt_file.write_text(prompt, encoding="utf-8")

    launch = scratch / "launch.command"
    # `--strict-mcp-config` (with no `--mcp-config`) starts the session with zero
    # MCP servers, so cd'ing into a project folder no longer triggers Claude's
    # "N new MCP servers found in this project — confirm" approval screen. This is
    # a lightweight reading-companion handoff; it doesn't need the user's MCP fleet.
    launch.write_text(
        "#!/bin/zsh -l\nset -e\ncd {folder}\nexec {claude} --strict-mcp-config \"$(cat {pf})\"\n".format(
            folder=_shq(str(folder)), claude=_shq(claude), pf=_shq(str(prompt_file))
        ),
        encoding="utf-8",
    )
    launch.chmod(0o755)

    if kind == "ghostty":
        # `open -na` forces a NEW Ghostty instance (so our `-e` command is honored
        # even when Ghostty is already running). On macOS a fresh instance also
        # RESTORES the previous session's saved windows — so the user got the
        # restored window(s) *plus* our command window (the duplicate tab).
        # `--window-save-state=never` is a per-launch override that tells this
        # instance not to restore or save state, so exactly one tab opens. It does
        # not touch the user's config or their primary Ghostty instance.
        # See cxmail claude_handoff.rs / ghostty-org/ghostty#9612.
        cmd = [
            "/usr/bin/open", "-na", term_path, "--args",
            "--window-save-state=never",
            f"--working-directory={folder}", "-e", str(launch),
        ]
    else:  # iTerm / Terminal.app run an executable .command when opened
        cmd = ["/usr/bin/open", "-a", term_path, str(launch)]

    subprocess.Popen(
        cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
