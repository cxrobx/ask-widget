from __future__ import annotations

import asyncio
import json
import os
import re
import socket
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.parse
import urllib.request
from pathlib import Path
from unittest.mock import patch

import uvicorn
from playwright.sync_api import expect, sync_playwright

from ask_widget.app import create_app
from ask_widget.config import AppConfig
from ask_widget.launcher_ui import glass_alphas
from ask_widget.runner import _sse
from ask_widget.storage import Storage

LAUNCHER_SWIFT = Path(__file__).resolve().parent.parent / "launcher" / "AskWidget.swift"
PLUGIN_SRC = Path(__file__).resolve().parent.parent / "integrations" / "obsidian" / "src"
PLUGIN_ESBUILD = PLUGIN_SRC.parent / "node_modules" / ".bin" / "esbuild"


def stock_menu_guard() -> str:
    """The app's WebKit-menu guard, read out of the Swift source so the test runs what ships."""
    source = LAUNCHER_SWIFT.read_text(encoding="utf-8")
    return re.search(r'private let stockMenuGuard = """\n(.*?)\n"""', source, re.DOTALL).group(1)


# The WCAG contrast of an element's ink (text, or an icon's currentColor) against what is drawn under it: its own
# background and its ancestors', composited down to the first opaque one. Glass blur is not modelled; test pages are flat.
CONTRAST = r"""e => {
  const parse = c => {
    const m = /rgba?\(([^)]*)\)/.exec(c);
    if (!m) throw new Error('unparsed colour ' + c);
    const p = m[1].split(/[\s,\/]+/).filter(Boolean).map(Number);
    return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1];
  };
  const over = (top, under) => under.map((v, i) => top[i] * top[3] + v * (1 - top[3]));
  const lum = c => c.reduce((sum, v, i) => {
    v /= 255;
    return sum + [0.2126, 0.7152, 0.0722][i] * (v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4));
  }, 0);
  const layers = [];
  for (let el = e; el; el = el.parentElement) {
    const bg = parse(getComputedStyle(el).backgroundColor);
    if (bg[3] > 0) layers.push(bg);
    if (bg[3] >= 1) break;
  }
  let ground = [255, 255, 255];
  while (layers.length) ground = over(layers.pop(), ground);
  const [hi, lo] = [lum(over(parse(getComputedStyle(e).color), ground)), lum(ground)].sort((x, y) => y - x);
  return Math.round((hi + 0.05) / (lo + 0.05) * 100) / 100;
}"""


class BrowserSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.document = self.root / "guide.md"
        self.document.write_text(
            "# Guide\n\nSelect this passage to ask a question.\n\n"
            "| Feature | State |\n| --- | --- |\n| Browser smoke | active |\n",
            encoding="utf-8",
        )
        self.interactive_document = self.root / "interactive.html"
        self.interactive_document.write_text(
            """<!doctype html><html><body>
            <button id="level" onclick="setLevel('elii')">ELII</button>
            <output id="state">eli5</output>
            <script>
            function setLevel(level) {
              document.documentElement.dataset.level = level;
              document.getElementById('state').textContent = level;
            }
            </script>
            </body></html>""",
            encoding="utf-8",
        )

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 0))
        sock.listen(128)
        self.sock = sock
        self.port = int(sock.getsockname()[1])
        config = AppConfig(
            default_folder=self.root,
            allowed_roots=(self.root,),
            port=self.port,
            data_dir=self.root / "data",
        )
        self.app = create_app(config)
        catalogs = [
            {
                "id": "claude",
                "label": "Claude",
                "subscription": True,
                "plan": "test",
                "models": [
                    {
                        "id": "sonnet",
                        "label": "Claude Sonnet",
                        "description": "Browser smoke model.",
                        "efforts": ["low", "medium", "high"],
                        "default_effort": "medium",
                    }
                ],
                "selected_model": "sonnet",
                "selected_effort": "medium",
            },
            {
                "id": "codex",
                "label": "Codex",
                "subscription": True,
                "models": [
                    {
                        "id": "gpt-5.6-sol",
                        "label": "GPT-5.6-Sol",
                        "description": "Browser smoke model.",
                        "efforts": ["low", "medium"],
                        "default_effort": "low",
                    }
                ],
                "selected_model": "gpt-5.6-sol",
                "selected_effort": "low",
            },
        ]
        self.catalog_patch = patch("ask_widget.app.provider_catalogs", return_value=catalogs)
        self.catalog_patch.start()

        server_config = uvicorn.Config(
            self.app,
            host="127.0.0.1",
            port=self.port,
            log_level="warning",
        )
        self.server = uvicorn.Server(server_config)
        self.thread = threading.Thread(
            target=self.server.run,
            kwargs={"sockets": [self.sock]},
            daemon=True,
        )
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started and self.thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        if not self.server.started:
            self.fail("Local browser-smoke server did not start.")
        self.base_url = f"http://127.0.0.1:{self.port}"

    def tearDown(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)
        self.catalog_patch.stop()
        self.temp.cleanup()

    def test_markdown_theme_updates_in_place_and_can_be_disabled(self) -> None:
        storage = self.app.state.storage
        storage.update_settings({"vault_root": str(self.root)}, model_default="sonnet")
        theme = {"mode": "dark", "styles": {
            "content": {"color": "rgb(196, 197, 181)", "background-color": "rgb(26, 26, 26)",
                        "font-family": "Georgia, serif", "font-size": "19px", "max-width": "700px"},
            "h1": {"color": "rgb(88, 209, 235)", "font-size": "38px"},
            "th": {"color": "rgb(196, 197, 181)", "background-color": "rgb(21, 21, 21)"},
        }}
        storage.save_markdown_theme(self.root, theme)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1100, "height": 800}, color_scheme="light")
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(self.base_url + "/view?src=" + urllib.parse.quote(str(self.document)), wait_until="networkidle")
            self.assertEqual(page.locator("main").evaluate("e => getComputedStyle(e).color"), "rgb(196, 197, 181)")
            self.assertEqual(page.locator("main").evaluate("e => getComputedStyle(e).fontSize"), "19px")
            self.assertEqual(page.locator("h1").evaluate("e => getComputedStyle(e).color"), "rgb(88, 209, 235)")
            self.assertEqual(page.locator("body").evaluate("e => getComputedStyle(e).backgroundColor"), "rgb(26, 26, 26)")
            widget_font = page.locator(".askw-pill").evaluate("e => getComputedStyle(e).fontFamily")
            self.assertNotIn("Georgia", widget_font)
            page.evaluate("""() => {
                window.themeTestSentinel = 42;
                const range = document.createRange(); range.selectNodeContents(document.querySelector('main p'));
                getSelection().removeAllRanges(); getSelection().addRange(range);
            }""")
            selection = page.evaluate("getSelection().toString()")
            theme["styles"]["content"]["color"] = "rgb(20, 30, 40)"
            theme["styles"]["content"]["background-color"] = "rgb(245, 240, 230)"
            theme["mode"] = "light"
            session = page.request.get(self.base_url + "/api/session").json()
            response = page.request.post(self.base_url + "/api/markdown-theme", data={
                "token": session["token"], "vault_root": str(self.root), "snapshot": theme,
            })
            self.assertEqual(response.status, 200)
            expect(page.locator("main")).to_have_css("color", "rgb(20, 30, 40)", timeout=8000)
            self.assertEqual(page.evaluate("window.themeTestSentinel"), 42)
            self.assertEqual(page.evaluate("getSelection().toString()"), selection)
            self.assertEqual(page.locator(".askw-pill").evaluate("e => getComputedStyle(e).fontFamily"), widget_font)
            storage.update_settings({"markdown_follow_obsidian": False}, model_default="sonnet")
            expect(page.locator("main")).to_have_css("font-size", "17px", timeout=8000)
            self.assertEqual(page.locator("#askw-markdown-theme").text_content(), "")
            self.assertEqual(errors, [])
            browser.close()

    def test_launcher_reader_and_selection_widget(self) -> None:
        console_errors: list[str] = []
        page_errors: list[str] = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 720})
            page.on(
                "console",
                lambda message: console_errors.append(message.text)
                if message.type == "error"
                else None,
            )
            page.on("pageerror", lambda error: page_errors.append(str(error)))

            response = page.goto(self.base_url, wait_until="networkidle")
            self.assertIsNotNone(response)
            self.assertEqual(response.status, 200)
            # The app opens on Library, and Library rests on its home page.
            expect(page.locator(".vault-switch a.active")).to_have_text("Library")
            expect(page.locator("#home-docs .home-empty")).to_have_text("Documents you open will appear here.")
            expect(page.locator("#home-asks .home-empty")).to_have_text("Your completed answers will be saved here.")

            page.get_by_role("button", name="Settings", exact=True).click()
            expect(page.locator("#settings-modal")).to_be_visible()
            expect(page.locator("#provider-status")).to_contain_text("Using Claude")
            self.assertEqual(page.locator("#model").input_value(), "sonnet")
            page.keyboard.press("Escape")
            expect(page.locator("#settings-modal")).to_be_hidden()

            # Narrow, the sidebar stacks above the reader: three segments that fit, and the foot's two buttons.
            page.set_viewport_size({"width": 760, "height": 800})
            page.goto(self.base_url, wait_until="networkidle")
            page.evaluate("document.body.classList.add('native')")
            segments = page.locator(".vault-switch a").evaluate_all(
                "links => links.map(link => link.getBoundingClientRect().width)"
            )
            self.assertEqual(len(segments), 3)
            self.assertTrue(all(40 < width < 300 for width in segments), segments)
            expect(page.locator("#open-history")).to_be_visible()
            expect(page.locator("#open-settings")).to_be_visible()

            query = urllib.parse.urlencode(
                {"src": str(self.document), "folder": str(self.root)}
            )
            page.goto(f"{self.base_url}/view?{query}", wait_until="networkidle")
            passage = page.locator("main p").first
            passage.select_text()
            passage.dispatch_event("mouseup", {"button": 0})
            trigger = page.get_by_role("button", name="Ask about the selected text")
            trigger.wait_for(state="visible")
            self.assertEqual(trigger.get_attribute("aria-hidden"), "false")
            trigger.click()

            popup = page.get_by_role("dialog", name="Ask about selected text")
            popup.wait_for(state="visible")
            self.assertTrue(
                page.get_by_role("button", name="ELI5", exact=True).evaluate(
                    "element => element === document.activeElement"
                )
            )
            page.keyboard.press("ArrowDown")
            self.assertTrue(
                page.get_by_role("button", name="Prove it", exact=True).evaluate(
                    "element => element === document.activeElement"
                )
            )
            page.keyboard.press("Escape")
            trigger.wait_for(state="visible")
            self.assertTrue(trigger.evaluate("element => element === document.activeElement"))

            interactive_query = urllib.parse.urlencode(
                {"src": str(self.interactive_document)}
            )
            page.goto(f"{self.base_url}/view?{interactive_query}", wait_until="networkidle")
            page.get_by_role("button", name="ELII", exact=True).click()
            self.assertEqual(page.locator("html").get_attribute("data-level"), "elii")
            self.assertEqual(page.locator("#state").inner_text(), "elii")

            browser.close()

        self.assertEqual(page_errors, [])
        self.assertEqual(console_errors, [])

    def test_answer_panel_never_hides_the_passage_or_the_question(self) -> None:
        # The quoted passage used to be clipped mid-line, and every streamed token
        # scrolled the body to the bottom, pushing the question out of view.
        passage = (
            "An eight-turn transcript ends with a wrong answer. You can inspect any one "
            "turn's full state in about a minute. How many turns do you need to inspect "
            "to find the first one that went wrong, and which turn do you check first?"
        )
        document = self.root / "quiz.md"
        document.write_text(f"# Quiz\n\n{passage}\n", encoding="utf-8")

        async def long_stream(*args, **kwargs):
            for index in range(36):
                yield _sse("token", {"text": f"Paragraph {index} of an answer long enough to overflow the panel.\n\n"})
                await asyncio.sleep(0.04)
            yield _sse("done", {"elapsed_ms": 5})

        # Line boxes cut by the element's visible bottom edge (0 = no half-drawn line).
        sliced_lines = """e => {
            const box = e.getBoundingClientRect(), range = document.createRange();
            range.selectNodeContents(e);
            return [...range.getClientRects()]
                .filter(r => r.top < box.bottom - 0.5 && r.bottom > box.bottom + 0.5).length;
        }"""
        # Pixels of the element scrolled above the answer body's visible top.
        hidden_above = """e => Math.max(0,
            e.closest('.askw-body').getBoundingClientRect().top - e.getBoundingClientRect().top)"""

        page_errors: list[str] = []
        with patch("ask_widget.app.stream_answer", long_stream), sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1100, "height": 640})
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            query = urllib.parse.urlencode({"src": str(document), "folder": str(self.root)})
            page.goto(f"{self.base_url}/view?{query}", wait_until="networkidle")
            paragraph = page.locator("main p").first
            paragraph.select_text()
            paragraph.dispatch_event("mouseup", {"button": 0})
            page.get_by_role("button", name="Ask about the selected text").click()
            page.get_by_role("button", name="Ask a question…").click()
            page.get_by_label("Question about the highlighted text").fill("How many turns?")
            page.get_by_role("button", name="Go", exact=True).click()

            panel = page.get_by_role("dialog", name="Onyx answer")
            body = panel.locator(".askw-body")
            expect(panel).to_have_attribute("aria-busy", "false", timeout=15000)
            self.assertGreater(body.evaluate("e => e.scrollHeight - e.clientHeight"), 100)
            self.assertEqual(panel.locator(".askw-q").last.evaluate(hidden_above), 0)

            quote = panel.locator(".askw-selq")
            self.assertEqual(quote.evaluate(sliced_lines), 0)
            quote.click()
            self.assertIn(passage, quote.inner_text())
            self.assertLessEqual(quote.evaluate("e => e.scrollHeight - e.clientHeight"), 1)
            quote.click()
            self.assertEqual(quote.evaluate(sliced_lines), 0)

            follow = panel.get_by_label("Follow-up question")
            follow.fill("Is this binary search?")
            follow.press("Enter")
            page.wait_for_function(
                "() => { const a = document.querySelectorAll('.askw-a'); return a.length === 2 && a[1].querySelectorAll('p').length >= 12; }"
            )
            self.assertEqual(panel.locator(".askw-q").last.evaluate(hidden_above), 0)
            # The reader scrolls back up mid-stream; later tokens must not drag them down.
            body.evaluate("e => { e.scrollTop = 0; }")
            expect(panel).to_have_attribute("aria-busy", "false", timeout=15000)
            self.assertEqual(body.evaluate("e => e.scrollTop"), 0)
            browser.close()

        self.assertEqual(page_errors, [])

    def test_a_selection_never_runs_into_pinned_chrome(self) -> None:
        # Selecting a term beside an artifact's sticky contents rail selected the whole
        # page above it. Pinned chrome sits beside the text on screen but before all of
        # it in the DOM, so a drag that touched the rail or a sticky bar, or one begun on
        # the bar's faded lower edge, ran from there to the pointer. Both engines; the app
        # is WebKit.
        for engine in ("chromium", "webkit"):
            with self.subTest(engine=engine):
                self._selection_stays_out_of_pinned_chrome(engine)

    def _selection_stays_out_of_pinned_chrome(self, engine: str) -> None:
        prose = "Filler prose standing in for the page above the passage, one line of it."
        paragraphs = "".join(f"<p>{index}. {prose}</p>" for index in range(40))
        links = "".join(f'<a href="#part-{index}">Part {index}</a>' for index in range(12))
        pinned = self.root / "pinned.html"
        pinned.write_text(
            "<!doctype html><html><head><title>Pinned chrome</title><style>"
            "body{margin:0;font:16px/1.6 Helvetica,Arial,sans-serif}"
            ".bar{position:sticky;top:0;z-index:5;padding:10px 24px 30px;"
            "background:linear-gradient(#fff 70%,transparent)}.bar p{margin:6px 0 0}"
            ".layout{display:grid;grid-template-columns:200px 1fr;gap:48px;padding:0 24px}"
            ".rail{position:sticky;top:110px;align-self:start}.rail a{display:block;padding:6px 0}"
            "dl{display:grid;grid-template-columns:max-content 1fr;gap:6px 16px}dd{margin:0}"
            "#banner{position:fixed;left:0;right:0;bottom:0;height:44px;background:#eee}"
            "</style></head><body>"
            '<div class="bar"><label><input type="checkbox" id="flag"> Flag</label>'
            '<p id="note">Pinned note text.</p></div>'
            f'<div class="layout"><aside class="rail"><nav>{links}</nav></aside><main>{paragraphs}'
            '<dl><dt id="term">tool_use_id</dt><dd>The field that quotes back the id of the call.</dd></dl>'
            f"{paragraphs}</main></div>"
            "<div id=\"banner\" onclick=\"this.dataset.clicked = 'yes'\"><span>Banner</span></div>"
            "</body></html>",
            encoding="utf-8",
        )
        with sync_playwright() as playwright:
            browser = getattr(playwright, engine).launch(headless=True)
            page = browser.new_page(viewport={"width": 1100, "height": 700})
            query = urllib.parse.urlencode({"src": str(pinned), "folder": str(self.root)})
            page.goto(f"{self.base_url}/view?{query}", wait_until="networkidle")
            # Scroll the term's row beside one of the rail's links.
            page.evaluate("scrollBy(0, document.getElementById('term').getBoundingClientRect().top - 330)")
            at = page.evaluate("""() => {
              const range = document.createRange();
              range.selectNodeContents(document.getElementById('term'));
              const term = range.getBoundingClientRect(), mid = (term.top + term.bottom) / 2;
              const link = [...document.querySelectorAll('.rail a')].find(a => {
                const r = a.getBoundingClientRect();
                return r.top <= mid && mid <= r.bottom;
              });
              const l = link.getBoundingClientRect(), note = document.getElementById('note').getBoundingClientRect();
              const bar = document.querySelector('.bar').getBoundingClientRect();
              return {left: term.left, right: term.right, mid, href: link.getAttribute('href'),
                      link: [l.left + 20, (l.top + l.bottom) / 2], note: [term.left + 30, (note.top + note.bottom) / 2],
                      fade: [term.left + 30, bar.bottom - 8]};
            }""")
            end = (at["right"] - 2, at["mid"])

            def drag(start, stop) -> str:
                page.evaluate("getSelection().removeAllRanges()")
                page.mouse.move(*start)
                page.mouse.down()
                page.mouse.move(*stop, steps=12)
                page.mouse.up()
                page.wait_for_timeout(200)
                return " ".join(page.evaluate("getSelection().toString()").split())

            self.assertEqual(drag(end, (at["left"] + 2, at["mid"])), "tool_use_id")
            self.assertEqual(drag(end, tuple(at["link"])), "tool_use_id")  # onto the rail beside it
            # Up into the sticky bar, and down from its faded edge: the passage under it, never the page.
            for gesture in (drag(end, tuple(at["note"])), drag(tuple(at["fade"]), end)):
                self.assertTrue(gesture.endswith("tool_use_id"), gesture[-60:])
                self.assertNotIn("Part ", gesture)
                self.assertNotIn("Pinned note", gesture)
                self.assertLess(len(gesture), 1200)
            self.assertEqual(page.evaluate("document.querySelectorAll('[data-askw-passthrough]').length"), 0)

            # The chrome's own controls still work, and a click on its background stays a click.
            page.mouse.click(*at["link"])
            self.assertEqual(page.evaluate("location.hash"), at["href"])
            page.get_by_label("Flag").check()
            page.mouse.click(450, 692)
            self.assertEqual(page.evaluate("document.getElementById('banner').dataset.clicked"), "yes")
            browser.close()

    def test_follow_up_box_drags_taller_and_keeps_its_height(self) -> None:
        # The follow-up box could not be resized: it grew to four lines as you typed
        # and no further. Both engines, with the panel at full height: the app is
        # WebKit, whose own resize:vertical ran the box away to its cap there.
        for engine in ("chromium", "webkit"):
            with self.subTest(engine=engine):
                self._follow_up_box_resizes(engine)

    def _follow_up_box_resizes(self, engine: str) -> None:
        async def long_stream(*args, **kwargs):
            for index in range(24):
                yield _sse("token", {"text": f"Paragraph {index} of an answer long enough to overflow the panel.\n\n"})
            yield _sse("done", {"elapsed_ms": 5})

        page_errors: list[str] = []
        with patch("ask_widget.app.stream_answer", long_stream), sync_playwright() as playwright:
            browser = getattr(playwright, engine).launch(headless=True)
            page = browser.new_page(viewport={"width": 1100, "height": 640})
            page.on("pageerror", lambda error: page_errors.append(f"{engine}: {error}"))
            query = urllib.parse.urlencode({"src": str(self.document), "folder": str(self.root)})
            page.goto(f"{self.base_url}/view?{query}", wait_until="networkidle")
            paragraph = page.locator("main p").first
            paragraph.select_text()
            paragraph.dispatch_event("mouseup", {"button": 0})
            page.get_by_role("button", name="Ask about the selected text").click()
            page.get_by_role("button", name="Ask a question…").click()
            page.get_by_label("Question about the highlighted text").fill("Why?")
            page.get_by_role("button", name="Go", exact=True).click()

            panel = page.get_by_role("dialog", name="Onyx answer")
            follow = panel.get_by_label("Follow-up question")
            expect(follow).to_be_enabled(timeout=15000)
            self.assertLess(panel.evaluate("e => parseFloat(getComputedStyle(e).maxHeight) - e.offsetHeight"), 2)

            def height() -> int:
                return follow.evaluate("e => e.offsetHeight")

            def press_grip() -> float:
                box = follow.bounding_box()
                x, y = box["x"] + box["width"] - 6, box["y"] + box["height"] - 6
                page.mouse.move(x, y)
                page.mouse.down()
                return y

            start = height()
            follow.fill("one\ntwo\nthree")
            self.assertGreater(height(), start)  # it still grows with what you type
            follow.fill("")
            self.assertEqual(height(), start)

            y = press_grip()
            page.mouse.move(follow.bounding_box()["x"], y + 50, steps=8)
            page.mouse.up()
            tall = height()
            self.assertAlmostEqual(tall, start + 50, delta=2)  # by the drag, not away to the cap
            follow.press("a")  # typing no longer snaps it back to one line
            self.assertEqual(height(), tall)
            follow.press("Enter")  # sent: the box empties, and keeps its height for the next one
            expect(follow).to_be_enabled(timeout=15000)
            self.assertEqual(follow.input_value(), "")
            self.assertEqual(height(), tall)

            # Dragged far past the window's bottom edge (a pointer Playwright can't
            # move there itself), it stops where the footer still fits in the panel
            # and the answer keeps a few lines.
            y = press_grip()
            page.evaluate("y => document.dispatchEvent(new MouseEvent('mousemove', {clientY: y, bubbles: true}))", y + 2000)
            page.mouse.up()
            self.assertGreater(height(), tall)
            foot, box = panel.locator(".askw-foot").bounding_box(), panel.bounding_box()
            self.assertLessEqual(foot["y"] + foot["height"], box["y"] + box["height"] + 0.5)
            self.assertGreaterEqual(panel.locator(".askw-body").evaluate("e => e.clientHeight"), 90)
            browser.close()

        self.assertEqual(page_errors, [])

    def test_ask_menu_moves_by_dragging_it(self) -> None:
        # The menu stayed where it opened, over the passage it asks about. Both
        # engines: the app is WebKit.
        for engine in ("chromium", "webkit"):
            with self.subTest(engine=engine):
                self._ask_menu_moves(engine)

    def _ask_menu_moves(self, engine: str) -> None:
        async def short_stream(*args, **kwargs):
            yield _sse("token", {"text": "Because."})
            yield _sse("done", {"elapsed_ms": 5})

        page_errors: list[str] = []
        with patch("ask_widget.app.stream_answer", short_stream), sync_playwright() as playwright:
            browser = getattr(playwright, engine).launch(headless=True)
            page = browser.new_page(viewport={"width": 1100, "height": 640})
            page.on("pageerror", lambda error: page_errors.append(f"{engine}: {error}"))
            query = urllib.parse.urlencode({"src": str(self.document), "folder": str(self.root)})
            page.goto(f"{self.base_url}/view?{query}", wait_until="networkidle")
            paragraph = page.locator("main p").first
            paragraph.select_text()
            paragraph.dispatch_event("mouseup", {"button": 0})
            trigger = page.get_by_role("button", name="Ask about the selected text")
            trigger.click()
            menu = page.get_by_role("dialog", name="Ask about selected text")
            panel = page.get_by_role("dialog", name="Onyx answer")
            prove = menu.get_by_role("button", name="Prove it", exact=True)
            field = menu.get_by_label("Question about the highlighted text")
            self.assertEqual(menu.evaluate("e => getComputedStyle(e).cursor"), "move")
            self.assertEqual(field.evaluate("e => getComputedStyle(e).cursor"), "auto")

            def press(handle) -> tuple[float, float]:
                box = handle.bounding_box()
                x, y = box["x"] + 20, box["y"] + box["height"] / 2
                page.mouse.move(x, y)
                page.mouse.down()
                return x, y

            # Pressed on an action and moved, the menu goes with the pointer and runs nothing.
            before = menu.bounding_box()
            x, y = press(prove)
            page.mouse.move(x + 240, y + 150, steps=8)
            page.mouse.up()
            after = menu.bounding_box()
            self.assertAlmostEqual(after["x"], before["x"] + 240, delta=1)
            self.assertAlmostEqual(after["y"], before["y"] + 150, delta=1)
            expect(panel).to_be_hidden()

            # Moved into the window's corner, it stops whole inside it. The release
            # lands on the page, and the Ask button stays away from the open menu.
            press(prove)
            page.mouse.move(1096, 636, steps=8)
            page.mouse.up()
            corner = menu.bounding_box()
            self.assertAlmostEqual(corner["x"] + corner["width"], 1100 - 8, delta=1)
            self.assertAlmostEqual(corner["y"] + corner["height"], 640 - 8, delta=1)
            page.wait_for_timeout(300)  # past the reader's selection check on release
            expect(trigger).to_be_hidden()

            # A press that stays put is a click. The field it opens at the bottom
            # edge stays inside the window, and a move keeps its question and focus.
            menu.get_by_role("button", name="Ask a question…").click()
            expect(field).to_be_focused()
            box = menu.bounding_box()
            self.assertLessEqual(box["y"] + box["height"], 640 - 8 + 0.5)
            field.fill("Why?")
            x, y = press(prove)
            page.mouse.move(x - 300, y - 200, steps=8)
            page.mouse.up()
            expect(field).to_be_focused()
            self.assertEqual(field.input_value(), "Why?")

            # Pressed in the field, the pointer selects its text; the menu stays put.
            before = menu.bounding_box()
            box = field.bounding_box()
            page.mouse.move(box["x"] + 10, box["y"] + 12)
            page.mouse.down()
            page.mouse.move(box["x"] + 120, box["y"] + 14, steps=5)
            page.mouse.up()
            after = menu.bounding_box()
            self.assertEqual((after["x"], after["y"]), (before["x"], before["y"]))

            menu.get_by_role("button", name="ELI5", exact=True).click()
            expect(panel).to_be_visible()
            browser.close()

        self.assertEqual(page_errors, [])

    def test_answer_tables_render_as_tables(self) -> None:
        # A table in an answer used to print as raw "| a | b |" lines.
        answer = (
            "No. Row two is Claude's **first** reply.\n\n"
            "| Row | Role | Who wrote it |\n"
            "|---|:---:|---:|\n"
            "| 1 | `user` | your app |\n"
            "| 2 | `assistant` | <b>Claude</b> \\| model |\n"
            "So row two asks for the tool.\n"
        )

        async def table_stream(*args, **kwargs):
            # In pieces, so the half-arrived table renders along the way too.
            for start in range(0, len(answer), 25):
                yield _sse("token", {"text": answer[start:start + 25]})
                await asyncio.sleep(0.01)
            yield _sse("done", {"elapsed_ms": 5})

        page_errors: list[str] = []
        with patch("ask_widget.app.stream_answer", table_stream), sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1100, "height": 640})
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            query = urllib.parse.urlencode({"src": str(self.document), "folder": str(self.root)})
            page.goto(f"{self.base_url}/view?{query}", wait_until="networkidle")
            paragraph = page.locator("main p").first
            paragraph.select_text()
            paragraph.dispatch_event("mouseup", {"button": 0})
            page.get_by_role("button", name="Ask about the selected text").click()
            page.get_by_role("button", name="Ask a question…").click()
            page.get_by_label("Question about the highlighted text").fill("Which row?")
            page.get_by_role("button", name="Go", exact=True).click()

            panel = page.get_by_role("dialog", name="Onyx answer")
            expect(panel).to_have_attribute("aria-busy", "false", timeout=15000)
            reply = panel.locator(".askw-a").last
            table = reply.locator("table")
            expect(table).to_have_count(1)
            self.assertEqual(table.locator("th").all_inner_texts(), ["Row", "Role", "Who wrote it"])
            rows = table.locator("tbody tr")
            self.assertEqual(rows.count(), 2)
            self.assertEqual(rows.nth(0).locator("code").inner_text(), "user")
            # Markup in a cell stays text, and an escaped pipe stays in its cell.
            cell = rows.nth(1).locator("td").nth(2)
            self.assertEqual(cell.inner_text(), "<b>Claude</b> | model")
            self.assertEqual(cell.locator("b").count(), 0)
            self.assertEqual(cell.evaluate("e => getComputedStyle(e).textAlign"), "right")
            self.assertEqual(table.locator("th").nth(1).evaluate("e => getComputedStyle(e).textAlign"), "center")
            self.assertNotIn("|---", reply.inner_text())
            expect(reply.locator("p", has_text="So row two asks for the tool.")).to_have_count(1)
            expect(reply.locator("td", has_text="So row two")).to_have_count(0)
            browser.close()

        self.assertEqual(page_errors, [])

    def test_wheel_over_the_answer_panel_never_scrolls_the_page(self) -> None:
        # At the answer's top or bottom, or over the panel's header and footer,
        # the wheel used to carry on into the page underneath. Both engines: the
        # app is WebKit.
        for engine in ("chromium", "webkit"):
            with self.subTest(engine=engine):
                self._wheel_stays_in_the_panel(engine)

    def _wheel_stays_in_the_panel(self, engine: str) -> None:
        # An HTML page, as in Artifacts: it keeps its own look, and the panel
        # stays fixed to the window while the page scrolls.
        document = self.root / "long.html"
        document.write_text(
            "<!doctype html><html><body><main><h1>Long</h1>"
            + "".join(f"<p>Paragraph {i} of a page long enough to scroll.</p>" for i in range(150))
            + "</main></body></html>",
            encoding="utf-8",
        )

        async def long_stream(*args, **kwargs):
            for index in range(36):
                yield _sse("token", {"text": f"Paragraph {index} of an answer long enough to overflow the panel.\n\n"})
            yield _sse("done", {"elapsed_ms": 5})

        page_top = "() => document.scrollingElement.scrollTop"
        page_errors: list[str] = []
        with patch("ask_widget.app.stream_answer", long_stream), sync_playwright() as playwright:
            browser = getattr(playwright, engine).launch(headless=True)
            page = browser.new_page(viewport={"width": 1100, "height": 640})
            page.on("pageerror", lambda error: page_errors.append(f"{engine}: {error}"))
            query = urllib.parse.urlencode({"src": str(document), "folder": str(self.root)})
            page.goto(f"{self.base_url}/view?{query}", wait_until="networkidle")
            # Scroll the page first, so it could move either way under the panel,
            # then ask about a passage that is on screen.
            page.evaluate("() => { document.scrollingElement.scrollTop = 400; }")
            on_screen = page.evaluate(
                "() => [...document.querySelectorAll('main p')].findIndex(p => p.getBoundingClientRect().top > 80)"
            )
            paragraph = page.locator("main p").nth(on_screen)
            paragraph.select_text()
            paragraph.dispatch_event("mouseup", {"button": 0})
            page.get_by_role("button", name="Ask about the selected text").click()
            page.get_by_role("button", name="Ask a question…").click()
            page.get_by_label("Question about the highlighted text").fill("Why?")
            page.get_by_role("button", name="Go", exact=True).click()

            panel = page.get_by_role("dialog", name="Onyx answer")
            body = panel.locator(".askw-body")
            expect(panel).to_have_attribute("aria-busy", "false", timeout=15000)
            self.assertGreater(body.evaluate("e => e.scrollHeight - e.clientHeight"), 100)
            body.evaluate("e => { e.scrollTop = 0; }")
            start = page.evaluate(page_top)
            self.assertGreater(start, 100)

            def wheel_over(locator, dy: int) -> None:
                box = locator.bounding_box()
                x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
                # The point must really be the panel's, or the test proves nothing.
                self.assertTrue(page.evaluate(
                    "([x, y]) => !!document.elementFromPoint(x, y)?.closest('.askw-panel')", [x, y]
                ))
                page.mouse.move(x, y)
                page.mouse.wheel(0, dy)
                page.wait_for_timeout(250)

            wheel_over(body, -300)  # the answer is already at its top
            self.assertEqual(page.evaluate(page_top), start)
            wheel_over(body, 300)  # the answer itself still scrolls
            self.assertGreater(body.evaluate("e => e.scrollTop"), 0)
            self.assertEqual(page.evaluate(page_top), start)
            body.evaluate("e => { e.scrollTop = e.scrollHeight; }")
            wheel_over(body, 300)  # ...and now it is at its bottom
            self.assertEqual(page.evaluate(page_top), start)
            wheel_over(panel.locator(".askw-head"), 300)
            wheel_over(panel.locator(".askw-foot"), -300)
            self.assertEqual(page.evaluate(page_top), start)

            # Off the panel, the page scrolls as ever.
            box = panel.bounding_box()
            x = box["x"] - 20 if box["x"] > 40 else box["x"] + box["width"] + 20
            self.assertFalse(page.evaluate(
                "([x, y]) => !!document.elementFromPoint(x, y)?.closest('.askw-panel')", [x, 320]
            ))
            page.mouse.move(x, 320)
            page.mouse.wheel(0, 300)
            page.wait_for_function("start => document.scrollingElement.scrollTop > start", arg=start)
            browser.close()

        self.assertEqual(page_errors, [])

    def test_tool_calls_show_under_the_answer_being_written(self) -> None:
        # The tool pills sat in a bar under the panel's header, far above the
        # "Thinking…" line at the foot of a long conversation.
        async def tool_stream(*args, **kwargs):
            yield _sse("tool_status", {"tool": "Grep", "status": "calling"})
            await asyncio.sleep(1.5)
            yield _sse("tool_status", {"tool": "Grep", "status": "complete"})
            yield _sse("token", {"text": "Found it."})
            yield _sse("done", {"elapsed_ms": 5})

        # The pill's row comes straight after the newest answer, inside the conversation.
        under_newest = """p => p.closest('.askw-body') !== null
            && p.parentElement.previousElementSibling === [...document.querySelectorAll('.askw-a')].pop()"""
        page_errors: list[str] = []
        with patch("ask_widget.app.stream_answer", tool_stream), sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1100, "height": 640})
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            query = urllib.parse.urlencode({"src": str(self.document), "folder": str(self.root)})
            page.goto(f"{self.base_url}/view?{query}", wait_until="networkidle")
            paragraph = page.locator("main p").first
            paragraph.select_text()
            paragraph.dispatch_event("mouseup", {"button": 0})
            page.get_by_role("button", name="Ask about the selected text").click()
            page.get_by_role("button", name="Ask a question…").click()
            page.get_by_label("Question about the highlighted text").fill("Where is it?")
            page.get_by_role("button", name="Go", exact=True).click()

            panel = page.get_by_role("dialog", name="Onyx answer")
            pill = panel.locator(".askw-pillt", has_text="Grep")
            expect(pill).to_be_visible()
            self.assertTrue(pill.evaluate(under_newest))
            thinking = panel.locator(".askw-a").last.locator(".askw-think")
            self.assertGreater(pill.bounding_box()["y"], thinking.bounding_box()["y"])
            expect(panel).to_have_attribute("aria-busy", "false", timeout=15000)
            expect(pill).to_have_count(0)

            # A follow-up's tools sit under the follow-up's answer, not the first one.
            follow = panel.get_by_label("Follow-up question")
            follow.fill("And the second one?")
            follow.press("Enter")
            expect(pill).to_be_visible()
            expect(panel.locator(".askw-a")).to_have_count(2)
            self.assertTrue(pill.evaluate(under_newest))
            expect(panel).to_have_attribute("aria-busy", "false", timeout=15000)
            browser.close()

        self.assertEqual(page_errors, [])

    def test_vault_shell_navigates_reader_iframe(self) -> None:
        vault = self.root / "vault"
        (vault / "notes").mkdir(parents=True)
        alpha = vault / "notes" / "Alpha.md"
        alpha.write_text(
            "---\ntags: [career, ai]\nstatus: active\n---\n\n# Alpha\n\n"
            "Read [[Beta]] next, or [[Nowhere]] which does not exist.\n\n"
            "A passage worth selecting for a question.\n",
            encoding="utf-8",
        )
        (vault / "Beta.md").write_text("# Beta\n\nBack to [[Alpha]].\n", encoding="utf-8")
        storage: Storage = self.app.state.storage
        storage.update_settings({"vault_root": str(vault)}, model_default="sonnet")
        storage.add_root(vault)

        console_errors: list[str] = []
        page_errors: list[str] = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 720})
            page.on(
                "console",
                lambda message: console_errors.append(message.text)
                if message.type == "error"
                else None,
            )
            page.on("pageerror", lambda error: page_errors.append(str(error)))

            page.goto(
                f"{self.base_url}/vault?src={urllib.parse.quote(str(alpha))}",
                wait_until="networkidle",
            )
            reader = page.frame_locator("iframe[name=reader]")
            self.assertEqual(reader.locator("h1").inner_text(), "Alpha")
            reader.locator(".askw-properties").wait_for(state="visible")
            self.assertIn("Properties · 2", reader.locator(".askw-properties summary").inner_text())
            self.assertEqual(reader.locator(".askw-wikilink-missing").inner_text(), "Nowhere")
            active = page.locator("#tree a.active")
            active.wait_for()
            self.assertTrue(active.get_attribute("data-path").endswith("Alpha.md"))
            self.assertEqual(page.locator("#vault-count").inner_text(), "2 notes")
            self.assertTrue(page.locator("#reader-empty").evaluate("el => el.hidden"))

            reader.get_by_role("link", name="Beta", exact=True).click()
            page.wait_for_function(
                "() => new URLSearchParams(document.querySelector('iframe[name=reader]').contentWindow.location.search).get('src')?.endsWith('Beta.md')"
            )
            page.wait_for_function("() => location.href.includes('Beta.md')")
            self.assertEqual(reader.locator("h1").inner_text(), "Beta")
            page.locator("#tree a.active[data-path$='Beta.md']").wait_for()
            self.assertEqual(page.locator("#tree a.active").count(), 1)
            self.assertIn("Beta.md", page.url)
            self.assertIn("Beta", page.title())

            page.locator("#vault-filter").fill("alp")
            page.locator("#tree .results a.file").wait_for()
            self.assertEqual(page.locator("#tree .results a.file").count(), 1)
            page.locator("#tree .results a.file").click()
            page.wait_for_function("() => location.href.includes('Alpha.md')")
            page.locator("#vault-filter").fill("")
            page.locator("#tree details").first.wait_for()

            passage = reader.locator("main p").last
            passage.select_text()
            passage.dispatch_event("mouseup", {"button": 0})
            trigger = reader.get_by_role("button", name="Ask about the selected text")
            trigger.wait_for(state="visible")

            browser.close()

        self.assertEqual(page_errors, [])
        self.assertEqual(console_errors, [])

    def test_vault_sidebar_pins_from_the_reader_and_context_pill_rests_as_an_icon(self) -> None:
        vault = self.root / "vault"
        vault.mkdir()
        note = vault / "Alpha.md"
        note.write_text("# Alpha\n\nA passage.\n", encoding="utf-8")
        storage: Storage = self.app.state.storage
        storage.update_settings({"vault_root": str(vault)}, model_default="sonnet")
        storage.add_root(vault)

        page_errors: list[str] = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 720})
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            page.goto(f"{self.base_url}/vault?src={urllib.parse.quote(str(note))}", wait_until="networkidle")
            reader = page.frame_locator("iframe[name=reader]")
            reader.locator("h1").wait_for()
            side, pane = page.locator("#vault-side"), page.locator("#reader-pane")
            # Named for what it does while visible; tracked by id after, since hidden it is inert and leaves the a11y tree.
            expect(page.get_by_role("button", name="Pin sidebar")).to_have_attribute("aria-pressed", "true")
            pin = page.locator("#side-pin")
            self.assertTrue(side.is_visible())

            pin.focus()
            pin.press("Enter")  # unpinned from the keyboard: the pointer is elsewhere, so it goes at once
            expect(pin).to_have_attribute("aria-pressed", "false")
            expect(side).to_be_hidden()
            self.assertEqual(pane.evaluate("e => e.getBoundingClientRect().width"), 1280)
            page.reload(wait_until="networkidle")
            reader.locator("h1").wait_for()
            expect(side).to_be_hidden()  # remembered

            reader.locator("body").press("Control+Backslash")  # from inside the reader
            expect(side).to_be_visible()
            expect(pin).to_have_attribute("aria-pressed", "true")
            reader.locator("body").press("Control+Backslash")
            expect(side).to_be_hidden()
            reader.locator("body").press("Control+Backslash")
            expect(side).to_be_visible()

            # The context folder rests as an icon; its name slides out on hover.
            pill = reader.locator(".askw-pill")
            label = pill.locator(".askw-pill-label")
            expect(label).to_have_css("opacity", "0")
            self.assertLess(pill.evaluate("e => e.getBoundingClientRect().width"), 34)
            self.assertRegex(pill.get_attribute("aria-label"), r"^Context folder: .*/vault\.")
            pill.hover()
            expect(label).to_have_css("opacity", "1")
            self.assertEqual(label.inner_text(), "vault")
            page.frame(name="reader").wait_for_function(
                "() => document.querySelector('.askw-pill').getBoundingClientRect().width > 60"
            )
            browser.close()

        self.assertEqual(page_errors, [])

    def test_vault_rows_are_single_lines_and_hovering_one_previews_the_page(self) -> None:
        sources = self.root / "sources"
        (sources / "guides").mkdir(parents=True)
        long_title = "Debugging a broken Claude agent, live — a walkthrough far too long for one sidebar line"
        (sources / "guides" / "debugging.html").write_text(
            f"<title>{long_title}</title><p class=subtitle>Read the transcript, find the first wrong turn, fix it.</p>",
            encoding="utf-8",
        )
        (sources / "guides" / "wire.html").write_text("<title>Reading the wire</title>", encoding="utf-8")
        vault = self.root / "Artifacts"
        vault.mkdir()
        (vault / "Architect").symlink_to(sources, target_is_directory=True)
        (vault / "gone.html").symlink_to(self.root / "nowhere.html")
        self.app.state.storage.update_settings({"html_vault_root": str(vault)}, model_default="sonnet")

        page_errors: list[str] = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1200, "height": 700})
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            page.goto(f"{self.base_url}/vault?vault=html", wait_until="networkidle")
            row = page.locator("#tree a.file", has_text="Debugging a broken")
            wire = page.locator("#tree a.file", has_text="Reading the wire")
            row.wait_for()
            # One line per row however long the title; the card carries the rest.
            self.assertLess(row.evaluate("e => e.getBoundingClientRect().height"), 40)
            self.assertTrue(row.locator(".lbl").evaluate("e => e.scrollWidth > e.clientWidth"))
            # A folder's icon opens and shuts with it.
            folder = page.locator("#tree details[data-path$='guides'] > summary")
            self.assertTrue(folder.locator("svg.open").is_visible())
            folder.click()
            self.assertTrue(folder.locator("svg.shut").is_visible())
            folder.click()
            # The right-click menu finds every page row by its path, missing ones too.
            self.assertEqual(page.locator("#tree .file.missing").get_attribute("data-path"), str(vault / "gone.html"))

            peek = page.locator("#peek")
            self.assertTrue(peek.is_hidden())
            row.hover()
            expect(peek).to_be_visible()
            self.assertEqual(row.get_attribute("aria-describedby"), "peek")
            self.assertEqual(peek.locator(".peek-title").inner_text(), long_title)
            self.assertEqual(peek.locator(".peek-sum").inner_text(), "Read the transcript, find the first wrong turn, fix it.")
            self.assertEqual(peek.locator(".peek-row").nth(0).inner_text(), "Architect › guides")
            self.assertRegex(peek.locator(".peek-row").nth(1).inner_text(), r"^HTML page · Updated \d+m ago$")
            side_right = page.locator("#vault-side").evaluate("e => e.getBoundingClientRect().right")
            self.assertGreaterEqual(peek.evaluate("e => e.getBoundingClientRect().left"), side_right)  # beside, never over
            wire.hover()  # warm: the next row previews at once
            expect(peek.locator(".peek-title")).to_have_text("Reading the wire", timeout=400)
            self.assertEqual(peek.locator(".peek-sum").count(), 0)  # nothing to summarise, no empty line
            wire.click(button="right")  # the row menu puts it away, and keeps it away while open
            expect(peek).to_be_hidden()
            page.wait_for_function("() => OnyxMenu.isOpen()")  # the menu opens after a round trip
            wire.hover()
            page.wait_for_timeout(600)
            expect(peek).to_be_hidden()
            page.keyboard.press("Escape")
            page.wait_for_function("() => !OnyxMenu.isOpen()")
            page.mouse.move(900, 400)

            page.locator("#vault-filter").focus()  # from the keyboard, focus shows it too
            for _ in range(3):
                page.keyboard.press("Tab")
            self.assertTrue(row.evaluate("e => e === document.activeElement"))
            expect(peek).to_be_visible()
            expect(peek.locator(".peek-title")).to_have_text(long_title)
            page.keyboard.press("Escape")
            expect(peek).to_be_hidden()
            browser.close()

        self.assertEqual(page_errors, [])

    def test_artifacts_rows_drag_into_folders_pin_and_rename_in_place(self) -> None:
        topic = self.root / "learnings" / "topic"
        (topic / "guides").mkdir(parents=True)
        (topic / "guides" / "wire.html").write_text("<title>Reading the wire</title><p>On the wire.</p>", encoding="utf-8")
        dashboard = self.root / "learnings" / "dashboard"
        dashboard.mkdir()
        (dashboard / "index.html").write_text("<title>Mastery Map</title>", encoding="utf-8")
        vault = self.root / "Artifacts"
        (vault / "Learnings").mkdir(parents=True)  # empty, and listed all the same: somewhere to drop
        (vault / "Architect").symlink_to(topic, target_is_directory=True)
        (vault / "Mastery Map").symlink_to(dashboard, target_is_directory=True)
        self.app.state.storage.update_settings({"html_vault_root": str(vault)}, model_default="sonnet")

        page_errors: list[str] = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1200, "height": 700})
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            page.goto(f"{self.base_url}/vault?vault=html", wait_until="networkidle")
            learnings = page.locator("#tree details[data-rel='Learnings'] > summary")
            expect(learnings).to_be_visible()
            wire = page.locator("#tree a.file", has_text="Reading the wire")
            self.assertEqual(wire.get_attribute("draggable"), "false")  # inside the linked topic: another tree's
            wire.click()
            page.wait_for_function("() => (document.getElementById('reader').contentDocument || {}).title === 'Reading the wire'")

            # Dragging the link onto the folder moves the link, not the topic; the open page follows it.
            page.locator(f"#tree summary[data-entry='{vault / 'Architect'}']").drag_to(learnings)
            expect(page.locator("#tree details[data-rel='Learnings'] summary", has_text="Architect")).to_be_visible()
            self.assertEqual(os.readlink(vault / "Learnings" / "Architect"), str(topic))
            self.assertFalse(os.path.lexists(vault / "Architect"))
            page.wait_for_function(
                "p => decodeURIComponent(document.getElementById('reader').src).includes(p)",
                arg=str(vault / "Learnings" / "Architect" / "guides" / "wire.html"),
            )
            # A guide-folder link two levels down is one page; pinned, it sorts ahead of the folder.
            page.locator(f"#tree summary[data-entry='{vault / 'Mastery Map'}']").drag_to(learnings)
            mastery = page.locator("#tree details[data-rel='Learnings'] a.file", has_text="Mastery Map")
            expect(mastery).to_be_visible()
            mastery.click(button="right")
            page.wait_for_function("() => OnyxMenu.isOpen()")
            page.locator(".onyx-menu button", has_text="Pin to Top").click()
            first = page.locator("#tree details[data-rel='Learnings'] > ul > li").first
            expect(first.locator(".pinned")).to_be_visible()
            expect(first).to_contain_text("Mastery Map")

            # Rename in place: Return keeps the name.
            learnings.click(button="right")
            page.wait_for_function("() => OnyxMenu.isOpen()")
            page.locator(".onyx-menu button", has_text="Rename").click()
            page.locator("#tree .name-edit").fill("Study")
            page.keyboard.press("Enter")
            expect(page.locator("#tree details[data-rel='Study']")).to_be_visible()
            self.assertTrue((vault / "Study" / "Mastery Map").is_symlink())
            # New Folder from the list's empty space; Escape on a second one leaves nothing behind.
            box = page.locator("#tree").bounding_box()
            for name, key in (("Resources", "Enter"), ("Scratch", "Escape")):
                page.mouse.click(box["x"] + 100, box["y"] + box["height"] - 15, button="right")
                page.wait_for_function("() => OnyxMenu.isOpen()")
                page.locator(".onyx-menu button", has_text="New Folder").click()
                page.locator("#tree .name-edit").fill(name)
                page.keyboard.press(key)
            expect(page.locator("#tree details[data-rel='Resources']")).to_be_visible()
            self.assertEqual(page.locator("#tree .name-edit").count(), 0)
            self.assertEqual(sorted(p.name for p in vault.iterdir()), ["Resources", "Study"])
            browser.close()

        self.assertEqual(page_errors, [])

    def test_vault_sidebar_wears_the_obsidian_explorer_and_falls_back_live(self) -> None:
        notes = self.root / "CX"
        for folder in ("Archive", "Inbox/Quick Notes", "Projects"):  # the Notes tree lists folders that hold notes
            (notes / folder).mkdir(parents=True)
            (notes / folder / "Note.md").write_text("# Note\n", encoding="utf-8")
        artifacts = self.root / "Artifacts"
        for folder in ("Alpha", "Beta"):
            (artifacts / folder).mkdir(parents=True)
            (artifacts / folder / "page.html").write_text(f"<title>{folder} page</title>", encoding="utf-8")
        storage: Storage = self.app.state.storage
        storage.update_settings({"vault_root": str(notes), "html_vault_root": str(artifacts)}, model_default="sonnet")
        teal, red, orange = "rgb(42, 161, 152)", "rgb(220, 50, 47)", "rgb(203, 75, 22)"
        storage.save_sidebar_theme(notes, {
            "mode": "light",
            "styles": {
                "pane": {"background-color": "rgb(253, 246, 227)", "color": "rgb(7, 54, 66)", "font-family": "Menlo, monospace"},
                "folder": {"color": "rgb(88, 110, 117)"}, "file": {"color": "rgb(7, 54, 66)"},
                "guide": {"border-left-color": "rgb(147, 161, 161)", "border-left-width": "1px", "border-left-style": "solid"},
            },
            # Listed out of the tree's order: Notes must match by name, not by position.
            "folders": [{"name": "Projects", "color": teal}, {"name": "Archive", "color": red},
                        {"name": "Inbox", "color": orange, "hover": "rgba(203, 75, 22, 0.1)"}],
        })
        color = "e => getComputedStyle(e).color"

        page_errors: list[str] = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1100, "height": 700}, color_scheme="dark")
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            page.goto(f"{self.base_url}/vault", wait_until="networkidle")
            inbox = page.locator("#tree details[data-path$='Inbox'] > summary")
            inbox.wait_for()
            self.assertEqual(page.locator("body").get_attribute("class"), "kind-notes obsidian-tree")
            self.assertEqual(page.locator("#vault-side").evaluate("e => getComputedStyle(e).backgroundColor"), "rgb(253, 246, 227)")
            # The dark app's chrome reads on the cream pane: dark ink, not white.
            self.assertEqual(page.locator(".brand-name").evaluate(color), "rgb(7, 54, 66)")
            self.assertEqual(inbox.evaluate(color), orange)
            inbox.hover()  # a folder hovers in its own colour
            expect(inbox).to_have_css("background-color", "rgba(203, 75, 22, 0.1)")
            self.assertEqual(page.locator("#tree details[data-path$='Archive'] > summary").evaluate(color), red)
            self.assertEqual(page.locator("#tree details[data-path$='Projects'] > summary").evaluate(color), teal)
            inbox.click()  # Notes starts folded; open Inbox to see its guide and child folder
            quick = page.locator("#tree details[data-path$='Quick Notes'] > summary")
            self.assertEqual(quick.evaluate(color), orange)  # a nested folder keeps its top folder's colour
            self.assertEqual(
                page.locator("#tree details[data-path$='Inbox'] > ul").evaluate("e => getComputedStyle(e).borderLeftColor"), orange
            )
            self.assertTrue(inbox.locator("svg.chev").is_visible())
            self.assertFalse(inbox.locator(".fold").is_visible())

            page.goto(f"{self.base_url}/vault?vault=html", wait_until="networkidle")
            page.locator("#tree details[data-path$='Beta']").wait_for()
            self.assertEqual(page.locator("body").get_attribute("class"), "kind-html obsidian-tree")
            # Artifacts' names never match the vault's: its folders take the colours in order.
            self.assertEqual(page.locator("#tree details[data-path$='Alpha'] > summary").evaluate(color), teal)
            self.assertEqual(page.locator("#tree details[data-path$='Beta'] > summary").evaluate(color), red)

            storage.update_settings({"sidebar_follow_obsidian": False}, model_default="sonnet")
            expect(page.locator("body")).to_have_attribute("class", "kind-html", timeout=8000)  # live, no reload
            alpha = page.locator("#tree details[data-path$='Alpha'] > summary")
            self.assertTrue(alpha.locator(".fold").is_visible())
            self.assertFalse(alpha.locator("svg.chev").is_visible())
            self.assertEqual(alpha.evaluate("e => e.parentElement.parentElement.style.getPropertyValue('--folder-color')"), "")
            storage.update_settings({"sidebar_follow_obsidian": True}, model_default="sonnet")
            expect(page.locator("body")).to_have_attribute("class", "kind-html obsidian-tree", timeout=8000)
            browser.close()

        self.assertEqual(page_errors, [])

    @unittest.skipUnless(PLUGIN_ESBUILD.exists(), "needs the Obsidian plugin's dev dependencies (npm ci)")
    def test_plugin_sidebar_capture_runs_and_the_service_accepts_it(self) -> None:
        # The capture only ever ran inside Obsidian, and each way it went wrong there is pinned here:
        # a createSvg with a spaced class string (threw); a root-folder wrapper Obsidian doesn't have,
        # under which AnuPpuccin's subfolder-inherit rule wiped every top-level colour; mounting on
        # the black frame instead of inside the cream pane; and colours the scheme defines across
        # lines, which a raw variable read keeps. The explorer is built as Obsidian builds it (a
        # spacer opens every list) and styled the way AnuPpuccin's simple rainbow styles it.
        shim = self.root / "obsidian-shim.js"
        shim.write_text("export class TFolder {}\n", encoding="utf-8")
        bundle = subprocess.run(
            [str(PLUGIN_ESBUILD), str(PLUGIN_SRC / "sidebar-theme.ts"), "--bundle", "--format=iife",
             "--global-name=OnyxSidebar", f"--alias:obsidian={shim}"],
            capture_output=True, text=True, check=False, cwd=PLUGIN_SRC.parent,
        )
        self.assertEqual(bundle.returncode, 0, bundle.stderr)
        helpers = """
            // Obsidian's DOM helpers: createEl/createDiv take a spaced cls string; createSvg adds tokens.
            const apply = (el, o, strict) => { if (typeof o === 'string') o = {cls: o}; if (!o) return;
              if (o.cls) { if (Array.isArray(o.cls)) el.classList.add(...o.cls); else if (strict) el.classList.add(o.cls); else el.className = o.cls; }
              if (o.text) el.textContent = o.text; if (o.type) el.setAttribute('type', o.type); };
            Element.prototype.createEl = function (tag, o) { const el = document.createElement(tag); apply(el, o, false); this.appendChild(el); return el; };
            Element.prototype.createDiv = function (o) { return this.createEl('div', o); };
            Element.prototype.createSvg = function (tag, o) { const el = document.createElementNS('http://www.w3.org/2000/svg', tag); apply(el, o, true); this.appendChild(el); return el; };
            Element.prototype.addClass = function (...c) { this.classList.add(...c); };
        """
        spacer = '<div style="width:1px;height:0.1px;margin-bottom:0"></div>'
        folders = "".join(
            f'<div class="tree-item nav-folder is-collapsed"><div class="tree-item-self nav-folder-title" data-path="{name}">'
            f'<div class="tree-item-icon collapse-icon"></div><div class="tree-item-inner">{name}</div></div></div>'
            for name in ("Archive", "Inbox", "Projects")
        )
        schemes = "".join(f"--ctp-{i}: {rgb};" for i, rgb in enumerate(("220\n\t,\n\t50\n,\n47", "203\n\t,\n\t75\n,\n22", "42\n\t,\n\t161\n,\n152")))
        rainbow = "".join(
            f".nav-folder-children > .nav-folder:nth-child({i + 2}), .nav-files-container > div > .nav-folder:nth-child({i + 2}) "
            f"{{ --rainbow: var(--ctp-{i}); }}\n" for i in range(3)
        )
        explorer = f"""<!doctype html><style>
            body {{ --nav-item-background-hover: rgba(0, 0, 0, 0.05); font: 13px Menlo, monospace; {schemes} }}
            .mod-left-split {{ background: rgb(0, 0, 0); }} .workspace-tab-container {{ background: rgb(253, 246, 227); color: rgb(7, 54, 66); }}
            .nav-folder-title {{ padding: 4px 0; }} .nav-file-title {{ color: rgb(7, 54, 66); }}
            .nav-file-title.is-active {{ background: rgb(238, 232, 213); }} .search-input-container input {{ border-radius: 999px; }}
            {rainbow}
            .rainbow-inherit .nav-files-container .nav-folder.nav-folder .nav-folder {{ --rainbow: inherit; }}
            .nav-files-container > div > .nav-folder .nav-folder-title {{ color: rgb(var(--rainbow)); --nav-item-background-hover: rgba(var(--rainbow), 0.1); }}
            .nav-files-container .nav-folder > .nav-folder-children {{ border-left: 1px solid rgba(var(--rainbow), 0.5); }}
            </style><body class="theme-light rainbow-inherit"><div class="workspace-split mod-left-split"><div class="workspace-tabs">
            <div class="workspace-tab-container"><div class="workspace-leaf"><div class="workspace-leaf-content" data-type="file-explorer">
            <div class="nav-files-container node-insert-event"><div>{spacer}{folders}</div></div>
            </div></div></div></div></div></body>"""

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.set_content(explorer)
            page.add_script_tag(content=helpers)
            page.add_script_tag(content=bundle.stdout)
            snapshot = page.evaluate("() => OnyxSidebar.captureSidebarTheme({ vault: { getRoot: () => ({ children: [] }) } })")
            self.assertEqual(page.locator(".onyx-sidebar-sample, .onyx-sample-folder").count(), 0)  # the copy is gone again
            browser.close()

        self.assertEqual(snapshot["mode"], "light")
        self.assertEqual(snapshot["styles"]["pane"]["background-color"], "rgb(253, 246, 227)")  # the pane, not the frame
        self.assertEqual(snapshot["styles"]["active"]["background-color"], "rgb(238, 232, 213)")
        self.assertEqual(snapshot["styles"]["hover"]["background-color"], "rgba(0, 0, 0, 0.05)")
        self.assertEqual(snapshot["folders"], [  # the rainbow, in the explorer's order, with its own-colour hovers
            {"name": "Archive", "color": "rgb(220, 50, 47)", "guide": "rgba(220, 50, 47, 0.5)", "hover": "rgba(220, 50, 47, 0.1)"},
            {"name": "Inbox", "color": "rgb(203, 75, 22)", "guide": "rgba(203, 75, 22, 0.5)", "hover": "rgba(203, 75, 22, 0.1)"},
            {"name": "Projects", "color": "rgb(42, 161, 152)", "guide": "rgba(42, 161, 152, 0.5)", "hover": "rgba(42, 161, 152, 0.1)"},
        ])
        vault = self.root / "CX"
        vault.mkdir()
        with urllib.request.urlopen(self.base_url + "/api/session") as response:  # as the plugin gets its token
            token = json.load(response)["token"]
        request = urllib.request.Request(
            self.base_url + "/api/sidebar-theme", method="POST", headers={"Content-Type": "application/json"},
            data=json.dumps({"token": token, "vault_root": str(vault), "snapshot": snapshot}).encode(),
        )
        with urllib.request.urlopen(request) as response:  # raises on 400: something off the service's allowlist
            self.assertEqual(response.status, 200)

    def test_glass_icons_take_the_page_tone_not_the_app_theme(self) -> None:
        # A dark app over a cream page (the usual Artifacts case) must get light
        # glass with dark ink there; dark glass would turn the icon into a smudge.
        cream = self.root / "cream.html"
        cream.write_text(
            "<!doctype html><title>Cream</title><body style='background:#FDF6E3'><p>Cream page.</p></body>",
            encoding="utf-8",
        )
        night = self.root / "night.html"
        night.write_text(
            "<!doctype html><title>Night</title><body style='background:#1a1a1a;color:#ddd'><p>Night page.</p></body>",
            encoding="utf-8",
        )
        alpha = "e => { const m = getComputedStyle(e).backgroundColor.match(/[\\d.]+/g); return m.length > 3 ? +m[3] : 1; }"

        page_errors: list[str] = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            for scheme, document, tone, ink in (
                ("dark", cream, "light", "rgb(13, 13, 13)"),
                ("light", night, "dark", "rgb(255, 255, 255)"),
            ):
                page = browser.new_page(viewport={"width": 1100, "height": 700}, color_scheme=scheme)
                page.on("pageerror", lambda error: page_errors.append(str(error)))
                page.goto(f"{self.base_url}/view?src={urllib.parse.quote(str(document))}", wait_until="networkidle")
                self.assertEqual(page.locator("html").get_attribute("data-askw-page"), tone)
                pill = page.locator(".askw-pill")
                self.assertLessEqual(pill.evaluate(alpha), 0.3)  # glass at rest
                self.assertIn("blur", pill.evaluate("e => getComputedStyle(e).backdropFilter"))
                pill.hover()  # the name shows, so the glass frosts to a readable floor
                expect(pill.locator("b")).to_have_css("color", ink)
                page.wait_for_function(
                    "() => { const m = getComputedStyle(document.querySelector('.askw-pill')).backgroundColor.match(/[\\d.]+/g); return m.length < 4 || +m[3] >= 0.7; }"
                )
                page.close()
            browser.close()

        self.assertEqual(page_errors, [])

    def test_chat_bubble_lists_the_pages_chats_and_continues_one(self) -> None:
        # A page with saved answers shows a chat bubble in its bottom-right corner, with how many. It lists every chat
        # about the page, newest first, and a row continues that conversation in the answer panel. A page with none
        # shows no bubble until its first answer lands. Both engines: the app is WebKit.
        chats = self.root / "chats.html"
        chats.write_text(
            "<!doctype html><title>Chats</title><body style='background:#FDF6E3'>"
            "<p id=first>First passage.</p><p id=second>Second passage.</p></body>",
            encoding="utf-8",
        )
        storage: Storage = self.app.state.storage

        def saved(request_id: str, document: Path, selection: str, action: str, question: str, answer: str) -> None:
            source = str(document.resolve())
            doc_id = storage.upsert_document(source=source, title=document.stem, kind="html", folder=str(self.root))
            storage.start_conversation(
                request_id=request_id, document_id=doc_id, document_source=source, document_title=document.stem,
                document_page=None, selection=selection, context="", action=action, question=question,
                folder=str(self.root), provider="claude", model="sonnet",
            )
            if answer:
                storage.finish_conversation(request_id, status="complete", answer=answer)
            else:
                storage.finish_conversation(request_id, status="error", error="It failed.")

        saved("chat-first", chats, "First passage.", "ask", "What does the first say?", "It opens the page.")
        saved("chat-second", chats, "Second passage.", "eli5", "", "It comes second.")
        saved("chat-failed", chats, "Second passage.", "prove", "", "")  # no answer, so not a chat
        saved("chat-elsewhere", self.root / "elsewhere.html", "Elsewhere.", "ask", "Somewhere else?", "Yes.")

        async def one_answer(*args, **kwargs):
            yield _sse("token", {"text": "Only, and simply."})
            yield _sse("done", {"elapsed_ms": 5})

        page_errors: list[str] = []
        with patch("ask_widget.app.stream_answer", one_answer), sync_playwright() as playwright:
            for engine in ("chromium", "webkit"):
                with self.subTest(engine=engine):
                    browser = getattr(playwright, engine).launch(headless=True)
                    # WebKit runs a dark app over the cream page, where the app theme once turned the bubble's ink white.
                    scheme = "dark" if engine == "webkit" else "light"
                    page = browser.new_page(viewport={"width": 1100, "height": 700}, color_scheme=scheme)
                    page.on("pageerror", lambda error, engine=engine: page_errors.append(f"{engine}: {error}"))
                    page.goto(f"{self.base_url}/view?src={urllib.parse.quote(str(chats))}", wait_until="networkidle")
                    bubble = page.get_by_role("button", name="2 chats on this page")
                    expect(bubble).to_be_visible()
                    expect(bubble).to_have_text("2")
                    for ink in (bubble.locator("svg"), bubble.locator(".askw-chats-n")):
                        expect(ink).to_have_css("color", "rgb(58, 131, 247)")
                    box = bubble.bounding_box()
                    self.assertGreater(box["x"] + box["width"], 1100 - 40)
                    self.assertGreater(box["y"] + box["height"], 700 - 40)

                    bubble.click()
                    listing = page.get_by_role("dialog", name="Chats on this page")
                    expect(listing).to_be_visible()
                    expect(bubble).to_have_attribute("aria-expanded", "true")
                    expect(listing.locator(".askw-chats-title")).to_have_text("2 chats on this page")
                    rows = listing.locator(".askw-chats-row")
                    expect(rows).to_have_count(2)
                    expect(rows.nth(0)).not_to_be_focused()  # a click opens it without picking a row
                    expect(rows.nth(0).locator(".askw-chats-q")).to_have_text("ELI5")
                    expect(rows.nth(0).locator(".askw-chats-sel")).to_have_text("“Second passage.”")
                    expect(rows.nth(0).locator(".askw-chats-meta")).to_contain_text("claude · sonnet")
                    expect(rows.nth(1).locator(".askw-chats-q")).to_have_text("What does the first say?")

                    # Escape puts the list away and hands focus back to the bubble.
                    page.keyboard.press("Escape")
                    expect(listing).to_be_hidden()
                    expect(bubble).to_be_focused()

                    # From the keyboard the list takes focus, and a row continues that conversation in the answer panel.
                    page.keyboard.press("Enter")
                    expect(rows.nth(0)).to_be_focused()
                    page.keyboard.press("ArrowDown")
                    expect(rows.nth(1)).to_be_focused()
                    page.keyboard.press("Enter")
                    expect(listing).to_be_hidden()
                    panel = page.get_by_role("dialog", name="Onyx answer")
                    expect(panel.locator(".askw-eyebrow")).to_have_text("Continue saved answer")
                    expect(panel.locator(".askw-selq")).to_have_text("“First passage.”")
                    expect(panel.locator(".askw-body")).to_contain_text("It opens the page.")
                    expect(panel.get_by_label("Follow-up question")).to_be_enabled()

                    # A page with no chats shows no bubble; its first answer brings one.
                    fresh = self.root / f"fresh-{engine}.html"
                    fresh.write_text("<!doctype html><title>Fresh</title><p id=only>Only passage.</p>", encoding="utf-8")
                    page.goto(f"{self.base_url}/view?src={urllib.parse.quote(str(fresh))}", wait_until="networkidle")
                    expect(page.locator(".askw-chats")).to_be_hidden()
                    paragraph = page.locator("#only")
                    paragraph.select_text()
                    paragraph.dispatch_event("mouseup", {"button": 0})
                    page.get_by_role("button", name="Ask about the selected text").click()
                    page.get_by_role("button", name="ELI5", exact=True).click()
                    expect(panel).to_have_attribute("aria-busy", "false", timeout=15000)
                    expect(page.get_by_role("button", name="1 chat on this page")).to_have_text("1")
                    browser.close()

        self.assertEqual(page_errors, [])

    def test_vault_rows_open_the_apps_own_menu_and_option_turns_reveal_into_copy(self) -> None:
        # WebKit's stock menu can't be themed, so the sidebar draws its own
        # (static/app-menu.js) and the app suppresses the stock one elsewhere.
        # Both engines: the app is WebKit.
        topic = self.root / "learnings" / "architect"
        guide = topic / "guides" / "who"
        guide.mkdir(parents=True)
        (guide / "index.html").write_text(
            '<title>Who holds the plan</title><p>A passage to read.</p><p><a href="#notes">Notes</a></p>',
            encoding="utf-8",
        )
        artifacts = self.root / "Artifacts"
        (artifacts / "Mine").mkdir(parents=True)
        (artifacts / "Mine" / "draft.html").write_text("<title>Draft</title>", encoding="utf-8")  # listed once it holds a page
        (artifacts / "Architect").symlink_to(topic, target_is_directory=True)
        row_path = artifacts / "Architect" / "guides" / "who" / "index.html"
        real = str(row_path.resolve())
        self.app.state.storage.update_settings({"html_vault_root": str(artifacts)}, model_default="sonnet")
        # Stands in for navigator.clipboard the way the app's pasteboard bridge does.
        clipboard = (
            "Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:t=>"
            "{(window.__copied=window.__copied||[]).push(t);return Promise.resolve(true)}}});"
        )
        prevented = """e => { const ev = new MouseEvent('contextmenu', {bubbles: true, cancelable: true});
            e.dispatchEvent(ev); return ev.defaultPrevented }"""
        marked = re.compile(r"\bmenu-for\b")

        revealed: list[str] = []
        page_errors: list[str] = []
        with (
            patch("ask_widget.vault.reveal_in_finder", side_effect=lambda target: revealed.append(str(target))),
            sync_playwright() as playwright,
        ):
            for engine in ("chromium", "webkit"):
                with self.subTest(engine=engine):
                    browser = getattr(playwright, engine).launch(headless=True)
                    context = browser.new_context(viewport={"width": 1180, "height": 760})
                    context.add_init_script(clipboard + stock_menu_guard())
                    page = context.new_page()
                    page.on("pageerror", lambda error, engine=engine: page_errors.append(f"{engine}: {error}"))
                    page.goto(f"{self.base_url}/vault?vault=html", wait_until="networkidle")
                    row = page.locator("#tree a.file", has_text="Who holds the plan")
                    menu = page.get_by_role("menu")
                    items = menu.get_by_role("menuitem")

                    row.click(button="right")
                    expect(menu).to_have_attribute("aria-label", "Page actions")
                    expect(items).to_have_text(["Open", "Reveal in Finder", "Reveal Link in Finder"])
                    expect(row).to_have_class(marked)
                    self.assertEqual(page.evaluate("getSelection().toString()"), "")  # WebKit's word-select is undone
                    # Themed and opaque: the app's elevated surface, not the glass under it.
                    self.assertEqual(menu.evaluate("e => getComputedStyle(e).backgroundColor"), "rgb(255, 255, 255)")
                    width = menu.evaluate("e => e.getBoundingClientRect().width")

                    # Holding Option swaps each Reveal for a Copy in place, at the same width.
                    page.keyboard.down("Alt")
                    expect(items).to_have_text(["Open", "Copy Path", "Copy Link Path"])
                    self.assertEqual(menu.evaluate("e => e.getBoundingClientRect().width"), width)
                    page.keyboard.press("ArrowDown")
                    page.keyboard.press("ArrowDown")
                    expect(items.nth(1)).to_be_focused()
                    self.assertEqual(items.nth(1).evaluate("e => getComputedStyle(e).backgroundColor"), "rgb(58, 131, 247)")
                    page.keyboard.press("Enter")  # Option still down: no keypress in Chromium
                    page.keyboard.up("Alt")
                    expect(menu).to_be_hidden()
                    self.assertEqual(page.evaluate("window.__copied"), [real])
                    expect(page.locator(".onyx-toast")).to_contain_text("Copied")
                    expect(row).not_to_have_class(marked)

                    # An Option-right-click opens straight into the copies.
                    page.keyboard.down("Alt")
                    row.click(button="right")
                    expect(items.nth(2)).to_have_text("Copy Link Path")
                    items.nth(2).click()
                    page.keyboard.up("Alt")
                    self.assertEqual(page.evaluate("window.__copied"), [real, str(row_path)])

                    for label, target in (("Reveal in Finder", real), ("Reveal Link in Finder", str(artifacts / "Architect"))):
                        row.click(button="right")
                        with page.expect_response("**/api/vault/reveal"):
                            menu.get_by_role("menuitem", name=label).click()
                        self.assertEqual(revealed[-1], target)

                    # Folders: a linked one offers both sides, the vault's own folder only itself. Both sit in the
                    # vault's own top level, so both rename, pin and come out; only the vault's own takes a new folder.
                    owned = ["Rename", "Pin to Top", "Remove from Artifacts"]
                    page.locator("#tree summary", has_text="Architect").click(button="right")
                    expect(menu).to_have_attribute("aria-label", "Folder actions")
                    expect(items).to_have_text(["Reveal in Finder", "Reveal Link in Finder", *owned])
                    page.keyboard.press("Escape")
                    expect(menu).to_be_hidden()
                    page.locator("#tree summary", has_text="Mine").click(button="right")
                    expect(items).to_have_text(["Reveal in Finder", "New Folder", *owned])
                    page.locator("#vault-count").click()
                    expect(menu).to_be_hidden()

                    # Open reads the page, as a click on the row does; a click into the
                    # reader (another document) still dismisses a menu.
                    row.click(button="right")
                    menu.get_by_role("menuitem", name="Open").click()
                    reader = page.frame_locator("iframe[name=reader]")
                    passage = reader.locator("p", has_text="A passage")
                    passage.wait_for()
                    row.click(button="right")
                    expect(menu).to_be_visible()
                    passage.click()
                    expect(menu).to_be_hidden()

                    # The stock menu stays suppressed wherever nothing of ours claimed the
                    # event, except in an editable field and on a reader document's links.
                    self.assertTrue(page.locator("#vault-count").evaluate(prevented))
                    self.assertFalse(page.locator("#vault-filter").evaluate(prevented))
                    self.assertTrue(passage.evaluate(prevented))
                    self.assertFalse(reader.locator("a", has_text="Notes").evaluate(prevented))
                    browser.close()

        self.assertEqual(page_errors, [])

    def test_vault_sidebar_unpins_to_the_edge_and_pins_back(self) -> None:
        # Unpinned, the sidebar floats over the reader: out after a beat on the left edge, gone once the
        # pointer leaves, unless it is in use. Both engines: the app is WebKit.
        artifacts = self.root / "Artifacts"
        (artifacts / "Pages").mkdir(parents=True)
        (artifacts / "Pages" / "one.html").write_text("<title>One</title><p>First page.</p>", encoding="utf-8")
        self.app.state.storage.update_settings({"html_vault_root": str(artifacts)}, model_default="sonnet")

        page_errors: list[str] = []
        with sync_playwright() as playwright:
            for engine in ("chromium", "webkit"):
                with self.subTest(engine=engine):
                    browser = getattr(playwright, engine).launch(headless=True)
                    page = browser.new_page(viewport={"width": 1100, "height": 700})
                    page.on("pageerror", lambda error, engine=engine: page_errors.append(f"{engine}: {error}"))
                    page.goto(f"{self.base_url}/vault?vault=html", wait_until="networkidle")
                    side, pin = page.locator("#vault-side"), page.locator("#side-pin")

                    def reader_x() -> float:
                        return page.locator("#reader").bounding_box()["x"]

                    # Pinned by default: docked beside the reader.
                    expect(pin).to_have_attribute("aria-pressed", "true")
                    self.assertGreater(reader_x(), 250)

                    # Unpinned from its own pin: the reader takes the width; the sidebar stays while
                    # the pointer is on it and goes once it leaves.
                    pin.click()
                    expect(pin).to_have_attribute("aria-pressed", "false")
                    self.assertEqual(reader_x(), 0)
                    expect(side).to_be_visible()
                    page.mouse.move(700, 350)
                    expect(side).to_be_hidden()

                    # A beat on the left edge brings it out; on it, it stays; off it, it goes.
                    page.mouse.move(3, 350)
                    expect(side).to_be_visible()
                    page.mouse.move(120, 350)
                    page.wait_for_timeout(700)
                    expect(side).to_be_visible()
                    page.mouse.move(700, 350)
                    expect(side).to_be_hidden()
                    # Out from the edge and straight back to the page, never touching the (inset) panel: it still goes.
                    page.mouse.move(3, 350)
                    expect(side).to_be_visible()
                    page.mouse.move(700, 350)
                    expect(side).to_be_hidden()
                    # The edge is 24 px deep: 30 px in is just the page; 20 px in brings it out, and it goes again.
                    page.mouse.move(30, 350)
                    page.wait_for_timeout(300)
                    expect(side).to_be_hidden()
                    page.mouse.move(20, 350)
                    expect(side).to_be_visible()
                    page.mouse.move(700, 350)
                    expect(side).to_be_hidden()

                    # In use holds it out: "/" brings it out to type in, Escape lets it go ...
                    page.keyboard.press("/")
                    expect(page.locator("#vault-filter")).to_be_focused()
                    page.wait_for_timeout(700)
                    expect(side).to_be_visible()
                    page.keyboard.press("Escape")
                    expect(side).to_be_hidden()

                    # ... and so does a row's menu, until it closes.
                    page.mouse.move(3, 350)
                    expect(side).to_be_visible()
                    page.locator("#tree a.file", has_text="One").click(button="right")
                    menu = page.get_by_role("menu")
                    expect(menu).to_be_visible()
                    page.mouse.move(700, 350)
                    page.wait_for_timeout(700)
                    expect(side).to_be_visible()
                    page.keyboard.press("Escape")
                    expect(menu).to_be_hidden()
                    expect(side).to_be_hidden()

                    # Remembered across a reload, and put back without a slide; ⌘\ pins it back, and that is remembered too.
                    page.add_init_script(
                        "window.__sideRuns = []; addEventListener('transitionrun', e => {"
                        " if (e.target.id === 'vault-side') __sideRuns.push(e.propertyName) }, true)"
                    )
                    page.reload(wait_until="networkidle")
                    expect(pin).to_have_attribute("aria-pressed", "false")
                    expect(side).to_be_hidden()
                    self.assertEqual(page.evaluate("__sideRuns"), [])
                    page.keyboard.press("Meta+Backslash")
                    expect(pin).to_have_attribute("aria-pressed", "true")
                    expect(side).to_be_visible()
                    self.assertGreater(reader_x(), 250)
                    page.reload(wait_until="networkidle")
                    expect(pin).to_have_attribute("aria-pressed", "true")

                    # Unpinned with the pointer away, it slides off (so the listener above does hear a slide);
                    # under Reduce Motion it fades where it is instead of travelling.
                    page.mouse.move(700, 350)
                    page.keyboard.press("Meta+Backslash")
                    expect(side).to_be_hidden()
                    self.assertIn("transform", page.evaluate("__sideRuns"))
                    page.emulate_media(reduced_motion="reduce")
                    self.assertEqual(side.evaluate("e => getComputedStyle(e).transform"), "none")
                    browser.close()

        self.assertEqual(page_errors, [])

    def test_outline_lists_the_page_headings_and_docks_on_the_right(self) -> None:
        # The outline: the page's headings on the reader's right, nested by level. Away by default, it comes out from
        # the toggle in the reader's corner (the context pill moves left of it); a row scrolls the page to its heading
        # and the section being read stays marked; its pin docks it as a third column, remembered; ⌘⇧\ works from
        # inside the reader; folds and the filter narrow it. Both engines: the app is WebKit.
        vault = self.root / "vault"
        vault.mkdir()
        filler = "\n\n".join(f"Filler paragraph {i}." for i in range(30))  # enough to scroll under each heading
        storage: Storage = self.app.state.storage
        storage.update_settings({"vault_root": str(vault)}, model_default="sonnet")
        storage.add_root(vault)

        page_errors: list[str] = []
        with sync_playwright() as playwright:
            for engine in ("chromium", "webkit"):
                with self.subTest(engine=engine):
                    # A note of its own per engine: the reader restores a page's last scroll position, saved by the service.
                    note = vault / f"Guide {engine}.md"
                    note.write_text(
                        f"# Guide\n\n{filler}\n\n## One\n\n{filler}\n\n### One A\n\n{filler}\n\n## Two\n\n{filler}\n",
                        encoding="utf-8",
                    )
                    browser = getattr(playwright, engine).launch(headless=True)
                    page = browser.new_page(viewport={"width": 1100, "height": 700})
                    page.on("pageerror", lambda error, engine=engine: page_errors.append(f"{engine}: {error}"))
                    page.goto(f"{self.base_url}/vault?src={urllib.parse.quote(str(note))}", wait_until="networkidle")
                    reader = page.frame_locator("iframe[name=reader]")
                    reader.locator("h1").wait_for()
                    outline, toggle, pin = page.locator("#outline-side"), page.locator("#outline-toggle"), page.locator("#outline-pin")
                    rows, pill = page.locator("#outline .h"), reader.locator(".askw-pill")

                    def reader_width() -> float:
                        return page.locator("#reader").bounding_box()["width"]

                    # Away by default: the reader keeps its width, the toggle rests in its corner and the pill left of it.
                    expect(outline).to_be_hidden()
                    expect(toggle).to_be_visible()
                    expect(pin).to_have_attribute("aria-pressed", "false")
                    expect(pill).to_have_css("right", "50px")
                    self.assertEqual(reader_width(), 840)

                    # A beat on the reader's right edge brings it out (24 px deep: 30 px in is just the page), and off it,
                    # it goes; so does hovering the toggle. Out: every heading, nested, the first marked as being read.
                    page.mouse.move(1070, 350)
                    page.wait_for_timeout(300)
                    expect(outline).to_be_hidden()
                    page.mouse.move(1090, 350)
                    expect(outline).to_be_visible()
                    page.mouse.move(700, 350)
                    expect(outline).to_be_hidden()
                    toggle.hover()
                    expect(outline).to_be_visible()
                    expect(rows).to_have_text(["Guide", "One", "One A", "Two"])
                    self.assertEqual(page.locator("#outline li.has-kids").count(), 2)  # Guide holds One and Two; One holds One A
                    expect(rows.nth(0)).to_have_class(re.compile(r"\bactive\b"))

                    # A row scrolls the page to its heading, which becomes the section being read.
                    rows.nth(3).click()
                    page.frame(name="reader").wait_for_function(
                        "() => Math.abs(document.querySelectorAll('h2')[1].getBoundingClientRect().top) < 2"
                    )
                    expect(rows.nth(3)).to_have_class(re.compile(r"\bactive\b"))
                    expect(rows.nth(0)).not_to_have_class(re.compile(r"\bactive\b"))

                    # Its pin docks it: a third column, the toggle gone, the pill back in its corner; remembered.
                    pin.click()
                    expect(pin).to_have_attribute("aria-pressed", "true")
                    page.mouse.move(400, 350)
                    page.wait_for_timeout(600)
                    expect(outline).to_be_visible()
                    self.assertEqual(reader_width(), 590)
                    expect(toggle).to_be_hidden()
                    expect(pill).to_have_css("right", "12px")
                    page.reload(wait_until="networkidle")
                    reader.locator("h1").wait_for()
                    expect(pin).to_have_attribute("aria-pressed", "true")
                    expect(outline).to_be_visible()
                    expect(rows).to_have_text(["Guide", "One", "One A", "Two"])

                    # A twisty folds a section; the header's button folds and unfolds them all; the filter keeps a match
                    # and the rows above it.
                    page.locator("#outline li[data-k='2:One'] > .row .tw").click()
                    expect(rows.nth(2)).to_be_hidden()
                    expect(rows.nth(1)).to_be_visible()
                    fold = page.locator("#outline-fold")
                    fold.click()
                    expect(rows.nth(1)).to_be_hidden()
                    expect(fold).to_have_attribute("aria-label", "Expand all headings")
                    fold.click()
                    expect(rows.nth(2)).to_be_visible()
                    page.locator("#outline-filter").fill("two")
                    expect(rows.nth(1)).to_be_hidden()
                    expect(rows.nth(0)).to_be_visible()
                    expect(rows.nth(3)).to_be_visible()
                    page.locator("#outline-filter").fill("")
                    expect(rows.nth(1)).to_be_visible()

                    # ⌘⇧\ from inside the reader unpins it: away again (the pointer is off it and its filter no longer
                    # has focus), and the reader takes the width back.
                    page.mouse.click(400, 350)  # into the page: focus leaves the filter, the pointer leaves the pane
                    reader.locator("body").press("Control+Shift+Backslash")
                    expect(pin).to_have_attribute("aria-pressed", "false")
                    expect(outline).to_be_hidden()
                    self.assertEqual(reader_width(), 840)
                    expect(toggle).to_be_visible()

                    # Library's home is no page: no toggle to hover.
                    page.locator(".vault-switch a[data-kind=library]").click()
                    expect(page.locator("#home")).to_be_visible()
                    expect(toggle).to_be_hidden()
                    browser.close()

        self.assertEqual(page_errors, [])

    def test_page_changes_are_instant_and_right_from_the_first_frame(self) -> None:
        # Moving between notes, and between Notes and Artifacts, is instant, as in Obsidian, and nothing about a page
        # changes after its first frame: the reader never fades or animates, the vault look is on the page when it first
        # paints (not fetched after it) and the theme never flips, a remembered position is where the page opens (no ride
        # from the top, even under the page's own scroll-behavior:smooth), and a switch resizes the sidebar at once. The
        # page the shell opened on is remembered as its vault's last page even when it loads before the tree does.
        # Both engines: the app is WebKit.
        notes = self.root / "vault"
        notes.mkdir()
        alpha = notes / "Alpha.md"
        alpha.write_text("# Alpha\n\nRead [[Beta]] next.\n", encoding="utf-8")
        beta = notes / "Beta.md"
        beta.write_text("# Beta\n\n" + "\n\n".join(f"Paragraph {i}. " + "words " * 60 for i in range(80)), encoding="utf-8")
        artifacts = self.root / "Artifacts"
        (artifacts / "Pages").mkdir(parents=True)
        long_page = artifacts / "Pages" / "long.html"
        long_page.write_text(
            "<!doctype html><html><head><title>Long page</title><style>html{scroll-behavior:smooth}</style></head><body>"
            + "".join(f"<p style='height:120px'>Paragraph {i}</p>" for i in range(100)) + "</body></html>",
            encoding="utf-8",
        )
        storage: Storage = self.app.state.storage
        storage.update_settings(
            {"vault_root": str(notes), "html_vault_root": str(artifacts), "appearance_theme": "dark",
             "markdown_follow_obsidian": True, "sidebar_follow_obsidian": True},
            model_default="sonnet",
        )
        storage.save_markdown_theme(notes, {"mode": "light", "styles": {
            "content": {"background-color": "rgb(253, 246, 227)", "color": "rgb(0, 43, 54)"},
        }})
        storage.add_root(notes)

        def remember(path: Path, y: float) -> None:
            # The reader keys a document by the path it was read at; on macOS the temp dir is also /private/var.
            rows = [src for src in {str(path), os.path.realpath(path)} if storage.document(src)]
            self.assertTrue(rows, f"{path.name} was never recorded")
            for src in rows:
                storage.update_position(src, y)

        # Every frame from the click on: the reader's opacity and animations, its width, and the page in it.
        SAMPLE = """() => { const out = window.__frames = [], r = document.getElementById('reader'), t0 = performance.now();
          (function tick() { let d = null; try { d = r.contentDocument } catch (e) {}
            const de = d && d.documentElement, look = d && d.getElementById('askw-vault-look');
            out.push({ t: performance.now() - t0, op: getComputedStyle(r).opacity, anims: r.getAnimations().length,
              w: Math.round(r.getBoundingClientRect().width), href: decodeURIComponent(r.contentWindow.location.href),
              ready: d ? d.readyState : '', body: !!(d && d.body && d.body.childElementCount),
              look: de ? de.getAttribute('data-askw-look') : null, css: !!(look && look.textContent),
              color: de ? de.getAttribute('data-askw-color') : null, y: r.contentWindow.scrollY });
            if (performance.now() - t0 < 1500) requestAnimationFrame(tick) })() }"""

        page_errors: list[str] = []
        with sync_playwright() as playwright:
            for engine in ("chromium", "webkit"):
                with self.subTest(engine=engine):
                    browser = getattr(playwright, engine).launch(headless=True)
                    page = browser.new_page(viewport={"width": 1100, "height": 700}, color_scheme="dark")
                    page.on("pageerror", lambda error, engine=engine: page_errors.append(f"{engine}: {error}"))
                    reader = page.frame_locator("iframe[name=reader]")

                    # Read each once, so each has a row to remember a position in.
                    for path in (beta, long_page):
                        page.goto(f"{self.base_url}/view?{urllib.parse.urlencode({'src': str(path)})}", wait_until="networkidle")
                    remember(beta, 900)
                    remember(long_page, 1500)

                    # The tree made to come AFTER the page, as it can by a few ms: the page is still its vault's last.
                    page.route(re.compile(r"/api/vault/tree"), lambda route: (page.wait_for_timeout(250), route.continue_()))
                    page.goto(f"{self.base_url}/vault?src={urllib.parse.quote(str(alpha))}", wait_until="networkidle")
                    expect(reader.locator("h1")).to_have_text("Alpha")
                    page.unroute(re.compile(r"/api/vault/tree"))
                    self.assertEqual(page.evaluate("localStorage.getItem('askw:vault:last')"), str(alpha))
                    expect(page.locator("#tree a.active")).to_have_attribute("data-path", str(alpha))

                    def measure(action, name: str) -> list[dict]:
                        page.evaluate(SAMPLE)
                        action()
                        page.wait_for_function(
                            "name => { const f = window.__frames, l = f[f.length - 1]; return l && l.t > 900 && l.href.includes(name) && l.ready === 'complete' }",
                            arg=name,
                        )
                        return page.evaluate("window.__frames")

                    def check(frames: list[dict], name: str, y: float, widths: int = 1) -> None:
                        new = [f for f in frames if name in f["href"] and f["body"]]
                        self.assertTrue(new, f"{name} never showed")
                        self.assertEqual({f["op"] for f in frames}, {"1"}, "the reader faded")
                        self.assertEqual({f["anims"] for f in frames}, {0}, "the reader animated")
                        self.assertLessEqual(len({f["w"] for f in frames}), widths, "the reader's width eased")
                        bare = [f for f in new if not (f["look"] == "light" and f["css"])]
                        self.assertEqual(bare, [], f"{name} painted without the vault look")
                        self.assertNotIn("dark", {f["color"] for f in new}, f"{name}'s theme flipped")
                        ys = [f["y"] for f in new]
                        self.assertAlmostEqual(ys[-1], y, delta=2)
                        self.assertEqual([v for v in ys if 2 < v < y - 2], [], f"{name} rode to its position")
                        loaded = [f for f in new if f["ready"] == "complete"]
                        self.assertAlmostEqual(loaded[0]["y"], y, delta=2, msg=f"{name} jumped after it loaded")

                    # A note to a note, from its row.
                    frames = measure(lambda: page.locator("#tree a.file[data-path$='Beta.md']").click(), "Beta.md")
                    check(frames, "Beta.md", 900)
                    expect(reader.locator("h1")).to_have_text("Beta")

                    # Notes to Artifacts: the sidebar is wider there, and the reader takes the narrower width at once.
                    page.evaluate(SAMPLE)
                    page.locator(".vault-switch a", has_text="Artifacts").click()
                    expect(page.locator("#reader-empty")).to_be_visible()
                    page.wait_for_function("() => window.__frames[window.__frames.length - 1].t > 400")
                    frames = page.evaluate("window.__frames")
                    self.assertEqual(len({f["w"] for f in frames}), 2, "the reader's width eased, or never changed")
                    self.assertEqual({f["anims"] for f in frames}, {0})

                    # An artifact whose own CSS scrolls smoothly still opens at its place, not after a ride down the page.
                    frames = measure(lambda: page.locator("#tree a.file", has_text="Long page").click(), "long.html")
                    check(frames, "long.html", 1500)

                    # Back to Notes: its last note comes back, at its place, in the vault look, at the wider width at once.
                    frames = measure(lambda: page.locator(".vault-switch a", has_text="Notes").click(), "Beta.md")
                    check(frames, "Beta.md", 900, widths=2)
                    browser.close()

        self.assertEqual(page_errors, [])

    def test_vault_switch_swaps_in_place_without_a_reload(self) -> None:
        # Notes ⇄ Artifacts is one shell: the sidebar stays put (no reload, no "Loading…" between trees), the segment's
        # pill slides across, the reader brings back each vault's last page, and history that crosses vaults brings the
        # sidebar along. Both engines: the app is WebKit.
        notes = self.root / "vault"
        notes.mkdir()
        alpha = notes / "Alpha.md"
        alpha.write_text("# Alpha\n\nA note.\n", encoding="utf-8")
        artifacts = self.root / "Artifacts"
        (artifacts / "Pages").mkdir(parents=True)
        one = artifacts / "Pages" / "one.html"
        one.write_text("<title>Page one</title><p id=first>First page.</p>", encoding="utf-8")
        storage: Storage = self.app.state.storage
        storage.update_settings({"vault_root": str(notes), "html_vault_root": str(artifacts)}, model_default="sonnet")
        storage.add_root(notes)
        kind = lambda k: re.compile(rf"\bkind-{k}\b")  # noqa: E731

        page_errors: list[str] = []
        with sync_playwright() as playwright:
            for engine in ("chromium", "webkit"):
                with self.subTest(engine=engine):
                    browser = getattr(playwright, engine).launch(headless=True)
                    page = browser.new_page(viewport={"width": 1100, "height": 700})
                    page.on("pageerror", lambda error, engine=engine: page_errors.append(f"{engine}: {error}"))
                    page.goto(f"{self.base_url}/vault?src={urllib.parse.quote(str(alpha))}", wait_until="networkidle")
                    reader = page.frame_locator("iframe[name=reader]")
                    expect(reader.locator("h1")).to_have_text("Alpha")
                    # A marker a reload would wipe, and a watch for the tree ever reading "Loading…".
                    page.evaluate(
                        "window.__same = true; window.__loading = 0; const t = document.getElementById('tree');"
                        " new MutationObserver(() => { if (/Loading/.test(t.textContent)) __loading++ })"
                        ".observe(t, {childList: true, subtree: true, characterData: true})"
                    )
                    switch, pill = page.locator(".vault-switch"), "e => getComputedStyle(e, '::before').transform"
                    at_notes = switch.evaluate(pill)

                    page.locator(".vault-switch a", has_text="Artifacts").click()
                    expect(page.locator("body")).to_have_class(kind("html"))
                    self.assertTrue(page.evaluate("window.__same === true"), "switching vaults reloaded the page")
                    expect(page.locator(".vault-switch a.active")).to_have_text("Artifacts")
                    expect(page.locator(".brand-name")).to_have_text("Artifacts")
                    expect(page.locator("#add-toggle")).to_be_visible()
                    expect(page.locator("#vault-filter")).to_have_attribute("placeholder", "Filter pages… (press /)")
                    expect(page.locator("#tree a.file", has_text="Page one")).to_be_visible()
                    expect(page.locator("#vault-count")).to_have_text("1 page")
                    expect(page.locator("#reader-empty")).to_contain_text("Pick a page from the sidebar.")  # none read yet
                    self.assertIn("vault=html", page.url)
                    # The pill went across, to the third segment. Read once it has moved and settled: Notes is the
                    # middle segment, and WebKit reports a transition's first frame as where it started.
                    page.wait_for_function(
                        "from => getComputedStyle(document.querySelector('.vault-switch'), '::before').transform !== from",
                        arg=at_notes,
                    )
                    page.wait_for_timeout(250)
                    link = page.locator(".vault-switch a.active").bounding_box()["width"]
                    self.assertAlmostEqual(switch.evaluate("e => parseFloat(getComputedStyle(e, '::before').width)"), link, delta=0.5)
                    shift = switch.evaluate("e => new DOMMatrix(getComputedStyle(e, '::before').transform).m41")
                    self.assertAlmostEqual(shift, 2 * (link + 2), delta=0.5)

                    page.locator("#tree a.file", has_text="Page one").click()
                    expect(reader.locator("#first")).to_have_text("First page.")
                    page.wait_for_function("() => location.search.includes('vault=html') && location.search.includes('src=')")

                    # Back to Notes: its last note comes back, highlighted, and the + goes.
                    page.locator(".vault-switch a", has_text="Notes").click()
                    expect(page.locator("body")).to_have_class(kind("notes"))
                    expect(reader.locator("h1")).to_have_text("Alpha")
                    expect(page.locator("#tree a.active")).to_have_attribute("data-path", str(alpha))
                    expect(page.locator("#add-toggle")).to_be_hidden()
                    expect(page.locator(".brand-name")).to_have_text("Vault")
                    self.assertNotIn("vault=html", page.url)

                    # Back in history crosses into Artifacts again, and the sidebar comes with it.
                    page.evaluate("history.back()")
                    expect(reader.locator("#first")).to_have_text("First page.")
                    expect(page.locator("body")).to_have_class(kind("html"))
                    expect(page.locator("#tree a.active")).to_have_attribute("data-path", str(one))

                    # The app menu's Vault / Artifacts items (⌘⇧V / ⌘⇧H) switch through this, and load when it's absent.
                    self.assertTrue(page.evaluate("onyxVault.switchTo('notes')"))
                    expect(page.locator("body")).to_have_class(kind("notes"))
                    expect(reader.locator("h1")).to_have_text("Alpha")

                    self.assertTrue(page.evaluate("window.__same === true"))
                    self.assertEqual(page.evaluate("__loading"), 0)
                    browser.close()

        self.assertEqual(page_errors, [])

    def test_home_lists_each_conversation_once_with_its_lines_across_the_row(self) -> None:
        # A follow-up is saved as an ask of its own naming the one it followed: home lists the conversation once, by its
        # latest question, with how many asks it holds, while See all keeps every turn. Onyx.app's WebKit (17) gives each
        # <button> align-items:flex-start, which neither Playwright engine does, so a row's lines shrank to their text
        # there: the question ran off the page and the age sat against the title. The injected rule stands in for it.
        notes = self.root / "vault"
        notes.mkdir()
        alpha = notes / "Alpha.md"
        alpha.write_text("# Alpha\n\nA note.\n", encoding="utf-8")
        storage: Storage = self.app.state.storage
        storage.update_settings({"vault_root": str(notes)}, model_default="sonnet")
        storage.add_root(notes)
        source = str(alpha.resolve())
        doc_id = storage.upsert_document(source=source, title="Alpha", kind="markdown", folder=str(notes))
        long_question = " ".join(["so the else is basically the wrapping of the entire call in a try/except statement?"] * 4)
        for request_id, parent, action, question in (
            ("req-single", None, "ask", "What is this note?"),
            ("req-root", None, "eli5", ""),
            ("req-follow", "req-root", "ask", "And the server side limit?"),
            ("req-latest", "req-follow", "ask", long_question),
        ):
            storage.start_conversation(
                request_id=request_id, document_id=doc_id, document_source=source, document_title="Alpha",
                document_page=None, selection="A note.", context="", action=action, question=question,
                folder=str(notes), provider="claude", model="sonnet", parent_request_id=parent,
            )
            storage.finish_conversation(request_id, status="complete", answer="An answer.")

        # Every <button> laid out as a column, and how far each of its lines ends from the row's content edge: 0 is across it.
        line_gaps = r"""() => [...document.querySelectorAll('button')].filter(b => {
          const s = getComputedStyle(b);
          return s.display.endsWith('flex') && s.flexDirection === 'column' && b.getClientRects().length;
        }).map(b => {
          const edge = b.getBoundingClientRect().right - parseFloat(getComputedStyle(b).paddingRight);
          return [b.className, [...b.children].map(c => Math.round(edge - c.getBoundingClientRect().right))];
        })"""
        page_errors: list[str] = []
        with sync_playwright() as playwright:
            for engine in ("chromium", "webkit"):
                with self.subTest(engine=engine):
                    browser = getattr(playwright, engine).launch(headless=True)
                    page = browser.new_page(viewport={"width": 1200, "height": 760})
                    page.on("pageerror", lambda error, engine=engine: page_errors.append(f"{engine}: {error}"))
                    page.goto(self.base_url, wait_until="networkidle")
                    page.add_style_tag(content="button{align-items:flex-start}")
                    rows = page.locator("#home-asks .home-ask")
                    expect(rows).to_have_count(2)
                    expect(rows.first.locator(".q")).to_have_text(long_question)
                    expect(rows.first.locator(".a")).to_have_text(re.compile(r"^3 asks · "))
                    expect(rows.nth(1).locator(".q")).to_have_text("What is this note?")
                    expect(rows.nth(1).locator(".a")).not_to_contain_text("asks")
                    page.locator("#home-all").click()
                    expect(page.locator("#history-results .hist-row")).to_have_count(4)
                    measured = page.evaluate(line_gaps)
                    self.assertLessEqual({"home-ask", "hist-row"}, {cls for cls, _ in measured})
                    for cls, gaps in measured:
                        self.assertEqual(gaps, [0] * len(gaps), f"{engine} .{cls}: a line stops short of or runs past the row")
                    self.assertTrue(
                        rows.first.locator(".q").evaluate("q => q.scrollWidth > q.clientWidth"),
                        f"{engine}: the long question is not clipped inside its row",
                    )
                    browser.close()
        self.assertEqual(page_errors, [])

    def test_library_is_home_and_settings_and_history_are_dialogs(self) -> None:
        # The app opens on Library: both vaults' trees under their headings, and a home page of what was read and asked.
        # Settings and Recent conversations are dialogs at the sidebar's foot; a setting applies as it changes, and a saved
        # conversation opens in the reader beside the sidebar, never in place of it. Both engines: the app is WebKit.
        notes = self.root / "vault"
        notes.mkdir()
        alpha = notes / "Alpha.md"
        alpha.write_text("# Alpha\n\nA note.\n", encoding="utf-8")
        artifacts = self.root / "Artifacts"
        (artifacts / "Pages").mkdir(parents=True)
        one = artifacts / "Pages" / "one.html"
        one.write_text("<title>Page one</title><p id=first>First page.</p>", encoding="utf-8")
        storage: Storage = self.app.state.storage
        storage.update_settings({"vault_root": str(notes), "html_vault_root": str(artifacts)}, model_default="sonnet")
        storage.add_root(notes)
        doc_id = storage.upsert_document(source=str(alpha.resolve()), title="Alpha", kind="markdown", folder=str(notes))
        storage.start_conversation(
            request_id="req-alpha", document_id=doc_id, document_source=str(alpha.resolve()), document_title="Alpha",
            document_page=None, selection="A note.", context="", action="ask", question="What is this note?",
            folder=str(notes), provider="claude", model="sonnet",
        )
        storage.finish_conversation("req-alpha", status="complete", answer="It is a **note**.")

        def saved_theme() -> str:
            return storage.settings(model_default="sonnet")["appearance_theme"]

        page_errors: list[str] = []
        with sync_playwright() as playwright:
            for engine in ("chromium", "webkit"):
                with self.subTest(engine=engine):
                    storage.update_settings({"appearance_theme": "system"}, model_default="sonnet")
                    storage.upsert_document(source=str(one.resolve()), title="Page one", kind="html", folder=str(self.root))
                    browser = getattr(playwright, engine).launch(headless=True)
                    page = browser.new_page(viewport={"width": 1200, "height": 760})
                    page.on("pageerror", lambda error, engine=engine: page_errors.append(f"{engine}: {error}"))
                    page.goto(self.base_url, wait_until="networkidle")
                    expect(page.locator("body")).to_have_class(re.compile(r"\bkind-library\b"))
                    expect(page.locator("#tree .group-head .lbl")).to_have_text(["Notes", "Artifacts"])
                    expect(page.locator("#tree a.file[data-vault=html]", has_text="Page one")).to_be_visible()
                    expect(page.locator("#vault-count")).to_have_text("1 note · 1 page")
                    cards = page.locator("#home-docs .home-card")
                    expect(cards).to_have_count(2)
                    expect(cards.first.locator(".t")).to_have_text("Page one")
                    expect(cards.first.locator(".tag")).to_have_text("Artifact")
                    expect(cards.nth(1).locator(".tag")).to_have_text("Note")
                    expect(page.locator("#home-asks .home-ask")).to_contain_text("What is this note?")

                    # A card reads in the reader beside the sidebar, highlighted in its tree; Library again goes home.
                    cards.first.click()
                    reader = page.frame_locator("iframe[name=reader]")
                    expect(reader.locator("#first")).to_have_text("First page.")
                    expect(page.locator("#home")).to_be_hidden()
                    expect(page.locator("#tree a.active")).to_have_attribute("data-path", str(one))
                    page.locator(".vault-switch a", has_text="Library").click()
                    expect(page.locator("#home")).to_be_visible()
                    self.assertEqual(urllib.parse.urlparse(page.url).path, "/")

                    # Settings: the cog's dialog. The theme applies at once and is saved, and there is no Save button.
                    page.get_by_role("button", name="Settings", exact=True).click()
                    dialog = page.locator("#settings-modal")
                    expect(dialog).to_be_visible()
                    # Its body is taller than the window, so the dialog stands at its cap (760 less 48). It has no height
                    # of its own, and a flex:1 body there collapses to its padding in WebKit: a strip with the title in it.
                    self.assertGreater(dialog.bounding_box()["height"], 600)
                    self.assertEqual(dialog.get_by_role("button", name=re.compile("^Save")).count(), 0)
                    page.select_option("#appearance-theme", "dark")
                    expect(page.locator("html")).to_have_attribute("data-theme", "dark")
                    for _ in range(50):
                        if saved_theme() == "dark":
                            break
                        time.sleep(0.1)
                    self.assertEqual(saved_theme(), "dark")
                    # A number out of range is refused and put back.
                    first = page.locator("#first-activity")
                    expect(first).not_to_have_value("")
                    before = first.input_value()
                    first.fill("5")
                    first.press("Tab")
                    expect(first).to_have_value(before)
                    page.keyboard.press("Escape")
                    expect(dialog).to_be_hidden()

                    # Recent conversations: the clock's dialog. One conversation opens in place of the list, and
                    # Continue puts it in the reader beside the sidebar.
                    page.get_by_role("button", name="Recent conversations", exact=True).click()
                    recent = page.locator("#history-modal")
                    expect(recent).to_be_visible()
                    row = recent.locator(".hist-row", has_text="What is this note?")
                    expect(row).to_be_visible()
                    recent.locator("#history-action [role=radio]", has_text="ELI5").click()
                    expect(recent.locator(".hist-row")).to_have_count(0)
                    recent.locator("#history-action [role=radio]", has_text="All").click()
                    row.click()
                    expect(page.locator("#history-detail-answer")).to_have_text("It is a note.")
                    expect(page.locator("#history-detail-answer strong")).to_have_text("note")  # drawn, not **raw**
                    recent.get_by_role("button", name="Continue", exact=True).click()
                    expect(recent).to_be_hidden()
                    expect(reader.locator("h1")).to_have_text("Alpha")
                    search = page.locator("iframe[name=reader]").evaluate("f => f.contentWindow.location.search")
                    self.assertIn("history=req-alpha", search)
                    self.assertIn("history_action=continue", search)
                    expect(page.locator("#tree a.active")).to_have_attribute("data-path", str(alpha))

                    # The launcher's old links name a dialog, and land on it.
                    page.goto(self.base_url + "/#diagnostics", wait_until="networkidle")
                    expect(page.locator("#settings-modal")).to_be_visible()
                    self.assertNotIn("#", page.url)
                    browser.close()

        self.assertEqual(page_errors, [])

    def test_the_reader_and_the_answer_panel_wear_the_vault_look(self) -> None:
        # Match vault appearance dresses the reader too: a text page takes the vault's reading styles and the answer
        # panel its palette, in the vault's mode whatever the app theme says; off, they are Onyx's own again, live.
        # Both engines: the app is WebKit.
        vault = self.root / "vault"
        vault.mkdir()
        text = vault / "plain.txt"
        text.write_text("Plain words to ask about.\n", encoding="utf-8")
        storage: Storage = self.app.state.storage
        storage.update_settings({"vault_root": str(vault), "appearance_theme": "dark"}, model_default="sonnet")
        storage.save_markdown_theme(vault, {"mode": "light", "styles": {
            "content": {"background-color": "rgb(253, 246, 227)", "color": "rgb(0, 43, 54)", "font-family": '"JetBrains Mono", monospace'},
            "a": {"color": "rgb(203, 75, 22)"},
            "code": {"background-color": "rgb(224, 215, 184)"},
        }})
        look = lambda on: storage.update_settings(  # noqa: E731
            {"markdown_follow_obsidian": on, "sidebar_follow_obsidian": on}, model_default="sonnet"
        )
        page_errors: list[str] = []
        with sync_playwright() as playwright:
            for engine in ("chromium", "webkit"):
                with self.subTest(engine=engine):
                    look(True)
                    browser = getattr(playwright, engine).launch(headless=True)
                    page = browser.new_page(viewport={"width": 1100, "height": 700}, color_scheme="dark")
                    page.on("pageerror", lambda error, engine=engine: page_errors.append(f"{engine}: {error}"))
                    page.goto(f"{self.base_url}/view?{urllib.parse.urlencode({'src': str(text)})}", wait_until="networkidle")
                    html = page.locator("html")
                    expect(html).to_have_attribute("data-askw-look", "light")
                    expect(html).to_have_attribute("data-askw-color", "light")  # the vault's mode, not the app's dark
                    expect(page.locator("body")).to_have_css("background-color", "rgb(253, 246, 227)")
                    panel = page.locator(".askw-panel")
                    self.assertEqual(panel.evaluate("e => getComputedStyle(e).backgroundColor"), "rgba(253, 246, 227, 0.97)")
                    accent = panel.evaluate("e => getComputedStyle(e).getPropertyValue('--askw-accent').trim()")
                    self.assertEqual(accent.replace(",", "").replace("  ", " "), "rgb(203 75 22)")
                    look(False)
                    expect(html).not_to_have_attribute("data-askw-look", re.compile(".*"), timeout=8000)
                    expect(html).to_have_attribute("data-askw-color", "dark")
                    self.assertNotEqual(panel.evaluate("e => getComputedStyle(e).backgroundColor"), "rgba(253, 246, 227, 0.97)")
                    browser.close()

        self.assertEqual(page_errors, [])

    def test_the_vault_look_keeps_every_label_readable_where_it_lands(self) -> None:
        # Match vault appearance once drew labels in colours it had only checked against the vault's own ground. Over
        # a cream page a dark vault left the Ask button's text in the vault's ink on its cyan link colour (1:1), a white
        # arrow on that cyan, the vault's pale ink on the pill's light glass, and the page's own dark `strong` and
        # `code` inside the dark panel. Whatever the vault's mode and the page's tone, each label must read on what is
        # under it: text at 4.5:1, the accent's marks at the 3:1 the palette holds the accent to. Muted and faint
        # metadata are quiet by design and not listed. Both engines: the app is WebKit.
        text, mark = 4.5, 3.0
        vault = self.root / "vault"
        vault.mkdir()
        storage: Storage = self.app.state.storage
        storage.update_settings({"vault_root": str(vault)}, model_default="sonnet")
        vaults = {
            "dark": {"mode": "dark", "styles": {
                "content": {"background-color": "rgb(26, 26, 26)", "color": "rgb(196, 197, 181)"},
                "a": {"color": "rgb(88, 209, 235)"},
                "code": {"background-color": "rgb(20, 20, 20)"},
            }},
            "light": {"mode": "light", "styles": {
                "content": {"background-color": "rgb(253, 246, 227)", "color": "rgb(0, 43, 54)"},
                "a": {"color": "rgb(203, 75, 22)"},
                "code": {"background-color": "rgb(224, 215, 184)"},
            }},
        }
        # Pages that colour their own text elements, as the HTML Artifact Kit's do: dark ink on cream, pale ink on dark.
        pages = {}
        for tone, ground, ink in (("light", "#FDF6E3", "#073642"), ("dark", "#1E1E1E", "#EEEEEE")):
            pages[tone] = self.root / f"{tone}-page.html"
            pages[tone].write_text(
                f"<!doctype html><title>{tone}</title><style>body{{background:{ground};color:{ink}}}"
                f"p,h2,strong,b,em,li,th,td,code{{color:{ink}}}code{{background:{ground}}}</style>"
                "<p id=passage>Select this passage to ask about it.</p>",
                encoding="utf-8",
            )
        answer = (
            "## A heading\n\nPlain words with **bold** and `code` in them.\n\n- A listed point\n\n"
            "| Term | Meaning |\n|---|---|\n| cell | `value` |\n"
        )
        failing = {"now": False}

        async def canned(*args, **kwargs):
            if failing["now"]:
                yield _sse("error", {"message": "It failed."})
                return
            yield _sse("token", {"text": answer})
            yield _sse("done", {"elapsed_ms": 5})

        page_errors: list[str] = []
        with patch("ask_widget.app.stream_answer", canned), sync_playwright() as playwright:
            for engine in ("chromium", "webkit"):
                browser = getattr(playwright, engine).launch(headless=True)
                for mode, tone in (("dark", "light"), ("light", "dark"), ("dark", "dark"), ("light", "light")):
                    with self.subTest(engine=engine, vault=mode, page=tone):
                        storage.save_markdown_theme(vault, vaults[mode])
                        failing["now"] = False
                        ratios: dict[str, tuple[float, float]] = {}

                        def measure(name, locator, least, ratios=ratios) -> None:
                            expect(locator).to_be_visible()
                            ratios[name] = (locator.evaluate(CONTRAST), least)

                        page = browser.new_page(viewport={"width": 1100, "height": 760})
                        page.on("pageerror", lambda error, engine=engine: page_errors.append(f"{engine}: {error}"))
                        query = urllib.parse.urlencode({"src": str(pages[tone]), "folder": str(self.root)})
                        page.goto(f"{self.base_url}/view?{query}", wait_until="networkidle")
                        expect(page.locator("html")).to_have_attribute("data-askw-look", mode)
                        expect(page.locator("html")).to_have_attribute("data-askw-page", tone)

                        passage = page.locator("#passage")
                        passage.select_text()
                        passage.dispatch_event("mouseup", {"button": 0})
                        trigger = page.get_by_role("button", name="Ask about the selected text")
                        measure("Ask button", trigger, text)
                        trigger.click()
                        page.get_by_role("button", name="Ask a question…").click()
                        measure("menu item", page.locator(".askw-item").first, text)
                        page.get_by_label("Question about the highlighted text").fill("What is it?")
                        go = page.get_by_role("button", name="Go", exact=True)
                        measure("Go", go, text)
                        go.click()

                        panel = page.get_by_role("dialog", name="Onyx answer")
                        expect(panel).to_have_attribute("aria-busy", "false", timeout=15000)
                        reply = panel.locator(".askw-a").last
                        measure("eyebrow", panel.locator(".askw-eyebrow"), mark)
                        measure("selection", panel.locator(".askw-selq"), text)
                        measure("answer", reply.locator("p").first, text)
                        measure("heading", reply.locator("h2"), text)
                        measure("bold", reply.locator("strong"), text)
                        measure("inline code", reply.locator("p code"), text)
                        measure("list item", reply.locator("li"), text)
                        measure("table header", reply.locator("th").first, text)
                        measure("table cell", reply.locator("td").first, text)
                        measure("Open session", panel.locator(".askw-claude"), text)
                        measure("Copy", panel.locator(".askw-copy"), text)
                        measure("chats count", page.locator(".askw-chats-n"), mark)
                        measure("chats icon", page.locator(".askw-chats .askw-ico"), mark)

                        panel.get_by_label("Follow-up question").fill("And then?")
                        send = panel.locator(".askw-follow-go")
                        measure("send", send, mark)  # an icon: the arrow, in Onyx's red
                        failing["now"] = True
                        send.click()
                        measure("error", panel.locator(".askw-err"), text)
                        measure("Retry", panel.locator(".askw-retry"), mark)

                        pill = page.locator(".askw-pill")
                        measure("pill icon", pill.locator(".askw-ico"), mark)  # at rest, on its thinnest glass
                        pill.click()
                        expect(pill.locator("b")).to_have_css("opacity", "1")
                        measure("pill label", pill.locator("b"), text)
                        measure("picker label", page.locator(".askw-picker label").first, text)
                        measure("picker Save", page.locator(".askw-picker-save"), text)
                        page.close()

                        unreadable = {name: f"{ratio}:1 < {least}:1" for name, (ratio, least) in ratios.items() if ratio < least}
                        self.assertEqual(unreadable, {})
                browser.close()

        self.assertEqual(page_errors, [])

    def test_html_vault_lists_titles_links_pages_and_reads_them(self) -> None:
        html_vault = self.root / "Artifacts"
        topic = self.root / "learnings" / "topic"
        guide = topic / "guides" / "who-holds-the-plan"
        guide.mkdir(parents=True)
        (guide / "index.html").write_text(
            "<!doctype html><html><head><title>Who holds the plan</title>"
            "<style>body{background:#FDF6E3}</style></head><body><main>"
            '<section id="predict"><p>Before you read, predict the answer.</p></section>'
            "</main></body></html>",
            encoding="utf-8",
        )
        (html_vault / "Study").mkdir(parents=True)
        (html_vault / "Architect").symlink_to(topic, target_is_directory=True)
        report = self.root / "elsewhere" / "report.html"
        report.parent.mkdir()
        report.write_text("<title>Quarterly report</title><body><p>Numbers.</p></body>", encoding="utf-8")
        storage: Storage = self.app.state.storage
        storage.update_settings({"html_vault_root": str(html_vault)}, model_default="sonnet")
        storage.add_root(self.root / "learnings")

        console_errors: list[str] = []
        page_errors: list[str] = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 760})
            page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            page.goto(f"{self.base_url}/vault?vault=html", wait_until="networkidle")

            self.assertEqual(page.locator("#vault-count").inner_text(), "1 page")
            self.assertEqual(page.locator(".vault-switch a.active").inner_text(), "Artifacts")
            self.assertNotEqual(page.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--pane-alpha')"), "")
            # The live slider (JS) and the first paint (Python) must be one curve.
            for t in (0, 0.1, 0.38, 0.7, 1):
                for dark in (True, False):
                    js = page.evaluate("([t, dark]) => glassAlphas(t, dark)", [t, dark])
                    expected = glass_alphas(t, dark)
                    for key, value in zip(("pane", "sidebar", "surface"), expected):
                        self.assertAlmostEqual(js[key], value, places=9, msg=(t, dark, key))
            link = page.locator("#tree a.file", has_text="Who holds the plan")
            self.assertEqual(link.count(), 1)
            self.assertEqual(page.locator("#tree summary", has_text="Study").count(), 1)  # empty, but the vault's own: listed
            link.click()
            reader = page.frame_locator("iframe[name=reader]")
            self.assertEqual(reader.locator("#predict p").inner_text(), "Before you read, predict the answer.")
            self.assertEqual(
                reader.locator('meta[name="askw-folder"]').get_attribute("content"), str(topic.resolve())
            )
            page.locator("#tree a.file.active").wait_for()
            page.wait_for_function("() => location.search.includes('vault=html') && location.search.includes('src=')")
            # An opaque page keeps its own background; the canvas guard leaves it alone.
            self.assertEqual(reader.locator("html").evaluate("e => e.style.backgroundColor"), "")

            page.locator("#add-toggle").click()
            page.locator("#add-panel").wait_for(state="visible")
            self.assertFalse(page.locator("#add-pick-files").is_visible())  # native picker only
            page.locator("#add-dest").select_option("Study")
            page.locator("#add-path").fill(report.as_uri())
            page.locator("#add-path-go").click()
            expect(page.locator("#add-status")).to_contain_text("Linked 1 item", timeout=8000)
            page.locator("#tree a.file", has_text="Quarterly report").wait_for()
            self.assertTrue((html_vault / "Study" / "report.html").is_symlink())
            self.assertEqual(page.locator("#vault-count").inner_text(), "2 pages")

            page.locator("#add-folder-name").fill("Week 1")
            page.locator("#add-mkdir").click()
            # Made but empty: + offers it at once, the list only once it holds a page.
            expect(page.locator('#add-dest option[value="Study/Week 1"]')).to_have_count(1)
            self.assertEqual(page.locator("#tree summary", has_text="Week 1").count(), 1)  # empty, but the vault's own
            self.assertTrue((html_vault / "Study" / "Week 1").is_dir())

            page.locator("#add-dest").select_option("")
            page.locator("#add-path").fill(str(topic / "guides"))
            page.locator("#add-path-go").click()
            expect(page.locator("#add-status")).to_contain_text("Linked 1 item", timeout=8000)
            # Linked folders are another tree: never offered as a place to add.
            options = page.locator("#add-dest option").evaluate_all("o => o.map(x => x.value)")
            self.assertNotIn("Architect", options)
            self.assertNotIn("guides", options)
            self.assertIn("Study/Week 1", options)

            page.locator("#vault-filter").fill("quarter")
            page.locator("#tree .results a.file").wait_for()
            self.assertEqual(page.locator("#tree .results a.file span").inner_text(), "Quarterly report")

            # A transparent page opened on its own gets a canvas instead of the bare window.
            page.goto(f"{self.base_url}/view?src={urllib.parse.quote(str(report))}", wait_until="networkidle")
            self.assertEqual(page.evaluate("document.documentElement.style.backgroundColor"), "canvas")
            browser.close()

        self.assertEqual(page_errors, [])
        self.assertEqual(console_errors, [])

if __name__ == "__main__":
    unittest.main()
