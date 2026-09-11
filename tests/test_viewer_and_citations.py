from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from urllib.parse import quote

from reportlab.pdfgen import canvas

from ask_widget.citations import extract_citations
from ask_widget.vault import VaultIndex
from ask_widget.launcher_ui import (
    BASE_RGB,
    READABLE_CONTRAST,
    SIDEBAR_LABEL,
    SIDEBAR_READABLE,
    SIDEBAR_TINT,
    blur_radius,
    glass_alphas,
    glass_script,
    sidebar_contrast,
    theme_style,
)
from ask_widget.viewer import (
    RangeNotSatisfiable,
    ViewerError,
    byte_range,
    load_local_document,
    parse_flat_frontmatter,
    prepare_html,
    split_frontmatter,
    validate_remote_url,
)


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

    def test_frontmatter_becomes_a_properties_block(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "note.md"
            path.write_text(
                "---\n"
                "title: \"Quoted title\"\n"
                "tags: [career, 'ai-lab']\n"
                "aliases:\n"
                "  - Trusted Counselor\n"
                "  - TC\n"
                "status:\n"
                "created: 2026-07-02\n"
                "# a comment\n"
                "---\n\n"
                "# Real heading\n\nBody text.\n",
                encoding="utf-8",
            )
            loaded = load_local_document(path)
            self.assertEqual(loaded.title, "Real heading")
            self.assertIn('<details class="askw-properties"><summary>Properties · 5</summary>', loaded.html)
            self.assertIn('<span class="askw-tag">#career</span><span class="askw-tag">#ai-lab</span>', loaded.html)
            self.assertIn("<dd>Trusted Counselor, TC</dd>", loaded.html)
            self.assertIn('<dt>status</dt><dd><span class="askw-empty">—</span></dd>', loaded.html)
            self.assertIn("<dd>Quoted title</dd>", loaded.html)
            self.assertNotIn("<hr", loaded.html)
            self.assertNotIn("created: 2026", loaded.html)

            path.write_text("---\ntitle: From frontmatter\n---\n\nNo heading here.\n", encoding="utf-8")
            self.assertEqual(load_local_document(path).title, "From frontmatter")

            path.write_text("---\nnested:\n  deep: value\n---\n\n# Nested\n", encoding="utf-8")
            nested = load_local_document(path)
            self.assertIn('<details class="askw-properties"><summary>Properties</summary><pre>', nested.html)
            self.assertIn("deep: value", nested.html)
            self.assertEqual(nested.title, "Nested")

            path.write_text("---\n\nJust a rule, then prose\n\n---\n\nmore\n", encoding="utf-8")
            rule = load_local_document(path)
            self.assertEqual(rule.html.count("<hr />"), 2)
            self.assertIn("Just a rule, then prose", rule.html)
            self.assertEqual(split_frontmatter("---\n---\nbody")[0], "")
            self.assertIsNone(parse_flat_frontmatter("a:\n  b: c"))

    def test_relative_document_links_rewrite_to_the_reader(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "notes").mkdir()
            path = root / "notes" / "Index.md"
            path.write_text(
                "[x](How%20to.md) [y](../B.md#sec) [site](https://example.com/a.md) "
                "[img](notes.png) [f](file:///tmp/nope.md) [same](#local)\n",
                encoding="utf-8",
            )
            loaded = load_local_document(path, folder=str(root))
            self.assertIn(
                f'href="/view?src={quote(str(root / "notes" / "How to.md"))}&amp;folder={quote(str(root))}"',
                loaded.html,
            )
            self.assertIn(f'href="/view?src={quote(str(root / "B.md"))}&amp;folder={quote(str(root))}#sec"', loaded.html)
            self.assertIn('href="https://example.com/a.md" rel="noreferrer noopener" target="_top"', loaded.html)
            self.assertIn('href="notes.png"', loaded.html)
            self.assertIn('href="file:///tmp/nope.md"', loaded.html)  # absent file stays untouched
            self.assertIn('href="#local"', loaded.html)

    def test_wikilinks_render_only_in_vault_context(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            vault = Path(raw) / "vault"
            (vault / "Other" / "Attachments").mkdir(parents=True)
            alpha = vault / "Alpha.md"
            alpha.write_text(
                "[[Beta]] and [[Beta|the alias]] and [[Beta#Part]] and `[[Beta]]` and [[Nowhere]] "
                "![[Pasted image.png]] ![[Beta]] ![[song.mp3]]\n",
                encoding="utf-8",
            )
            (vault / "Beta.md").write_text("# Beta\n", encoding="utf-8")
            (vault / "Other" / "Attachments" / "Pasted image.png").write_bytes(b"png")
            (vault / "song.mp3").write_bytes(b"mp3")
            index = VaultIndex.build(vault)
            loaded = load_local_document(alpha, folder=str(vault), vault=index)
            beta = quote(str(vault / "Beta.md"))
            self.assertIn(f'<a href="/view?src={beta}&amp;folder={quote(str(vault))}" class="askw-wikilink" title="Beta.md" rel="noreferrer noopener">Beta</a>', loaded.html)
            self.assertIn('class="askw-wikilink" title="Beta.md" rel="noreferrer noopener">the alias</a>', loaded.html)
            self.assertIn(f'href="/view?src={beta}&amp;folder={quote(str(vault))}#Part"', loaded.html)
            self.assertIn("<code>[[Beta]]</code>", loaded.html)
            self.assertIn('<span class="askw-wikilink-missing" title="No note named “Nowhere”">Nowhere</span>', loaded.html)
            self.assertIn(f'<img src="{quote(str(vault / "Other" / "Attachments" / "Pasted image.png"))}" alt="Pasted image.png" />', loaded.html)
            self.assertIn('class="askw-wikilink askw-embed" title="Beta.md" rel="noreferrer noopener">Beta.md</a>', loaded.html)
            self.assertIn('<span class="askw-wikilink-missing" title="Unsupported embed">song.mp3</span>', loaded.html)

            plain = load_local_document(alpha, folder=str(vault))
            self.assertIn("[[Beta]] and [[Beta|the alias]]", plain.html)
            self.assertNotIn('class="askw-wikilink', plain.html)

            html, assets = prepare_html(
                str(alpha),
                html_text=loaded.html,
                server_origin="http://127.0.0.1:8899",
                folder=str(vault),
                asset_token="cap",
            )
            self.assertIn("/_fs/cap/", html)
            self.assertEqual(assets, {str((vault / "Other" / "Attachments" / "Pasted image.png").resolve())})

    def test_percent_encoded_relative_images_are_registered(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            image = root / "Pasted image.png"
            image.write_bytes(b"png")
            html, assets = prepare_html(
                str(root / "doc.html"),
                html_text='<html><body><img src="Pasted%20image.png"></body></html>',
                server_origin="http://127.0.0.1:8899",
                folder=str(root),
                asset_token="cap",
            )
            self.assertEqual(assets, {str(image.resolve())})
            self.assertIn("/_fs/cap" + quote(str(image.resolve())), html)

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


class RangeAndGlassTests(unittest.TestCase):
    def test_byte_range_follows_rfc_9110_for_single_ranges(self) -> None:
        self.assertIsNone(byte_range(None, 10))
        self.assertEqual(byte_range("bytes=0-1", 10), (0, 1))
        self.assertEqual(byte_range("bytes=5-", 10), (5, 9))
        self.assertEqual(byte_range("bytes=-3", 10), (7, 9))
        self.assertEqual(byte_range("bytes=-20", 10), (0, 9))
        self.assertEqual(byte_range("bytes=8-100", 10), (8, 9))
        self.assertEqual(byte_range(" Bytes = 2 - 4 ", 10), (2, 4))
        for ignored in ("items=0-1", "bytes=0-1,3-4", "bytes=abc", "bytes=-", "bytes=3-1", "bytes", "bytes=1"):
            self.assertIsNone(byte_range(ignored, 10), ignored)
        for unsatisfiable, size in (("bytes=10-", 10), ("bytes=10-12", 10), ("bytes=-0", 10), ("bytes=0-", 0)):
            with self.assertRaises(RangeNotSatisfiable, msg=unsatisfiable):
                byte_range(unsatisfiable, size)

    def test_media_tags_are_rewritten_to_capability_urls(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "audio").mkdir()
            for name in ("one.m4a", "clip.webm", "en.vtt"):
                (root / "audio" / name).write_bytes(b"x")
            html, assets = prepare_html(
                str(root / "guide.html"),
                html_text=(
                    '<body><audio controls preload="none" src="audio/one.m4a"></audio>'
                    "<video src='audio/clip.webm'><track src=\"audio/en.vtt\"></video></body>"
                ),
                server_origin="http://127.0.0.1:8899",
                folder=str(root),
                asset_token="cap",
            )
            self.assertEqual(
                assets, {str((root / "audio" / n).resolve()) for n in ("one.m4a", "clip.webm", "en.vtt")}
            )
            self.assertNotIn('src="audio/one.m4a"', html)
            self.assertIn('<audio controls preload="none" src="http://127.0.0.1:8899/_fs/cap/', html)

    def test_blur_radius_is_the_cxtasks_curve(self) -> None:
        self.assertEqual(blur_radius(0), 10)
        self.assertEqual(blur_radius(0.38), 24)  # the default slider lands where cxtasks pins it
        self.assertEqual(blur_radius(1), 48)
        self.assertEqual(blur_radius(7), 48)
        self.assertEqual(blur_radius(float("nan")), 10)  # a corrupt value must not reach the bridge as NaN
        script = glass_script({"glass_transparency": 38})
        self.assertIn("Math.round(10+t*(48-10))", script)
        self.assertIn("requestAnimationFrame(()=>requestAnimationFrame(startGlass))", script)
        self.assertIn("window.askwReduceTransparency=", script)
        # The opaque colours the page sends must be the --bg-primary tokens it paints.
        style = theme_style({})
        for theme, (r, g, b) in BASE_RGB.items():
            self.assertIn(f"[{r},{g},{b}]", script, theme)
            self.assertIn(f"--bg-primary:{r} {g} {b}", style, theme)

    def test_sidebar_labels_stay_readable_over_any_backdrop(self) -> None:
        for theme in ("dark", "light"):
            floor = SIDEBAR_READABLE[theme]
            # The floor is the tightest alpha that clears AA, not a padded guess.
            self.assertGreaterEqual(sidebar_contrast(floor, theme), READABLE_CONTRAST, theme)
            self.assertLess(sidebar_contrast(floor - 0.01, theme), READABLE_CONTRAST, theme)
            for step in range(101):
                sidebar = glass_alphas(step / 100, theme == "dark")[1]
                self.assertGreaterEqual(sidebar, floor, (theme, step))
                self.assertGreaterEqual(sidebar_contrast(sidebar, theme), READABLE_CONTRAST, (theme, step))
        self.assertEqual(SIDEBAR_READABLE, {"dark": 0.79, "light": 0.84})
        self.assertEqual(glass_alphas(0, True), (1, 1, 1))  # opaque still means opaque
        # The floor is derived from these tokens; if the palette moves, so must they.
        style = theme_style({})
        self.assertIn("--bg-sidebar:42 43 43", style)
        self.assertEqual(SIDEBAR_TINT["dark"], 43)
        self.assertIn(f"--bg-sidebar:{SIDEBAR_TINT['light']} {SIDEBAR_TINT['light']} {SIDEBAR_TINT['light']}", style)
        for theme in ("dark", "light"):
            label = SIDEBAR_LABEL[theme]
            self.assertIn(f"--secondary:{label} {label} {label}", style)
        self.assertIn("dark?0.79:0.84", glass_script({}))
