"""Extract validated, clickable evidence references from streamed answers."""

from __future__ import annotations

import html
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any


_FILE_REF = re.compile(
    r"(?P<path>(?:~?/|\.\.?/)?(?:[A-Za-z0-9_@+ .-]+/)*[A-Za-z0-9_@+.-]+\.[A-Za-z0-9]{1,10})"
    # The line as `path:12` or `path#L12`, or in words straight after the path: "`path` (line 12)", "path, lines 12-14".
    r"(?:(?::|#L)(?P<line>\d+)|`?,?\s*\(?\s*\blines?\s+(?P<line_word>\d+))?"
)
_PAGE_REF = re.compile(r"\b(?:page|p\.)\s+(\d{1,5})\b", re.IGNORECASE)
_TEXT_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".rs", ".go", ".java", ".kt",
    ".swift", ".c", ".h", ".cpp", ".hpp", ".css", ".scss", ".html", ".htm",
    ".md", ".markdown", ".txt", ".toml", ".yaml", ".yml", ".json", ".xml",
    ".sh", ".zsh", ".bash", ".sql", ".ini", ".cfg", ".conf",
}
_HTML = {".html", ".htm"}
_MARKDOWN = {".md", ".markdown"}
_READABLE = _HTML | _MARKDOWN | {".txt"}  # what Onyx's reader shows, so evidence can land inside it
# Enough letters and digits for the reader to find a passage by. A cited line with fewer (bare markup, a short
# heading) reads on into the next few.
_PASSAGE_MIN = 24
_PASSAGE_LINES = 6
_PASSAGE_CHARS = 600
_TAG = re.compile(r"<[^>]*>")
# An id attribute, not `el.id="x"` in a script or `[id="x"]` in a stylesheet.
_ID_ATTR = re.compile(r"""(?<![\w.:\[-])id\s*=\s*["']([^"'\s>]+)["']""", re.IGNORECASE)
_MD_MARKS = (
    (re.compile(r"!?\[\[[^\]|]*\|([^\]]*)\]\]"), r"\1"),  # [[Note|alias]] shows its alias
    (re.compile(r"!?\[\[([^\]]*)\]\]"), r"\1"),
    (re.compile(r"!?\[([^\]]*)\]\([^)]*\)"), r"\1"),  # [text](url) shows its text
    (re.compile(r"^\s{0,3}(?:#{1,6}\s+|>\s*|[-*+]\s+(?:\[[ xX]\]\s+)?|\d+[.)]\s+)"), ""),
    (re.compile(r"\*\*|__|==|~~|`"), ""),
)


def _inside(path: Path, root: Path) -> bool:
    try:
        return path == root or path.is_relative_to(root)
    except ValueError:
        return False


def _read_lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []


def _html_text(line: str) -> str:
    """A source line of HTML as the words it shows: tags gone (one cut off at either end too), entities decoded."""
    close, opening = line.find(">"), line.find("<")
    if close != -1 and (opening == -1 or close < opening):
        line = line[close + 1:]  # the end of a tag begun on the line above
    line = re.sub(r"<[^>]*$", "", line)  # a tag that runs on to the next line
    text = " ".join(html.unescape(_TAG.sub(" ", line)).split())
    # A tag stripped to a space leaves "built )." — close punctuation back up.
    return re.sub(r"([(\[“‘])\s+", r"\1", re.sub(r"\s+([,.;:!?)\]”’])", r"\1", text))


def _markdown_text(line: str) -> str:
    for pattern, replacement in _MD_MARKS:
        line = pattern.sub(replacement, line)
    return " ".join(line.split())


def _plain_text(line: str) -> str:
    return " ".join(line.split())


def _passage(lines: list[str], target: int, suffix: str) -> str:
    """The words a cited line shows, read on into the next lines while there are too few to find it by."""
    read = _html_text if suffix in _HTML else _markdown_text if suffix in _MARKDOWN else _plain_text
    words: list[str] = []
    for number in range(target, min(len(lines), target + _PASSAGE_LINES - 1) + 1):
        if text := read(lines[number - 1]):
            words.append(text)
        if sum(ch.isalnum() for ch in "".join(words)) >= _PASSAGE_MIN:
            break
    return " ".join(words)[:_PASSAGE_CHARS]


