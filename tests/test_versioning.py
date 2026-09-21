from __future__ import annotations

import json
import plistlib
import tomllib
import unittest
from pathlib import Path

from onyx import __version__


class VersioningTests(unittest.TestCase):
    def test_all_release_metadata_uses_the_same_version(self) -> None:
        root = Path(__file__).resolve().parent.parent
        project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        with (root / "launcher" / "Info.plist").open("rb") as handle:
            plist = plistlib.load(handle)

        self.assertEqual(project["project"]["version"], __version__)
        self.assertEqual(plist["CFBundleShortVersionString"], __version__)
        self.assertEqual(plist["CFBundleVersion"], __version__)

    def test_obsidian_manifest_and_versions_json_agree(self) -> None:
        plugin = Path(__file__).resolve().parent.parent / "integrations" / "obsidian"
        manifest = json.loads((plugin / "manifest.json").read_text(encoding="utf-8"))
        versions = json.loads((plugin / "versions.json").read_text(encoding="utf-8"))
        package = json.loads((plugin / "package.json").read_text(encoding="utf-8"))

        self.assertEqual(manifest["id"], "onyx")
        self.assertTrue(manifest["isDesktopOnly"])
        self.assertEqual(package["version"], manifest["version"])
        self.assertEqual(versions[manifest["version"]], manifest["minAppVersion"])


if __name__ == "__main__":
    unittest.main()
