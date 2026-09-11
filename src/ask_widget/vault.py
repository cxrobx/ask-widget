"""Vault mode: a read-only index over an Obsidian-style folder of notes.

The index answers three questions for the reader:
  * what is in the vault (the folder tree and the filter box),
  * where does ``[[Note]]`` / ``![[image.png]]`` point (Obsidian's shortest-path
    resolution, approximated), and
  * is this path inside the vault at all (containment).

Paths are **lexical** end to end. Real vaults contain symlinked folders that
resolve outside the vault root, so every comparison uses ``os.path.normpath`` +
``Path.is_relative_to`` on the path the user sees — never ``Path.resolve()`` and
never ``str.startswith`` (which would accept ``vault-evil`` next to ``vault``).

The same index also serves **Artifacts** (``kind="html"``): a folder of
symlinks to HTML scattered across the disk, browsed the way Obsidian browses
Markdown. Three things differ there, all in service of scanning the list:

  * a page is labelled by its ``<title>``, not its filename — guides are all
    called ``index.html``;
  * below the top level, a folder holding an ``index.html`` IS a page, so a
    guide folder reads as one entry and its ``index.inline.html`` twin, audio
    and notes stay out of the list (they are reachable from the page itself);
  * a symlink whose target is gone stays listed as *missing* rather than
    silently vanishing — in a vault made of links, a moved file is the common
    failure and the list is where you would notice it.

Top-level folders are the vault's projects and are always listed, empty or not.
"""

from __future__ import annotations

import html as html_lib
import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import viewer

EXCLUDED_DIRS = {"node_modules", "__pycache__"}  # plus any name starting with "."
NOTE_EXTENSIONS = viewer.LOCAL_DOCUMENT_EXTENSIONS
HTML_EXTENSIONS = {".html", ".htm"}
INDEX_NAMES = ("index.html", "index.htm")
IMAGE_EXTENSIONS = {ext for ext, ct in viewer.ASSET_CONTENT_TYPES.items() if ct.startswith("image/")}
ATTACHMENT_EXTENSIONS = set(viewer.ASSET_CONTENT_TYPES)
MAX_ENTRIES = 20_000
MAX_DEPTH = 24
DEFAULT_ATTACHMENT_FOLDER = "Other/Attachments"
VAULT_KINDS = ("notes", "html")

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title\s*>", re.IGNORECASE | re.DOTALL)
_TITLE_SCAN_BYTES = 64 * 1024
# (path, mtime_ns, size) → title. The index rebuilds every few seconds while the
# vault is open; re-reading every page's head each time would be the cost of
# the whole feature, and a stat is enough to know nothing changed.
_TITLE_CACHE: dict[tuple[str, int, int], str] = {}
_TITLE_CACHE_MAX = 10_000


def normalize(path: Path | str) -> Path:
    """Collapse ``.``/``..`` segments without touching symlinks."""
    return Path(os.path.normpath(str(path)))


def is_inside(path: Path | str, root: Path | str) -> bool:
    """Lexical containment: ``path`` is ``root`` or lives under it."""
    candidate = normalize(path)
    base = normalize(root)
    if not candidate.is_absolute() or not base.is_absolute():
        return False
    try:
        return candidate == base or candidate.is_relative_to(base)
    except ValueError:
        return False


def html_page_meta(path: Path) -> tuple[str, float] | None:
    """``(title, mtime)`` for an HTML page, following symlinks; None if it is gone.

    The title is the document's ``<title>`` with tags stripped and whitespace
    collapsed, or "" when it has none — the caller decides the fallback, since
    only it knows whether the file stands for itself or for its folder.
    """
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = (str(path), st.st_mtime_ns, st.st_size)
    title = _TITLE_CACHE.get(key)
    if title is None:
        try:
            with open(path, "rb") as handle:
                head = handle.read(_TITLE_SCAN_BYTES).decode("utf-8", errors="replace")
        except OSError:
            head = ""
        match = _TITLE_RE.search(head)
        raw = re.sub(r"<[^>]+>", "", match.group(1)) if match else ""
        title = " ".join(html_lib.unescape(raw).split())[:300]
        if len(_TITLE_CACHE) >= _TITLE_CACHE_MAX:
            _TITLE_CACHE.clear()
        _TITLE_CACHE[key] = title
    return title, st.st_mtime


