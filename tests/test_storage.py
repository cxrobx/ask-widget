from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from unittest.mock import patch

from ask_widget.storage import Storage


class StorageTests(unittest.TestCase):
    def test_first_start_adopts_the_ask_widget_database_and_leaves_it_in_place(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()  # Storage resolves /var → /private/var
            legacy_dir, new_dir = root / "Ask Widget", root / "Onyx"
            legacy = legacy_dir / "ask-widget.db"
            old = Storage(legacy_dir)
            old.upsert_document(source="/notes/guide.md", title="Guide", kind="markdown", folder="/notes")
            old.update_settings({"appearance_theme": "dark"}, model_default="sonnet")
            old.close()
            (legacy_dir / "onyx.db").rename(legacy)

            with patch("ask_widget.storage.default_data_dir", return_value=new_dir), \
                 patch("ask_widget.storage.legacy_database", return_value=legacy):
                store = Storage()
                self.assertEqual(store.path, new_dir / "onyx.db")
                self.assertEqual([d["title"] for d in store.recent_documents()], ["Guide"])
                self.assertEqual(store.settings()["appearance_theme"], "dark")
                store.upsert_document(source="/notes/new.md", title="New", kind="markdown", folder="/notes")
                store.close()

                # A second start keeps the adopted copy rather than re-copying over it.
                again = Storage()
                self.assertEqual(len(again.recent_documents()), 2)
                again.close()

            self.assertTrue(legacy.is_file())
            self.assertFalse((new_dir / "onyx.db.partial").exists())

    def test_an_explicit_data_dir_never_adopts_the_legacy_database(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            legacy = Path(raw) / "ask-widget.db"
            Storage(Path(raw)).close()
            (Path(raw) / "onyx.db").rename(legacy)
            with patch("ask_widget.storage.legacy_database", return_value=legacy):
                store = Storage(Path(raw) / "fresh")
                self.assertEqual(store.recent_documents(), [])
                store.close()

    def test_settings_documents_conversations_and_export_persist(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            data = Path(raw)
            source = str(data / "guide.md")
            store = Storage(data)
            settings = store.update_settings(
                {
                    "model": "haiku",
                    "response_style": "balanced",
                    "cache_max_entries": 12,
                    "glass_transparency": 64,
                    "appearance_theme": "dark",
                },
                model_default="sonnet",
            )
            self.assertEqual(settings["model"], "haiku")
            self.assertEqual(settings["glass_transparency"], 64)
            self.assertEqual(settings["appearance_theme"], "dark")
            document_id = store.upsert_document(
                source=source, title="Guide", kind="markdown", folder=str(data)
            )
            store.start_conversation(
                request_id="request-1",
                document_id=document_id,
                document_source=source,
                document_title="Guide",
                document_page=None,
                selection="The service is local.",
                context="Context",
                action="prove",
                question="",
                folder=str(data),
                provider="claude",
                model="haiku",
                effort="high",
                request_mode="rerun",
                parent_request_id="request-parent",
            )
            store.finish_conversation(
                "request-1",
                status="complete",
                answer="Supported by `src/app.py:10`.",
                citations=[{"path": "src/app.py", "line": 10}],
                trace=[{"tool": "Read"}],
                latency_ms=125,
            )
            store.close()

            reopened = Storage(data)
            self.assertEqual(reopened.settings()["model"], "haiku")
            self.assertEqual(reopened.recent_documents()[0]["conversation_count"], 1)
            item = reopened.recent_conversations()[0]
            self.assertEqual(item["status"], "complete")
            self.assertEqual(item["provider"], "claude")
            self.assertEqual(item["effort"], "high")
            self.assertEqual(item["request_mode"], "rerun")
            self.assertEqual(item["parent_request_id"], "request-parent")
            self.assertEqual(item["citations"][0]["line"], 10)
            self.assertEqual(reopened.conversation("request-1")["answer"], item["answer"])
            self.assertEqual(len(reopened.recent_conversations(provider="claude", model="haiku")), 1)
            self.assertEqual(reopened.recent_conversations(provider="codex"), [])
            self.assertIn("haiku", reopened.history_facets()["models"])
            exported = reopened.export_markdown(source)
            self.assertIn("# Guide", exported)
            self.assertIn("Supported by", exported)
            reopened.close()

    def test_v2_history_columns_migrate_without_losing_answers(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            data = Path(raw)
            store = Storage(data)
            store.start_conversation(
                request_id="legacy",
                document_id=None,
                document_source="legacy.md",
                document_title="Legacy",
                document_page=None,
                selection="Legacy passage",
                context="",
                action="ask",
                question="Old question?",
                folder=raw,
                provider="claude",
                model="sonnet",
            )
            store.finish_conversation("legacy", status="complete", answer="Old answer.")
            with store._lock:
                store._db.execute("ALTER TABLE conversations DROP COLUMN effort")
                store._db.execute("ALTER TABLE conversations DROP COLUMN request_mode")
                store._db.execute("ALTER TABLE conversations DROP COLUMN parent_request_id")
                store._db.commit()
            store.close()

            migrated = Storage(data)
            item = migrated.conversation("legacy")
            self.assertEqual(item["answer"], "Old answer.")
            self.assertEqual(item["effort"], "medium")
            self.assertEqual(item["request_mode"], "generated")
            self.assertIsNone(item["parent_request_id"])
            migrated.close()

    def test_builtin_root_cannot_be_removed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            data = Path(raw)
            root = data / "root"
            extra = data / "extra"
            root.mkdir()
            extra.mkdir()
            store = Storage(data / "db")
            store.sync_builtin_roots((root,))
            store.add_root(extra)
            self.assertFalse(store.remove_root(root))
            self.assertTrue(store.remove_root(extra))
            store.close()

    def test_ephemeral_service_selections_do_not_clutter_the_library(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            store = Storage(Path(raw))
            store.upsert_document(
                source="service://selection/abc",
                title="Shared selection",
                kind="selection",
                folder=raw,
            )
            self.assertEqual(store.recent_documents(), [])
            self.assertEqual(store.search("Shared selection")["documents"], [])
            self.assertEqual(store.stats()["documents"], 0)
            store.close()

    def test_vault_root_setting_validates_and_normalizes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            data = Path(raw)
            vault = data / "vault"
            vault.mkdir()
            store = Storage(data / "db")
            self.assertTrue(store.settings()["vault_root"].endswith("CX"))
            saved = store.update_settings({"vault_root": f"{vault}/./"}, model_default="sonnet")
            self.assertEqual(saved["vault_root"], str(vault))
            disabled = store.update_settings({"vault_root": "  "}, model_default="sonnet")
            self.assertEqual(disabled["vault_root"], "")
            with self.assertRaises(ValueError):
                store.update_settings({"vault_root": str(data / "missing")}, model_default="sonnet")
            with self.assertRaises(ValueError):
                store.update_settings({"vault_root": "relative/path"}, model_default="sonnet")
            store.close()

    def test_allowed_origins_default_and_update(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            store = Storage(Path(raw))
            self.assertEqual(store.settings()["allowed_origins"], ["app://obsidian.md"])
            saved = store.update_settings(
                {"allowed_origins": ["app://obsidian.md/", "http://localhost:9999", "app://obsidian.md"]},
                model_default="sonnet",
            )
            self.assertEqual(saved["allowed_origins"], ["app://obsidian.md", "http://localhost:9999"])
            self.assertEqual(
                store.update_settings({"allowed_origins": []}, model_default="sonnet")["allowed_origins"], []
            )
            for bad in (["*"], ["app://obsidian.md/path"], "app://obsidian.md", [{"a": 1}]):
                with self.assertRaises(ValueError):
                    store.update_settings({"allowed_origins": bad}, model_default="sonnet")
            with store._lock:
                store._db.execute(
                    "INSERT OR REPLACE INTO settings(key, value_json, updated_at) VALUES('allowed_origins', '\"oops\"', 0)"
                )
                store._db.commit()
            self.assertEqual(store.settings()["allowed_origins"], ["app://obsidian.md"])
            store.close()
