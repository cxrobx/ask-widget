"""A bounded, vault-scoped snapshot of Obsidian's computed reading styles.

Only known elements and inert CSS properties cross the plugin boundary. Theme
stylesheets, selectors, URLs, and scripts are never imported into the reader.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any


SELECTORS = {
    "content": "", "p": "p", **{f"h{i}": f"h{i}" for i in range(1, 7)},
    "a": "a", "internal-link": "a.askw-wikilink", "strong": "strong", "em": "em",
    "ul": "ul", "ol": "ol", "li": "li", "blockquote": "blockquote",
    "pre": "pre", "code": ":not(pre) > code", "pre-code": "pre code",
    "table": "table", "th": "th", "td": "td", "hr": "hr",
    "row-odd": "tbody tr:nth-child(odd)", "row-even": "tbody tr:nth-child(even)",
}
PROPERTIES = frozenset({
    "color", "background-color", "font-family", "font-size", "font-weight",
    "font-style", "line-height", "letter-spacing", "text-transform",
    "text-decoration-line", "text-decoration-color", "text-decoration-thickness",
    "text-underline-offset", "border-collapse", "border-spacing", "border-radius",
    "list-style-type", "max-width",
    *(f"{kind}-{side}" for kind in ("margin", "padding") for side in ("top", "right", "bottom", "left")),
    *(f"border-{side}-{kind}" for side in ("top", "right", "bottom", "left") for kind in ("color", "width", "style")),
})
# Computed values can include quoted font names and modern color functions.
# Reject escapes, rule/declaration delimiters, comments and resource functions.
_UNSAFE = re.compile(r"[;{}<>\\\x00-\x1f]|/\*|\*/|(?:url|var|env|attr|expression)\s*\(", re.I)
MAX_SNAPSHOT_BYTES = 64 * 1024
# The reader kinds that wear the vault's reading styles: notes, and the other pages Onyx lays out itself. HTML is
# authored and keeps its own look.
KINDS = ("markdown", "text", "pdf", "selection")


def validate_snapshot(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("mode") not in ("light", "dark"):
        raise ValueError("Reading theme must specify light or dark mode.")
    styles = value.get("styles")
    if not isinstance(styles, dict) or not styles or set(styles) - SELECTORS.keys():
        raise ValueError("Reading theme contains unknown elements.")
    if not isinstance(styles.get("content"), dict) or not styles["content"].get("color"):
        raise ValueError("Reading theme is missing its content styles.")
    clean: dict[str, dict[str, str]] = {}
    for element, declarations in styles.items():
        if not isinstance(declarations, dict) or set(declarations) - PROPERTIES:
            raise ValueError("Reading theme contains unsupported CSS properties.")
        clean[element] = {}
        for prop, raw in declarations.items():
            if not isinstance(raw, str) or not raw.strip() or len(raw) > 512 or _UNSAFE.search(raw):
                raise ValueError("Reading theme contains an unsafe CSS value.")
            clean[element][prop] = raw.strip()
    result = {"mode": value["mode"], "styles": clean}
    if len(json.dumps(result).encode()) > MAX_SNAPSHOT_BYTES:
        raise ValueError("Reading theme is too large.")
    return result


def stylesheet(snapshot: dict[str, Any] | None) -> str:
    if not snapshot:
        return ""
    snapshot = validate_snapshot(snapshot)
    body = ":is(" + ",".join(f'body[data-askw-document-kind="{kind}"]' for kind in KINDS) + ")"
    main = body + " > main"
    background = snapshot["styles"]["content"].get("background-color", "transparent")
    rules = [
        f'{body}{{background-color:{background};color-scheme:{snapshot["mode"]};backdrop-filter:none}}',
        f"{main}{{box-sizing:content-box;min-height:100vh;padding:48px clamp(24px,5vw,48px);"
        "border:0;box-shadow:none;backdrop-filter:none;overflow-wrap:break-word}",
        f"{main} table{{width:auto}}",
    ]
    for key, selector in SELECTORS.items():
        declarations = snapshot["styles"].get(key, {})
        if declarations:
            css = ";".join(f"{prop}:{value}" for prop, value in sorted(declarations.items()))
            rules.append(f'{main}{" " + selector if selector else ""}{{{css}}}')
    return "\n".join(rules)


def revision(css: str) -> str:
    return hashlib.sha256(css.encode()).hexdigest()[:20]