def html_context_folder(path: Path | str, root: Path | str) -> Path | None:
    """The real folder a page in Artifacts draws its evidence from.

    The vault itself is only links, so it is useless as a context folder — the
    provider's search tools would find nothing but symlinks. The first symlink
    on the page's path is where the vault hands off to the real world: a linked
    folder means that folder's target, a linked file means the target's own
    folder. A page with no symlink on its path lives in the vault for real, and
    its top-level project folder is the context.
    """
    lexical = normalize(path)
    base = normalize(root)
    if lexical == base or not is_inside(lexical, base):
        return None
    parts = lexical.relative_to(base).parts
    current = base
    for part in parts:
        current = current / part
        if os.path.islink(current):
            try:
                target = current.resolve()
            except (OSError, RuntimeError):
                return None
            return target if target.is_dir() else target.parent
    project = base / parts[0]
    return project.resolve() if project.is_dir() else lexical.parent.resolve()


def _clean_entry_name(name: str) -> str:
    name = str(name or "").strip()
    if (
        not name
        or name in {".", ".."}
        or name.startswith(".")
        or "/" in name
        or "\x00" in name
        or len(name.encode("utf-8")) > 255
    ):
        raise ValueError("Use a plain name: no slashes, and not starting with a dot.")
    return name


def writable_folder(root: Path | str, rel: str) -> Path:
    """Resolve ``rel`` to a folder the app may write into, or raise ValueError.

    The app only ever writes inside the vault's OWN folders. A folder reached
    through a symlink is somebody else's directory — ``Anthropic/guides`` is
    really ``~/learnings/.../guides`` — and creating a link there would write
    into the real tree the vault is meant to point at, not contain.
    """
    base = normalize(root)
    rel = str(rel or "").strip().strip("/")
    parts = [part for part in rel.split("/") if part] if rel else []
    if any(part in {".", ".."} or part.startswith(".") for part in parts):
        raise ValueError("That folder is not inside Artifacts.")
    current = base
    for part in parts:
        current = current / part
        if os.path.islink(current):
            raise ValueError(
                f"“{part}” is a linked folder, so it belongs to another tree. "
                "Add to a folder that lives in Artifacts instead."
            )
    if not current.is_dir():
        raise ValueError("That folder does not exist in Artifacts.")
    real_base = Path(os.path.realpath(base))
    real_current = Path(os.path.realpath(current))
    if not (real_current == real_base or real_current.is_relative_to(real_base)):
        raise ValueError("That folder is not inside Artifacts.")
    return current


def create_folder(root: Path | str, parent_rel: str, name: str) -> Path:
    """Make a new (real) folder in Artifacts."""
    folder = writable_folder(root, parent_rel) / _clean_entry_name(name)
    if os.path.lexists(folder):
        raise ValueError(f"“{folder.name}” already exists there.")
    folder.mkdir()
    return folder


