#!/usr/bin/env python3
"""Trace modern ladder facings to generated 1.7.10 ladder metadata.

Example:
  PYTHONPATH=src python3 scripts/diagnose_p018_ladders.py \
    --source 'Deep Sea oil rig Build.zip' --converted 'Oil Rig.zip' --y-offset 64

The script is read-only. It accepts world folders, region folders, .mca files, or
ZIPs containing region/*.mca. Vanilla 1.7.10 ladder block ID 65 is used for the
target identity check; Forge keeps vanilla numeric block IDs stable in 1.7.10.
"""
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

import numpy as np

from wgmap_backporter_studio.core import legacy1710_engine as engine


def _legacy_lookup(raw: bytes, x: int, y: int, z: int):
    _, root = engine.parse_nbt(raw)
    level = root.get("Level") if isinstance(root, dict) else None
    if not isinstance(level, dict):
        return None, None
    sy = y >> 4
    lx, ly, lz = x & 15, y & 15, z & 15
    flat = ly * 256 + lz * 16 + lx
    for section in level.get("Sections") or []:
        if section.get("Y") != sy:
            continue
        low = section.get("Blocks") or b""
        if len(low) != 4096:
            return None, None
        block_id = low[flat]
        add = section.get("Add")
        if isinstance(add, (bytes, bytearray)) and len(add) == 2048:
            block_id |= int(engine.unpack_nibbles(add)[flat]) << 8
        data = section.get("Data") or b""
        if not isinstance(data, (bytes, bytearray)) or len(data) != 2048:
            return block_id, None
        return block_id, int(engine.unpack_nibbles(data)[flat])
    return 0, 0


def _target_chunks(region_path: Path):
    return {idx: raw for idx, raw in engine.RegionReader(region_path).chunks()}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", required=True, help="Modern source world/ZIP/region")
    ap.add_argument("--converted", required=True, help="Converted 1.7.10 world/ZIP/region")
    ap.add_argument("--y-offset", type=int, default=64)
    ap.add_argument("--limit", type=int, default=200, help="Maximum ladder records to print (0 = unlimited)")
    ap.add_argument("--json", action="store_true", help="Emit JSON instead of text")
    ns = ap.parse_args(argv)

    if ns.y_offset % 16:
        raise SystemExit("--y-offset must be a multiple of 16 for this converter")

    rows = []
    mismatches = 0
    with tempfile.TemporaryDirectory(prefix="wgmap_p018_ladder_diag_") as td:
        td = Path(td)
        src_dir = engine.discover_source_regions(Path(ns.source), td / "source")
        dst_dir = engine.discover_source_regions(Path(ns.converted), td / "converted")

        for src_region in sorted(src_dir.glob("r.*.*.mca")):
            dst_region = dst_dir / src_region.name
            if not dst_region.is_file():
                continue
            target = _target_chunks(dst_region)
            for idx, raw in engine.RegionReader(src_region).chunks():
                target_raw = target.get(idx)
                if target_raw is None:
                    continue
                chunk = engine.parse_modern_chunk(raw)
                cx, cz = chunk.get("xPos"), chunk.get("zPos")
                if cx is None or cz is None:
                    continue
                for section in chunk.get("sections") or []:
                    sy = section.get("Y")
                    palette = section.get("palette") or []
                    if sy is None or not palette:
                        continue
                    ladder_indexes = {
                        i: props for i, (name, props) in enumerate(palette)
                        if str(name).lower() == "minecraft:ladder"
                    }
                    if not ladder_indexes:
                        continue
                    inds = engine.unpack_palette_indices(section.get("data"), len(palette), 4096, 4)
                    for palette_index, props in ladder_indexes.items():
                        for flat in np.flatnonzero(inds == palette_index).tolist():
                            ly = flat // 256
                            rem = flat % 256
                            lz = rem // 16
                            lx = rem % 16
                            source_y = sy * 16 + ly
                            target_y = source_y + ns.y_offset
                            if not (0 <= target_y <= 255):
                                continue
                            x, z = cx * 16 + lx, cz * 16 + lz
                            expected = engine.ladder_meta(props)
                            block_id, meta = _legacy_lookup(target_raw, x, target_y, z)
                            ok = block_id == 65 and meta == expected
                            mismatches += 0 if ok else 1
                            rows.append({
                                "source_coordinate": [x, source_y, z],
                                "source_state": "minecraft:ladder[facing=%s]" % props.get("facing", "north"),
                                "source_facing": props.get("facing", "north"),
                                "target_coordinate": [x, target_y, z],
                                "generated_target_block_id": block_id,
                                "generated_target_meta": meta,
                                "expected_1_7_10_block_id": 65,
                                "expected_1_7_10_meta": expected,
                                "match": ok,
                            })
                            if ns.limit and len(rows) >= ns.limit:
                                break
                        if ns.limit and len(rows) >= ns.limit:
                            break
                    if ns.limit and len(rows) >= ns.limit:
                        break
                if ns.limit and len(rows) >= ns.limit:
                    break
            if ns.limit and len(rows) >= ns.limit:
                break

    if ns.json:
        print(json.dumps({"ladders": rows, "records": len(rows), "mismatches": mismatches}, indent=2))
    else:
        for row in rows:
            print(
                "%s %s -> %s block=%s meta=%s expected_meta=%s %s"
                % (
                    tuple(row["source_coordinate"]), row["source_state"],
                    tuple(row["target_coordinate"]), row["generated_target_block_id"],
                    row["generated_target_meta"], row["expected_1_7_10_meta"],
                    "OK" if row["match"] else "MISMATCH",
                )
            )
        print("records=%d mismatches=%d" % (len(rows), mismatches))
    return 1 if mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())
