from __future__ import annotations

import re
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

from ask_widget.config import AppConfig
from ask_widget.vault_ui import vault_page

LAUNCHER_SWIFT = Path(__file__).resolve().parent.parent / "launcher" / "AskWidget.swift"

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}

# What a mouse-down lands on and expects to keep: anything that takes a click, a caret, or a drag of its own.
# The page's own skip list is read off the page; this is the independent statement of what has to be in it.
INTERACTIVE_TAGS = {"a", "button", "input", "textarea", "select", "summary", "label", "option", "details"}
INTERACTIVE_ROLES = {"button", "tab", "link", "textbox", "checkbox", "menuitem", "separator"}


def _matches(selector_tokens: list[str], tag: str, attrs: dict[str, str]) -> bool:
    for token in selector_tokens:
        if token.startswith("["):
            name, _, value = token[1:-1].partition("=")
            if name in attrs and (not value or attrs[name] == value.strip("\"'")):
                return True
        elif token == tag:
            return True
    return False


class _Elements(HTMLParser):
    """Every element with the chain of open tags above it, as `closest()` would walk."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, dict[str, str]]] = []
        self.elements: list[list[tuple[str, dict[str, str]]]] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        node = (tag, {k: (v if v is not None else "") for k, v in attrs})
        self.elements.append([node, *reversed(self.stack)])
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs) -> None:
        node = (tag, {k: (v if v is not None else "") for k, v in attrs})
        self.elements.append([node, *reversed(self.stack)])

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                return


def _shell() -> str:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw).resolve()
        config = AppConfig(default_folder=root, allowed_roots=(root,), port=8899, data_dir=root / "data")
        return vault_page(config, None, kind="html")


class WindowDragTests(unittest.TestCase):
    """The window drags by its chrome (T: 'I can only drag it on the thin edge').

    A WebView swallows the mouse, so the page hands the mouse-down back to the
    launcher and AppKit drags the window by it. That splits the shell in two —
    chrome that moves the window, controls that keep their click — and the split
    is an attribute on markup nobody re-reads. These check it mechanically.
    """

    def setUp(self) -> None:
        self.page = _shell()
        keep = re.search(r"const DRAG_KEEP='([^']+)'", self.page)
        self.assertIsNotNone(keep, "the shell no longer carries a drag skip list")
        self.tokens = [token.strip() for token in keep.group(1).split(",")]
        self.elements = _Elements()
        self.elements.feed(self.page)

    def _drags(self, chain: list[tuple[str, dict[str, str]]]) -> bool:
        """`closest('[data-drag]') && !closest(DRAG_KEEP)`, the listener's own test."""
        region = any("data-drag" in attrs for _, attrs in chain)
        kept = any(_matches(self.tokens, tag, attrs) for tag, attrs in chain)
        return region and not kept

    def test_the_bridge_the_shell_posts_to_is_one_the_launcher_answers(self) -> None:
        swift = LAUNCHER_SWIFT.read_text(encoding="utf-8")
        registered = set(re.findall(r'name: "(askw\w+)"', swift))
        answered = set(re.findall(r'message\.name == "(askw\w+)"', swift))
        posted = set(re.findall(r"messageHandlers\.(askw\w+)", self.page))

        self.assertIn("askwDrag", posted)
        self.assertLessEqual(posted, registered)
        # askwPick is the fall-through at the end of the dispatch, so it is registered but never compared.
        self.assertLessEqual(registered - answered, {"askwPick"})

    def test_the_chrome_drags_and_the_lists_and_grip_do_not(self) -> None:
        regions, inert = {}, {}
        for chain in self.elements.elements:
            tag, attrs = chain[0]
            name = attrs.get("id") or attrs.get("class") or tag
            if "data-drag" in attrs:
                regions[name] = chain
            if "data-nodrag" in attrs:
                inert[name] = chain

        # The three panes of chrome: the sidebar (its top strip sits under the traffic lights), the
        # library's home page, and the outline panel.
        self.assertEqual(set(regions), {"vault-side", "home", "outline-side"})
        # A list is not chrome, the context-folder disclosure is a form, and the grip resizes the
        # sidebar with a drag of its own.
        self.assertEqual(set(inert), {"tree", "outline", "related", "side-grip", "open-context"})
        for name, chain in inert.items():
            self.assertFalse(self._drags(chain), f"#{name} would drag the window")
        for name, chain in regions.items():
            self.assertTrue(self._drags(chain), f"#{name} no longer drags the window")

    def test_no_control_inside_the_chrome_loses_its_click(self) -> None:
        # The failure class: a button or a field added to a drag region stops responding, because the
        # window drag eats the mouse-down. Every control has to be named by the skip list.
        lost = []
        for chain in self.elements.elements:
            tag, attrs = chain[0]
            interactive = (
                tag in INTERACTIVE_TAGS
                or attrs.get("role", "") in INTERACTIVE_ROLES
                or "tabindex" in attrs
                or "contenteditable" in attrs
                or attrs.get("draggable") == "true"
            )
            if interactive and self._drags(chain):
                lost.append(f"<{tag} {attrs}>")
        self.assertEqual(lost, [])


if __name__ == "__main__":
    unittest.main()
