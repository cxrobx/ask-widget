from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from onyx import vault as vault_mod
from onyx.vault import VaultCache, VaultIndex, is_inside, normalize, read_attachment_folder


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

    def test_a_real_file_maps_back_to_the_row_the_tree_lists_it_under(self) -> None:
        # The reading history keys a document by its realpath; the sidebar lists it by its vault path.
        linked = self.index.by_real(self.outside / "L.md")
        self.assertIsNotNone(linked)
        self.assertEqual(linked.rel, "linked/L.md")
        plain = self.index.by_real((self.vault / "notes" / "A.md").resolve())
        self.assertEqual(plain.rel, "notes/A.md")
        self.assertIs(self.index.by_real(self.vault / "notes" / "A.md"), plain)  # either spelling finds it
        self.assertIsNone(self.index.by_real(self.vault / "docs" / "img.png"))  # an attachment is not a row
        self.assertIsNone(self.index.by_real(self.base / "elsewhere.md"))

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


def make_html_vault(base: Path) -> tuple[Path, Path]:
    """An Artifacts folder: a real project, a linked topic, a dangling link, a guide tree."""
    vault = base / "Artifacts"
    topic = base / "learnings" / "topics" / "architect"
    guides = topic / "guides"
    for folder in (vault / "Scratch" / "drafts", vault / "Empty Project", guides / "who-holds-the-plan" / "audio",
                   guides / "debugging-method", guides / "site" / "sub"):
        folder.mkdir(parents=True)
    (topic / "agent-sdk-onepager.html").write_text("<title>Agent SDK one-pager</title>", encoding="utf-8")
    (topic / "notes.md").write_text("# notes\n", encoding="utf-8")
    who = guides / "who-holds-the-plan"
    (who / "index.html").write_text(
        "<html><head><title>\n  Who holds\n the <b>plan</b> &amp; why </title></head></html>", encoding="utf-8"
    )
    (who / "index.inline.html").write_text("<title>Who holds the plan (inline)</title>", encoding="utf-8")
    (who / "audio" / "one.m4a").write_bytes(b"m4a")
    (guides / "debugging-method" / "index.html").write_text("<title>Debugging method</title>", encoding="utf-8")
    (guides / "site" / "index.html").write_text("<title>A site</title>", encoding="utf-8")
    (guides / "site" / "sub" / "page.html").write_text("<title>Deep page</title>", encoding="utf-8")
    (vault / "Architect").symlink_to(topic, target_is_directory=True)
    (vault / "Scratch" / "zeta.html").write_text("<title>Alpha scratch</title>", encoding="utf-8")
    (vault / "Scratch" / "untitled.htm").write_text("<p>no title</p>", encoding="utf-8")
    (vault / "Scratch" / "readme.md").write_text("# not html\n", encoding="utf-8")
    (vault / "Scratch" / "gone.html").symlink_to(base / "nowhere" / "gone.html")
    (vault / "Scratch" / "loop").symlink_to(vault, target_is_directory=True)
    return vault, topic


class HtmlVaultIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.vault, self.topic = make_html_vault(self.base)
        self.index = VaultIndex.build(self.vault, kind="html")
        self.tree = self.index.tree_json()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def child(self, node: dict, name: str) -> dict:
        return next(c for c in node["children"] if c["name"] == name)

    def test_top_level_folders_are_projects_even_when_empty(self) -> None:
        self.assertEqual([c["name"] for c in self.tree["children"]], ["Architect", "Empty Project", "Scratch"])
        self.assertEqual(self.child(self.tree, "Empty Project")["children"], [])
        # Your own empty folders show; empty folders inside a linked tree do not.
        scratch = self.child(self.tree, "Scratch")
        self.assertEqual(self.child(scratch, "drafts")["children"], [])
        (self.topic / "empty-in-link").mkdir()
        rebuilt = VaultIndex.build(self.vault, kind="html").tree_json()
        architect_names = [c["name"] for c in self.child(rebuilt, "Architect")["children"]]
        self.assertNotIn("empty-in-link", architect_names)
        guides = self.child(self.child(rebuilt, "Architect"), "guides")
        self.assertNotIn("dir", {c["kind"] for c in guides["children"]})  # page folders are pages
        architect = self.child(self.tree, "Architect")
        self.assertTrue(architect["symlink"])
        self.assertTrue(architect["linked"])
        self.assertTrue(self.child(architect, "guides")["linked"])  # inherited: another tree
        self.assertFalse(self.child(self.tree, "Scratch")["linked"])

    def test_a_folder_with_an_index_page_is_one_page_titled_by_its_title(self) -> None:
        guides = self.child(self.child(self.tree, "Architect"), "guides")
        self.assertEqual(
            [(c["kind"], c["title"]) for c in guides["children"]],
            [("file", "A site"), ("file", "Debugging method"), ("file", "Who holds the plan & why")],
        )
        who = self.child(guides, "who-holds-the-plan")
        self.assertEqual(who["path"], str(self.vault / "Architect" / "guides" / "who-holds-the-plan" / "index.html"))
        rels = {item.rel for item in self.index.files}
        self.assertNotIn("Architect/guides/who-holds-the-plan/index.inline.html", rels)
        self.assertNotIn("Architect/guides/site/sub/page.html", rels)  # reachable from the site itself
        self.assertIn("Architect/agent-sdk-onepager.html", rels)
        self.assertNotIn("Architect/notes.md", rels)

    def test_labels_fall_back_and_sort_by_title(self) -> None:
        scratch = self.child(self.tree, "Scratch")
        self.assertEqual(
            [c["title"] for c in scratch["children"] if c["kind"] == "file"], ["Alpha scratch", "gone.html", "untitled"]
        )
        missing = next(c for c in scratch["children"] if c.get("missing"))
        self.assertEqual(missing["name"], "gone.html")
        self.assertEqual(missing["target"], str(self.base / "nowhere" / "gone.html"))
        self.assertGreater(self.child(scratch, "zeta.html")["mtime"], 0)
        self.assertFalse(any(item.rel.startswith("Scratch/loop/") for item in self.index.files))

    def test_search_matches_titles_and_skips_missing_links(self) -> None:
        results, _ = self.index.search("who")
        self.assertEqual([item.label for item in results], ["Who holds the plan & why"])
        self.assertEqual(results[0].entry_rel, "Architect/guides/who-holds-the-plan")
        self.assertEqual([item.label for item in self.index.search("alpha")[0]], ["Alpha scratch"])
        self.assertEqual(self.index.search("gone")[0], [])

    def test_title_changes_are_picked_up(self) -> None:
        page = self.vault / "Scratch" / "zeta.html"
        page.write_text("<title>Renamed page, longer</title>", encoding="utf-8")
        rebuilt = VaultIndex.build(self.vault, kind="html")
        labels = [item.label for item in rebuilt.files if item.name == "zeta.html"]
        self.assertEqual(labels, ["Renamed page, longer"])

    def test_pages_carry_a_one_line_summary_for_the_preview(self) -> None:
        scratch = self.vault / "Scratch"
        (scratch / "described.html").write_text(
            '<head><meta content="Said &amp; done." name="description"><title>D</title></head>'
            "<body><p class=subtitle>The description wins over the subtitle.</p></body>",
            encoding="utf-8",
        )
        (scratch / "subtitled.html").write_text(
            '<title>S</title><style>p{}</style><h1>S</h1><p class="subtitle">Localize, falsify, <code>fix</code> .</p>',
            encoding="utf-8",
        )
        (scratch / "plain.html").write_text(
            "<title>P</title><p>Short.</p><script>'<p>a script string long enough to pass for prose here</p>'</script>"
            "<p>The first real paragraph, long enough to preview the page by.</p>",
            encoding="utf-8",
        )
        tree = VaultIndex.build(self.vault, kind="html").tree_json()
        nodes = {c["name"]: c for c in self.child(tree, "Scratch")["children"]}
        self.assertEqual(nodes["described.html"]["summary"], "Said & done.")
        self.assertEqual(nodes["subtitled.html"]["summary"], "Localize, falsify, fix.")
        self.assertEqual(nodes["plain.html"]["summary"], "The first real paragraph, long enough to preview the page by.")
        self.assertNotIn("summary", nodes["zeta.html"])  # nothing to say: no field
        self.assertNotIn("summary", nodes["gone.html"])  # a dangling link has no page to read

    def test_context_folder_is_the_real_folder_behind_the_first_link(self) -> None:
        who = self.vault / "Architect" / "guides" / "who-holds-the-plan" / "index.html"
        self.assertEqual(vault_mod.html_context_folder(who, self.vault), self.topic.resolve())
        (self.vault / "Scratch" / "who.html").symlink_to(self.topic / "guides" / "who-holds-the-plan" / "index.html")
        self.assertEqual(
            vault_mod.html_context_folder(self.vault / "Scratch" / "who.html", self.vault),
            (self.topic / "guides" / "who-holds-the-plan").resolve(),
        )
        self.assertEqual(
            vault_mod.html_context_folder(self.vault / "Scratch" / "zeta.html", self.vault),
            (self.vault / "Scratch").resolve(),
        )
        self.assertIsNone(vault_mod.html_context_folder(self.base / "elsewhere.html", self.vault))
        self.assertIsNone(vault_mod.html_context_folder(self.vault, self.vault))

    def test_a_rows_menu_paths_are_its_real_file_and_the_link_on_the_way(self) -> None:
        who = self.vault / "Architect" / "guides" / "who-holds-the-plan" / "index.html"
        linked = vault_mod.entry_paths(who, self.vault)
        self.assertEqual(linked["path"], str(who))  # the row as the tree shows it
        self.assertEqual(linked["real"], str((self.topic / "guides" / "who-holds-the-plan" / "index.html").resolve()))
        self.assertEqual(linked["link"], str(self.vault / "Architect"))
        self.assertEqual((linked["exists"], linked["is_dir"]), (True, False))
        # On macOS this temp dir sits under /var -> /private/var. A link above the
        # root is how the vault is reached, so a row that lives in it keeps its own path.
        own = vault_mod.entry_paths(self.vault / "Scratch" / "zeta.html", self.vault)
        self.assertEqual((own["real"], own["link"]), (str(self.vault / "Scratch" / "zeta.html"), None))
        folder = vault_mod.entry_paths(self.vault / "Architect", self.vault)
        self.assertEqual(
            (folder["is_dir"], folder["real"], folder["link"]), (True, str(self.topic.resolve()), str(self.vault / "Architect"))
        )
        gone = vault_mod.entry_paths(self.vault / "Scratch" / "gone.html", self.vault)
        self.assertEqual((gone["exists"], gone["real"], gone["link"]), (False, None, str(self.vault / "Scratch" / "gone.html")))
        self.assertIsNone(vault_mod.entry_paths(self.vault, self.vault))
        self.assertIsNone(vault_mod.entry_paths(self.vault / ".." / "outside.html", self.vault))
        self.assertIsNone(vault_mod.entry_paths("Scratch/zeta.html", self.vault))

    def test_links_are_created_only_in_the_vaults_own_folders(self) -> None:
        guide = self.topic / "guides" / "debugging-method" / "index.html"
        before = guide.read_bytes()
        link = vault_mod.create_link(self.vault, "Scratch", guide)
        self.assertEqual(link, self.vault / "Scratch" / "debugging-method.html")  # not "index.html"
        self.assertTrue(link.is_symlink())
        self.assertEqual(Path(os.readlink(link)), guide)
        self.assertEqual(guide.read_bytes(), before)
        folder_link = vault_mod.create_link(self.vault, "", self.topic / "guides", "Guides")
        self.assertTrue(folder_link.is_symlink() and folder_link.is_dir())
        named = vault_mod.create_link(self.vault, "Scratch", self.topic / "agent-sdk-onepager.html", "SDK")
        self.assertEqual(named.name, "SDK.html")
        with self.assertRaisesRegex(ValueError, "already exists"):
            vault_mod.create_link(self.vault, "Scratch", guide)
        with self.assertRaisesRegex(ValueError, "linked folder"):
            vault_mod.create_link(self.vault, "Architect/guides", self.topic / "agent-sdk-onepager.html")
        self.assertFalse((self.topic / "guides" / "agent-sdk-onepager.html").exists())
        with self.assertRaisesRegex(ValueError, "Only HTML"):
            vault_mod.create_link(self.vault, "Scratch", self.topic / "notes.md")
        with self.assertRaisesRegex(ValueError, "does not exist"):
            vault_mod.create_link(self.vault, "Scratch", self.base / "missing.html")
        with self.assertRaisesRegex(ValueError, "already inside"):
            vault_mod.create_link(self.vault, "Empty Project", self.vault / "Scratch" / "zeta.html")
        with self.assertRaisesRegex(ValueError, "full path"):
            vault_mod.create_link(self.vault, "Scratch", "relative/page.html")
        with self.assertRaisesRegex(ValueError, "would loop"):
            vault_mod.create_link(self.vault, "Scratch", self.base)
        for bad in ("../outside", ".hidden", "Scratch/../..", "Nope"):
            with self.assertRaises(ValueError, msg=bad):
                vault_mod.create_link(self.vault, bad, guide, "x")
        from_url = vault_mod.create_link(self.vault, "Empty Project", guide.as_uri())
        self.assertEqual(from_url.name, "debugging-method.html")

    def test_folders_are_created_with_plain_names(self) -> None:
        made = vault_mod.create_folder(self.vault, "", "Claude Certified Architect")
        self.assertTrue(made.is_dir() and not made.is_symlink())
        self.assertTrue(vault_mod.create_folder(self.vault, "Claude Certified Architect", "Week 1").is_dir())
        for bad in ("", ".secret", "a/b", "..", "x" * 300):
            with self.assertRaises(ValueError, msg=bad):
                vault_mod.create_folder(self.vault, "", bad)
        with self.assertRaisesRegex(ValueError, "already exists"):
            vault_mod.create_folder(self.vault, "", "Scratch")
        with self.assertRaisesRegex(ValueError, "linked folder"):
            vault_mod.create_folder(self.vault, "Architect", "new")

    def test_a_linked_pages_real_file_maps_back_to_its_row(self) -> None:
        guide = self.topic / "guides" / "who-holds-the-plan" / "index.html"
        self.assertEqual(self.index.by_real(guide).rel, "Architect/guides/who-holds-the-plan/index.html")
        self.assertEqual(self.index.by_real(self.vault / "Scratch" / "zeta.html").rel, "Scratch/zeta.html")
        self.assertIsNone(self.index.by_real(self.base / "nowhere" / "gone.html"))  # a dangling link is no row
        self.assertIsNone(self.index.by_real(self.topic / "notes.md"))  # not a page

    def test_cache_keeps_one_index_per_kind(self) -> None:
        cache = VaultCache(ttl=60)
        html_index = cache.get(self.vault, "html")
        notes_index = cache.get(self.vault)
        self.assertIsNot(html_index, notes_index)
        self.assertIs(cache.get(self.vault, "html"), html_index)
        self.assertEqual(notes_index.kind, "notes")
        self.assertIn("Scratch/readme.md", {item.rel for item in notes_index.files})


if __name__ == "__main__":
    unittest.main()
