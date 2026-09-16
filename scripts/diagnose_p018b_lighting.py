#!/usr/bin/env python3
"""Inspect a converted 1.7.10 world for P018b lighting/storage invariants.

The diagnostic is read-only. It accepts a converted world directory, region
directory, .mca file, or ZIP and reports whether legacy chunks contain phantom
all-air ExtendedBlockStorage sections, malformed light arrays, or population
flags that would prevent the target runtime from reconciling lighting.
"""
from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from wgmap_backporter_studio.core import legacy1710_engine as engine


def _section_all_air(section: dict) -> bool:
    blocks = section.get("Blocks")
    add = section.get("Add")
    if not isinstance(blocks, (bytes, bytearray)) or len(blocks) != 4096:
        return False
    if any(blocks):
        return False
    if isinstance(add, (bytes, bytearray)) and any(add):
        return False
    return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Audit P018b legacy lighting/storage output")
    ap.add_argument("--converted", required=True, help="converted world/region folder, .mca, or ZIP")
    args = ap.parse_args(argv)

    chunks = sections = all_air = bad_light = light_populated = terrain_unpopulated = 0
    max_section = -1
    with tempfile.TemporaryDirectory(prefix="wg_p018b_diag_") as td:
        region_dir = engine.discover_source_regions(Path(args.converted), Path(td))
        for region in sorted(region_dir.glob("r.*.*.mca")):
            if region.stat().st_size < 8192:
                continue
            for _, raw in engine.RegionReader(region).chunks():
                chunks += 1
                _, root = engine.parse_nbt(raw)
                level = root.get("Level") or {}
                if int(level.get("LightPopulated", 0)) != 0:
                    light_populated += 1
                if int(level.get("TerrainPopulated", 0)) != 1:
                    terrain_unpopulated += 1
                for section in level.get("Sections") or []:
                    sections += 1
                    max_section = max(max_section, int(section.get("Y", -1)))
                    if _section_all_air(section):
                        all_air += 1
                    if len(section.get("SkyLight") or b"") != 2048 or len(section.get("BlockLight") or b"") != 2048:
                        bad_light += 1

    print(f"chunks={chunks}")
    print(f"legacy_sections={sections}")
    print(f"phantom_all_air_sections={all_air}")
    print(f"malformed_light_sections={bad_light}")
    print(f"light_populated_chunks={light_populated}")
    print(f"terrain_unpopulated_chunks={terrain_unpopulated}")
    print(f"highest_serialized_section={max_section}")

    if all_air or bad_light or light_populated or terrain_unpopulated:
        return 1
    print("P018b lighting/storage audit: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
