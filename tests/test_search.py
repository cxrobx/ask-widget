"""⌘P's passages (search.py): vault-mcp's index read in place, ranked by words and meaning, placed in Onyx's trees."""

from __future__ import annotations

import array
import math
import socket
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from ask_widget import search
from ask_widget.app import create_app
from ask_widget.config import AppConfig
from ask_widget.vault import VaultIndex

LAUNCHER_SWIFT = Path(__file__).resolve().parent.parent / "launcher" / "AskWidget.swift"

# vault-mcp's tables as its store.py creates them (SCHEMA and FTS_SCHEMA). search.py reads them and never writes; if
# vault-mcp's schema changes, this is the copy that changes with it.
VAULT_MCP_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, mtime REAL NOT NULL, size INTEGER NOT NULL, content_hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT, file_path TEXT NOT NULL, heading_path TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL, tags TEXT NOT NULL DEFAULT '', embedding BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_file ON chunks(file_path);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    text, tags, content='chunks', content_rowid='id', tokenize="unicode61 remove_diacritics 2"
);
CREATE TRIGGER IF NOT EXISTS chunks_fts_ai AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, text, tags) VALUES (new.id, new.text, new.tags);
END;
CREATE TRIGGER IF NOT EXISTS chunks_fts_ad AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, text, tags) VALUES ('delete', old.id, old.text, old.tags);
END;
"""

# One direction of meaning per subject, so a stand-in embedder can put a query beside one of them.
DEPLOY, MUSIC, LAUNCH = [1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0]
NEUTRAL = [1, 1, 1, 1]  # 0.5 from every subject: under the floor, so meaning says nothing
PASSAGES = [
    ("Projects/Deploy.md", "Deploy > NAS", "The nas-tunnel carries every service out to the web.", DEPLOY),
    ("Areas/Music.md", "Music > Release", "Plan the release of the next single.", MUSIC),
    ("Artifacts/Pages/one.html", "Page one > Launch plan", "Friday it opens to everyone.", LAUNCH),
]


def make_index(path: Path, passages, *, stamp: str = "2026-09-13T00:00:00+00:00") -> None:
    """A vault-mcp index at ``path`` holding ``passages``: (file_path, heading_path, text, vector) each."""
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(VAULT_MCP_SCHEMA)
        for file_path, heading, text, vector in passages:
            norm = math.sqrt(sum(x * x for x in vector)) or 1.0
            conn.execute(
                "INSERT INTO chunks(file_path, heading_path, text, tags, embedding) VALUES (?, ?, ?, '', ?)",
                (file_path, heading, text, array.array("f", [x / norm for x in vector]).tobytes()),
            )
        conn.executemany(
            "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
            [("model", "nomic-embed-text"), ("fts_version", "1"), ("last_index_time", stamp)],
        )
        conn.commit()


def stand_in(meanings: dict[str, list[float]]):
    """An embedder in Ollama's place: the vector of the first phrase the query holds, else NEUTRAL."""

    def embed(model: str, text: str) -> list[float]:
        return next((vector for phrase, vector in meanings.items() if phrase in text.lower()), NEUTRAL)

    return embed


def as_notes(path: str) -> dict:
    return {"vault": "notes", "path": "/vault/" + path, "title": Path(path).stem, "folder": ""}


def found(result: dict) -> list[tuple[str, str]]:
    return [(item["title"], item["match"]) for item in result["items"]]


class PassageSearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "index.db"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def index(self, passages, meanings: dict[str, list[float]] | None = None) -> search.PassageIndex:
        make_index(self.db, passages)
        return search.PassageIndex(self.db, embed=stand_in(meanings or {}))

    def test_what_is_typed_becomes_a_safe_match_with_the_last_word_as_a_prefix(self) -> None:
        self.assertEqual(search.fts_query("nas tunn"), '"nas" AND "tunn"*')
        self.assertEqual(search.fts_query("nas tunnel "), '"nas" AND "tunnel"')  # a space: the word is finished
        self.assertEqual(search.fts_query("nas-tunnel"), '"nas tunnel"*')
        self.assertEqual(search.fts_query('a:b "c'), '"a b" AND "c"*')  # nothing typed reaches FTS5's parser
        self.assertEqual(search.fts_query("  -- "), "")

    def test_words_find_the_passages_holding_every_word_the_last_as_typed_so_far(self) -> None:
        index = self.index(PASSAGES)
        result = index.search("nas tunn", place=as_notes)
        self.assertEqual(found(result), [("Deploy", "words")])
        self.assertEqual(result["words"], {"ok": True, "reason": ""})
        self.assertEqual(result["meaning"], {"ok": True, "reason": ""})
        self.assertEqual(index.search("nas release", place=as_notes)["items"], [])  # every word, in one passage

    def test_meaning_finds_a_page_that_shares_no_word_with_the_query(self) -> None:
        index = self.index(PASSAGES, {"shipping": [1, 0.2, 0, 0]})
        self.assertEqual(found(index.search("shipping it", place=as_notes)), [("Deploy", "meaning")])
        # Noise is still nearest to something; under the floor, that says nothing.
        self.assertEqual(index.search("xqzv", place=as_notes)["items"], [])

    def test_found_both_ways_ranks_first_and_says_so(self) -> None:
        passages = [
            ("Areas/Single.md", "Single", "Notes on the release of the single.", [0.3, 1, 0, 0]),
            ("Areas/Album.md", "Album", "Sequencing the album, track by track.", MUSIC),
        ]
        index = self.index(passages, {"release": MUSIC})
        self.assertEqual(found(index.search("release", place=as_notes)), [("Single", "both"), ("Album", "meaning")])

    def test_a_page_is_one_row_at_its_best_passage(self) -> None:
        passages = [
            (
                "Projects/Deploy.md",
                "Deploy > Overview",
                "Every service runs on the NAS, with backups, monitoring, logs, alerts, dashboards and much else.",
                DEPLOY,
            ),
            ("Projects/Deploy.md", "Deploy > Tunnel", "Service, service: the service tunnel.", DEPLOY),
        ]
        rows = self.index(passages).search("service", place=as_notes)["items"]
        self.assertEqual([(row["title"], row["heading"]) for row in rows], [("Deploy", "Tunnel")])

    def test_a_row_names_its_section_and_quotes_its_passage(self) -> None:
        text = "Intro. " * 40 + "The nas-tunnel carries every service. " + "More. " * 40
        passages = [("Projects/Deploy.md", "Deploy > The **NAS** > Tunnel [[setup|set-up]]", text, DEPLOY)]
        row = self.index(passages).search("nas-tunnel", place=as_notes)["items"][0]
        self.assertEqual(row["heading"], "Tunnel set-up")  # as the page shows it, which is how the reader finds it
        self.assertEqual(row["section"], "The NAS › Tunnel set-up")  # the page's own title isn't repeated
        self.assertIn("The nas-tunnel carries every service.", row["snippet"])
        self.assertTrue(row["snippet"].startswith("…") and row["snippet"].endswith("…"), row["snippet"])
        self.assertLessEqual(len(row["snippet"]), search.SNIPPET_LEN + 2)
        # Where a word starts, as the words leg matched it: "on" inside "rationale" is no place to begin.
        line = "Every service runs behind a single tunnel, and then the rationale follows."
        self.assertEqual(search.snippet(line, "on"), line)

    def test_a_passage_found_by_meaning_alone_is_quoted_from_its_start(self) -> None:
        text = "Every service runs behind a single tunnel, and the rota says who is on call when it drops."
        index = self.index([("Projects/Deploy.md", "Deploy", text, DEPLOY)], {"pager": DEPLOY})
        row = index.search("pager duty on call", place=as_notes)["items"][0]
        self.assertEqual((row["match"], row["snippet"]), ("meaning", text))

    def test_markdown_reads_as_the_page_shows_it(self) -> None:
        self.assertEqual(
            search.plain("See [[Beta|the beta note]], [docs](https://x.y) and **bold** _it_ in snake_case\n## Next"),
            "See the beta note, docs and bold it in snake_case Next",
        )

    def test_only_a_page_one_of_the_trees_lists_comes_back(self) -> None:
        base = Path(self.temp.name)
        notes, artifacts = base / "notes", base / "Artifacts"
        (notes / "Projects").mkdir(parents=True)
        (notes / "Projects" / "Deploy.md").write_text("# Deploy\n", encoding="utf-8")
        (artifacts / "Pages").mkdir(parents=True)
        (artifacts / "Pages" / "one.html").write_text("<title>Page one</title><p>x</p>", encoding="utf-8")
        (artifacts / "Guides" / "Setup").mkdir(parents=True)
        (artifacts / "Guides" / "Setup" / "index.html").write_text("<title>Set-up guide</title>", encoding="utf-8")
        trees = VaultIndex.build(notes, "notes"), VaultIndex.build(artifacts, "html")

        self.assertEqual(
            search.place("Projects/Deploy.md", *trees),
            {"vault": "notes", "path": str(notes / "Projects" / "Deploy.md"), "title": "Deploy", "folder": "Projects"},
        )
        self.assertEqual(
            search.place("Artifacts/Pages/one.html", *trees),
            {"vault": "html", "path": str(artifacts / "Pages" / "one.html"), "title": "Page one", "folder": "Pages"},
        )
        guide = search.place("Artifacts/Guides/Setup/index.html", *trees)  # a folder holding index.html is one page
        self.assertEqual((guide["title"], guide["folder"]), ("Set-up guide", "Guides"))
        for elsewhere in ("Projects/Gone.md", "../notes/Projects/Deploy.md", "Artifacts/../x.md", "Pages/one.html"):
            self.assertIsNone(search.place(elsewhere, *trees), elsewhere)
        self.assertIsNone(search.place("Projects/Deploy.md", None, trees[1]))  # Notes not set up

    def test_without_an_index_there_are_no_passages_and_it_says_where_it_looked(self) -> None:
        index = search.PassageIndex(self.db, embed=stand_in({}))
        result = index.search("tunnel", place=as_notes)
        self.assertEqual(result["items"], [])
        self.assertFalse(result["words"]["ok"])
        self.assertIn("no vault index at", result["words"]["reason"])
        self.assertIn("index.db", result["words"]["reason"])
        self.assertEqual(index.status(warm=True)["passages"], 0)

    def test_without_ollama_words_still_work_and_it_says_why_meaning_is_off(self) -> None:
        make_index(self.db, PASSAGES)
        with socket.socket() as sock:  # a port nothing listens on
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        index = search.PassageIndex(self.db, ollama=f"http://127.0.0.1:{port}")
        result = index.search("nas tunnel", place=as_notes)
        self.assertEqual(found(result), [("Deploy", "words")])
        self.assertEqual(result["meaning"], {"ok": False, "reason": "Ollama isn't running"})
        self.assertEqual(index.status(warm=True)["meaning"], {"ok": False, "reason": "Ollama isn't running"})

    def test_a_rebuilt_index_is_read_again(self) -> None:
        index = self.index(PASSAGES[:1])
        self.assertEqual(index.search("release", place=as_notes)["items"], [])
        make_index(self.db, PASSAGES[1:2], stamp="2026-09-14T00:00:00+00:00")  # vault-mcp's next reindex adds a page
        with patch.object(search, "RELOAD_EVERY", 0):
            self.assertEqual(found(index.search("release", place=as_notes)), [("Music", "words")])

    def test_a_newer_query_stops_an_older_ones_scan(self) -> None:
        index = self.index(PASSAGES)
        data = index._passages()
        index._latest = 2
        with self.assertRaises(search._Superseded):
            index._meaning(data, array.array("f", [1, 0, 0, 0]), 1)


class SearchApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.notes, self.artifacts = base / "notes", base / "Artifacts"
        (self.notes / "Projects").mkdir(parents=True)
        self.deploy = self.notes / "Projects" / "Deploy.md"
        self.deploy.write_text("# Deploy\n\n## NAS\n\nThe nas-tunnel carries every service out to the web.\n", encoding="utf-8")
        (self.artifacts / "Pages").mkdir(parents=True)
        self.one = self.artifacts / "Pages" / "one.html"
        self.one.write_text("<title>Page one</title><h2>Launch plan</h2><p>Friday it opens to everyone.</p>", encoding="utf-8")
        db = base / "index.db"
        # Music.md is in the index but in neither vault, and so is a page no vault lists: neither comes back.
        make_index(db, PASSAGES + [("Elsewhere.md", "", "The nas-tunnel, from a file no vault lists.", DEPLOY)])
        config = AppConfig(default_folder=base, allowed_roots=(base,), port=8899, data_dir=base / "data")
        self.app = create_app(config)
        self.app.state.storage.update_settings(
            {"vault_root": str(self.notes), "html_vault_root": str(self.artifacts)}, model_default=config.model
        )
        self.app.state.passages = search.PassageIndex(db, embed=stand_in({"going live": LAUNCH}))
        self.client_context = TestClient(self.app, base_url="http://127.0.0.1:8899")
        self.client = self.client_context.__enter__()

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        self.temp.cleanup()

    def test_passages_come_back_as_the_trees_list_their_pages(self) -> None:
        result = self.client.get("/api/search", params={"q": "nas tunn"}).json()
        self.assertTrue(result["ok"])
        self.assertEqual(
            result["items"],
            [
                {
                    "vault": "notes",
                    "path": str(self.deploy),
                    "title": "Deploy",
                    "folder": "Projects",
                    "heading": "NAS",
                    "section": "NAS",
                    "snippet": "The nas-tunnel carries every service out to the web.",
                    "match": "words",
                }
            ],
        )
        live = self.client.get("/api/search", params={"q": "going live"}).json()["items"]
        self.assertEqual(
            [(i["vault"], i["path"], i["title"], i["folder"], i["heading"], i["match"]) for i in live],
            [("html", str(self.one), "Page one", "Pages", "Launch plan", "meaning")],
        )

    def test_status_says_what_a_search_would_use(self) -> None:
        status = self.client.get("/api/search/status").json()
        self.assertEqual(
            (status["ok"], status["words"]["ok"], status["meaning"]["ok"], status["passages"]), (True, True, True, 4)
        )

    def test_both_routes_refuse_another_origin(self) -> None:
        for route in ("/api/search?q=nas", "/api/search/status"):
            response = self.client.get(route, headers={"origin": "https://attacker.example"})
            self.assertEqual(response.status_code, 403, route)

    def test_the_shell_carries_the_palette_and_the_app_menu_opens_it(self) -> None:
        page = self.client.get("/").text
        self.assertIn("<dialog id=search-modal", page)
        self.assertIn("openSearch:()=>SEARCH.open()", page)
        # File ▸ Search… (⌘P) calls the entry point the shell exposes, or loads the fragment that opens it.
        swift = LAUNCHER_SWIFT.read_text(encoding="utf-8")
        self.assertIn('menuItem("Search…", #selector(openSearch), "p")', swift)
        self.assertIn('shellCall("onyxShell.openSearch()", fallback: "/#search")', swift)


if __name__ == "__main__":
    unittest.main()
