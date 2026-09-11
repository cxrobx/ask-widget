from __future__ import annotations

import asyncio
import json
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
            self.assertEqual(
                page.locator("#documents .empty").inner_text(),
                "Documents you open will appear here.",
            )
            self.assertEqual(
                page.locator("#conversations .empty").inner_text(),
                "Your completed answers will be saved here.",
            )

            page.get_by_role("button", name="Settings", exact=True).click()
            page.locator("#provider-status").wait_for()
            self.assertIn("Using Claude", page.locator("#provider-status").inner_text())
            self.assertEqual(page.locator("#model").input_value(), "sonnet")

            page.set_viewport_size({"width": 760, "height": 800})
            page.goto(self.base_url, wait_until="networkidle")
            page.evaluate("document.body.classList.add('native')")
            nav_widths = page.locator("nav button").evaluate_all(
                "buttons => buttons.map(button => button.getBoundingClientRect().width)"
            )
            self.assertEqual(len(nav_widths), 5)  # Diagnostics lives inside Settings
            self.assertTrue(all(width < 160 for width in nav_widths), nav_widths)
            self.assertLess(page.locator("aside").evaluate("aside => aside.offsetHeight"), 160)

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
            "folders": [{"name": "Projects", "color": teal}, {"name": "Archive", "color": red}, {"name": "Inbox", "color": orange}],
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
        # The capture only ever ran inside Obsidian, so a DOM-helper misuse (createSvg with a
        # spaced class string) shipped. Run the real module against Obsidian's helpers, as
        # strict as Obsidian's, on an explorer with a positional rainbow snippet.
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
        tree = lambda names: "".join(  # noqa: E731 — the live explorer, top-level folders only
            f'<div class="tree-item nav-folder"><div class="tree-item-self nav-folder-title" data-path="{n}">{n}</div></div>' for n in names
        )
        explorer = f"""<!doctype html><style>
            body {{ --nav-item-background-hover: rgba(0, 0, 0, 0.05); font: 13px Menlo, monospace; }}
            .mod-left-split {{ background: rgb(253, 246, 227); color: rgb(7, 54, 66); }}
            .nav-folder-title {{ color: rgb(88, 110, 117); padding: 4px 0; }} .nav-file-title {{ color: rgb(7, 54, 66); }}
            .nav-file-title.is-active {{ background: rgb(238, 232, 213); }} .nav-folder-children {{ border-left: 1px solid rgb(147, 161, 161); }}
            .search-input-container input {{ border-radius: 999px; }}
            .nav-files-container > div > .nav-folder > .nav-folder-children > .nav-folder:nth-child(1) > .nav-folder-title {{ color: rgb(220, 50, 47); }}
            .nav-files-container > div > .nav-folder > .nav-folder-children > .nav-folder:nth-child(2) > .nav-folder-title {{ color: rgb(203, 75, 22); }}
            .nav-files-container > div > .nav-folder > .nav-folder-children > .nav-folder:nth-child(2) > .nav-folder-children {{ border-left-color: rgb(203, 75, 22); }}
            </style><body class="theme-light"><div class="workspace-split mod-left-split"><div class="nav-files-container"><div>
            <div class="tree-item nav-folder mod-root"><div class="tree-item-children nav-folder-children">{tree(["Archive", "Inbox", "Projects"])}</div></div>
            </div></div></div></body>"""

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.set_content(explorer)
            page.add_script_tag(content=helpers)
            page.add_script_tag(content=bundle.stdout)
            snapshot = page.evaluate("() => OnyxSidebar.captureSidebarTheme({ vault: { getRoot: () => ({ children: [] }) } })")
            self.assertEqual(page.locator(".onyx-sample-folder").count(), 0)  # the hidden copy is gone again
            browser.close()

        self.assertEqual(snapshot["mode"], "light")
        self.assertEqual(snapshot["styles"]["pane"]["background-color"], "rgb(253, 246, 227)")
        self.assertEqual(snapshot["styles"]["active"]["background-color"], "rgb(238, 232, 213)")
        self.assertEqual(snapshot["styles"]["hover"]["background-color"], "rgba(0, 0, 0, 0.05)")
        self.assertEqual(
            snapshot["folders"],
            [{"name": "Archive", "color": "rgb(220, 50, 47)", "guide": "rgb(147, 161, 161)"},
             {"name": "Inbox", "color": "rgb(203, 75, 22)", "guide": "rgb(203, 75, 22)"},
             {"name": "Projects", "color": "rgb(88, 110, 117)", "guide": "rgb(147, 161, 161)"}],
        )
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

                    # Folders: a linked one offers both sides, the vault's own folder only itself.
                    page.locator("#tree summary", has_text="Architect").click(button="right")
                    expect(menu).to_have_attribute("aria-label", "Folder actions")
                    expect(items).to_have_text(["Reveal in Finder", "Reveal Link in Finder"])
                    page.keyboard.press("Escape")
                    expect(menu).to_be_hidden()
                    page.locator("#tree summary", has_text="Mine").click(button="right")
                    expect(items).to_have_text(["Reveal in Finder"])
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

                    # Remembered across a reload; ⌘\ pins it back, and that is remembered too.
                    page.reload(wait_until="networkidle")
                    expect(pin).to_have_attribute("aria-pressed", "false")
                    expect(side).to_be_hidden()
                    page.keyboard.press("Meta+Backslash")
                    expect(pin).to_have_attribute("aria-pressed", "true")
                    expect(side).to_be_visible()
                    self.assertGreater(reader_x(), 250)
                    page.reload(wait_until="networkidle")
                    expect(pin).to_have_attribute("aria-pressed", "true")
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
            self.assertEqual(page.locator("#tree summary", has_text="Study").count(), 0)  # no page yet: not listed
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
            self.assertEqual(page.locator("#tree summary", has_text="Week 1").count(), 0)
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
