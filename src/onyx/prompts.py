"""Server-side prompt construction.

Two halves per request:
  * the **user prompt** (``build_user_prompt``) — the highlighted passage or
    page context, and the task; and
  * the **provider instruction** (``append_system_for``) — a per-action
    instruction passed to Claude as an appended system prompt and to Codex as a
    clearly delimited instruction block.
"""

from __future__ import annotations

SHARED_PREAMBLE = (
    "You are a reading companion answering a question about a document the user "
    "is reading, sometimes about a highlighted passage. A context folder's CLAUDE.md and "
    "files are available to you through the Read, Grep, and Glob tools. Be concise "
    "and answer in plain Markdown (no raw HTML)."
)

# Settings → Answers → "Check outside facts on the web" picks one of these two.
CHECK_ON_THE_WEB = (
    " Where you have them, you can also search the web, fetch a web page, or load "
    "a relevant skill. The folder is the source for anything about the document "
    "itself. Before you state a fact from outside it that could be wrong or out of "
    "date (how an API, library, or product behaves; versions, prices, dates, "
    "recent events), check it with one of those tools and cite the URL or skill "
    "you used. Settled general knowledge needs no check."
)

CHECK_OFFLINE = (
    " Web lookups are off for speed, so don't try to reach the web, directly or "
    "through a subagent. The folder is the source for anything about the document "
    "itself; when a relevant skill covers a fact from outside it, check it there "
    "and cite the skill."
)

NO_HEDGING = (
    " Never say you are answering from memory, and never tell the reader to check "
    "something themselves: do the check. Don't announce the checks either; the "
    "citations show them. If a check comes up empty, say which specific point you "
    "could not confirm."
)

ELI5_APPEND = (
    " Explain the highlighted passage in the simplest possible terms. A short, "
    "concrete analogy is welcome. Under 120 words, no preamble — start with the "
    "explanation."
)

PROVE_APPEND = (
    " You MUST use Grep/Glob/Read to look for evidence in the context folder "
    "before answering — rely on the files, not your memory. If the files cannot "
    "settle a claim about the outside world, check it with the other tools you "
    "have. Start with a one-line verdict (one of: Supported / Partially supported "
    "/ Not supported / No evidence found), then give bullet points, each citing an "
    "exact path/to/file (with line numbers where useful), URL, or skill. Under 200 "
    "words."
)

ASK_APPEND = (
    " Answer the user's question directly and concretely. Cite folder file paths "
    "when you rely on them."
)

_APPENDS = {"eli5": ELI5_APPEND, "prove": PROVE_APPEND, "ask": ASK_APPEND}

_STYLE_APPEND = {
    "concise": " Keep the answer compact and omit unnecessary preamble.",
    "balanced": " Give enough explanation to make the answer self-contained.",
    "detailed": " Be thorough, explain reasoning, and include useful supporting detail.",
}


def append_system_for(action: str, response_style: str = "concise", web: bool = True) -> str:
    return (
        SHARED_PREAMBLE
        + (CHECK_ON_THE_WEB if web else CHECK_OFFLINE)
        + NO_HEDGING
        + _APPENDS.get(action, "")
        + _STYLE_APPEND.get(response_style, "")
    )


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
    context: str | None = None,
) -> str:
    """Seed prompt for a dedicated provider session scoped to ``folder``."""
    from pathlib import Path  # local import keeps module import-light

    name = Path(folder).name
    sel = (selection or "").strip()
    ans = (answer or "").strip()
    q = (question or "").strip()

    lines = [
        f"I was reading a document and {'highlighted a passage' if sel else 'asked about the page'}. Continue with me in a dedicated "
        f"session grounded in the **{name}** folder — you're running inside it, so its CLAUDE.md "
        f"and files are available to you (Read/Grep/Glob).",
    ]
    if sel:
        lines += ["", "Highlighted passage:", '"""', sel, '"""']
    elif context:
        lines += ["", "Page text (excerpt):", '"""', context[:4000], '"""']
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
    answer panel. The runner is stateless (one headless CLI run per request), so the
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
    document_source: str | None = None,
    document_page: int | None = None,
) -> str:
    sel = (selection or "").strip()
    ctx = (context or "").strip()

    blocks: list[str] = []
    if document_source:
        location = f"Source document: {document_source}"
        if document_page:
            location += f" (page {document_page})"
        blocks.append(location)
    if sel:
        blocks.append('Highlighted passage:\n"""\n' + sel + '\n"""')
        if ctx and ctx != sel:
            blocks.append('Surrounding text (for reference only):\n"""\n' + ctx + '\n"""')
    elif ctx:
        blocks.append('Page text (excerpt):\n"""\n' + ctx + '\n"""')

    convo = _conversation_block(history)
    if convo:
        blocks.append(convo)

    if action == "eli5":
        blocks.append("Task: Explain the highlighted passage in the simplest terms (ELI5).")
    elif action == "prove":
        blocks.append(
            "Task: Fact-check the claim(s) in the highlighted passage against the "
            "context folder. Find supporting or contradicting evidence in the files "
            "first, then check anything they cannot settle."
        )
    elif action == "ask":
        q = (question or "").strip()
        label = "My follow-up question: " if convo else (
            "Question about the highlighted passage: " if sel else "Question about this document: "
        )
        blocks.append(label + q)

    return "\n\n".join(blocks)
