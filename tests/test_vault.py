from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from ask_widget.vault import VaultCache, VaultIndex, is_inside, normalize, read_attachment_folder


def make_vault(base: Path) -> tuple[Path, Path]:
    """A small vault next to an outside folder it symlinks into."""
    vault = base / "vault"
    outside = base / "outside"
    for folder in (
        vault / "notes" / "sub",
        vault / "docs",
        vault / "Other" / "Attachments",
        vault / ".obsidian",
        vault / ".hidden",
        vault / "node_modules",
        outside,
    ):
        folder.mkdir(parents=True)
    (vault / ".obsidian" / "app.json").write_text(
        json.dumps({"attachmentFolderPath": "Other/Attachments"}), encoding="utf-8"
    )
    (vault / "notes" / "A.md").write_text("# A\n\n[[B]] and [[Nowhere]]\n", encoding="utf-8")
    (vault / "notes" / "sub" / "B.md").write_text("# B (sub)\n", encoding="utf-8")
    (vault / "notes" / "sub" / "C.md").write_text("# C\n\n[[B]]\n", encoding="utf-8")
    (vault / "docs" / "B.md").write_text("# B (docs)\n", encoding="utf-8")
    (vault / "docs" / "img.png").write_bytes(b"png-in-docs")
    (vault / "docs" / "Report.html").write_text("<title>Report</title>", encoding="utf-8")
    (vault / "Other" / "Attachments" / "img.png").write_bytes(b"png")
    (vault / "Trusted Counselor — “Career” Synthesis.md").write_text("# Synthesis\n", encoding="utf-8")
    (vault / ".hidden" / "secret.md").write_text("# Secret\n", encoding="utf-8")
    (vault / "node_modules" / "pkg.md").write_text("# Package\n", encoding="utf-8")
    (vault / "notes" / "ignored.json").write_text("{}", encoding="utf-8")
    (outside / "L.md").write_text("# Linked\n\n[[M]]\n", encoding="utf-8")
    (outside / "M.md").write_text("# M\n", encoding="utf-8")
    (vault / "linked").symlink_to(outside, target_is_directory=True)
    (vault / "loop").symlink_to(vault, target_is_directory=True)
    return vault, outside


class VaultIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.vault, self.outside = make_vault(self.base)
        self.index = VaultIndex.build(self.vault)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def rels(self) -> set[str]:
        return {item.rel for item in self.index.files}

    def test_walker_follows_symlinks_and_guards_cycles(self) -> None:
        rels = self.rels()
        self.assertIn("linked/L.md", rels)
        self.assertIn("linked/M.md", rels)
        self.assertFalse(any(rel.startswith("loop/") for rel in rels), rels)
        self.assertIn("linked", self.index.symlinked_dirs)
        # Symlinked files keep their vault-visible path, not the realpath.
        linked = next(item for item in self.index.files if item.rel == "linked/L.md")
        self.assertEqual(linked.path, self.vault / "linked" / "L.md")
        self.assertFalse(self.index.truncated)

    def test_dot_dirs_node_modules_and_unknown_extensions_are_excluded(self) -> None:
        rels = self.rels()
        self.assertNotIn(".hidden/secret.md", rels)
        self.assertNotIn("node_modules/pkg.md", rels)
        self.assertNotIn("notes/ignored.json", rels)
        self.assertIn("Trusted Counselor — “Career” Synthesis.md", rels)
        self.assertEqual(read_attachment_folder(self.vault), "Other/Attachments")
        self.assertEqual(read_attachment_folder(self.outside), "Other/Attachments")

    def test_wikilink_prefers_same_folder_then_unique_then_shortest(self) -> None:
        same_folder = self.index.resolve_wikilink("B", source=self.vault / "notes" / "sub" / "C.md")
        self.assertEqual(same_folder.rel, "notes/sub/B.md")
        shortest = self.index.resolve_wikilink("B", source=self.vault / "notes" / "A.md")
        self.assertEqual(shortest.rel, "docs/B.md")
        unique = self.index.resolve_wikilink("a", source=self.vault / "docs" / "B.md")
        self.assertEqual(unique.rel, "notes/A.md")
        self.assertEqual(self.index.resolve_wikilink("A.md").rel, "notes/A.md")
        self.assertEqual(self.index.resolve_wikilink("notes/sub/B").rel, "notes/sub/B.md")
        self.assertEqual(self.index.resolve_wikilink("sub/B.md").rel, "notes/sub/B.md")
        self.assertEqual(self.index.resolve_wikilink("Report").rel, "docs/Report.html")
        self.assertIsNone(self.index.resolve_wikilink("Nowhere"))
        self.assertIsNone(self.index.resolve_wikilink(""))
        inside_symlink = self.index.resolve_wikilink("M", source=self.vault / "linked" / "L.md")
        self.assertEqual(inside_symlink.path, self.vault / "linked" / "M.md")

    def test_embed_prefers_attachment_folder_then_falls_back_to_notes(self) -> None:
        image = self.index.resolve_embed("img.png", source=self.vault / "docs" / "B.md")
        self.assertEqual(image.rel, "Other/Attachments/img.png")
        self.assertEqual(image.kind, "attachment")
        self.assertEqual(self.index.resolve_embed("docs/img.png").rel, "docs/img.png")
        self.assertEqual(self.index.resolve_embed("A").rel, "notes/A.md")
        self.assertIsNone(self.index.resolve_embed("missing.png"))

    def test_containment_is_lexical(self) -> None:
        evil = self.base / "vault-evil"
        evil.mkdir()
        self.assertTrue(is_inside(self.vault / "notes" / "A.md", self.vault))
        self.assertTrue(is_inside(self.vault, self.vault))
        self.assertTrue(self.index.contains(self.vault / "linked" / "L.md"))
        self.assertFalse(self.index.contains(self.outside / "L.md"))
        self.assertFalse(is_inside(self.vault / ".." / "x.md", self.vault))
        self.assertFalse(is_inside(evil / "x.md", self.vault))
        self.assertFalse(is_inside("relative/x.md", self.vault))
        self.assertEqual(normalize(self.vault / "notes" / ".." / "docs"), self.vault / "docs")

    def test_tree_lists_notes_only_dirs_first(self) -> None:
        tree = self.index.tree_json()
        self.assertEqual(tree["kind"], "dir")
        self.assertEqual(tree["path"], str(self.vault))
        names = [child["name"] for child in tree["children"]]
        self.assertEqual(
            names,
            ["docs", "linked", "notes", "Trusted Counselor — “Career” Synthesis.md"],
        )
        docs = next(child for child in tree["children"] if child["name"] == "docs")
        self.assertEqual([c["name"] for c in docs["children"]], ["B.md", "Report.html"])
        linked = next(child for child in tree["children"] if child["name"] == "linked")
        self.assertTrue(linked.get("symlink"))
        self.assertNotIn("Other", names)  # attachments never appear in the tree
        notes = next(child for child in tree["children"] if child["name"] == "notes")
        self.assertEqual(notes["children"][0]["name"], "sub")
        self.assertEqual(notes["children"][0]["kind"], "dir")

    def test_search_is_prefix_first(self) -> None:
        results, truncated = self.index.search("b")
        self.assertFalse(truncated)
        self.assertEqual([item.rel for item in results[:2]], ["docs/B.md", "notes/sub/B.md"])
        self.assertEqual(self.index.search("")[0], [])
        partial, _ = self.index.search("career")
        self.assertEqual(partial[0].name, "Trusted Counselor — “Career” Synthesis.md")
        limited, truncated = self.index.search("md", limit=1)
        self.assertEqual(len(limited), 1)
        self.assertTrue(truncated)

    def test_cache_honours_ttl_and_invalidate(self) -> None:
        cache = VaultCache(ttl=60)
        first = cache.get(self.vault)
        self.assertIs(cache.get(self.vault), first)
        first.built_at = time.time() - 120
        self.assertIsNot(cache.get(self.vault), first)
        current = cache.get(self.vault)
        cache.invalidate()
        self.assertIsNot(cache.get(self.vault), current)
        self.assertIsNot(cache.get(self.outside), current)


if __name__ == "__main__":
    unittest.main()
