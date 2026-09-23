from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from onyx import first_run
from onyx.app import create_app
from onyx.config import AppConfig
from onyx.storage import DEFAULT_SETTINGS, Storage


def make_plugin(base: Path) -> Path:
    source = base / "built-plugin"
    source.mkdir()
    (source / "manifest.json").write_text(json.dumps({"id": "onyx", "version": "9.9.9"}), encoding="utf-8")
    (source / "main.js").write_text("module.exports = {};\n", encoding="utf-8")
    (source / "styles.css").write_text(".onyx {}\n", encoding="utf-8")
    return source


class FirstRunFolderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.storage = Storage(self.base / "data")
        self.defaults = patch.dict(DEFAULT_SETTINGS, {
            "vault_root": str(self.base / "Documents" / "CX"),
            "html_vault_root": str(self.base / "Documents" / "Artifacts"),
        })
        self.defaults.start()
        self.vault = self.base / "Obsidian" / "Mine"
        self.vault.mkdir(parents=True)
        self.config = self.base / "obsidian.json"

    def tearDown(self) -> None:
        self.defaults.stop()
        self.storage.close()
        self.temp.cleanup()

    def write_config(self, vaults: dict) -> None:
        self.config.write_text(json.dumps({"vaults": vaults}), encoding="utf-8")

    def adopt(self) -> dict:
        first_run.adopt_folders(self.storage, model_default="sonnet", config=self.config)
        return self.storage.settings()

    def test_open_obsidian_vault_is_listed_first_and_missing_ones_are_skipped(self) -> None:
        other = self.base / "Other"
        other.mkdir()
        self.write_config({
            "a": {"path": str(other), "ts": 200},
            "b": {"path": str(self.vault), "ts": 100, "open": True},
            "c": {"path": str(self.base / "gone"), "ts": 300},
            "d": "not an entry",
        })
        self.assertEqual(first_run.obsidian_vaults(self.config), [self.vault, other])

    def test_a_malformed_obsidian_config_finds_no_vaults(self) -> None:
        for text in ("not json", "[]", '{"vaults": []}'):
            self.config.write_text(text, encoding="utf-8")
            self.assertEqual(first_run.obsidian_vaults(self.config), [])
        self.assertEqual(first_run.obsidian_vaults(self.base / "missing.json"), [])

    def test_a_missing_default_vault_is_swapped_for_obsidians_open_one(self) -> None:
        self.write_config({"b": {"path": str(self.vault), "open": True}})
        self.assertEqual(self.adopt()["vault_root"], str(self.vault))

    def test_with_no_obsidian_vault_the_missing_default_is_cleared(self) -> None:
        self.assertEqual(self.adopt()["vault_root"], "")

    def test_a_default_vault_that_exists_is_kept(self) -> None:
        Path(DEFAULT_SETTINGS["vault_root"]).mkdir(parents=True)
        self.write_config({"b": {"path": str(self.vault), "open": True}})
        self.adopt()
        self.assertNotIn("vault_root", self.storage.stored_setting_keys())

    def test_a_vault_the_user_chose_or_cleared_is_never_replaced(self) -> None:
        self.write_config({"b": {"path": str(self.vault), "open": True}})
        self.storage.update_settings({"vault_root": ""}, model_default="sonnet")
        self.assertEqual(self.adopt()["vault_root"], "")

    def test_the_default_artifacts_folder_is_created(self) -> None:
        self.adopt()
        self.assertTrue(Path(DEFAULT_SETTINGS["html_vault_root"]).is_dir())

    def test_an_artifacts_setting_the_user_cleared_creates_nothing(self) -> None:
        self.storage.update_settings({"html_vault_root": ""}, model_default="sonnet")
        self.adopt()
        self.assertFalse(Path(DEFAULT_SETTINGS["html_vault_root"]).exists())


class PluginInstallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.source = make_plugin(self.base)
        self.vault = self.base / "vault"
        (self.vault / ".obsidian").mkdir(parents=True)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_install_copies_the_plugin_and_enables_it_beside_others(self) -> None:
        (self.vault / ".obsidian" / "community-plugins.json").write_text('["dataview"]', encoding="utf-8")
        status = first_run.install_plugin(self.vault, self.source)
        target = self.vault / ".obsidian" / "plugins" / "onyx"
        for name in first_run.PLUGIN_FILES:
            self.assertEqual((target / name).read_bytes(), (self.source / name).read_bytes())
        listing = json.loads((self.vault / ".obsidian" / "community-plugins.json").read_text(encoding="utf-8"))
        self.assertEqual(listing, ["dataview", "onyx"])
        self.assertEqual((status["installed"], status["enabled"], status["version"]), (True, True, "9.9.9"))
        first_run.install_plugin(self.vault, self.source)  # reinstalling lists it once
        listing = json.loads((self.vault / ".obsidian" / "community-plugins.json").read_text(encoding="utf-8"))
        self.assertEqual(listing, ["dataview", "onyx"])

    def test_a_folder_without_obsidian_is_refused(self) -> None:
        plain = self.base / "plain"
        plain.mkdir()
        with self.assertRaises(ValueError):
            first_run.install_plugin(plain, self.source)
        self.assertEqual(list(plain.iterdir()), [])

    def test_install_never_writes_through_a_symlinked_folder(self) -> None:
        elsewhere = self.base / "elsewhere"
        elsewhere.mkdir()
        (self.vault / ".obsidian" / "plugins").symlink_to(elsewhere, target_is_directory=True)
        with self.assertRaises(ValueError):
            first_run.install_plugin(self.vault, self.source)
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_install_replaces_a_symlinked_file_instead_of_writing_its_target(self) -> None:
        target = self.vault / ".obsidian" / "plugins" / "onyx"
        target.mkdir(parents=True)
        victim = self.base / "victim.js"
        victim.write_text("keep me", encoding="utf-8")
        (target / "main.js").symlink_to(victim)
        first_run.install_plugin(self.vault, self.source)
        self.assertEqual(victim.read_text(encoding="utf-8"), "keep me")
        self.assertFalse((target / "main.js").is_symlink())

    def test_a_malformed_plugin_list_is_left_alone(self) -> None:
        listing = self.vault / ".obsidian" / "community-plugins.json"
        listing.write_text('{"not": "a list"}', encoding="utf-8")
        with self.assertRaises(ValueError):
            first_run.install_plugin(self.vault, self.source)
        self.assertEqual(listing.read_text(encoding="utf-8"), '{"not": "a list"}')


class SetupApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.vault = self.base / "vault"
        (self.vault / ".obsidian").mkdir(parents=True)
        self.source = make_plugin(self.base)
        self.config = AppConfig(default_folder=self.base, allowed_roots=(self.base,), data_dir=self.base / "data")
        self.app = create_app(self.config)
        self.app.state.storage.update_settings({"vault_root": str(self.vault)}, model_default="sonnet")
        self.client_context = TestClient(self.app, base_url="http://127.0.0.1:8899")
        self.client = self.client_context.__enter__()
        self.source_patch = patch.object(first_run, "plugin_source", return_value=self.source)
        self.source_patch.start()

    def tearDown(self) -> None:
        self.source_patch.stop()
        self.client_context.__exit__(None, None, None)
        self.temp.cleanup()

    def test_setup_reports_each_step_and_the_install_needs_the_token(self) -> None:
        state = self.client.get("/api/setup").json()
        self.assertTrue(state["vault"]["ok"])
        self.assertFalse(state["plugin"]["installed"])
        self.assertFalse(state["theme"]["ok"])
        self.assertFalse(state["dismissed"])

        refused = self.client.post("/api/setup/obsidian-plugin", json={"token": "wrong"})
        self.assertEqual(refused.status_code, 403)
        self.assertFalse((self.vault / ".obsidian" / "plugins").exists())

        installed = self.client.post("/api/setup/obsidian-plugin", json={"token": self.config.token}).json()
        self.assertTrue(installed["plugin"]["installed"] and installed["plugin"]["enabled"])

    def test_the_test_app_never_adopts_folders(self) -> None:
        self.assertFalse(self.config.first_run)


if __name__ == "__main__":
    unittest.main()