def create_link(root: Path | str, parent_rel: str, target: Path | str, name: str | None = None) -> Path:
    """Link an HTML file or a folder into Artifacts. Never touches ``target``.

    A linked ``index.html`` is named after its folder, since every guide would
    otherwise arrive as ``index.html`` and collide with the last one.
    """
    folder = writable_folder(root, parent_rel)
    raw = str(target or "").strip()
    if raw.lower().startswith("file://"):
        from urllib.parse import unquote, urlparse

        raw = unquote(urlparse(raw).path)
    source = normalize(Path(raw).expanduser())
    if not source.is_absolute():
        raise ValueError("Give the full path of the HTML file or folder.")
    if source.is_dir():
        default = source.name
    elif source.is_file():
        if source.suffix.lower() not in HTML_EXTENSIONS:
            raise ValueError("Only HTML files (.html, .htm) or folders can be linked.")
        default = (
            f"{source.parent.name}{source.suffix.lower()}"
            if source.name.lower() in INDEX_NAMES and source.parent.name
            else source.name
        )
    else:
        raise ValueError("That file or folder does not exist.")
    real_vault = Path(os.path.realpath(normalize(root)))
    real_source = Path(os.path.realpath(source))
    if real_source == real_vault or real_source.is_relative_to(real_vault):
        raise ValueError("That is already inside Artifacts.")
    if real_vault.is_relative_to(real_source):
        raise ValueError("That folder contains the Artifacts folder itself, so linking it would loop.")
    link = folder / _clean_entry_name(name or default)
    if source.is_file() and link.suffix.lower() not in HTML_EXTENSIONS:
        link = link.with_name(link.name + source.suffix.lower())
    if os.path.lexists(link):
        raise ValueError(f"“{link.name}” already exists there.")
    os.symlink(str(source), str(link), target_is_directory=source.is_dir())
    return link


def read_attachment_folder(root: Path) -> str:
    """Obsidian's ``attachmentFolderPath`` (vault-relative), with its default."""
    try:
        raw = json.loads((root / ".obsidian" / "app.json").read_text(encoding="utf-8"))
        value = str(raw.get("attachmentFolderPath") or "").strip().strip("/")
    except (OSError, ValueError, AttributeError):
        value = ""
    if not value or value.startswith("."):
        return DEFAULT_ATTACHMENT_FOLDER
    return value


@dataclass(frozen=True)
class VaultFile:
    path: Path  # lexical absolute path — never resolve()d
    rel: str  # posix path relative to the vault root
    name: str
    stem_key: str  # casefolded stem, the wikilink lookup key
    kind: str  # "note" | "attachment"
    # Artifacts only. ``title`` is the display label; ``page_dir`` marks an
    # ``index.html`` standing in for its folder; ``missing`` a dangling link.
    title: str = ""
    mtime: float = 0.0
    page_dir: bool = False
    missing: bool = False

    @property
    def depth(self) -> int:
        return self.rel.count("/")

    @property
    def folder(self) -> str:
        return self.rel.rsplit("/", 1)[0] if "/" in self.rel else ""

    @property
    def entry_rel(self) -> str:
        """Where the entry sits in the tree: a page folder sits where its folder does."""
        return self.folder if self.page_dir else self.rel

    @property
    def label(self) -> str:
        if self.title:
            return self.title
        if self.page_dir:
            return self.path.parent.name
        return os.path.splitext(self.name)[0] if not self.missing else self.name


def _sort_key(item: VaultFile) -> tuple[int, str]:
    return (item.depth, item.rel.casefold())


