"""The Alfred onx / onxc search: it must list exactly what Onyx's sidebar lists.

The script is a standalone copy of vault.py's listing rules (Alfred runs it with
the system Python, which cannot import the app), so the parity tests below are
what keeps the copy honest.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from ask_widget.vault import VaultIndex

SCRIPT = Path(__file__).resolve().parents[1] / "integrations" / "alfred" / "onyx_search.py"
ALFRED_PYTHON = "/usr/bin/python3"

_spec = importlib.util.spec_from_file_location("onyx_search", SCRIPT)
search = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(search)

GUIDE = """<!doctype html><html><head><title>Domain 1: Agentic Architecture</title>
<style>.walrus { color: red }</style><script>var secretToken = "kumquat";</script></head>
<body><div class="narwhal"><h1>Domain 1</h1>
<p>The orchestrator pattern keeps one agent in charge &amp; hands work to others.</p>
<img src="data:image/png;base64,QUJDREVGR0g="></div><!-- a comment about a pelican --></body></html>"""


def make_vaults(base: Path) -> tuple[str, str]:
    """A Notes vault and an Artifacts folder shaped like the real ones."""
    notes, artifacts, topic = base / "CX", base / "Artifacts", base / "learnings" / "topic"
    for folder in (
        notes / "Inbox",
        notes / "Resources" / "Claude Certified Architect",
        notes / "Resources" / "Playbook",
        notes / "Other" / "Attachments",
        notes / ".obsidian",
        notes / ".trash",
        notes / "node_modules",
        base / "outside",
        topic / "domain-1" / "audio",
        artifacts / "Agent Debug Lab" / "deep" / "guide",
        artifacts / "Empty Project",
    ):
        folder.mkdir(parents=True)
    (notes / "Inbox" / "Quick Idea.md").write_text("# Idea\n\nRemember the **zebra** protocol.\n", encoding="utf-8")
    (notes / "Inbox" / "gone.md").symlink_to(base / "nowhere.md")
    (notes / "Resources" / "Claude Certified Architect" / "Domain 1.md").write_text("Agentic loops.\n", encoding="utf-8")
    (notes / "Resources" / "Playbook" / "playbook.html").write_text(
        "<title>Late-Payment Playbook</title><p>Send the reminder on day three.</p>", encoding="utf-8"
    )
    (notes / "Resources" / "scan.pdf").write_bytes(b"%PDF-1.4")
    (notes / "Resources" / "list.txt").write_text("groceries\n", encoding="utf-8")
    (notes / "Other" / "Attachments" / "img.png").write_bytes(b"png")
    (notes / ".trash" / "old.md").write_text("zebra\n", encoding="utf-8")
    (notes / "node_modules" / "pkg.md").write_text("zebra\n", encoding="utf-8")
    (base / "outside" / "Outside Note.md").write_text("From a linked folder.\n", encoding="utf-8")
    (notes / "Linked").symlink_to(base / "outside")

    (topic / "domain-1" / "index.html").write_text(GUIDE, encoding="utf-8")
    (topic / "domain-1" / "index.inline.html").write_text(GUIDE, encoding="utf-8")
    (topic / "domain-1" / "audio" / "narration.m4a").write_bytes(b"m4a")
    (topic / "overview.html").write_text("<title>Topic Overview</title><p>Start here.</p>", encoding="utf-8")
    (artifacts / "Guides").symlink_to(topic)
    (artifacts / "Playbook").symlink_to(notes / "Resources" / "Playbook")
    (artifacts / "Agent Debug Lab" / "lab.html").write_text("<p>No title here.</p>", encoding="utf-8")
    (artifacts / "Agent Debug Lab" / "deep" / "guide" / "index.html").write_text("<p>Untitled guide.</p>", encoding="utf-8")
    (artifacts / "Agent Debug Lab" / "missing.html").symlink_to(base / "nowhere.html")
    return str(notes), str(artifacts)


class AlfredSearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.notes, self.artifacts = make_vaults(self.base)
        self.roots = {"notes": self.notes, "html": self.artifacts}

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def labels(self, items: list) -> list:
        return [item["title"] for item in items]

    # MARK: - Parity with Onyx's own index

    def test_artifacts_list_exactly_what_onyx_lists(self) -> None:
        onyx = VaultIndex.build(Path(self.artifacts), kind="html")
        expected = {(f.rel, f.label) for f in onyx.notes if not f.missing}
        got = {(p.rel, p.label) for p in search.walk_artifacts(self.artifacts)}
        self.assertEqual(got, expected)
        # And the rules that set Artifacts apart hold in the copy on their own:
        self.assertIn(("Guides/domain-1/index.html", "Domain 1: Agentic Architecture"), got)
        self.assertNotIn("Guides/domain-1/index.inline.html", {rel for rel, _ in got})
        self.assertIn(("Agent Debug Lab/lab.html", "lab"), got)
        self.assertIn(("Agent Debug Lab/deep/guide/index.html", "guide"), got)
        self.assertNotIn("Agent Debug Lab/missing.html", {rel for rel, _ in got})

    def test_notes_list_exactly_what_onyx_lists(self) -> None:
        onyx = VaultIndex.build(Path(self.notes))
        expected = {f.rel for f in onyx.notes if os.path.exists(f.path)}
        got = {p.rel for p in search.walk_notes(self.notes)}
        self.assertEqual(got, expected)
        self.assertIn("Linked/Outside Note.md", got)
        self.assertNotIn("Inbox/gone.md", got)

    def test_a_page_in_both_vaults_is_listed_once_as_an_artifact(self) -> None:
        pages = [p for p in search.collect(self.roots) if p.path.endswith("playbook.html")]
        self.assertEqual([(p.vault, p.label) for p in pages], [("html", "Late-Payment Playbook")])

    # MARK: - onx

    def test_titles_need_every_word_and_whole_title_matches_come_first(self) -> None:
        pages = search.collect(self.roots)
        self.assertEqual(
            self.labels(search.search_titles(pages, "architect")),
            ["Domain 1: Agentic Architecture", "Domain 1"],  # the note is found through its folder
        )
        self.assertEqual(self.labels(search.search_titles(pages, "claude domain")), ["Domain 1"])
        self.assertEqual(search.search_titles(pages, "zebra domain"), [])
        [first] = search.search_titles(pages, "late payment")
        self.assertTrue(first["subtitle"].startswith("Artifacts · Playbook · "), first["subtitle"])
        self.assertEqual(first["arg"], os.path.join(self.artifacts, "Playbook", "playbook.html"))

    def test_an_empty_title_query_lists_recent_pages_newest_first(self) -> None:
        newest = os.path.join(self.notes, "Inbox", "Quick Idea.md")
        second = os.path.join(self.artifacts, "Guides", "overview.html")
        os.utime(newest, (2_000_000_000, 2_000_000_000))
        os.utime(second, (1_999_999_000, 1_999_999_000))
        items = search.search_titles(search.collect(self.roots), "")
        self.assertEqual(self.labels(items)[:2], ["Quick Idea", "Topic Overview"])

    # MARK: - onxc

    def test_content_matches_visible_text_only(self) -> None:
        pages = search.collect(self.roots)
        hits = search.search_content(pages, "orchestrator")
        self.assertEqual(self.labels(hits), ["Domain 1: Agentic Architecture"])
        self.assertIn("orchestrator pattern", hits[0]["subtitle"])
        self.assertIn("&", hits[0]["subtitle"])  # entities come through as the reader sees them
        for hidden in ("kumquat", "walrus", "narwhal", "QUJDREVGR0g", "pelican"):
            self.assertEqual(search.search_content(pages, hidden), [], hidden)

    def test_content_needs_every_word_in_the_same_page(self) -> None:
        pages = search.collect(self.roots)
        self.assertEqual(self.labels(search.search_content(pages, "zebra protocol")), ["Quick Idea"])
        self.assertEqual(search.search_content(pages, "zebra orchestrator"), [])

    def test_a_long_line_is_cut_to_a_window_around_the_hit(self) -> None:
        text = "lorem " * 200 + "the needle sits here " + "ipsum " * 200
        excerpt = search.snippet(text, "needle")
        self.assertIn("needle", excerpt)
        self.assertTrue(excerpt.startswith("…") and excerpt.endswith("…"))
        self.assertLessEqual(len(excerpt), search.SNIPPET_LEN + 2)

    # MARK: - Settings and the real runtime

    def test_folders_come_from_onyx_settings_and_empty_hides_a_vault(self) -> None:
        db = self.base / "onyx.db"
        with closing(sqlite3.connect(db)) as conn:
            conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value_json TEXT NOT NULL, updated_at REAL NOT NULL)")
            conn.executemany(
                "INSERT INTO settings VALUES (?, ?, 0)",
                [("vault_root", json.dumps(self.notes)), ("html_vault_root", json.dumps(""))],
            )
            conn.commit()
        self.assertEqual(search.configured_roots({"ONYX_DB": str(db)}), {"notes": self.notes})
        overridden = search.configured_roots(
            {"ONYX_DB": str(db), "ONYX_NOTES_ROOT": "", "ONYX_ARTIFACTS_ROOT": self.artifacts}
        )
        self.assertEqual(overridden, {"html": self.artifacts})

    @unittest.skipUnless(os.path.exists(ALFRED_PYTHON), "no system Python")
    def test_runs_under_the_python_alfred_uses(self) -> None:
        env = dict(
            os.environ,
            ONYX_DB=str(self.base / "no-such.db"),
            ONYX_NOTES_ROOT=self.notes,
            ONYX_ARTIFACTS_ROOT=self.artifacts,
        )

        def run(*args: str) -> list:
            done = subprocess.run(
                [ALFRED_PYTHON, str(SCRIPT), *args], capture_output=True, text=True, env=env, timeout=30
            )
            self.assertEqual(done.returncode, 0, done.stderr)
            return json.loads(done.stdout)["items"]

        self.assertEqual(self.labels(run("titles", "overview")), ["Topic Overview"])
        self.assertEqual(self.labels(run("content", "reminder day")), ["Late-Payment Playbook"])
        self.assertEqual(len(run("titles", "…")), len(search.search_titles(search.collect(self.roots), "")))
        [nothing] = run("content", "no such words anywhere")
        self.assertFalse(nothing["valid"])


if __name__ == "__main__":
    unittest.main()
