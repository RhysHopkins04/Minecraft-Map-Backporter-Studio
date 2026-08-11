from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any


WORKSPACE_SCHEMA = 1


def _safe_component(value: str, fallback: str = "catalog") -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-._")
    return value[:96] or fallback


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


class WorkspaceStore:
    """Persistent user-facing storage rooted under the user's Documents folder.

    The UI decides what the platform's Documents location is and passes the
    application-specific root here. The store itself stays free of Qt so its
    persistence behavior can be tested independently.
    """

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.catalogs_dir = self.root / "Catalogs"
        self.workspaces_dir = self.root / "Workspaces"
        self.exports_dir = self.root / "Exports"
        self.default_workspace_path = self.workspaces_dir / "default-workspace.json"

    def ensure_layout(self) -> None:
        for directory in (self.root, self.catalogs_dir, self.workspaces_dir, self.exports_dir):
            directory.mkdir(parents=True, exist_ok=True)

    def save_default_workspace(self, payload: dict[str, Any]) -> Path:
        self.ensure_layout()
        _atomic_json_write(self.default_workspace_path, payload)
        return self.default_workspace_path

    def load_default_workspace(self) -> dict[str, Any] | None:
        if not self.default_workspace_path.is_file():
            return None
        data = json.loads(self.default_workspace_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("kind") != "catalog_workspace":
            raise ValueError("The default catalog workspace file is not a valid WG catalog workspace.")
        return data

    def save_catalog_snapshot(self, catalog: dict[str, Any]) -> Path:
        if not isinstance(catalog, dict) or catalog.get("kind") != "mod_block_catalog":
            raise ValueError("Only mod block catalogs can be stored as catalog snapshots.")

        self.ensure_layout()
        mod_ids = catalog.get("mod_ids") or []
        mod_id = str(mod_ids[0]).strip() if isinstance(mod_ids, list) and mod_ids else ""
        mod_name = str(catalog.get("mod_name") or "").strip()
        version = str(catalog.get("mod_version") or "").strip()
        source = str(catalog.get("source") or "")

        identity = "\n".join((source, mod_id, version)).encode("utf-8", "replace")
        short_hash = hashlib.sha256(identity).hexdigest()[:10]
        stem = _safe_component(mod_id or mod_name or Path(source).stem or "catalog")
        if version:
            stem += "-" + _safe_component(version, "version")
        destination = self.catalogs_dir / f"{stem}-{short_hash}.json"
        _atomic_json_write(destination, catalog)
        return destination

    def save_workspace_copy(self, path: str | Path, payload: dict[str, Any]) -> Path:
        destination = Path(path)
        _atomic_json_write(destination, payload)
        return destination
