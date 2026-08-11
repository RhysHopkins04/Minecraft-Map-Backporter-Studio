from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MappingProfile:
    """Resolved catalog policy used by conversion mapping.

    Catalogs do not invent block-to-block mappings on their own. They decide
    which mod namespaces are eligible for the converter's reviewed safe mapping
    rules. The target world's Forge registry remains authoritative for whether a
    concrete target block actually exists.
    """

    allow_safe_mod_replacements: bool = True
    catalog_bound: bool = False
    enabled_catalogs: tuple[str, ...] = ()
    enabled_mod_ids: frozenset[str] = frozenset()
    registry_hints: frozenset[str] = frozenset()
    candidate_count: int = 0

    def allows_namespace(self, namespace: str) -> bool:
        if not self.allow_safe_mod_replacements:
            return False
        if not self.catalog_bound:
            # CLI/backwards-compatible mode: use reviewed built-in safe mappings
            # exactly as older builds did when no UI catalog snapshot is supplied.
            return True
        return namespace.strip().lower() in self.enabled_mod_ids

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": "enabled_catalogs_safe_rules" if self.catalog_bound else "legacy_safe_rules",
            "allow_safe_mod_replacements": bool(self.allow_safe_mod_replacements),
            "catalog_bound": bool(self.catalog_bound),
            "enabled_catalogs": list(self.enabled_catalogs),
            "enabled_mod_ids": sorted(self.enabled_mod_ids),
            "registry_hint_count": len(self.registry_hints),
            "candidate_count": int(self.candidate_count),
        }

    def fingerprint_payload(self) -> dict[str, Any]:
        payload = self.to_dict()
        # Include the actual hint set in the fingerprint so changing a catalog
        # invalidates an already-completed conversion preflight.
        payload["registry_hints"] = sorted(self.registry_hints)
        return payload


def profile_from_catalog_snapshot(
    snapshot: dict[str, Any] | None,
    allow_safe_mod_replacements: bool = True,
) -> MappingProfile:
    """Build a conversion profile from the active Catalog Workspace snapshot.

    ``snapshot is None`` deliberately means legacy/CLI behavior where reviewed
    safe mappings are not catalog-gated. A supplied (even empty) snapshot means
    the desktop UI is catalog-bound, so no mod namespace is eligible unless an
    enabled catalog declares that mod id.
    """

    if snapshot is None:
        return MappingProfile(
            allow_safe_mod_replacements=bool(allow_safe_mod_replacements),
            catalog_bound=False,
        )

    labels = tuple(str(x) for x in (snapshot.get("enabled_catalogs") or []) if str(x).strip())
    mod_ids = frozenset(
        str(x).strip().lower()
        for x in (snapshot.get("enabled_mod_ids") or [])
        if str(x).strip()
    )
    hints = frozenset(
        str(x).strip().lower()
        for x in (snapshot.get("registry_hints") or [])
        if str(x).strip()
    )
    return MappingProfile(
        allow_safe_mod_replacements=bool(allow_safe_mod_replacements),
        catalog_bound=True,
        enabled_catalogs=labels,
        enabled_mod_ids=mod_ids,
        registry_hints=hints,
        candidate_count=int(snapshot.get("candidate_count") or 0),
    )
