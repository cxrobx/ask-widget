"""First run on a new Mac: find the vault, make the Artifacts folder, install the Obsidian plugin.

The shipped defaults (``storage.DEFAULT_SETTINGS``) name ``~/Documents/CX`` and
``~/Documents/Artifacts``. On any other Mac the first is usually missing, and the
vault look then never arrives even with the plugin running: the plugin's snapshot
is stored under the vault's real path, and a vault setting pointing somewhere else
never reads it back. So while a folder setting is still the untouched default,
``adopt_folders`` swaps a missing vault for the one Obsidian itself has open and
creates the Artifacts folder. A folder the user chose, or cleared, is never touched.

``install_plugin`` copies the plugin the app carries into
``<vault>/.obsidian/plugins/onyx`` and lists it in ``community-plugins.json``. Those
are its only writes; it refuses when any folder on the way is a symlink, and each
file lands through a temporary name and ``os.replace``, which swaps a symlink at the
destination rather than writing through it.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger("onyx.first_run")

PLUGIN_ID = "onyx"
PLUGIN_FILES = ("manifest.json", "main.js", "styles.css")


def obsidian_config() -> Path:
    return Path.home() / "Library" / "Application Support" / "obsidian" / "obsidian.json"


def obsidian_vaults(config: Path | None = None) -> list[Path]:
    """The vaults Obsidian knows, the open one first, then most recently used. Obsidian's file, read defensively."""
    try:
        data = json.loads((config or obsidian_config()).read_text(encoding="utf-8"))
        entries = data.get("vaults") if isinstance(data, dict) else None
        if not isinstance(entries, dict):
            return []
    except (OSError, ValueError):
        return []
    found: list[tuple[bool, float, Path]] = []
    for entry in entries.values():
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            continue
        path = Path(entry["path"])
        ts = entry.get("ts")
        try:
            if path.is_absolute() and path.is_dir():
                found.append((bool(entry.get("open")), float(ts) if isinstance(ts, (int, float)) else 0.0, path))
        except OSError:
            continue
    found.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [path for _open, _ts, path in found]


def adopt_folders(storage: Any, *, model_default: str, config: Path | None = None) -> None:
    from .storage import DEFAULT_SETTINGS

    stored = storage.stored_setting_keys()
    if "vault_root" not in stored and not Path(DEFAULT_SETTINGS["vault_root"]).is_dir():
        vaults = obsidian_vaults(config)
        # With no Obsidian vault the setting is cleared, so Notes hides instead of pointing at a missing folder.
        chosen = str(vaults[0]) if vaults else ""
        try:
            storage.update_settings({"vault_root": chosen}, model_default=model_default)
            logger.info("First run: vault folder set to %s", chosen or "(none)")
        except ValueError as exc:
            logger.warning("First run: couldn't adopt the vault folder %s: %s", chosen, exc)
    if "html_vault_root" not in stored:
        artifacts = Path(DEFAULT_SETTINGS["html_vault_root"])
        try:
            if not artifacts.exists():
                artifacts.mkdir(parents=True)
                logger.info("First run: created the Artifacts folder %s", artifacts)
        except OSError as exc:
            logger.warning("First run: couldn't create the Artifacts folder %s: %s", artifacts, exc)


def plugin_source() -> Path | None:
    """The built plugin the app carries: bundled by build-app.sh, or the checkout's own build when run from source."""
    frozen = getattr(sys, "_MEIPASS", None)
    folder = (
        Path(frozen) / "obsidian-plugin"
        if frozen
        else Path(__file__).resolve().parent.parent.parent / "integrations" / "obsidian"
    )
    return folder if all((folder / name).is_file() for name in PLUGIN_FILES) else None


def _version(manifest: Path) -> str | None:
    try:
        value = json.loads(manifest.read_text(encoding="utf-8")).get("version")
    except (OSError, ValueError, AttributeError):
        return None
    return value if isinstance(value, str) else None


def _enabled_list(obsidian: Path) -> list[str] | None:
    try:
        value = json.loads((obsidian / "community-plugins.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        return None
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else None


def plugin_status(vault_root: Path | None, source: Path | None = None) -> dict[str, Any]:
    source = source if source is not None else plugin_source()
    status: dict[str, Any] = {
        "bundled": source is not None,
        "bundled_version": _version(source / "manifest.json") if source else None,
        "vault": vault_root is not None and (vault_root / ".obsidian").is_dir(),
        "installed": False,
        "version": None,
        "enabled": False,
    }
    if not status["vault"]:
        return status
    target = vault_root / ".obsidian" / "plugins" / PLUGIN_ID
    status["version"] = _version(target / "manifest.json")
    status["installed"] = status["version"] is not None and (target / "main.js").is_file()
    status["enabled"] = PLUGIN_ID in (_enabled_list(vault_root / ".obsidian") or [])
    return status


def _write(path: Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".onyx-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _real_dir(path: Path, *, create: bool) -> None:
    if path.is_symlink():
        raise ValueError(f"{path} is a symlink; Onyx won't install through it.")
    if not path.exists():
        if not create:
            raise ValueError(f"{path} does not exist.")
        path.mkdir()
    elif not path.is_dir():
        raise ValueError(f"{path} is not a folder.")


def install_plugin(vault_root: Path, source: Path | None = None) -> dict[str, Any]:
    """Copy the bundled plugin into the vault and enable it. Writes only under ``.obsidian``; see the module doc."""
    source = source if source is not None else plugin_source()
    if source is None:
        raise ValueError("This build of Onyx doesn't carry the Obsidian plugin.")
    obsidian = vault_root / ".obsidian"
    if not obsidian.exists():
        raise ValueError("That folder isn't an Obsidian vault: it has no .obsidian folder. Open it in Obsidian once first.")
    _real_dir(obsidian, create=False)
    plugins = obsidian / "plugins"
    _real_dir(plugins, create=True)
    target = plugins / PLUGIN_ID
    _real_dir(target, create=True)
    for name in PLUGIN_FILES:
        _write(target / name, (source / name).read_bytes())
    enabled = _enabled_list(obsidian)
    if enabled is None:
        raise ValueError("Obsidian's community-plugins.json isn't a list; enable Onyx in Obsidian ▸ Community plugins.")
    if PLUGIN_ID not in enabled:
        listing = obsidian / "community-plugins.json"
        if listing.is_symlink():
            raise ValueError(f"{listing} is a symlink; Onyx won't write through it.")
        _write(listing, (json.dumps(enabled + [PLUGIN_ID], indent=2) + "\n").encode("utf-8"))
    return plugin_status(vault_root, source)
