"""
Solutions Gallery — catalog source abstraction.

The gallery lists solutions from two sources:

1. **Bundled**: zip files (or already-unzipped folders) in
   `SOLUTIONS_BUILTIN_DIR` — first-party solutions shipped with AI Hub.

2. **Remote**: a JSON manifest at `SOLUTIONS_CATALOG_URL` describing
   solutions that can be downloaded on demand. Downloaded bundles are
   cached in `SOLUTIONS_CACHE_DIR`.

This module does **not** install solutions — it just enumerates them and
returns metadata plus a local path to the bundle (downloading it if
necessary). The installer in `solution_installer.py` does the real work.
"""

from __future__ import annotations

import io
import json
import logging
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from solution_manifest import (
    MANIFEST_FILENAME,
    PREVIEW_DIR,
    README_FILENAME,
    SolutionManifest,
)

logger = logging.getLogger(__name__)


@dataclass
class CatalogEntry:
    """Summary row the gallery renders as a tile."""

    id: str
    name: str
    version: str
    description: str
    vertical: str = ""
    tags: List[str] = field(default_factory=list)
    author: str = ""
    source: str = "bundled"  # "bundled" | "remote"
    # Local path to the solution bundle. Empty for remote entries until
    # get_bundle_path() is called, which downloads + caches.
    local_path: str = ""
    # URL the bundle can be fetched from (remote entries only).
    remote_url: str = ""
    # Has a preview image? (drives the tile UI)
    icon_path: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "vertical": self.vertical,
            "tags": list(self.tags),
            "author": self.author,
            "source": self.source,
            "has_icon": bool(self.icon_path),
        }


def _read_manifest_from_zip(zip_path: Path) -> Optional[SolutionManifest]:
    try:
        with zipfile.ZipFile(zip_path) as zf:
            if MANIFEST_FILENAME not in zf.namelist():
                logger.warning("Bundle %s missing %s", zip_path, MANIFEST_FILENAME)
                return None
            with zf.open(MANIFEST_FILENAME) as f:
                data = json.loads(f.read().decode("utf-8"))
            return SolutionManifest.from_dict(data)
    except (zipfile.BadZipFile, json.JSONDecodeError, OSError) as e:
        logger.warning("Could not read bundle %s: %s", zip_path, e)
        return None


def _read_manifest_from_folder(folder: Path) -> Optional[SolutionManifest]:
    manifest_path = folder / MANIFEST_FILENAME
    if not manifest_path.exists():
        return None
    try:
        return SolutionManifest.from_json_file(manifest_path)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Could not read manifest %s: %s", manifest_path, e)
        return None


def _icon_exists(container_path: Path, is_zip: bool) -> bool:
    """Return True if the bundle has an icon under preview/."""
    if is_zip:
        try:
            with zipfile.ZipFile(container_path) as zf:
                names = zf.namelist()
                return any(
                    n.startswith(f"{PREVIEW_DIR}/icon.") for n in names
                )
        except (zipfile.BadZipFile, OSError):
            return False
    else:
        p = container_path / PREVIEW_DIR
        if not p.exists():
            return False
        return any((p / f"icon.{ext}").exists() for ext in ("png", "jpg", "jpeg", "svg", "gif"))


def list_bundled_solutions(builtin_dir: Path) -> List[CatalogEntry]:
    """Enumerate solutions in the builtin directory.

    Accepts either:
      - A `.zip` file directly inside `builtin_dir`
      - A subfolder containing `solution.json` (for iteration during
        development — treated as already-unzipped)
    """
    entries: List[CatalogEntry] = []
    if not builtin_dir.exists():
        return entries

    # Zip files at the top level
    for zip_path in sorted(builtin_dir.glob("*.zip")):
        m = _read_manifest_from_zip(zip_path)
        if m is None:
            continue
        entries.append(
            CatalogEntry(
                id=m.id, name=m.name, version=m.version,
                description=m.description, vertical=m.vertical,
                tags=m.tags, author=m.author, source="bundled",
                local_path=str(zip_path),
                icon_path=f"{PREVIEW_DIR}/icon" if _icon_exists(zip_path, is_zip=True) else "",
            )
        )

    # Subfolders with a solution.json (dev-mode)
    for child in sorted(builtin_dir.iterdir()):
        if not child.is_dir():
            continue
        m = _read_manifest_from_folder(child)
        if m is None:
            continue
        entries.append(
            CatalogEntry(
                id=m.id, name=m.name, version=m.version,
                description=m.description, vertical=m.vertical,
                tags=m.tags, author=m.author, source="bundled",
                local_path=str(child),
                icon_path=f"{PREVIEW_DIR}/icon" if _icon_exists(child, is_zip=False) else "",
            )
        )

    return entries