def _snippet(path: Path, line: int | None) -> tuple[str, int | None]:
    suffix = path.suffix.lower()
    if suffix not in _TEXT_EXTENSIONS:
        return "", line
    lines = _read_lines(path)
    if not lines:
        return "", line
    if suffix in _HTML:
        # A page is read in Onyx, never as source: its preview is the words at the cited line, and with no line to
        # point at there is none (a page's first lines are only <!DOCTYPE html> and <head>).
        if not line:
            return "", None
        target = min(max(line, 1), len(lines))
        return _passage(lines, target, suffix), target
    target = min(max(line or 1, 1), len(lines))
    start = max(1, target - 2)
    end = min(len(lines), target + 2)
    text = "\n".join(f"{number:>5}  {lines[number - 1]}" for number in range(start, end + 1))
    return text[:2400], target


def extract_citations(
    answer: str, folder: Path, *, document_source: str | None = None, max_items: int = 12
) -> list[dict[str, Any]]:
    root = folder.resolve()
    citations: list[dict[str, Any]] = []
    seen: set[tuple[str, int | None]] = set()
    for match in _FILE_REF.finditer(answer):
        raw = match.group("path").strip("`'\"()[]{}.,")
        if not raw or raw.startswith(("http://", "https://")):
            continue
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = root / candidate
        try:
            path = candidate.resolve()
        except (OSError, RuntimeError, ValueError):
            continue
        if not _inside(path, root) or not path.is_file():
            continue
        number = match.group("line") or match.group("line_word")
        line = int(number) if number else None
        key = (str(path), line)
        if key in seen:
            continue
        seen.add(key)
        cited = line
        snippet, line = _snippet(path, line)
        item: dict[str, Any] = {
            "kind": "file",
            "label": str(path.relative_to(root)) + (f":{line}" if line else ""),
            "path": str(path),
            "line": line,
            "snippet": snippet,
        }
        if cited and path.suffix.lower() in _READABLE:
            # The words the answer pointed at, kept with it: a page edited later moves its lines, not its words.
            target = reader_target(path, line=cited)
            item["text"], item["anchor"] = target["text"], target["anchor"]
        citations.append(item)
        if len(citations) >= max_items:
            return citations

    if document_source and Path(document_source).suffix.lower() == ".pdf":
        try:
            pdf_path = Path(document_source).expanduser().resolve()
        except (OSError, RuntimeError, ValueError):
            pdf_path = None
        if pdf_path and pdf_path.is_file():
            for match in _PAGE_REF.finditer(answer):
                page = int(match.group(1))
                key = (str(pdf_path), page)
                if key in seen:
                    continue
                seen.add(key)
                citations.append(
                    {
                        "kind": "pdf-page",
                        "label": f"{pdf_path.name} - page {page}",
                        "path": str(pdf_path),
                        "page": page,
                        "line": None,
                        "snippet": "",
                    }
                )
                if len(citations) >= max_items:
                    break
    return citations


def reader_target(path: Path, *, line: int | None = None, page: int | None = None) -> dict[str, Any]:
    """Where Onyx's reader lands a citation: the words at its line, the nearest id above it, or its PDF page.

    The reader finds the words on the rendered page, so markup never has to line up with source lines; the id is the
    fallback for when they can't be found or are out of sight.
    """
    target: dict[str, Any] = {"text": "", "anchor": None, "page": page or None}
    suffix = path.suffix.lower()
    if not line or suffix == ".pdf":
        return target
    lines = _read_lines(path)
    if not lines:
        return target
    number = min(max(line, 1), len(lines))
    target["text"] = _passage(lines, number, suffix)
    if suffix in _HTML:
        for above in range(number, 0, -1):
            if ids := _ID_ATTR.findall(lines[above - 1]):
                target["anchor"] = ids[-1]
                break
    return target


def open_source(path: Path, *, line: int | None = None, page: int | None = None) -> None:
    """Open a validated source in the user's editor, or the default macOS app."""
    if not path.exists() or not path.is_file():
        raise RuntimeError(f"Source no longer exists: {path}")
    if path.suffix.lower() == ".pdf" and page:
        subprocess.Popen(
            ["/usr/bin/open", str(path)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return
    if line:
        for command in ("code", "cursor"):
            executable = shutil.which(command)
            if executable:
                subprocess.Popen(
                    [executable, "--goto", f"{path}:{line}"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return
    subprocess.Popen(
        ["/usr/bin/open", str(path)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def safe_tool_trace(tool: str, payload: object) -> dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    allowed_keys = ("file_path", "path", "pattern", "glob", "query", "command", "url", "skill")
    clean = {key: str(data[key])[:1000] for key in allowed_keys if data.get(key) is not None}
    return {"tool": tool, "input": clean}
