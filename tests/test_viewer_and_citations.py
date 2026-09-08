from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from reportlab.pdfgen import canvas

from ask_widget.citations import extract_citations
from ask_widget.viewer import ViewerError, load_local_document, prepare_html, validate_remote_url


class ViewerAndCitationTests(unittest.TestCase):
    def test_markdown_is_rendered_without_executing_raw_html(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "notes.md"
            path.write_text(
                "# Notes\n\n"
                "<script>alert(1)</script>\n\n"
                "- a wrapped item\n"
                "  that stays in the same list entry\n\n"
                "1. first\n"
                "2. second\n\n"
                "| Source | Result |\n"
                "| --- | --- |\n"
                "| local | safe |\n\n"
                "[unsafe](javascript:alert(1))\n",
                encoding="utf-8",
            )
            loaded = load_local_document(path)
            self.assertEqual(loaded.kind, "markdown")
            self.assertIn("<h1>Notes</h1>", loaded.html)
            self.assertNotIn("<script>alert", loaded.html)
            self.assertIn("&lt;script&gt;", loaded.html)
            self.assertIn("<li>a wrapped item\nthat stays in the same list entry</li>", loaded.html)
            self.assertIn("<ol>", loaded.html)
            self.assertIn("<table>", loaded.html)
            self.assertNotIn('href="javascript:', loaded.html)
            self.assertIn("prefers-color-scheme:dark", loaded.html)
            self.assertIn("backdrop-filter:blur(22px)", loaded.html)

    def test_pdf_reader_preserves_page_numbers(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "two-pages.pdf"
            writer = canvas.Canvas(str(path), pagesize=(612, 792))
            writer.setTitle("Two Pages")
            writer.drawString(72, 720, "Evidence on the first page")
            writer.showPage()
            writer.drawString(72, 720, "Evidence on the second page")
            writer.save()
            loaded = load_local_document(path)
            self.assertEqual(loaded.kind, "pdf")
            self.assertEqual(loaded.page_count, 2)
            self.assertIn('data-askw-page="1"', loaded.html)
            self.assertIn('data-askw-page="2"', loaded.html)
            self.assertIn("Evidence on the first page", loaded.html)
            self.assertIn("Evidence on the second page", loaded.html)

    def test_remote_private_addresses_are_rejected(self) -> None:
        with self.assertRaises(ViewerError):
            validate_remote_url("http://127.0.0.1/private")
        self.assertEqual(
            validate_remote_url("http://127.0.0.1/private", allow_private=True),
            "http://127.0.0.1/private",
        )

    def test_document_assets_use_a_capability_url(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            doc = root / "doc.html"
            image = root / "image.png"
            image.write_bytes(b"png")
            html, assets = prepare_html(
                str(doc),
                html_text='<html><body><img src="image.png"></body></html>',
                server_origin="http://127.0.0.1:8899",
                folder=str(root),
                asset_token="capability",
            )
            self.assertIn("/_fs/capability/", html)
            self.assertIn('name="askw-doc-token" content="capability"', html)
            self.assertEqual(assets, {str(image.resolve())})

    def test_trusted_local_html_keeps_interactive_scripts(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            doc = root / "interactive.html"
            behavior = root / "behavior.js"
            behavior.write_text("window.externalButtonReady = true", encoding="utf-8")
            html, assets = prepare_html(
                str(doc),
                html_text=(
                    '<html><body><button onclick="setLevel(\'elii\')">ELII</button>'
                    '<script>window.setLevel = function () { return true }</script>'
                    '<script src="behavior.js"></script></body></html>'
                ),
                server_origin="http://127.0.0.1:8899",
                folder=str(root),
                asset_token="capability",
                allow_document_scripts=True,
            )
            self.assertIn('onclick="setLevel(\'elii\')"', html)
            self.assertIn("window.setLevel", html)
            self.assertIn("/_fs/capability/", html)
            self.assertEqual(assets, {str(behavior.resolve())})

    def test_remote_html_scripts_stay_inert_even_if_requested(self) -> None:
        html, _ = prepare_html(
            "https://example.com/article",
            html_text='<html><body onclick="alert(1)"><script>alert(1)</script></body></html>',
            server_origin="http://127.0.0.1:8899",
            folder=None,
            allow_document_scripts=True,
        )
        self.assertNotIn("<script>alert(1)</script>", html)

    def test_citations_are_validated_and_include_snippets(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "src" / "app.py"
            source.parent.mkdir()
            source.write_text("one\ntwo\nimportant evidence\nfour\n", encoding="utf-8")
            outside = root.parent / "not-allowed.py"
            answer = f"See `src/app.py:3` and `{outside}:1`."
            citations = extract_citations(answer, root)
            self.assertEqual(len(citations), 1)
            self.assertEqual(citations[0]["label"], "src/app.py:3")
            self.assertIn("important evidence", citations[0]["snippet"])
