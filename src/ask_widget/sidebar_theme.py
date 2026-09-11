"""A bounded, vault-scoped snapshot of Obsidian's file explorer, for the vault sidebar.

The Obsidian plugin measures the explorer after the vault's theme and snippets
resolve: the pane, folder and file rows, the open file, hover, chevrons, the
indent guides, the search box, and each top-level folder's own colour (a
rainbow snippet colours them one by one). The same safety rules as the reading
theme apply (see markdown_theme): known elements and inert CSS properties only,
never a selector, URL, variable or escape from the theme itself.

``stylesheet`` scopes everything under ``body.obsidian-tree`` — the vault shell
wears that class only while a snapshot is in force — and switches the tree to
Obsidian's shape: chevrons instead of folder icons, indent guides, its spacing.
Folder colours travel separately, as a list the shell hands to each top-level
folder by name (Notes) or by position (Artifacts, whose names never match).
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from .markdown_theme import _UNSAFE

TYPE = ("color", "font-family", "font-size", "font-weight", "font-style", "letter-spacing", "line-height", "text-transform")
ELEMENTS: dict[str, frozenset[str]] = {
    "pane": frozenset({"color", "background-color", "font-family", "font-size", "font-weight", "letter-spacing", "line-height"}),
    "folder": frozenset({*TYPE, "padding-top", "padding-bottom", "border-radius"}),
    "file": frozenset({*TYPE, "padding-top", "padding-bottom", "border-radius"}),
    "active": frozenset({"color", "background-color", "font-weight", "border-radius"}),
    "hover": frozenset({"color", "background-color"}),
    "chevron": frozenset({"color", "opacity"}),
    "guide": frozenset({"border-left-color", "border-left-width", "border-left-style"}),
    "search": frozenset({
        "color", "background-color", "font-family", "font-size", "border-radius",
        *(f"border-{side}-{kind}" for side in ("top", "right", "bottom", "left") for kind in ("color", "width", "style")),
    }),
}
MAX_FOLDERS = 256
MAX_SNAPSHOT_BYTES = 64 * 1024
SCOPE = "body.obsidian-tree"

# The shell's own light/dark tokens (launcher_ui.theme_style), so the sidebar's
# chrome — brand, switch, filter, footer — reads on Obsidian's pane whatever the
# app theme is: a dark app over a cream explorer must not draw white on cream.
MODE_TOKENS = {
    "light": "--ink:13 13 13;--secondary:93 93 93;--muted:143 143 143;--faint:175 175 175;--line:rgb(0 0 0/.10);"
    "--line-soft:rgb(0 0 0/.055);--bg-input:255 255 255;--bg-elevated:255 255 255;--selected:rgb(0 0 0/.07)",
    "dark": "--ink:255 255 255;--secondary:205 205 205;--muted:175 175 175;--faint:143 143 143;--line:rgb(255 255 255/.15);"
    "--line-soft:rgb(255 255 255/.06);--bg-input:45 45 45;--bg-elevated:45 45 45;--selected:rgb(255 255 255/.10)",
}
_RGB = re.compile(r"^rgba?\(\s*(\d{1,3})[\s,]+(\d{1,3})[\s,]+(\d{1,3})")


def _value(raw: Any, what: str) -> str:
    if not isinstance(raw, str) or not raw.strip() or len(raw) > 512 or _UNSAFE.search(raw):
        raise ValueError(f"Sidebar theme contains an unsafe {what}.")
    return raw.strip()


def validate_snapshot(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("mode") not in ("light", "dark"):
        raise ValueError("Sidebar theme must specify light or dark mode.")
    styles = value.get("styles")
    if not isinstance(styles, dict) or set(styles) - ELEMENTS.keys():
        raise ValueError("Sidebar theme contains unknown elements.")
    if not isinstance(styles.get("file"), dict) or not styles["file"].get("color"):
        raise ValueError("Sidebar theme is missing its file styles.")
    clean: dict[str, dict[str, str]] = {}
    for element, declarations in styles.items():
        if not isinstance(declarations, dict) or set(declarations) - ELEMENTS[element]:
            raise ValueError("Sidebar theme contains unsupported CSS properties.")
        clean[element] = {prop: _value(raw, "CSS value") for prop, raw in declarations.items()}
    folders = value.get("folders", [])
    if not isinstance(folders, list) or len(folders) > MAX_FOLDERS:
        raise ValueError("Sidebar theme has an invalid folder list.")
    tints: list[dict[str, str]] = []
    for folder in folders:
        if not isinstance(folder, dict) or set(folder) - {"name", "color", "guide"}:
            raise ValueError("Sidebar theme has an invalid folder entry.")
        name = folder.get("name")
        if not isinstance(name, str) or not name or len(name) > 255 or re.search(r"[\x00-\x1f]", name):
            raise ValueError("Sidebar theme has an invalid folder name.")
        tint = {"name": name, "color": _value(folder.get("color"), "folder colour")}
        if folder.get("guide") is not None:
            tint["guide"] = _value(folder["guide"], "guide colour")
        tints.append(tint)
    result = {"mode": value["mode"], "styles": clean, "folders": tints}
    if len(json.dumps(result).encode()) > MAX_SNAPSHOT_BYTES:
        raise ValueError("Sidebar theme is too large.")
    return result


def _decls(declarations: dict[str, str], *only: str) -> str:
    return ";".join(f"{prop}:{value}" for prop, value in sorted(declarations.items()) if not only or prop in only)


def _triplet(color: str) -> str:
    """``rgb(253, 246, 227)`` → ``253 246 227`` for the shell's ``rgb(var(--ink)/a)`` tokens; "" if not plain RGB."""
    match = _RGB.match(color or "")
    return " ".join(match.groups()) if match else ""


