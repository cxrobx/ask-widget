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
