from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class TargetVersion:
    version: str
    label: str
    status: str
    backend: str | None
    notes: str
    recommended_y_offset: int | None = None
    recommended_strip_below_y: int | None = None

TARGETS = [
    TargetVersion("1.7.10", "Minecraft 1.7.10 / Forge", "Ready", "legacy1710",
                  "Legacy numeric block IDs + metadata. Uses the target world's FML registry and reviewed catalog-gated architectural mappings.",
                  recommended_y_offset=0, recommended_strip_below_y=0),
    TargetVersion("1.8.9", "Minecraft 1.8.9 / Forge", "Planned", None,
                  "Target adapter scaffolded; no writer is enabled yet."),
    TargetVersion("1.10.2", "Minecraft 1.10.2 / Forge", "Planned", None,
                  "Target adapter scaffolded; no writer is enabled yet."),
    TargetVersion("1.12.2", "Minecraft 1.12.2 / Forge", "Planned", None,
                  "Pre-flattening target support is planned; conversion is intentionally disabled until mapping/serialization validation exists."),
    TargetVersion("1.14.4", "Minecraft 1.14.4 / Forge", "Planned", None,
                  "Flattened registry/palette target adapter planned."),
    TargetVersion("1.15.2", "Minecraft 1.15.2 / Forge", "Planned", None,
                  "Flattened registry/palette target adapter planned."),
    TargetVersion("1.16.5", "Minecraft 1.16.5 / Forge", "Planned", None,
                  "Flattened registry/palette target adapter planned."),
]

TARGET_BY_VERSION = {t.version: t for t in TARGETS}
