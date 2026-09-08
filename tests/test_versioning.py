from __future__ import annotations

import plistlib
import tomllib
import unittest
from pathlib import Path

from ask_widget import __version__


class VersioningTests(unittest.TestCase):
    def test_all_release_metadata_uses_the_same_version(self) -> None:
        root = Path(__file__).resolve().parent.parent
        project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        with (root / "launcher" / "Info.plist").open("rb") as handle:
            plist = plistlib.load(handle)

        self.assertEqual(project["project"]["version"], __version__)
        self.assertEqual(plist["CFBundleShortVersionString"], __version__)
        self.assertEqual(plist["CFBundleVersion"], __version__)


if __name__ == "__main__":
    unittest.main()
