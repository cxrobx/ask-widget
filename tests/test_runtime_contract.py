from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from ask_widget import __version__
from ask_widget.app import PROTOCOL_VERSION, create_app
from ask_widget.config import AppConfig


class RuntimeContractTests(unittest.TestCase):
    def test_health_identifies_the_service_and_protocol(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            app = create_app(
                AppConfig(default_folder=root, allowed_roots=(root,), port=8899, data_dir=root / "data")
            )
            with TestClient(app, base_url="http://127.0.0.1:8899") as client:
                body = client.get("/health").json()

            self.assertEqual(body["status"], "ok")
            self.assertEqual(body["service"], "ask-widget")
            self.assertEqual(body["version"], __version__)
            self.assertEqual(body["protocol"], PROTOCOL_VERSION)
            self.assertIsInstance(body["claude_available"], bool)
            app.state.storage.close()

    def test_allowlist_rejects_a_sibling_with_the_same_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            allowed = base / "Projects"
            sibling = base / "Projects-evil"
            allowed.mkdir()
            sibling.mkdir()
            config = AppConfig(default_folder=allowed, allowed_roots=(allowed.resolve(),))

            self.assertEqual(config.resolve_allowed(str(allowed)), allowed.resolve())
            self.assertIsNone(config.resolve_allowed(str(sibling)))

    def test_allowlist_follows_symlinks_before_checking_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            allowed = base / "allowed"
            outside = base / "outside"
            allowed.mkdir()
            outside.mkdir()
            (allowed / "escape").symlink_to(outside, target_is_directory=True)
            config = AppConfig(default_folder=allowed, allowed_roots=(allowed.resolve(),))

            self.assertIsNone(config.resolve_allowed(str(allowed / "escape")))


if __name__ == "__main__":
    unittest.main()
