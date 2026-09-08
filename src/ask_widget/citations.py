"""Extract validated, clickable evidence references from streamed answers."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any


_FILE_REF = re.compile(
    r"(?P<path>(?:~?/|\.\.?/)?(?:[A-Za-z0-9_@+ .-]+/)*[A-Za-z0-9_@+.-]+\.[A-Za-z0-9]{1,10})"
    r"(?:(?::|#L)(?P<line>\d+))?"
)
_PAGE_REF = re.compile(r"\b(?:page|p\.)\s+(\d{1,5})\b", re.IGNORECASE)
_TEXT_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".rs", ".go", ".java", ".kt",
    ".swift", ".c", ".h", ".cpp", ".hpp", ".css", ".scss", ".html", ".htm",
    ".md", ".markdown", ".txt", ".toml", ".yaml", ".yml", ".json", ".xml",
    ".sh", ".zsh", ".bash", ".sql", ".ini", ".cfg", ".conf",
}


def _inside(path: Path, root: Path) -> bool:
    try:
        return path == root or path.is_relative_to(root)
    except ValueError:
        return False


def _snippet(path: Path, line: int | None) -> tuple[str, int | None]:
    if path.suffix.lower() not in _TEXT_EXTENSIONS:
        return "", line
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return "", line
    if not lines:
        return "", line
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
        line = int(match.group("line")) if match.group("line") else None
        key = (str(path), line)
        if key in seen:
            continue
        seen.add(key)
        snippet, line = _snippet(path, line)
        citations.append(
            {
                "kind": "file",
                "label": str(path.relative_to(root)) + (f":{line}" if line else ""),
                "path": str(path),
                "line": line,
                "snippet": snippet,
            }
        )
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
    allowed_keys = ("file_path", "path", "pattern", "glob", "query", "command")
    clean = {key: str(data[key])[:1000] for key in allowed_keys if data.get(key) is not None}
    return {"tool": tool, "input": clean}
