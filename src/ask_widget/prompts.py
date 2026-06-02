"""Server-side prompt construction.

Two halves per request:
  * the **user prompt** (``build_user_prompt``) — the highlighted passage, its
    surrounding context, and the task; and
  * the **append-system-prompt** (``append_system_for``) — a per-action
    instruction appended to Claude's default system prompt via
    ``--append-system-prompt`` (confirmed to exist on CLI v2.1.160).
"""

from __future__ import annotations

SHARED_PREAMBLE = (
    "You are a reading companion answering a question about a passage the user "
    "highlighted in a document they are reading. A context folder's CLAUDE.md and "
    "files are available to you through the Read, Grep, and Glob tools. Be concise "
    "and answer in plain Markdown (no raw HTML)."
)

ELI5_APPEND = (
    SHARED_PREAMBLE
    + " Explain the highlighted passage in the simplest possible terms. A short, "
    "concrete analogy is welcome. Under 120 words, no preamble — start with the "
    "explanation."
)

PROVE_APPEND = (
    SHARED_PREAMBLE
    + " You MUST use Grep/Glob/Read to look for evidence in the context folder "
    "before answering — rely on the files, not your memory. Start with a one-line "
    "verdict (one of: Supported / Partially supported / Not supported / No evidence "
    "found), then give bullet points, each citing an exact path/to/file (with line "
    "numbers where useful). Under 200 words."
)

ASK_APPEND = (
    SHARED_PREAMBLE
    + " Answer the user's question directly and concretely. Cite folder file paths "
    "when you rely on them."
)

_APPENDS = {"eli5": ELI5_APPEND, "prove": PROVE_APPEND, "ask": ASK_APPEND}


def append_system_for(action: str) -> str:
    return _APPENDS.get(action, SHARED_PREAMBLE)


_HANDOFF_LABELS = {
    "eli5": "explain it simply (ELI5)",
    "prove": "fact-check it against the folder (Prove it)",
    "ask": "answer a question about it",
}


def build_handoff_prompt(
    folder: "Path",
    action: str,
    selection: str,
    question: str | None = None,
    answer: str | None = None,
) -> str:
    """Seed prompt for an 'Open in Claude' dedicated session, scoped to `folder`."""
    from pathlib import Path  # local import keeps module import-light

    name = Path(folder).name
    sel = (selection or "").strip()
    ans = (answer or "").strip()
    q = (question or "").strip()

    lines = [
        f"I was reading a document and highlighted a passage. Continue with me in a dedicated "
        f"session grounded in the **{name}** folder — you're running inside it, so its CLAUDE.md "
        f"and files are available to you (Read/Grep/Glob).",
        "",
        "Highlighted passage:",
        '"""',
        sel,
        '"""',
    ]
    if action == "ask" and q:
        lines += ["", f"My question was: {q}"]
    else:
        lines += ["", f"What I asked the reading widget to do: {_HANDOFF_LABELS.get(action, action)}."]
    if ans:
        lines += ["", "The quick answer it gave me was:", '"""', ans, '"""']
    lines += [
        "",
        "Pick up from here — verify it, go deeper, or take whatever next step I ask. "
        "You have full read access to this folder.",
    ]
    return "\n".join(lines)


def _conversation_block(history: "list[dict] | None") -> str | None:
    """Render prior Q&A turns as a transcript the model can read for context.

    ``history`` is a list of ``{"role": "user"|"assistant", "text": str}`` turns
    (oldest first), supplied by the widget when the user asks a follow-up in the
    answer panel. The runner is stateless (one ``claude -p`` per request), so the
    thread is reconstructed here rather than via session resume.
    """
    if not history:
        return None
    lines: list[str] = []
    for turn in history:
        if not isinstance(turn, dict):
            continue
        text = (turn.get("text") or "").strip()
        if not text:
            continue
        who = "You" if turn.get("role") == "assistant" else "Me"
        lines.append(f"{who}: {text}")
    if not lines:
        return None
    return "Conversation so far:\n" + "\n\n".join(lines)


def build_user_prompt(
    action: str,
    selection: str,
    context: str | None = None,
    question: str | None = None,
    history: "list[dict] | None" = None,
) -> str:
    sel = (selection or "").strip()
    ctx = (context or "").strip()

    blocks = ['Highlighted passage:\n"""\n' + sel + '\n"""']
    if ctx and ctx != sel:
        blocks.append('Surrounding text (for reference only):\n"""\n' + ctx + '\n"""')

    convo = _conversation_block(history)
    if convo:
        blocks.append(convo)

    if action == "eli5":
        blocks.append("Task: Explain the highlighted passage in the simplest terms (ELI5).")
    elif action == "prove":
        blocks.append(
            "Task: Fact-check the claim(s) in the highlighted passage against the "
            "context folder. Find supporting or contradicting evidence in the files."
        )
    elif action == "ask":
        q = (question or "").strip()
        label = "My follow-up question: " if convo else "Question about the highlighted passage: "
        blocks.append(label + q)

    return "\n\n".join(blocks)
