from __future__ import annotations

import socket
import tempfile
import threading
import time
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

import uvicorn
from playwright.sync_api import expect, sync_playwright

from ask_widget.app import create_app
from ask_widget.config import AppConfig
from ask_widget.launcher_ui import glass_alphas
from ask_widget.storage import Storage


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


    def test_html_vault_lists_titles_links_pages_and_reads_them(self) -> None:
        html_vault = self.root / "HTML Vault"
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
            self.assertEqual(page.locator(".vault-switch a.active").inner_text(), "HTML")
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
            page.locator("#tree summary", has_text="Week 1").wait_for()
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
