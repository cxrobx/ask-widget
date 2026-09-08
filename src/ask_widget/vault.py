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
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import viewer

EXCLUDED_DIRS = {"node_modules", "__pycache__"}  # plus any name starting with "."
NOTE_EXTENSIONS = viewer.LOCAL_DOCUMENT_EXTENSIONS
IMAGE_EXTENSIONS = {ext for ext, ct in viewer.ASSET_CONTENT_TYPES.items() if ct.startswith("image/")}
ATTACHMENT_EXTENSIONS = set(viewer.ASSET_CONTENT_TYPES)
MAX_ENTRIES = 20_000
MAX_DEPTH = 24
DEFAULT_ATTACHMENT_FOLDER = "Other/Attachments"


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

    @property
    def depth(self) -> int:
        return self.rel.count("/")

    @property
    def folder(self) -> str:
        return self.rel.rsplit("/", 1)[0] if "/" in self.rel else ""


def _sort_key(item: VaultFile) -> tuple[int, str]:
    return (item.depth, item.rel.casefold())


@dataclass
class VaultIndex:
    root: Path
    files: list[VaultFile] = field(default_factory=list)
    built_at: float = 0.0
    truncated: bool = False
    attachment_folder: str = DEFAULT_ATTACHMENT_FOLDER
    symlinked_dirs: set[str] = field(default_factory=set)
    _by_rel: dict[str, VaultFile] = field(default_factory=dict, repr=False)
    _by_stem: dict[str, list[VaultFile]] = field(default_factory=dict, repr=False)
    _by_name: dict[str, list[VaultFile]] = field(default_factory=dict, repr=False)
    _tree: dict[str, Any] = field(default_factory=dict, repr=False)

    # MARK: - Building

    @classmethod
    def build(cls, root: Path) -> "VaultIndex":
        root = normalize(root)
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

        def folder_node(rel: str) -> dict[str, Any]:
            node = dirs.get(rel)
            if node is not None:
                return node
            parent_rel, _, name = rel.rpartition("/")
            node = {"name": name, "path": str(self.root / rel), "kind": "dir", "children": []}
            if rel in self.symlinked_dirs:
                node["symlink"] = True
            dirs[rel] = node
            folder_node(parent_rel)["children"].append(node)
            return node

        for item in self.files:
            if item.kind != "note":
                continue
            folder_node(item.folder)["children"].append(
                {
                    "name": item.name,
                    "path": str(item.path),
                    "kind": "file",
                    "ext": item.path.suffix.lower(),
                }
            )

        def sort(node: dict[str, Any]) -> None:
            node["children"].sort(key=lambda n: (0 if n["kind"] == "dir" else 1, n["name"].casefold()))
            for child in node["children"]:
                if child["kind"] == "dir":
                    sort(child)

        sort(root_node)
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
            if item.stem_key.startswith(needle):
                prefix.append(item)
            elif needle in item.name.casefold() or needle in item.rel.casefold():
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
        self._index: VaultIndex | None = None

    def get(self, root: Path) -> VaultIndex:
        root = normalize(root)
        with self._lock:
            cached = self._index
            if (
                cached is not None
                and cached.root == root
                and time.time() - cached.built_at < self.ttl
            ):
                return cached
            index = VaultIndex.build(root)
            self._index = index
            return index

    def invalidate(self) -> None:
        with self._lock:
            self._index = None
