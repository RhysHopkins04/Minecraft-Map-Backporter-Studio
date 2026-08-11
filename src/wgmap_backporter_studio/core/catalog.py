from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


CATALOG_SCHEMA = 1


@dataclass
class BlockAsset:
    namespace: str
    registry_hint: str
    display_name: str = ""
    confidence: str = "low"
    evidence: str = ""
    texture_paths: list[str] = field(default_factory=list)
    model_paths: list[str] = field(default_factory=list)
    blockstate_path: str = ""
    source_mod: str = ""
    source_file: str = ""
    candidate_kind: str = "block asset candidate"
    localization_locale: str = ""
    mapping_aliases: list[str] = field(default_factory=list)
    model_kind: str = ""


@dataclass
class BlockEntityAsset:
    namespace: str
    class_name: str
    registry_hint: str = ""
    display_name: str = ""
    confidence: str = "medium"
    evidence: str = "packaged BlockEntity/TileEntity subclass"
    texture_paths: list[str] = field(default_factory=list)
    model_paths: list[str] = field(default_factory=list)
    source_mod: str = ""
    source_file: str = ""
    candidate_kind: str = "block entity class"


@dataclass
class ModCatalog:
    source: str
    loader_hint: str = "unknown"
    minecraft_hint: str = ""
    mod_ids: list[str] = field(default_factory=list)
    mod_name: str = ""
    mod_version: str = ""
    blocks: list[BlockAsset] = field(default_factory=list)
    block_entities: list[BlockEntityAsset] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    raw_metadata: dict[str, Any] = field(default_factory=dict)
    analysis_stats: dict[str, Any] = field(default_factory=dict)
    provider_role: str = "general"
    provider_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": CATALOG_SCHEMA,
            "kind": "mod_block_catalog",
            "source": self.source,
            "loader_hint": self.loader_hint,
            "minecraft_hint": self.minecraft_hint,
            "mod_ids": self.mod_ids,
            "mod_name": self.mod_name,
            "mod_version": self.mod_version,
            "provider_role": self.provider_role,
            "provider_reason": self.provider_reason,
            "notes": self.notes,
            "raw_metadata": self.raw_metadata,
            "analysis_stats": self.analysis_stats,
            "blocks": [asdict(b) for b in self.blocks],
            "block_entities": [asdict(b) for b in self.block_entities],
        }

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")


def load_catalog(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))