def stylesheet(snapshot: dict[str, Any] | None) -> str:
    if not snapshot:
        return ""
    snapshot = validate_snapshot(snapshot)
    styles, mode = snapshot["styles"], snapshot["mode"]
    pane, folder, file = styles.get("pane", {}), styles.get("folder", {}), styles["file"]
    ink = _triplet(pane.get("color", "")) or _triplet(file["color"])
    tokens = MODE_TOKENS[mode] + (f";--ink:{ink}" if ink else "")
    background = pane.get("background-color", "")
    s = SCOPE
    rules = [
        f"{s} aside{{{tokens};color:rgb(var(--ink));color-scheme:{mode}"
        + (f";background:{background};backdrop-filter:none;-webkit-backdrop-filter:none" if background else "") + "}",
        f"{s} #tree{{{_decls(pane, 'font-family', 'font-size', 'font-weight', 'letter-spacing', 'line-height')}}}",
        # Obsidian's shape: a chevron that turns, not a folder; rows flush; guides down the left.
        f"{s} #tree .fold{{display:none}} {s} #tree .chev{{display:block}}",
        f"{s} #tree summary,{s} #tree .file{{gap:6px;min-height:0;margin:0;padding:4px 8px}} {s} #tree .file{{padding-left:28px}}",
        f"{s} #tree summary{{{_decls(folder, *TYPE[1:], 'padding-top', 'padding-bottom', 'border-radius')};color:var(--folder-color,{folder.get('color', 'inherit')})}}",
        f"{s} #tree .file{{{_decls(file, *TYPE, 'padding-top', 'padding-bottom', 'border-radius')}}}",
        f"{s} #tree ul ul{{margin-left:15px;padding-left:6px;border-left:"
        f"{styles.get('guide', {}).get('border-left-width', '1px')} {styles.get('guide', {}).get('border-left-style', 'solid')} "
        f"var(--guide-color,{styles.get('guide', {}).get('border-left-color', 'currentColor')})}}",
        f"{s} #tree .chev{{color:var(--folder-color,{styles.get('chevron', {}).get('color', 'currentColor')})"
        + (f";opacity:{styles['chevron']['opacity']}" if "opacity" in styles.get("chevron", {}) else "") + "}",
    ]
    hover = styles.get("hover", {})
    if hover:
        rules.append(f"{s} #tree .file:hover,{s} #tree summary:hover{{{_decls(hover, 'background-color')}}}")
        if "color" in hover:
            rules.append(f"{s} #tree .file:hover{{color:{hover['color']}}}")
    if styles.get("active"):
        rules.append(f"{s} #tree a.file.active{{{_decls(styles['active'])}}}")
    if styles.get("search"):
        rules.append(f"{s} #vault-filter{{{_decls(styles['search'])}}}")
    return "\n".join(rules)


def folder_tints(snapshot: dict[str, Any] | None) -> list[dict[str, str]]:
    return validate_snapshot(snapshot)["folders"] if snapshot else []


def revision(css: str, folders: list[dict[str, str]]) -> str:
    return hashlib.sha256((css + json.dumps(folders, sort_keys=True)).encode()).hexdigest()[:20]