def list_remote_solutions(catalog_url: str, cache_dir: Path, timeout: float = 10.0) -> List[CatalogEntry]:
    """Fetch the remote manifest and return its entries (bundles NOT downloaded yet)."""
    if not catalog_url:
        return []

    try:
        import requests  # lazy import
        resp = requests.get(catalog_url, timeout=timeout)
        if resp.status_code != 200:
            logger.warning("Remote catalog %s returned HTTP %s", catalog_url, resp.status_code)
            return []
        data = resp.json()
    except Exception as e:
        logger.warning("Could not fetch remote catalog %s: %s", catalog_url, e)
        return []

    # Expected format: {"solutions": [{"id":..., "name":..., "version":..., "url":..., ...}]}
    entries: List[CatalogEntry] = []
    for raw in (data.get("solutions") or []):
        if not raw.get("id") or not raw.get("url"):
            continue
        entries.append(
            CatalogEntry(
                id=str(raw["id"]),
                name=str(raw.get("name") or raw["id"]),
                version=str(raw.get("version") or "1.0.0"),
                description=str(raw.get("description") or ""),
                vertical=str(raw.get("vertical") or ""),
                tags=list(raw.get("tags") or []),
                author=str(raw.get("author") or ""),
                source="remote",
                local_path="",  # filled in on demand by get_bundle_path()
                remote_url=str(raw["url"]),
                icon_path="",  # preview not known until downloaded
            )
        )
    return entries


def get_bundle_path(entry: CatalogEntry, cache_dir: Path, timeout: float = 60.0) -> Optional[Path]:
    """Return a local filesystem path to the bundle for this entry.

    For bundled entries this is just `entry.local_path`. For remote entries,
    this downloads the bundle into the cache (if not already there) and
    returns the cached path.
    """
    if entry.source == "bundled":
        return Path(entry.local_path) if entry.local_path else None

    if not entry.remote_url:
        return None

    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_name = f"{entry.id}_v{entry.version}.zip"
    cache_path = cache_dir / cache_name
    if cache_path.exists():
        return cache_path

    try:
        import requests
        logger.info("Downloading remote solution %s from %s", entry.id, entry.remote_url)
        with requests.get(entry.remote_url, stream=True, timeout=timeout) as resp:
            resp.raise_for_status()
            with open(cache_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
        return cache_path
    except Exception as e:
        logger.warning("Failed to download remote solution %s: %s", entry.id, e)
        if cache_path.exists():
            try:
                cache_path.unlink()
            except OSError:
                pass
        return None


def list_all_solutions(builtin_dir: Path, catalog_url: str, cache_dir: Path) -> List[CatalogEntry]:
    """Combine bundled + remote entries. If a solution id appears in both,
    the bundled entry wins (local takes precedence)."""
    bundled = list_bundled_solutions(builtin_dir)
    seen_ids = {e.id for e in bundled}
    remote = [e for e in list_remote_solutions(catalog_url, cache_dir) if e.id not in seen_ids]
    return bundled + remote


def read_bundle_preview_asset(bundle_path: Path, asset_rel_path: str) -> Optional[bytes]:
    """Return the bytes of a preview asset (icon, screenshot, README) from
    either a zipped or unzipped bundle. `asset_rel_path` is relative to the
    bundle root, e.g. 'preview/icon.png' or 'README.md'.
    """
    # Prevent path traversal.
    norm = asset_rel_path.replace("\\", "/").lstrip("/")
    if ".." in norm.split("/"):
        return None

    if bundle_path.is_dir():
        p = bundle_path / norm
        if p.exists() and p.is_file():
            try:
                return p.read_bytes()
            except OSError:
                return None
        return None

    if not bundle_path.is_file():
        return None

    try:
        with zipfile.ZipFile(bundle_path) as zf:
            if norm in zf.namelist():
                return zf.read(norm)
    except (zipfile.BadZipFile, OSError):
        return None
    return None


def read_bundle_manifest(bundle_path: Path) -> Optional[SolutionManifest]:
    """Convenience: read the manifest from a bundle (zip or folder)."""
    if bundle_path.is_dir():
        return _read_manifest_from_folder(bundle_path)
    if bundle_path.is_file():
        return _read_manifest_from_zip(bundle_path)
    return None
