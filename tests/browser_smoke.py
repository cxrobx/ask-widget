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
from playwright.sync_api import sync_playwright

from ask_widget.app import create_app
from ask_widget.config import AppConfig
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
            self.assertEqual(len(nav_widths), 5)
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


if __name__ == "__main__":
    unittest.main()
