from __future__ import annotations

import re
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from ask_widget.app import create_app
from ask_widget.claude_runner import _sse
from ask_widget.config import AppConfig
from ask_widget.storage import Storage


def make_vault(base: Path) -> tuple[Path, Path]:
    vault = base / "vault"
    outside = base / "outside"
    (vault / "notes").mkdir(parents=True)
    (vault / ".obsidian").mkdir()
    outside.mkdir()
    (vault / "notes" / "Alpha.md").write_text(
        "---\ntags: [career]\n---\n\n# Alpha\n\nSee [[Beta]] and [[Nowhere]].\n", encoding="utf-8"
    )
    (vault / "Beta.md").write_text("# Beta\n", encoding="utf-8")
    (vault / ".obsidian" / "workspace.md").write_text("# Hidden\n", encoding="utf-8")
    (outside / "L.md").write_text("# Linked\n\n[[M]]\n", encoding="utf-8")
    (outside / "M.md").write_text("# M\n", encoding="utf-8")
    (vault / "linked").symlink_to(outside, target_is_directory=True)
    return vault, outside


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.document = self.root / "guide.md"
        self.document.write_text("# Guide\n\nThe server is local.", encoding="utf-8")
        self.document = self.document.resolve()
        self.config = AppConfig(
            default_folder=self.root,
            allowed_roots=(self.root,),
            port=8899,
            data_dir=self.root / "data",
        )
        self.app = create_app(self.config)
        self.client_context = TestClient(self.app, base_url="http://127.0.0.1:8899")
        self.client = self.client_context.__enter__()

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        self.temp.cleanup()

    def test_sidebar_logo_is_the_transparent_gem(self) -> None:
        mark = self.client.get("/onyx-mark.png")
        self.assertEqual(mark.status_code, 200)
        self.assertEqual(mark.headers["content-type"], "image/png")
        self.assertEqual(mark.content[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(mark.content[25], 6)  # IHDR colour type 6: RGBA, so no background
        self.assertIn('<img class=mark src=/onyx-mark.png alt="">', self.client.get("/").text)

    def test_library_settings_and_document_capabilities(self) -> None:
        launcher = self.client.get("/")
        self.assertIn("Window transparency", launcher.text)
        self.assertIn("--pane-alpha", launcher.text)
        self.assertIn("Subscription provider", launcher.text)
        for select_id in (
            "history-provider",
            "history-model",
            "history-document",
            "history-date",
            "history-action",
            "appearance-theme",
            "provider",
            "model",
            "reasoning-effort",
            "response-style",
        ):
            self.assertIn(f"<select id={select_id}", launcher.text)
        self.assertIn("-webkit-appearance:menulist", launcher.text)
        self.assertNotIn('role="listbox"', launcher.text)
        self.assertIn("background:rgb(var(--button-bg))", launcher.text)
        self.assertNotIn(".primary{border:0;background:rgb(var(--accent))", launcher.text)
        self.assertIn("Question history", launcher.text)
        self.assertIn("Ask again", launcher.text)
        response = self.client.get(
            "/view", params={"src": str(self.document), "folder": str(self.root)}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("askw-doc-token", response.text)
        library = self.client.get("/api/library").json()
        self.assertEqual(library["documents"][0]["title"], "Guide")

        updated = self.client.post(
            "/api/settings",
            json={
                "token": self.config.token,
                "settings": {
                    "model": "opus",
                    "response_style": "balanced",
                    "glass_transparency": 52,
                    "appearance_theme": "dark",
                },
            },
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["settings"]["model"], "opus")
        self.assertEqual(updated.json()["settings"]["glass_transparency"], 52)
        self.assertEqual(updated.json()["settings"]["appearance_theme"], "dark")
        self.assertEqual(self.client.get("/config").json()["appearance_theme"], "dark")
        dark_launcher = self.client.get("/")
        self.assertIn('<html data-theme="dark">', dark_launcher.text)
        self.assertIn('name=appearance_theme', dark_launcher.text)

        provider_update = self.client.post(
            "/api/settings",
            json={
                "token": self.config.token,
                "settings": {
                    "provider": "codex",
                    "codex_model": "gpt-5.6-terra",
                    "codex_effort": "high",
                },
            },
        )
        self.assertEqual(provider_update.status_code, 200)
        selected = provider_update.json()["settings"]
        self.assertEqual(selected["provider"], "codex")
        self.assertEqual(selected["model"], "gpt-5.6-terra")
        self.assertEqual(selected["reasoning_effort"], "high")

    def test_local_html_interactions_are_enabled(self) -> None:
        document = self.root / "interactive.html"
        document.write_text(
            "<html><body><button onclick=\"document.body.dataset.clicked='yes'\">Run</button>"
            "<script>document.body.dataset.ready='yes'</script></body></html>",
            encoding="utf-8",
        )

        response = self.client.get("/view", params={"src": str(document)})

        self.assertEqual(response.status_code, 200)
        self.assertIn("onclick=", response.text)
        self.assertIn("dataset.ready", response.text)
        self.assertIn(
            "script-src 'self' 'unsafe-inline'",
            response.headers["content-security-policy"],
        )

    def test_model_catalog_reports_subscription_providers(self) -> None:
        catalogs = [
            {
                "id": "claude",
                "label": "Claude",
                "subscription": True,
                "models": [{"id": "sonnet", "label": "Claude Sonnet"}],
            },
            {
                "id": "codex",
                "label": "Codex",
                "subscription": True,
                "models": [{"id": "gpt-5.6-sol", "label": "GPT-5.6-Sol"}],
            },
        ]
        with patch("ask_widget.app.provider_catalogs", return_value=catalogs):
            response = self.client.get("/api/models")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["id"] for item in response.json()["providers"]], ["claude", "codex"])

    def test_stream_is_traced_and_saved(self) -> None:
        async def fake_stream(*args, **kwargs):
            yield _sse("tool_trace", {"tool": "Read", "input": {"file_path": "src/app.py"}})
            yield _sse("token", {"text": "Supported by `guide.md:3`."})
            yield _sse(
                "citations",
                {"items": [{"kind": "file", "path": str(self.document), "line": 3}]},
            )
            yield _sse("done", {"elapsed_ms": 12})

        body = {
            "token": self.config.token,
            "action": "prove",
            "selection": "The server is local.",
            "context": "Guide context",
            "folder": str(self.root),
            "document_source": str(self.document),
            "document_title": "Guide",
            "request_mode": "rerun",
            "parent_request_id": "parent-request",
        }
        with patch("ask_widget.app.stream_answer", fake_stream):
            response = self.client.post("/ask", json=body)
        self.assertEqual(response.status_code, 200)
        self.assertIn("event: meta", response.text)
        self.assertIn("event: tool_trace", response.text)
        self.assertIn("event: citations", response.text)
        history = self.client.get(
            "/api/history", params={"source": str(self.document), "selection": body["selection"]}
        ).json()["conversations"]
        self.assertEqual(history[0]["status"], "complete")
        self.assertEqual(history[0]["request_mode"], "rerun")
        self.assertEqual(history[0]["parent_request_id"], "parent-request")
        self.assertEqual(history[0]["effort"], "medium")
        self.assertEqual(history[0]["answer"], "Supported by `guide.md:3`.")
        self.assertEqual(history[0]["trace"][0]["tool"], "Read")
        detail = self.client.get(f"/api/conversations/{history[0]['request_id']}").json()
        self.assertEqual(detail["conversation"]["question"], "")
        filtered = self.client.get("/api/library", params={"provider": "claude"}).json()
        self.assertEqual(len(filtered["conversations"]), 1)
        self.assertIn("providers", filtered["facets"])
        self.assertEqual(
            self.client.get("/api/library", params={"provider": "codex"}).json()["conversations"],
            [],
        )

    def test_mutations_require_the_server_token(self) -> None:
        response = self.client.post("/api/settings", json={"settings": {"model": "opus"}})
        self.assertEqual(response.status_code, 403)

    def test_host_origin_and_document_capability_boundaries(self) -> None:
        bad_host = self.client.get(
            "/view",
            params={"src": str(self.document)},
            headers={"host": "attacker.example"},
        )
        self.assertEqual(bad_host.status_code, 403)

        bad_origin = self.client.get(
            "/api/library", headers={"origin": "https://attacker.example"}
        )
        self.assertEqual(bad_origin.status_code, 403)

        viewed = self.client.get("/view", params={"src": str(self.document)})
        match = re.search(r'name="askw-doc-token" content="([^"]+)"', viewed.text)
        self.assertIsNotNone(match)
        capability = match.group(1)
        current = self.client.get(
            "/_mtime", params={"src": str(self.document), "cap": capability}
        )
        self.assertTrue(current.json()["ok"])
        self.app.state.asset_caps[capability]["expires"] = 0
        expired = self.client.get(
            "/_mtime", params={"src": str(self.document), "cap": capability}
        )
        self.assertFalse(expired.json()["ok"])

    def test_position_and_export_round_trip(self) -> None:
        self.client.get("/view", params={"src": str(self.document)})
        updated = self.client.post(
            "/api/position",
            json={
                "token": self.config.token,
                "source": str(self.document),
                "scroll_y": 412.5,
            },
        )
        self.assertEqual(updated.status_code, 200)
        document = self.client.get(
            "/api/document", params={"source": str(self.document)}
        ).json()["document"]
        self.assertEqual(document["scroll_y"], 412.5)
        exported = self.client.get("/api/export", params={"src": str(self.document)})
        self.assertEqual(exported.status_code, 200)
        self.assertIn("Guide", exported.text)


class VaultApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "context"
        self.root.mkdir()
        self.vault, self.outside = make_vault(self.base)
        self.config = AppConfig(
            default_folder=self.root,
            allowed_roots=(self.root,),
            port=8899,
            data_dir=self.base / "data",
        )
        self.app = create_app(self.config)
        self.client_context = TestClient(self.app, base_url="http://127.0.0.1:8899")
        self.client = self.client_context.__enter__()

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        self.temp.cleanup()

    def set_vault(self, value: str):
        return self.client.post(
            "/api/settings", json={"token": self.config.token, "settings": {"vault_root": value}}
        )

    def test_saving_vault_root_registers_an_allowed_root(self) -> None:
        self.assertEqual(self.set_vault(str(self.vault)).status_code, 200)
        roots = [item["path"] for item in self.client.get("/api/settings").json()["roots"]]
        self.assertIn(str(self.vault.resolve()), roots)
        self.assertEqual(self.client.get("/api/settings").json()["settings"]["vault_root"], str(self.vault))
        invalid = self.set_vault(str(self.base / "missing"))
        self.assertEqual(invalid.status_code, 400)
        self.assertIn("does not exist", invalid.json()["error"])

    def test_tree_and_search_are_scoped_and_guarded(self) -> None:
        self.set_vault(str(self.vault))
        tree = self.client.get("/api/vault/tree").json()
        self.assertTrue(tree["ok"])
        self.assertEqual(tree["root"], str(self.vault))
        self.assertEqual(tree["files"], 4)
        names = [child["name"] for child in tree["tree"]["children"]]
        self.assertEqual(names, ["linked", "notes", "Beta.md"])
        self.assertNotIn(".obsidian", names)
        linked = next(child for child in tree["tree"]["children"] if child["name"] == "linked")
        self.assertTrue(linked["symlink"])
        self.assertEqual(linked["children"][0]["path"], str(self.vault / "linked" / "L.md"))
        self.assertNotIn(str(self.outside), str(tree))

        search = self.client.get("/api/vault/search", params={"q": "al"}).json()
        self.assertEqual([item["name"] for item in search["items"]], ["Alpha.md"])
        self.assertEqual(search["items"][0]["folder"], "notes")
        self.assertEqual(self.client.get("/api/vault/search", params={"q": ""}).json()["items"], [])

        forbidden = self.client.get("/api/vault/tree", headers={"origin": "https://attacker.example"})
        self.assertEqual(forbidden.status_code, 403)

        self.set_vault("")
        unset = self.client.get("/api/vault/tree")
        self.assertEqual(unset.status_code, 400)
        self.assertEqual(unset.json()["error"], "No vault folder is configured.")
        self.assertEqual(self.client.get("/api/vault/search", params={"q": "al"}).status_code, 400)

    def test_vault_page_embeds_the_reader_iframe(self) -> None:
        self.set_vault(str(self.vault))
        note = self.vault / "notes" / "Alpha.md"
        page = self.client.get("/vault", params={"src": str(note), "history": "abc", "history_action": "rerun"})
        self.assertEqual(page.status_code, 200)
        expected = urllib.parse.urlencode(
            {"src": str(note), "folder": str(self.vault), "history": "abc", "history_action": "rerun"},
            quote_via=urllib.parse.quote,
            safe="/",
        )
        self.assertIn(f'<iframe id=reader name=reader src="/view?{expected.replace("&", "&amp;")}"', page.text)
        self.assertIn("<title>Alpha.md — Vault</title>", page.text)
        self.assertIn('data-href=/vault>Vault</button>', self.client.get("/").text)
        self.assertIn("id=vault-form", self.client.get("/").text)
        blank = self.client.get("/vault")
        self.assertIn('src="about:blank"', blank.text)
        self.assertEqual(
            self.client.get("/vault", headers={"host": "attacker.example"}).status_code, 403
        )

    def test_symlinked_vault_note_keeps_vault_visible_links(self) -> None:
        self.set_vault(str(self.vault))
        note = self.vault / "linked" / "L.md"
        page = self.client.get("/view", params={"src": str(note), "folder": str(self.vault)})
        self.assertEqual(page.status_code, 200)
        sibling = urllib.parse.quote(str(self.vault / "linked" / "M.md"))
        # The folder seed is the resolved context folder (as ask.js expects);
        # the link target stays the vault-visible path through the symlink.
        self.assertIn(
            f'href="/view?src={sibling}&amp;folder={urllib.parse.quote(str(self.vault.resolve()))}"', page.text
        )
        self.assertIn(
            f'<meta name="askw-src" content="{(self.outside / "L.md").resolve()}">', page.text
        )
        self.assertNotIn(str(self.outside.resolve() / "M.md"), page.text)

        alpha = self.client.get("/view", params={"src": str(self.vault / "notes" / "Alpha.md")})
        self.assertIn('class="askw-properties"', alpha.text)
        self.assertIn('class="askw-wikilink-missing"', alpha.text)
        self.assertIn(urllib.parse.quote(str(self.vault / "Beta.md")), alpha.text)

        outside_note = self.client.get("/view", params={"src": str(self.outside / "L.md")})
        self.assertIn("[[M]]", outside_note.text)  # outside the vault: literal wikilink

    def test_row_menu_entry_reads_the_notes_vault_by_default(self) -> None:
        self.set_vault(str(self.vault))
        linked = self.client.get("/api/vault/entry", params={"path": str(self.vault / "linked" / "L.md")}).json()
        self.assertEqual((linked["real"], linked["link"]), (str((self.outside / "L.md").resolve()), str(self.vault / "linked")))
        own = self.client.get("/api/vault/entry", params={"path": str(self.vault / "Beta.md")}).json()
        self.assertEqual((own["real"], own["link"], own["is_dir"]), (str(self.vault / "Beta.md"), None, False))

    def test_startup_registers_the_configured_vault_root(self) -> None:
        data = self.base / "seeded"
        store = Storage(data)
        store.update_settings({"vault_root": str(self.vault)}, model_default="sonnet")
        store.close()
        app = create_app(
            AppConfig(default_folder=self.root, allowed_roots=(self.root,), port=8899, data_dir=data)
        )
        roots = [item["path"] for item in app.state.storage.roots()]
        self.assertIn(str(self.vault.resolve()), roots)
        app.state.storage.close()


class HtmlVaultApiTests(unittest.TestCase):
    """Artifacts: a folder of symlinks, browsed and extended from the shell."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()
        self.context = self.base / "context"
        self.context.mkdir()
        self.vault = self.base / "Artifacts"
        self.topic = self.base / "learnings" / "topics" / "architect"
        self.guide = self.topic / "guides" / "who-holds-the-plan"
        (self.guide / "audio").mkdir(parents=True)
        (self.vault / "Mine").mkdir(parents=True)
        (self.guide / "index.html").write_text(
            '<html><head><title>Who holds the plan</title></head><body><section id="predict">'
            '<p>Before you read.</p><audio controls preload="none" src="audio/one.m4a"></audio>'
            "</section></body></html>",
            encoding="utf-8",
        )
        self.audio = self.guide / "audio" / "one.m4a"
        self.audio.write_bytes(bytes(range(256)) * 4)  # 1024 bytes
        (self.vault / "Architect").symlink_to(self.topic, target_is_directory=True)
        self.page = self.vault / "Architect" / "guides" / "who-holds-the-plan" / "index.html"
        self.config = AppConfig(
            default_folder=self.context,
            allowed_roots=(self.context,),
            port=8899,
            data_dir=self.base / "data",
        )
        self.app = create_app(self.config)
        self.app.state.storage.update_settings({"html_vault_root": str(self.vault)}, model_default="sonnet")
        self.client_context = TestClient(self.app, base_url="http://127.0.0.1:8899")
        self.client = self.client_context.__enter__()

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        self.temp.cleanup()

    def post(self, url: str, body: dict, token: bool = True):
        return self.client.post(url, json={**({"token": self.config.token} if token else {}), **body})

    def test_tree_search_and_shell_use_titles_and_the_html_kind(self) -> None:
        tree = self.client.get("/api/vault/tree", params={"vault": "html"}).json()
        self.assertTrue(tree["ok"])
        self.assertEqual(tree["vault"], "html")
        self.assertEqual(tree["files"], 1)
        architect = next(c for c in tree["tree"]["children"] if c["name"] == "Architect")
        page = architect["children"][0]["children"][0]
        self.assertEqual((page["title"], page["path"]), ("Who holds the plan", str(self.page)))
        self.assertIn("Mine", [c["name"] for c in tree["tree"]["children"]])
        search = self.client.get("/api/vault/search", params={"vault": "html", "q": "plan"}).json()
        self.assertEqual([(i["title"], i["folder"]) for i in search["items"]], [("Who holds the plan", "Architect/guides")])

        shell = self.client.get("/vault", params={"vault": "html", "src": str(self.page)})
        self.assertEqual(shell.status_code, 200)
        expected = urllib.parse.urlencode({"src": str(self.page)}, quote_via=urllib.parse.quote, safe="/")
        self.assertIn(f'<iframe id=reader name=reader src="/view?{expected}"', shell.text)  # no vault folder
        self.assertIn('const KIND="html"', shell.text)
        self.assertIn("id=add-panel", shell.text)
        self.assertIn('<a href="/vault?vault=html" class=active', shell.text)
        self.assertNotIn("id=add-panel", self.client.get("/vault").text)
        self.assertIn('data-href="/vault?vault=html">Artifacts</button>', self.client.get("/").text)

        self.app.state.storage.update_settings({"html_vault_root": ""}, model_default="sonnet")
        unset = self.client.get("/api/vault/tree", params={"vault": "html"})
        self.assertEqual(unset.json()["error"], "No Artifacts folder is configured.")

    def test_reader_uses_the_real_folder_behind_the_link_when_it_is_allowed(self) -> None:
        # Not yet an allowed root: the reader falls back to the default folder.
        before = self.client.get("/view", params={"src": str(self.page)})
        self.assertIn(f'<meta name="askw-folder" content="{self.context}">', before.text)
        self.client.post("/api/roots", json={"token": self.config.token, "path": str(self.base / "learnings")})
        after = self.client.get("/view", params={"src": str(self.page)})
        self.assertIn(f'<meta name="askw-folder" content="{self.topic}">', after.text)
        # An explicit folder still wins, and the guide's audio is rewritten to a capability URL.
        explicit = self.client.get("/view", params={"src": str(self.page), "folder": str(self.context)})
        self.assertIn(f'<meta name="askw-folder" content="{self.context}">', explicit.text)
        self.assertRegex(after.text, r'<audio controls preload="none" src="http://127\.0\.0\.1:8899/_fs/[^/"]+/')
        self.assertIn(urllib.parse.quote(str(self.audio)), after.text)

    def test_audio_is_served_in_ranges_for_webkit(self) -> None:
        with patch("pathlib.Path.home", return_value=self.base):
            viewed = self.client.get("/view", params={"src": str(self.page)})
            url = re.search(r'<audio controls preload="none" src="http://127\.0\.0\.1:8899([^"]+)"', viewed.text).group(1)
            whole = self.client.get(url)
            self.assertEqual(whole.status_code, 200)
            self.assertEqual(whole.headers["accept-ranges"], "bytes")
            self.assertEqual(whole.headers["content-type"], "audio/mp4")
            self.assertEqual(len(whole.content), 1024)
            probe = self.client.get(url, headers={"range": "bytes=0-1"})
            self.assertEqual(probe.status_code, 206)
            self.assertEqual(probe.headers["content-range"], "bytes 0-1/1024")
            self.assertEqual(probe.content, bytes([0, 1]))
            tail = self.client.get(url, headers={"range": "bytes=-3"})
            self.assertEqual((tail.status_code, tail.content), (206, bytes([253, 254, 255])))
            open_ended = self.client.get(url, headers={"range": "bytes=1000-"})
            self.assertEqual(open_ended.headers["content-range"], "bytes 1000-1023/1024")
            past_end = self.client.get(url, headers={"range": "bytes=5000-"})
            self.assertEqual(past_end.status_code, 416)
            self.assertEqual(past_end.headers["content-range"], "bytes */1024")
            multi = self.client.get(url, headers={"range": "bytes=0-1,4-5"})
            self.assertEqual((multi.status_code, len(multi.content)), (200, 1024))

    def test_link_and_folder_routes_write_only_inside_the_vault(self) -> None:
        other = self.base / "elsewhere" / "report"
        other.mkdir(parents=True)
        (other / "index.html").write_text("<title>Quarterly report</title>", encoding="utf-8")
        self.assertEqual(self.post("/api/vault/html/link", {"target": str(other / "index.html")}, token=False).status_code, 403)
        denied = self.client.post(
            "/api/vault/html/link",
            json={"token": self.config.token, "target": str(other / "index.html")},
            headers={"origin": "https://attacker.example"},
        )
        self.assertEqual(denied.status_code, 403)

        made = self.post("/api/vault/html/folder", {"parent": "", "name": "Reports"})
        self.assertEqual(made.json()["rel"], "Reports")
        linked = self.post("/api/vault/html/link", {"parent": "Reports", "targets": [str(other / "index.html")]}).json()
        self.assertTrue(linked["ok"])
        self.assertEqual(linked["linked"], [str(self.vault / "Reports" / "report.html")])
        self.assertEqual(linked["context_roots"], [str(other)])
        self.assertIn(str(other), [r["path"] for r in self.client.get("/api/settings").json()["roots"]])
        tree = self.client.get("/api/vault/tree", params={"vault": "html"}).json()["tree"]
        reports = next(c for c in tree["children"] if c["name"] == "Reports")
        self.assertEqual(reports["children"][0]["title"], "Quarterly report")

        refused = self.post("/api/vault/html/link", {"parent": "Architect", "target": str(other / "index.html")})
        self.assertEqual(refused.status_code, 400)
        self.assertIn("linked folder", refused.json()["error"])
        self.assertFalse((self.topic / "report.html").exists())
        partial = self.post(
            "/api/vault/html/link",
            {"parent": "Reports", "targets": [str(other / "index.html"), str(self.guide / "index.html")]},
        ).json()
        self.assertTrue(partial["ok"])
        self.assertEqual(len(partial["linked"]), 1)
        self.assertIn("already exists", partial["errors"][0])
        loop = self.post("/api/vault/html/link", {"parent": "Mine", "target": str(self.base)})
        self.assertIn("would loop", loop.json()["error"])
        # A page sitting directly in home links fine, but home never becomes a context root.
        (self.base / "top.html").write_text("<title>Top</title>", encoding="utf-8")
        with patch("pathlib.Path.home", return_value=self.base):
            homed = self.post("/api/vault/html/link", {"parent": "Mine", "target": str(self.base / "top.html")})
        self.assertEqual((homed.json()["ok"], homed.json()["context_roots"]), (True, []))
        self.assertNotIn(str(self.base), [r["path"] for r in self.client.get("/api/settings").json()["roots"]])

    def test_row_menu_routes_take_a_row_and_work_out_the_rest(self) -> None:
        real = str(self.guide / "index.html")
        entry = self.client.get("/api/vault/entry", params={"vault": "html", "path": str(self.page)}).json()
        self.assertEqual((entry["ok"], entry["real"], entry["link"]), (True, real, str(self.vault / "Architect")))
        # The real file is not a row; only paths the tree lists are.
        self.assertEqual(self.client.get("/api/vault/entry", params={"vault": "html", "path": real}).status_code, 400)
        foreign = self.client.get(
            "/api/vault/entry", params={"vault": "html", "path": str(self.page)}, headers={"origin": "https://attacker.example"}
        )
        self.assertEqual(foreign.status_code, 403)

        with patch("ask_widget.vault.reveal_in_finder") as reveal:
            self.assertEqual(self.post("/api/vault/reveal", {"vault": "html", "path": str(self.page)}, token=False).status_code, 403)
            shown = self.post("/api/vault/reveal", {"vault": "html", "path": str(self.page), "which": "real"}).json()
            link = self.post("/api/vault/reveal", {"vault": "html", "path": str(self.page), "which": "link"}).json()
            escape = self.post("/api/vault/reveal", {"vault": "html", "path": str(self.vault / ".." / "context")})
            not_a_link = self.post("/api/vault/reveal", {"vault": "html", "path": str(self.vault / "Mine"), "which": "link"})
        self.assertEqual((shown["revealed"], link["revealed"]), (real, str(self.vault / "Architect")))
        self.assertEqual([call.args[0] for call in reveal.call_args_list], [real, str(self.vault / "Architect")])
        self.assertEqual((escape.status_code, not_a_link.status_code), (400, 400))
        self.assertEqual(not_a_link.json()["error"], "That row is not a link.")


class PluginOriginTests(unittest.TestCase):
    """The Obsidian plugin talks to the same API from app://obsidian.md."""

    OBSIDIAN = "app://obsidian.md"

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.document = (self.root / "guide.md")
        self.document.write_text("# Guide\n\nThe server is local.", encoding="utf-8")
        self.document = self.document.resolve()
        self.config = AppConfig(
            default_folder=self.root,
            allowed_roots=(self.root,),
            port=8899,
            data_dir=self.root / "data",
        )
        self.app = create_app(self.config)
        self.client_context = TestClient(self.app, base_url="http://127.0.0.1:8899")
        self.client = self.client_context.__enter__()

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        self.temp.cleanup()

    def ask(self, origin: str):
        async def fake_stream(*args, **kwargs):
            yield _sse("token", {"text": "ok"})
            yield _sse("done", {"elapsed_ms": 3})

        body = {
            "token": self.config.token,
            "action": "eli5",
            "selection": "The server is local.",
            "folder": str(self.root),
            "document_source": str(self.document),
        }
        with patch("ask_widget.app.stream_answer", fake_stream):
            return self.client.post("/ask", json=body, headers={"origin": origin})

    def test_obsidian_origin_is_allowed_on_ask_and_api(self) -> None:
        response = self.ask(self.OBSIDIAN)
        self.assertIn("event: meta", response.text)
        self.assertNotIn("origin not allowed", response.text)
        self.assertEqual(response.headers["access-control-allow-origin"], self.OBSIDIAN)
        self.assertEqual(response.headers["vary"], "Origin")
        history = self.client.get(
            "/api/history", params={"source": str(self.document)}, headers={"origin": self.OBSIDIAN}
        )
        self.assertEqual(history.status_code, 200)
        self.assertEqual(history.headers["access-control-allow-origin"], self.OBSIDIAN)

    def test_unlisted_origins_are_still_rejected(self) -> None:
        for origin in (
            "https://attacker.example",
            "app://evil.md",
            "app://obsidian.md.evil",
            "app://obsidian.md/",
        ):
            with self.subTest(origin=origin):
                blocked = self.client.get("/api/library", headers={"origin": origin})
                self.assertEqual(blocked.status_code, 403)
                self.assertNotIn("access-control-allow-origin", blocked.headers)
                stream = self.ask(origin)
                self.assertIn("Refused: origin not allowed.", stream.text)
                self.assertNotIn("access-control-allow-origin", stream.headers)

    def test_session_endpoint_returns_token_only_to_allowed_origins(self) -> None:
        allowed = self.client.get("/api/session", headers={"origin": self.OBSIDIAN})
        self.assertEqual(allowed.status_code, 200)
        payload = allowed.json()
        self.assertEqual(payload["token"], self.config.token)
        self.assertEqual(payload["service"], "onyx")
        self.assertEqual(payload["protocol"], 3)
        self.assertEqual(payload["provider"], "claude")
        self.assertEqual(payload["request_timeout"], 120)
        self.assertEqual(allowed.headers["cache-control"], "no-store")

        blocked = self.client.get("/api/session", headers={"origin": "https://attacker.example"})
        self.assertEqual(blocked.status_code, 403)
        self.assertNotIn("token", blocked.json())

        self.assertEqual(self.client.get("/api/session").status_code, 200)

    def test_preflight_echoes_allowed_origin_and_headers(self) -> None:
        for path in ("/ask", "/api/roots"):
            with self.subTest(path=path):
                response = self.client.options(path, headers={"origin": self.OBSIDIAN})
                self.assertEqual(response.status_code, 204)
                self.assertEqual(response.headers["access-control-allow-origin"], self.OBSIDIAN)
                self.assertIn("Content-Type", response.headers["access-control-allow-headers"])

    def test_allowed_origins_setting_is_persisted_and_validated(self) -> None:
        saved = self.client.post(
            "/api/settings",
            json={"token": self.config.token, "settings": {"allowed_origins": ["app://obsidian.md"]}},
        )
        self.assertEqual(saved.json()["settings"]["allowed_origins"], ["app://obsidian.md"])
        for bad in ("*", "app://x/path"):
            rejected = self.client.post(
                "/api/settings", json={"token": self.config.token, "settings": {"allowed_origins": [bad]}}
            )
            self.assertEqual(rejected.status_code, 400)
        self.assertEqual(
            self.client.post(
                "/api/settings", json={"token": self.config.token, "settings": {"allowed_origins": "nope"}}
            ).status_code,
            400,
        )

        cleared = self.client.post(
            "/api/settings", json={"token": self.config.token, "settings": {"allowed_origins": []}}
        )
        self.assertEqual(cleared.json()["settings"]["allowed_origins"], [])
        self.assertEqual(
            self.client.get("/api/library", headers={"origin": self.OBSIDIAN}).status_code, 403
        )
        self.assertEqual(
            self.client.get("/api/library", headers={"origin": "http://localhost:9999"}).status_code, 200
        )

    def test_health_and_plugin_routes_carry_cors_headers(self) -> None:
        headers = {"origin": self.OBSIDIAN}
        health = self.client.get("/health", headers=headers)
        self.assertEqual(health.json()["service"], "onyx")
        self.assertEqual(health.headers["access-control-allow-origin"], self.OBSIDIAN)
        for path, params in (
            ("/api/settings", None),
            ("/api/document", {"source": str(self.document)}),
        ):
            with self.subTest(path=path):
                response = self.client.get(path, params=params, headers=headers)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["access-control-allow-origin"], self.OBSIDIAN)
        added = self.client.post(
            "/api/roots", json={"token": self.config.token, "path": str(self.root)}, headers=headers
        )
        self.assertEqual(added.status_code, 200)
        self.assertEqual(added.headers["access-control-allow-origin"], self.OBSIDIAN)
        opened = self.client.post(
            "/api/open-source",
            json={"token": self.config.token, "path": str(self.document), "folder": str(self.root)},
            headers=headers,
        )
        self.assertIn("access-control-allow-origin", opened.headers)