@dataclass
class VaultIndex:
    root: Path
    kind: str = "notes"
    files: list[VaultFile] = field(default_factory=list)
    built_at: float = 0.0
    truncated: bool = False
    attachment_folder: str = DEFAULT_ATTACHMENT_FOLDER
    symlinked_dirs: set[str] = field(default_factory=set)
    # Artifacts: folders shown even without a page (see _build_html).
    listed_dirs: list[str] = field(default_factory=list)
    _by_rel: dict[str, VaultFile] = field(default_factory=dict, repr=False)
    _by_stem: dict[str, list[VaultFile]] = field(default_factory=dict, repr=False)
    _by_name: dict[str, list[VaultFile]] = field(default_factory=dict, repr=False)
    _tree: dict[str, Any] = field(default_factory=dict, repr=False)

    # MARK: - Building

    @classmethod
    def build(cls, root: Path, kind: str = "notes") -> "VaultIndex":
        root = normalize(root)
        if kind == "html":
            return cls._build_html(root)
        index = cls(root=root, attachment_folder=read_attachment_folder(root))
        seen: set[tuple[int, int]] = set()
        try:
            st = os.stat(root)
            seen.add((st.st_dev, st.st_ino))
        except OSError:
            index.built_at = time.time()
            index._finish()
            return index
        count = 0
        for dirpath, dirnames, filenames in os.walk(str(root), followlinks=True, onerror=lambda _e: None):
            current = Path(dirpath)
            rel_dir = current.relative_to(root).as_posix() if current != root else ""
            depth = rel_dir.count("/") + 1 if rel_dir else 0
            if depth >= MAX_DEPTH:
                dirnames[:] = []
            keep: list[str] = []
            for name in dirnames:
                if name.startswith(".") or name in EXCLUDED_DIRS:
                    continue
                child = os.path.join(dirpath, name)
                try:
                    st = os.stat(child)
                except OSError:
                    continue
                key = (st.st_dev, st.st_ino)
                if key in seen:
                    continue  # symlink loop or a second link to a folder already walked
                seen.add(key)
                if os.path.islink(child):
                    index.symlinked_dirs.add(f"{rel_dir}/{name}" if rel_dir else name)
                keep.append(name)
            keep.sort(key=str.casefold)
            dirnames[:] = keep
            for filename in sorted(filenames, key=str.casefold):
                if filename.startswith("."):
                    continue
                ext = os.path.splitext(filename)[1].lower()
                if ext in NOTE_EXTENSIONS:
                    kind = "note"
                elif ext in ATTACHMENT_EXTENSIONS:
                    kind = "attachment"
                else:
                    continue
                count += 1
                if count > MAX_ENTRIES:
                    index.truncated = True
                    break
                rel = f"{rel_dir}/{filename}" if rel_dir else filename
                index.files.append(
                    VaultFile(
                        path=current / filename,
                        rel=rel,
                        name=filename,
                        stem_key=os.path.splitext(filename)[0].casefold(),
                        kind=kind,
                    )
                )
            if index.truncated:
                break
        index.built_at = time.time()
        index._finish()
        return index

    @classmethod
    def _build_html(cls, root: Path) -> "VaultIndex":
        index = cls(root=root, kind="html")
        seen: set[tuple[int, int]] = set()
        try:
            st = os.stat(root)
            seen.add((st.st_dev, st.st_ino))
        except OSError:
            index.built_at = time.time()
            index._finish()
            return index
        count = 0

        def add(path: Path, rel: str, *, page_dir: bool = False) -> bool:
            nonlocal count
            count += 1
            if count > MAX_ENTRIES:
                index.truncated = True
                return False
            meta = html_page_meta(path)
            index.files.append(
                VaultFile(
                    path=path,
                    rel=rel,
                    name=path.name,
                    stem_key=os.path.splitext(path.name)[0].casefold(),
                    kind="note",
                    title=meta[0] if meta else "",
                    mtime=meta[1] if meta else 0.0,
                    page_dir=page_dir,
                    missing=meta is None,
                )
            )
            return True

        for dirpath, dirnames, filenames in os.walk(str(root), followlinks=True, onerror=lambda _e: None):
            current = Path(dirpath)
            rel_dir = current.relative_to(root).as_posix() if current != root else ""
            depth = rel_dir.count("/") + 1 if rel_dir else 0
            # Below the project level, a folder with an index page is one page.
            index_name = next((n for n in filenames if n.lower() in INDEX_NAMES), None) if depth >= 2 else None
            if index_name is not None:
                dirnames[:] = []
                if rel_dir in index.listed_dirs:
                    index.listed_dirs.remove(rel_dir)  # it is a page now, not a folder
                if not add(current / index_name, f"{rel_dir}/{index_name}", page_dir=True):
                    break
                continue
            if depth >= MAX_DEPTH:
                dirnames[:] = []
            keep: list[str] = []
            for name in dirnames:
                if name.startswith(".") or name in EXCLUDED_DIRS:
                    continue
                child = os.path.join(dirpath, name)
                try:
                    st = os.stat(child)
                except OSError:
                    continue
                key = (st.st_dev, st.st_ino)
                if key in seen:
                    continue
                seen.add(key)
                if os.path.islink(child):
                    index.symlinked_dirs.add(f"{rel_dir}/{name}" if rel_dir else name)
                keep.append(name)
            keep.sort(key=str.casefold)
            dirnames[:] = keep
            # Folders the vault owns are listed even while empty — a folder you
            # just made has to show up. Inside a linked tree only folders that
            # hold pages appear, or a linked repo would list every directory.
            for name in keep:
                child_rel = f"{rel_dir}/{name}" if rel_dir else name
                if depth == 0 or not any(
                    child_rel == linked or child_rel.startswith(linked + "/") for linked in index.symlinked_dirs
                ):
                    index.listed_dirs.append(child_rel)
            for filename in sorted(filenames, key=str.casefold):
                if filename.startswith("."):
                    continue
                path = current / filename
                rel = f"{rel_dir}/{filename}" if rel_dir else filename
                # os.walk files a dangling link under filenames whatever it once
                # pointed at; keep it visible so the break is noticed.
                dangling = os.path.islink(path) and not os.path.exists(path)
                if not dangling and os.path.splitext(filename)[1].lower() not in HTML_EXTENSIONS:
                    continue
                if not add(path, rel):
                    break
            if index.truncated:
                break
        index.built_at = time.time()
        index._finish()
        return index

    def _finish(self) -> None:
        for item in self.files:
            self._by_rel.setdefault(item.rel.casefold(), item)
            self._by_stem.setdefault(item.stem_key, []).append(item)
            self._by_name.setdefault(item.name.casefold(), []).append(item)
        self._tree = self._build_tree()

    def _build_tree(self) -> dict[str, Any]:
        root_node: dict[str, Any] = {
            "name": self.root.name or str(self.root),
            "path": str(self.root),
            "kind": "dir",
            "children": [],
        }
        dirs: dict[str, dict[str, Any]] = {"": root_node}

        html = self.kind == "html"

        def folder_node(rel: str) -> dict[str, Any]:
            node = dirs.get(rel)
            if node is not None:
                return node
            parent_rel, _, name = rel.rpartition("/")
            node = {"name": name, "path": str(self.root / rel), "kind": "dir", "children": []}
            if rel in self.symlinked_dirs:
                node["symlink"] = True
            parent = folder_node(parent_rel)
            if html:
                # The vault's own folders take new links; a linked one (or
                # anything under it) is another tree — see writable_folder.
                node["rel"] = rel
                node["linked"] = bool(node.get("symlink") or parent.get("linked"))
            dirs[rel] = node
            parent["children"].append(node)
            return node

        if html:
            for rel in self.listed_dirs:
                folder_node(rel)  # projects and your own folders show before they hold a page

        for item in self.files:
            if item.kind != "note":
                continue
            if not html:
                folder_node(item.folder)["children"].append(
                    {
                        "name": item.name,
                        "path": str(item.path),
                        "kind": "file",
                        "ext": item.path.suffix.lower(),
                    }
                )
                continue
            parent_rel = item.entry_rel.rpartition("/")[0]
            node = {
                "name": item.path.parent.name if item.page_dir else item.name,
                "title": item.label,
                "path": str(item.path),
                "kind": "file",
                "ext": item.path.suffix.lower(),
                "mtime": item.mtime,
            }
            if item.missing:
                node["missing"] = True
                try:
                    node["target"] = os.readlink(item.path)
                except OSError:
                    pass
            folder_node(parent_rel)["children"].append(node)

        def sort(node: dict[str, Any]) -> None:
            node["children"].sort(
                key=lambda n: (
                    0 if n["kind"] == "dir" else 1,
                    (n.get("title") or n["name"]).casefold(),
                )
            )
            for child in node["children"]:
                if child["kind"] == "dir":
                    sort(child)

        sort(root_node)
        if html:
            root_node["rel"] = ""
            root_node["linked"] = False
        return root_node

    # MARK: - Queries

    @property
    def notes(self) -> list[VaultFile]:
        return [item for item in self.files if item.kind == "note"]

    def contains(self, path: Path | str) -> bool:
        return is_inside(path, self.root)

    def tree_json(self) -> dict[str, Any]:
        return self._tree

    def _pick(self, candidates: list[VaultFile], source: Path | None) -> VaultFile | None:
        if not candidates:
            return None
        if source is not None:
            source_dir = normalize(source).parent
            same_folder = [item for item in candidates if item.path.parent == source_dir]
            if same_folder:
                return min(same_folder, key=_sort_key)
        if len(candidates) == 1:
            return candidates[0]
        return min(candidates, key=_sort_key)

    def resolve_wikilink(self, target: str, *, source: Path | None = None) -> VaultFile | None:
        """Resolve ``[[target]]`` the way Obsidian's shortest-path setting does.

        A target containing ``/`` is a vault-relative path (with or without
        ``.md``); anything else is a note name matched case-insensitively by
        stem. Ties prefer the linking note's own folder, then the shallowest,
        alphabetically-first match.
        """
        target = target.strip().strip("/")
        if not target:
            return None
        if target.casefold().endswith(".md"):
            target = target[:-3]
        key = target.casefold()
        if "/" in target:
            for candidate in (key, f"{key}.md", *(f"{key}{ext}" for ext in NOTE_EXTENSIONS)):
                item = self._by_rel.get(candidate)
                if item is not None and item.kind == "note":
                    return item
            suffix = "/" + key
            matches = [
                item
                for item in self.notes
                if item.rel.casefold().endswith((suffix, f"{suffix}.md"))
            ]
            return self._pick(matches, source)
        candidates = [item for item in self._by_stem.get(key, []) if item.kind == "note"]
        markdown = [item for item in candidates if item.path.suffix.lower() in {".md", ".markdown"}]
        return self._pick(markdown or candidates, source)

    def resolve_embed(self, target: str, *, source: Path | None = None) -> VaultFile | None:
        """Resolve ``![[target]]``: attachments by full filename, notes by name."""
        target = target.strip().strip("/")
        if not target:
            return None
        key = target.casefold()
        if "/" in target:
            item = self._by_rel.get(key)
            if item is not None:
                return item
            key = key.rsplit("/", 1)[1]
        candidates = list(self._by_name.get(key, []))
        if candidates:
            attachment_prefix = self.attachment_folder.casefold() + "/"
            in_attachments = [
                item for item in candidates if item.rel.casefold().startswith(attachment_prefix)
            ]
            if in_attachments:
                return min(in_attachments, key=_sort_key)
            return self._pick(candidates, source)
        if not os.path.splitext(target)[1]:
            return self.resolve_wikilink(target, source=source)
        return None

    def search(self, query: str, *, limit: int = 50) -> tuple[list[VaultFile], bool]:
        needle = query.strip().casefold()
        if not needle:
            return [], False
        prefix: list[VaultFile] = []
        other: list[VaultFile] = []
        for item in self.notes:
            if item.missing:
                continue  # nothing to open; the tree is where a broken link shows
            label = item.label.casefold() if self.kind == "html" else ""
            if item.stem_key.startswith(needle) or (label and label.startswith(needle)):
                prefix.append(item)
            elif needle in item.name.casefold() or needle in item.rel.casefold() or needle in label:
                other.append(item)
        prefix.sort(key=_sort_key)
        other.sort(key=lambda item: (len(item.rel), item.rel.casefold()))
        results = prefix + other
        return results[:limit], len(results) > limit


class VaultCache:
    """Rebuilds the index at most once per ``ttl`` seconds; thread-safe."""

    def __init__(self, ttl: float = 5.0) -> None:
        self.ttl = ttl
        self._lock = threading.Lock()
        # One slot per vault kind, so switching between Notes and HTML does not
        # throw the other index away.
        self._indexes: dict[str, VaultIndex] = {}

    def get(self, root: Path, kind: str = "notes") -> VaultIndex:
        root = normalize(root)
        with self._lock:
            cached = self._indexes.get(kind)
            if (
                cached is not None
                and cached.root == root
                and time.time() - cached.built_at < self.ttl
            ):
                return cached
            index = VaultIndex.build(root, kind=kind)
            self._indexes[kind] = index
            return index

    def invalidate(self) -> None:
        with self._lock:
            self._indexes.clear()
