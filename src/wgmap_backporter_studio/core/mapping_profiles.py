from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class BackportTarget:
    source_name: str
    target_name: str
    provider: str = ""
    confidence: str = ""


@dataclass(frozen=True)
class MappingProfile:
    """Resolved Catalog Workspace policy used by conversion mapping.

    The target world's actual registry remains authoritative. Catalogs can now
    contribute exact-name candidates only when they are explicitly classified as
    backport providers; general mods and architectural fallback mods do not gain
    automatic source->target authority merely by being enabled.
    """

    allow_safe_mod_replacements: bool = True
    catalog_bound: bool = False
    enabled_catalogs: tuple[str, ...] = ()
    enabled_mod_ids: frozenset[str] = frozenset()
    registry_hints: frozenset[str] = frozenset()
    candidate_count: int = 0
    backport_targets: tuple[BackportTarget, ...] = ()

    def allows_namespace(self, namespace: str) -> bool:
        if not self.allow_safe_mod_replacements:
            return False
        if not self.catalog_bound:
            return True
        return namespace.strip().lower() in self.enabled_mod_ids

    def backport_candidates(self, source_name: str) -> tuple[BackportTarget, ...]:
        source = source_name.strip().lower()
        if ":" not in source:
            source = "minecraft:" + source
        return tuple(target for target in self.backport_targets if target.source_name == source)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": "enabled_catalogs_provider_rules" if self.catalog_bound else "legacy_safe_rules",
            "allow_safe_mod_replacements": bool(self.allow_safe_mod_replacements),
            "catalog_bound": bool(self.catalog_bound),
            "enabled_catalogs": list(self.enabled_catalogs),
            "enabled_mod_ids": sorted(self.enabled_mod_ids),
            "registry_hint_count": len(self.registry_hints),
            "candidate_count": int(self.candidate_count),
            "backport_provider_target_count": len(self.backport_targets),
            "backport_provider_namespaces": sorted({x.target_name.split(":", 1)[0] for x in self.backport_targets if ":" in x.target_name}),
        }

    def fingerprint_payload(self) -> dict[str, Any]:
        payload = self.to_dict()
        payload["registry_hints"] = sorted(self.registry_hints)
        payload["backport_targets"] = [
            {
                "source_name": x.source_name,
                "target_name": x.target_name,
                "provider": x.provider,
                "confidence": x.confidence,
            }
            for x in self.backport_targets
        ]
        return payload


def _target_from_row(row: dict[str, Any], provider: str) -> list[BackportTarget]:
    hint = str(row.get("registry_hint") or "").strip().lower()
    if ":" not in hint:
        return []
    confidence = str(row.get("confidence") or "").strip().lower()
    kind = str(row.get("candidate_kind") or "").strip().lower()
    if confidence == "low" or "texture-only" in kind:
        return []
    aliases = [str(x).strip().lower() for x in (row.get("mapping_aliases") or []) if str(x).strip()]
    if not aliases:
        aliases = [hint.split(":", 1)[1]]
    out: list[BackportTarget] = []
    for alias in aliases:
        source = alias if ":" in alias else "minecraft:" + alias
        out.append(BackportTarget(source, hint, provider, confidence))
    return out


def profile_from_catalog_snapshot(
    snapshot: dict[str, Any] | None,
    allow_safe_mod_replacements: bool = True,
) -> MappingProfile:
    """Build a conversion profile from the active Catalog Workspace snapshot."""

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

    provider_targets: list[BackportTarget] = []
    seen: set[tuple[str, str]] = set()
    for provider in snapshot.get("backport_providers", []) or []:
        if not isinstance(provider, dict):
            continue
        label = str(provider.get("label") or "backport provider")
        for row in provider.get("blocks", []) or []:
            if not isinstance(row, dict):
                continue
            for target in _target_from_row(row, label):
                key = (target.source_name, target.target_name)
                if key not in seen:
                    seen.add(key)
                    provider_targets.append(target)

    return MappingProfile(
        allow_safe_mod_replacements=bool(allow_safe_mod_replacements),
        catalog_bound=True,
        enabled_catalogs=labels,
        enabled_mod_ids=mod_ids,
        registry_hints=hints,
        candidate_count=int(snapshot.get("candidate_count") or 0),
        backport_targets=tuple(provider_targets),
    )
