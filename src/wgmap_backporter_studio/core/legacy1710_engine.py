#!/usr/bin/env python3
"""
WG Modern -> Minecraft 1.7.10 Surface/Map Backporter

Designed for modern Java Anvil chunks (tested against DataVersion 3465 / 1.20.1-era
chunks) and Forge 1.7.10 target worlds. It clones a target/template 1.7.10 world,
uses that world's FML registry to resolve numeric block IDs (including HBM), then
writes legacy 1.7.10 Anvil chunks.

No NBT/Anvil package is required. NumPy is strongly recommended and required by
this build for practical map-scale conversion.
"""
from __future__ import annotations

import argparse
import collections
import dataclasses
import gzip
import hashlib
import json
import math
import os
import re
import shutil
import struct
import sys
import tempfile
import threading
import time
import traceback
import zipfile
import zlib
from pathlib import Path

from .mapping_profiles import MappingProfile, profile_from_catalog_snapshot

try:
    import numpy as np
except Exception:
    np = None

TOOL_VERSION = "0.2.2"
AIR_NAMES = {"minecraft:air", "minecraft:cave_air", "minecraft:void_air"}

# Patch 013 keeps conversion correctness conservative: terrain/block states are
# converted, while modern entities and block entities are audited and reported
# instead of being silently discarded. Legacy output chunks are emitted with
# LightPopulated=0 so the target 1.7.10 runtime can perform its own relight pass.
CONTENT_POLICY = "terrain_blocks_with_loss_manifest_and_efr_state_tile_entities"
LIGHTING_STRATEGY = "target_runtime_relight"
HEIGHTMAP_STRATEGY = "bootstrap_highest_non_air"
BLOCK_PROPERTY_STRATEGY = "source_properties_to_legacy_metadata_plus_efr_state_tile_entities_plus_runtime_neighbors"

# Minecraft dye/block metadata ordering in legacy 1.7.10.
COLOR_META = {
    "white": 0, "orange": 1, "magenta": 2, "light_blue": 3,
    "yellow": 4, "lime": 5, "pink": 6, "gray": 7,
    "light_gray": 8, "cyan": 9, "purple": 10, "blue": 11,
    "brown": 12, "green": 13, "red": 14, "black": 15,
}

WOOD_META = {"oak":0, "spruce":1, "birch":2, "jungle":3, "acacia":4, "dark_oak":5}
FLOWER_META = {
    "poppy": 0, "blue_orchid": 1, "allium": 2, "azure_bluet": 3,
    "red_tulip": 4, "orange_tulip": 5, "white_tulip": 6,
    "pink_tulip": 7, "oxeye_daisy": 8,
}
DOUBLE_PLANT_META = {"sunflower":0, "lilac":1, "tall_grass":2, "large_fern":3, "rose_bush":4, "peony":5}

# Et Futurum Requiem is a special first-class provider for the 1.7.10 writer.
# Unlike ordinary catalog providers, its presence can be proven directly from the
# selected template world's Forge registry. This means conversion does not depend
# on packaged textures/models (important for EFR Plus' launch-time Mojang asset
# downloader) or on the user manually adding an analyzer catalog first.
ET_FUTURUM_NAMESPACE = "etfuturum"

# Existing EFR blocks sometimes pack several modern vanilla identities into one
# legacy block ID + metadata value. These aliases are stable upstream registry
# conventions; the target registry is still authoritative and every candidate is
# rejected if that concrete EFR registry block is not actually present.
ET_FUTURUM_SIMPLE_SUBTYPES = {
    "granite": ("stone", 1),
    "polished_granite": ("stone", 2),
    "diorite": ("stone", 3),
    "polished_diorite": ("stone", 4),
    "andesite": ("stone", 5),
    "polished_andesite": ("stone", 6),
    "prismarine": ("prismarine_block", 0),
    "prismarine_bricks": ("prismarine_block", 1),
    "dark_prismarine": ("prismarine_block", 2),
    "cracked_deepslate_bricks": ("deepslate_bricks", 1),
    "deepslate_tiles": ("deepslate_bricks", 2),
    "cracked_deepslate_tiles": ("deepslate_bricks", 3),
    "chiseled_deepslate": ("deepslate_bricks", 4),
    "polished_tuff": ("tuff", 1),
    "tuff_bricks": ("tuff", 2),
    "chiseled_tuff": ("tuff", 3),
    "chiseled_tuff_bricks": ("tuff", 4),
    "red_nether_bricks": ("red_netherbrick", 0),
    "cracked_nether_bricks": ("red_netherbrick", 1),
    "chiseled_nether_bricks": ("red_netherbrick", 2),
    "end_stone_bricks": ("end_bricks", 0),
    "dirt_path": ("grass_path", 0),
    "bone_block": ("bone", 0),
    "slime_block": ("slime", 0),
    "crimson_nylium": ("nylium", 0),
    "warped_nylium": ("nylium", 1),
    "nether_wart_block": ("nether_wart", 0),
    "warped_wart_block": ("nether_wart", 1),
}

ET_FUTURUM_WOOD_META = {
    "crimson": 0, "warped": 1, "mangrove": 2, "cherry": 3, "bamboo": 4,
}

ET_FUTURUM_COPPER_BLOCK_META = {
    "copper_block": 0, "exposed_copper": 1, "weathered_copper": 2, "oxidized_copper": 3,
    "cut_copper": 4, "exposed_cut_copper": 5, "weathered_cut_copper": 6, "oxidized_cut_copper": 7,
    "waxed_copper_block": 8, "waxed_exposed_copper": 9, "waxed_weathered_copper": 10, "waxed_oxidized_copper": 11,
    "waxed_cut_copper": 12, "waxed_exposed_cut_copper": 13, "waxed_weathered_cut_copper": 14, "waxed_oxidized_cut_copper": 15,
}
ET_FUTURUM_CHISELED_COPPER_META = {
    "chiseled_copper": 0, "exposed_chiseled_copper": 1, "weathered_chiseled_copper": 2, "oxidized_chiseled_copper": 3,
    "waxed_chiseled_copper": 4, "waxed_exposed_chiseled_copper": 5, "waxed_weathered_chiseled_copper": 6, "waxed_oxidized_chiseled_copper": 7,
}
ET_FUTURUM_COPPER_GRATE_META = {
    "copper_grate": 0, "exposed_copper_grate": 1, "weathered_copper_grate": 2, "oxidized_copper_grate": 3,
    "waxed_copper_grate": 4, "waxed_exposed_copper_grate": 5, "waxed_weathered_copper_grate": 6, "waxed_oxidized_copper_grate": 7,
}
ET_FUTURUM_COPPER_BULB_BASE_META = {
    "copper_bulb": 0, "exposed_copper_bulb": 1, "weathered_copper_bulb": 2, "oxidized_copper_bulb": 3,
    "waxed_copper_bulb": 8, "waxed_exposed_copper_bulb": 9, "waxed_weathered_copper_bulb": 10, "waxed_oxidized_copper_bulb": 11,
}
ET_FUTURUM_CUT_COPPER_SLAB_META = {
    "cut_copper_slab": 0, "exposed_cut_copper_slab": 1, "weathered_cut_copper_slab": 2, "oxidized_cut_copper_slab": 3,
    "waxed_cut_copper_slab": 4, "waxed_exposed_cut_copper_slab": 5, "waxed_weathered_cut_copper_slab": 6, "waxed_oxidized_cut_copper_slab": 7,
}

# EFR 1.7.10 registry identities that intentionally do not mirror the modern
# vanilla block path one-for-one. These are not visual fallbacks: they are the
# actual compatibility contracts exposed by the attached/current EFR source.
ET_FUTURUM_STONE_SLAB_2_META = {
    "granite_slab": 0,
    "polished_granite_slab": 1,
    "diorite_slab": 2,
    "polished_diorite_slab": 3,
    "andesite_slab": 4,
    "polished_andesite_slab": 5,
}

ET_FUTURUM_STONE_WALL_2_META = {
    "granite_wall": 0,
    "diorite_wall": 1,
    "andesite_wall": 2,
}

ET_FUTURUM_STONE_SLAB_META = {
    "stone_slab": 0,
    "mossy_cobblestone_slab": 1,
    "mossy_stone_brick_slab": 2,
    "cut_sandstone_slab": 3,
}

ET_FUTURUM_STONE_WALL_META = {
    "stone_brick_wall": 0,
    "mossy_stone_brick_wall": 1,
    "sandstone_wall": 2,
    "brick_wall": 3,
}

ET_FUTURUM_RAW_ORE_META = {
    "raw_copper_block": 0,
    "raw_iron_block": 1,
    "raw_gold_block": 2,
}

ET_FUTURUM_BLACKSTONE_META = {
    "blackstone": 0,
    "polished_blackstone": 1,
    "polished_blackstone_bricks": 2,
    "cracked_polished_blackstone_bricks": 3,
    "chiseled_polished_blackstone": 4,
}

ET_FUTURUM_BLACKSTONE_SLAB_META = {
    "blackstone_slab": 0,
    "polished_blackstone_slab": 1,
    "polished_blackstone_brick_slab": 2,
}

ET_FUTURUM_BLACKSTONE_WALL_META = {
    "blackstone_wall": 0,
    "polished_blackstone_wall": 1,
    "polished_blackstone_brick_wall": 2,
}

ET_FUTURUM_NETHER_ROOT_META = {"crimson_roots": 0, "warped_roots": 1}
ET_FUTURUM_NETHER_FUNGUS_META = {"crimson_fungus": 0, "warped_fungus": 1}

# Modern 1.13+ names for the five pre-1.13 wood families are reversed in the
# EFR registry because the original backport predates the flattened naming
# scheme (for example spruce_door -> door_spruce).
ET_FUTURUM_LEGACY_WOOD_PATHS = {
    "fence": "fence_{wood}",
    "fence_gate": "fence_gate_{wood}",
    "door": "door_{wood}",
    "trapdoor": "trapdoor_{wood}",
    "button": "button_{wood}",
    "pressure_plate": "pressure_plate_{wood}",
    "sign": "sign_{wood}",
    "wall_sign": "wall_sign_{wood}",
}

# These modern names collide with a real 1.7.10 vanilla registry name whose
# *meaning* changed after flattening. They therefore must be allowed to select
# the EFR compatibility block even though a same-named legacy vanilla block is
# present in the target registry.
ET_FUTURUM_VANILLA_COLLISION_OVERRIDES = {
    "stone_stairs",
    "stone_slab",
}

# Modern potted-block identities -> the 1.7.10/Forge item identity and item
# damage stored by TileEntityFlowerPot. EFR's potable plants use their real
# registered ItemBlock IDs from the selected target world rather than guessed
# numeric IDs.
FLOWER_POT_CONTENTS = {
    "potted_dandelion": ("minecraft:yellow_flower", 0),
    "potted_poppy": ("minecraft:red_flower", 0),
    "potted_blue_orchid": ("minecraft:red_flower", 1),
    "potted_allium": ("minecraft:red_flower", 2),
    "potted_azure_bluet": ("minecraft:red_flower", 3),
    "potted_red_tulip": ("minecraft:red_flower", 4),
    "potted_orange_tulip": ("minecraft:red_flower", 5),
    "potted_white_tulip": ("minecraft:red_flower", 6),
    "potted_pink_tulip": ("minecraft:red_flower", 7),
    "potted_oxeye_daisy": ("minecraft:red_flower", 8),
    "potted_oak_sapling": ("minecraft:sapling", 0),
    "potted_spruce_sapling": ("minecraft:sapling", 1),
    "potted_birch_sapling": ("minecraft:sapling", 2),
    "potted_jungle_sapling": ("minecraft:sapling", 3),
    "potted_acacia_sapling": ("minecraft:sapling", 4),
    "potted_dark_oak_sapling": ("minecraft:sapling", 5),
    "potted_red_mushroom": ("minecraft:red_mushroom", 0),
    "potted_brown_mushroom": ("minecraft:brown_mushroom", 0),
    "potted_dead_bush": ("minecraft:deadbush", 0),
    "potted_fern": ("minecraft:tallgrass", 2),
    "potted_cactus": ("minecraft:cactus", 0),
    "potted_cornflower": ("etfuturum:cornflower", 0),
    "potted_lily_of_the_valley": ("etfuturum:lily_of_the_valley", 0),
    "potted_wither_rose": ("etfuturum:wither_rose", 0),
    "potted_crimson_roots": ("etfuturum:nether_roots", 0),
    "potted_warped_roots": ("etfuturum:nether_roots", 1),
    "potted_crimson_fungus": ("etfuturum:nether_fungus", 0),
    "potted_warped_fungus": ("etfuturum:nether_fungus", 1),
    # Parity-shell plants are still representable in the vanilla flower-pot TE
    # when their ItemBlock exists, even if they do not implement EFR's placement
    # helper interface. Marking quality is handled conservatively below.
    "potted_torchflower": ("etfuturum:torchflower", 0),
}

# Vanilla 1.7.10 biome IDs. Modern biomes are mapped to the nearest old biome.
BIOME_ID = {
    "ocean":0, "plains":1, "desert":2, "extreme_hills":3, "forest":4,
    "taiga":5, "swampland":6, "river":7, "hell":8, "sky":9,
    "frozen_ocean":10, "frozen_river":11, "ice_plains":12, "ice_mountains":13,
    "mushroom_island":14, "mushroom_island_shore":15, "beach":16,
    "desert_hills":17, "forest_hills":18, "taiga_hills":19,
    "extreme_hills_edge":20, "jungle":21, "jungle_hills":22, "jungle_edge":23,
    "deep_ocean":24, "stone_beach":25, "cold_beach":26, "birch_forest":27,
    "birch_forest_hills":28, "roofed_forest":29, "cold_taiga":30,
    "cold_taiga_hills":31, "mega_taiga":32, "mega_taiga_hills":33,
    "extreme_hills_plus":34, "savanna":35, "savanna_plateau":36,
    "mesa":37, "mesa_plateau_f":38, "mesa_plateau":39,
}

MODERN_BIOME_TO_1710 = {
    "minecraft:plains":1, "minecraft:sunflower_plains":1, "minecraft:meadow":1,
    "minecraft:desert":2, "minecraft:badlands":37, "minecraft:eroded_badlands":37,
    "minecraft:wooded_badlands":38,
    "minecraft:windswept_hills":3, "minecraft:windswept_gravelly_hills":3,
    "minecraft:windswept_forest":34, "minecraft:forest":4, "minecraft:flower_forest":4,
    "minecraft:dark_forest":29, "minecraft:birch_forest":27, "minecraft:old_growth_birch_forest":28,
    "minecraft:taiga":5, "minecraft:old_growth_pine_taiga":32, "minecraft:old_growth_spruce_taiga":32,
    "minecraft:snowy_taiga":30, "minecraft:snowy_plains":12, "minecraft:ice_spikes":12,
    "minecraft:jungle":21, "minecraft:sparse_jungle":23, "minecraft:bamboo_jungle":21,
    "minecraft:savanna":35, "minecraft:savanna_plateau":36, "minecraft:windswept_savanna":36,
    "minecraft:swamp":6, "minecraft:mangrove_swamp":6,
    "minecraft:river":7, "minecraft:frozen_river":11,
    "minecraft:beach":16, "minecraft:snowy_beach":26, "minecraft:stony_shore":25,
    "minecraft:ocean":0, "minecraft:lukewarm_ocean":0, "minecraft:warm_ocean":0,
    "minecraft:cold_ocean":0, "minecraft:frozen_ocean":10,
    "minecraft:deep_ocean":24, "minecraft:deep_lukewarm_ocean":24,
    "minecraft:deep_cold_ocean":24, "minecraft:deep_frozen_ocean":24,
    "minecraft:mushroom_fields":14,
    "minecraft:dripstone_caves":3, "minecraft:lush_caves":4, "minecraft:deep_dark":3,
    "minecraft:nether_wastes":8, "minecraft:soul_sand_valley":8,
    "minecraft:crimson_forest":8, "minecraft:warped_forest":8, "minecraft:basalt_deltas":8,
    "minecraft:the_end":9, "minecraft:end_highlands":9, "minecraft:end_midlands":9,
    "minecraft:small_end_islands":9, "minecraft:end_barrens":9,
}

# HBM blocks chosen specifically from the attached source as architectural/decorative
# substitutions. We deliberately avoid machines, ores, and expensive resource blocks.
HBM_SAFE_ARCHITECTURAL = {
    "concrete", "concrete_smooth", "concrete_colored", "brick_concrete",
    "brick_concrete_cracked", "brick_concrete_mossy", "brick_concrete_broken",
    "concrete_stairs", "concrete_smooth_stairs", "brick_concrete_stairs",
    "brick_concrete_cracked_stairs", "brick_concrete_mossy_stairs",
    "asphalt", "asphalt_stairs", "basalt", "basalt_polished", "basalt_smooth",
    "basalt_brick", "basalt_tiles", "stone_gneiss", "gneiss_brick", "gneiss_tile",
    "brick_light", "brick_light_stairs", "brick_red", "brick_obsidian",
    "brick_obsidian_stairs", "reinforced_stone", "reinforced_stone_stairs",
    "reinforced_glass", "reinforced_glass_pane", "steel_wall", "steel_roof",
    "steel_beam", "steel_grate", "steel_grate_wide", "steel_scaffold",
    "steel_poles", "fence_metal", "door_metal", "trapdoor_steel", "lantern",
    "lightstone", "reinforced_lamp_on", "reinforced_light", "mud_block",
    "frozen_planks", "frozen_log", "pvc_planks", "pvc_log", "vinyl_planks",
    "vinyl_log", "waste_planks", "waste_log", "pink_planks", "pink_log",
    "pink_stairs", "pink_slab", "vinyl_tile",
}


class ConversionError(RuntimeError):
    pass


class Reader:
    __slots__ = ("b", "i", "n")
    def __init__(self, b: bytes):
        self.b = b; self.i = 0; self.n = len(b)
    def read(self, n):
        j = self.i+n
        if j > self.n: raise EOFError("Unexpected end of NBT")
        v = self.b[self.i:j]; self.i=j; return v
    def u8(self):
        if self.i >= self.n: raise EOFError
        v=self.b[self.i]; self.i+=1; return v
    def i8(self):
        v=self.u8(); return v-256 if v >= 128 else v
    def u16(self): v=struct.unpack_from(">H",self.b,self.i)[0]; self.i+=2; return v
    def i16(self): v=struct.unpack_from(">h",self.b,self.i)[0]; self.i+=2; return v
    def i32(self): v=struct.unpack_from(">i",self.b,self.i)[0]; self.i+=4; return v
    def i64(self): v=struct.unpack_from(">q",self.b,self.i)[0]; self.i+=8; return v
    def f32(self): v=struct.unpack_from(">f",self.b,self.i)[0]; self.i+=4; return v
    def f64(self): v=struct.unpack_from(">d",self.b,self.i)[0]; self.i+=8; return v
    def string(self):
        n=self.u16(); return self.read(n).decode("utf-8", "replace")


def read_payload(r: Reader, t: int):
    if t == 1: return r.i8()
    if t == 2: return r.i16()
    if t == 3: return r.i32()
    if t == 4: return r.i64()
    if t == 5: return r.f32()
    if t == 6: return r.f64()
    if t == 7:
        n=r.i32(); return r.read(n)
    if t == 8: return r.string()
    if t == 9:
        et=r.u8(); n=r.i32(); return [read_payload(r, et) for _ in range(n)]
    if t == 10:
        out={}
        while True:
            et=r.u8()
            if et == 0: break
            name=r.string(); out[name]=read_payload(r, et)
        return out
    if t == 11:
        n=r.i32(); return [r.i32() for _ in range(n)]
    if t == 12:
        n=r.i32(); return [r.i64() for _ in range(n)]
    raise ConversionError("Unknown NBT tag %s" % t)


def parse_nbt(b: bytes):
    r=Reader(b); t=r.u8(); name=r.string(); return name, read_payload(r,t)


def skip_payload(r: Reader, t: int):
    if t == 0: return
    if t == 1: r.i += 1
    elif t == 2: r.i += 2
    elif t in (3,5): r.i += 4
    elif t in (4,6): r.i += 8
    elif t == 7:
        n=r.i32(); r.i += n
    elif t == 8:
        n=r.u16(); r.i += n
    elif t == 9:
        et=r.u8(); n=r.i32()
        for _ in range(n): skip_payload(r, et)
    elif t == 10:
        while True:
            et=r.u8()
            if et == 0: break
            n=r.u16(); r.i += n
            skip_payload(r, et)
    elif t == 11:
        n=r.i32(); r.i += 4*n
    elif t == 12:
        n=r.i32(); r.i += 8*n
    else: raise ConversionError("Bad NBT tag %r" % t)


def parse_properties(r: Reader):
    d={}
    while True:
        t=r.u8()
        if t == 0: break
        k=r.string()
        if t == 8: d[k]=r.string()
        else: skip_payload(r,t)
    return d


def parse_block_palette_entry(r: Reader):
    name="minecraft:air"; props={}
    while True:
        t=r.u8()
        if t == 0: break
        k=r.string()
        if k == "Name" and t == 8: name=r.string()
        elif k == "Properties" and t == 10: props=parse_properties(r)
        else: skip_payload(r,t)
    return (name,props)


def parse_block_states(r: Reader):
    palette=[]; data=None
    while True:
        t=r.u8()
        if t == 0: break
        k=r.string()
        if k == "palette" and t == 9:
            et=r.u8(); n=r.i32()
            if et != 10:
                for _ in range(n): skip_payload(r,et)
            else:
                palette=[parse_block_palette_entry(r) for _ in range(n)]
        elif k == "data" and t == 12:
            n=r.i32(); data=[r.i64() for _ in range(n)]
        else: skip_payload(r,t)
    return palette,data


def parse_biomes(r: Reader):
    palette=[]; data=None
    while True:
        t=r.u8()
        if t == 0: break
        k=r.string()
        if k == "palette" and t == 9:
            et=r.u8(); n=r.i32()
            if et == 8: palette=[r.string() for _ in range(n)]
            else:
                for _ in range(n): skip_payload(r,et)
        elif k == "data" and t == 12:
            n=r.i32(); data=[r.i64() for _ in range(n)]
        else: skip_payload(r,t)
    return palette,data


def parse_section(r: Reader):
    y=None; bp=[]; bd=None; biop=[]; biod=None
    while True:
        t=r.u8()
        if t == 0: break
        k=r.string()
        if k == "Y" and t == 1: y=r.i8()
        elif k == "block_states" and t == 10: bp,bd=parse_block_states(r)
        elif k == "biomes" and t == 10: biop,biod=parse_biomes(r)
        else: skip_payload(r,t)
    return {"Y":y, "palette":bp, "data":bd, "biome_palette":biop, "biome_data":biod}


def parse_modern_chunk(raw: bytes):
    r=Reader(raw); rt=r.u8(); r.string()
    if rt != 10: raise ConversionError("Modern chunk root is not a compound")
    out={
        "xPos":None,"zPos":None,"LastUpdate":0,"DataVersion":None,
        "sections":[],"block_entities":[],
    }
    while True:
        t=r.u8()
        if t == 0: break
        k=r.string()
        if k == "xPos" and t == 3: out["xPos"]=r.i32()
        elif k == "zPos" and t == 3: out["zPos"]=r.i32()
        elif k == "LastUpdate" and t == 4: out["LastUpdate"]=r.i64()
        elif k == "DataVersion" and t == 3: out["DataVersion"]=r.i32()
        elif k == "sections" and t == 9:
            et=r.u8(); n=r.i32()
            if et == 10: out["sections"]=[parse_section(r) for _ in range(n)]
            else:
                for _ in range(n): skip_payload(r,et)
        elif k == "block_entities" and t == 9:
            et=r.u8(); n=r.i32()
            if et == 10:
                out["block_entities"]=[read_payload(r,10) for _ in range(n)]
            else:
                for _ in range(n): skip_payload(r,et)
        else: skip_payload(r,t)
    return out


def unpack_palette_indices(data, palette_size, count, min_bits):
    if np is None: raise ConversionError("NumPy is required. Install with: python3 -m pip install numpy")
    if palette_size <= 1 or not data:
        return np.zeros(count, dtype=np.int32)
    bits=max(min_bits, (palette_size-1).bit_length())
    vpl=64//bits
    mask=(1<<bits)-1
    # Convert signed NBT longs to uint64 without overflow warnings.
    arr=np.fromiter(((v & 0xFFFFFFFFFFFFFFFF) for v in data), dtype=np.uint64, count=len(data))
    out=np.zeros(count, dtype=np.int32)
    for slot in range(vpl):
        dest=np.arange(slot, count, vpl)
        if len(dest)==0: continue
        src=np.arange(len(dest))
        valid=src < len(arr)
        if not np.any(valid): continue
        vals=((arr[src[valid]] >> np.uint64(slot*bits)) & np.uint64(mask)).astype(np.int32)
        out[dest[valid]]=vals
    # Corrupt/out-of-range palette indexes should not crash the whole map.
    out[(out < 0) | (out >= palette_size)] = 0
    return out


def pack_nibbles(vals):
    vals=np.asarray(vals,dtype=np.uint8).reshape(-1)
    if len(vals)%2: vals=np.pad(vals,(0,1))
    return bytes((vals[0::2] | (vals[1::2] << 4)).tolist())


@dataclasses.dataclass(frozen=True)
class Mapping:
    target: str
    meta: int = 0
    quality: str = "exact"  # exact, close, approximate, omitted
    note: str = ""


FML_BLOCK_DISCRIMINATOR = "\x01"
FML_ITEM_DISCRIMINATOR = "\x02"
TARGET_REGISTRY_SENTINELS = (
    "minecraft:air", "minecraft:stone", "minecraft:bedrock", "minecraft:dirt",
    "minecraft:grass", "minecraft:water", "minecraft:planks", "minecraft:cobblestone",
)


class TargetRegistry:
    def __init__(self, ids, *, source_format="unknown", raw_entries=0, ignored_items=0, aliases=None, item_ids=None):
        self.ids=dict(ids)
        self.item_ids=dict(item_ids or {})
        self.source_format=source_format
        self.raw_entries=int(raw_entries)
        self.ignored_items=int(ignored_items)
        self.aliases=dict(aliases or {})
        self._lower={k.lower():v for k,v in self.ids.items()}
        self._item_lower={k.lower():v for k,v in self.item_ids.items()}
        self._aliases_lower={str(k).lower():str(v) for k,v in self.aliases.items()}
        self._hbm_by_logical={}
        self._namespace_counts=collections.Counter()
        for k,v in self.ids.items():
            namespace=k.split(":",1)[0].lower() if ":" in k else "minecraft"
            self._namespace_counts[namespace]+=1
            if k.lower().startswith("hbm:"):
                suffix=k.split(":",1)[1]
                logical=suffix[5:] if suffix.startswith("tile.") else suffix
                self._hbm_by_logical[logical.lower()]=(k,v)

    def resolve(self, name):
        if name in self.ids: return self.ids[name]
        lowered=name.lower()
        direct=self._lower.get(lowered)
        if direct is not None: return direct
        seen=set()
        alias=self._aliases_lower.get(lowered)
        while alias and alias.lower() not in seen:
            seen.add(alias.lower())
            direct=self._lower.get(alias.lower())
            if direct is not None: return direct
            alias=self._aliases_lower.get(alias.lower())
        return None

    def resolve_item(self, name):
        if name in self.item_ids: return self.item_ids[name]
        return self._item_lower.get(str(name).lower())

    def resolve_hbm(self, logical):
        return self._hbm_by_logical.get(logical.lower())

    def has_hbm(self, logical):
        return logical.lower() in self._hbm_by_logical

    @property
    def hbm_count(self):
        return len(self._hbm_by_logical)

    def namespace_count(self, namespace):
        return int(self._namespace_counts.get(str(namespace).strip().lower(),0))

    def summary(self):
        ignored=("; %d item entries excluded from block-ID count" % self.ignored_items) if self.ignored_items else ""
        return "%d block IDs from %s (%d HBM entries%s)" % (len(self.ids),self.source_format,self.hbm_count,ignored)


def _block_aliases_from_fml(fml):
    aliases={}
    for e in fml.get("BlockAliases",[]) if isinstance(fml,dict) else []:
        if isinstance(e,dict) and "K" in e and "V" in e:
            aliases[str(e["K"])]=str(e["V"])
    return aliases


def _target_registry_from_fml(fml):
    if not isinstance(fml,dict):
        raise ConversionError("Target/template level.dat has no readable Forge FML compound")

    # Forge 1.8+ style registry snapshots use a dedicated fml:blocks registry
    # containing plain names. Keep support for those snapshots because template
    # worlds can occasionally be passed through newer tooling before use here.
    registries=fml.get("Registries",{})
    blocks=registries.get("fml:blocks",{}) if isinstance(registries,dict) else {}
    entries=blocks.get("ids",[]) if isinstance(blocks,dict) else []
    ids={}
    for e in entries:
        if not isinstance(e,dict) or "K" not in e or "V" not in e: continue
        name=str(e["K"]); value=int(e["V"])
        if name and name[0] in (FML_BLOCK_DISCRIMINATOR,FML_ITEM_DISCRIMINATOR):
            name=name[1:]
        if name: ids[name]=value
    if ids:
        return TargetRegistry(ids,source_format="FML/Registries/fml:blocks",raw_entries=len(entries),aliases=_block_aliases_from_fml(fml))

    # Forge/FML 1.7.10 stores BOTH blocks and items in FML/ItemData. The first
    # character is a type discriminator: U+0001 for blocks, U+0002 for items.
    # The previous parser kept that hidden character and mixed both registries,
    # producing thousands of unusable names such as '\x01minecraft:stone'.
    item_data=fml.get("ItemData",[])
    if isinstance(item_data,list) and item_data:
        block_ids={}; item_ids={}; ignored_items=0; malformed=0
        for e in item_data:
            if not isinstance(e,dict) or "K" not in e or "V" not in e:
                malformed+=1; continue
            raw_name=str(e["K"]); value=int(e["V"])
            if raw_name.startswith(FML_BLOCK_DISCRIMINATOR):
                name=raw_name[1:]
                if name: block_ids[name]=value
            elif raw_name.startswith(FML_ITEM_DISCRIMINATOR):
                name=raw_name[1:]
                if name: item_ids[name]=value
                ignored_items+=1
            else:
                malformed+=1
        if not block_ids:
            raise ConversionError(
                "Forge FML/ItemData exists but contains no U+0001 block entries. "
                "The template is not a usable Forge 1.7.10 registry snapshot; open/save it once in the exact target 1.7.10 modpack and try again."
            )
        reg=TargetRegistry(
            block_ids, source_format="Forge 1.7.10 FML/ItemData", raw_entries=len(item_data),
            ignored_items=ignored_items, aliases=_block_aliases_from_fml(fml), item_ids=item_ids,
        )
        reg.malformed_entries=malformed
        return reg

    if fml.get("ModItemData"):
        raise ConversionError(
            "The template world uses Forge's pre-1.7.10 ModItemData registry format. "
            "Load and save that world in Forge 1.7.10 first so FML writes the 1.7.10 ItemData map, then select the migrated template."
        )

    raise ConversionError(
        "Could not find a supported Forge block ID registry in target level.dat. "
        "Create/open the template world once in the exact Forge 1.7.10 modpack (with the intended mods/RTG), save, quit, then select it again."
    )


def load_target_registry(world: Path):
    level=world/"level.dat"
    if not level.is_file(): raise ConversionError("Target/template world has no level.dat: %s" % world)
    try:
        with gzip.open(level,"rb") as f: raw=f.read()
        _,root=parse_nbt(raw)
    except Exception as e:
        raise ConversionError("Could not read target/template level.dat: %s" % e) from e
    return _target_registry_from_fml(root.get("FML",{}))


def validate_target_registry(
    reg: TargetRegistry,
    use_hbm=True,
    log=print,
    mapping_profile: MappingProfile | None = None,
):
    missing=[name for name in TARGET_REGISTRY_SENTINELS if reg.resolve(name) is None]
    if missing:
        raise ConversionError(
            "Target registry parsed as %s but is missing required vanilla block names: %s. "
            "Refusing to create an output world because the target registry is not safe to use."
            % (reg.source_format, ", ".join(missing))
        )
    bad=[(name,value) for name,value in reg.ids.items() if not (0 <= int(value) <= 4095)]
    if bad:
        sample=", ".join("%s=%s"%x for x in bad[:5])
        raise ConversionError("Target block registry contains IDs outside the 1.7.10 block range 0..4095: %s" % sample)
    log("Target registry: %s" % reg.summary())

    etfuturum_entries=reg.namespace_count(ET_FUTURUM_NAMESPACE)
    provider_replacements_enabled=_safe_provider_replacements_enabled(use_hbm,mapping_profile)
    if etfuturum_entries:
        if provider_replacements_enabled:
            log(
                "Native backport provider: Et Futurum detected with %d registered block ID(s); "
                "registered exact/subtype matches will be tried before catalog, HBM, or vanilla fallbacks. "
                "Packaged textures/models are not required for this detection." % etfuturum_entries
            )
        else:
            log(
                "NOTICE: Et Futurum is present in the target registry (%d block ID(s)), but detected/catalog backport replacements are disabled."
                % etfuturum_entries
            )

    if mapping_profile is not None and mapping_profile.catalog_bound:
        enabled=", ".join(sorted(mapping_profile.enabled_mod_ids)) or "none"
        log(
            "Mapping profile: %d enabled catalog(s); eligible mod namespaces: %s"
            % (len(mapping_profile.enabled_catalogs), enabled)
        )
        if mapping_profile.backport_targets:
            provider_namespaces=mapping_profile.to_dict().get("backport_provider_namespaces") or []
            log(
                "Backport providers: %d exact-name target candidate(s) across %s"
                % (len(mapping_profile.backport_targets), ", ".join(provider_namespaces) or "none")
            )
            provider_counts={ns:reg.namespace_count(ns) for ns in provider_namespaces}
            available_targets={target.target_name for target in mapping_profile.backport_targets if reg.resolve(target.target_name) is not None}
            log(
                "Target registry provider coverage: %s; %d/%d catalog target name(s) are actually registered."
                % (
                    ", ".join("%s=%d"%(ns,provider_counts[ns]) for ns in provider_namespaces) or "none",
                    len(available_targets), len({target.target_name for target in mapping_profile.backport_targets}),
                )
            )
            for ns,count in provider_counts.items():
                if count == 0:
                    log("WARNING: backport provider %s is enabled in Catalog Workspace but has no registered blocks in the selected target/template world."%ns)
        if mapping_profile.allow_safe_mod_replacements and not mapping_profile.enabled_mod_ids:
            log("NOTICE: safe mod replacements are enabled, but no Catalog Workspace sources are enabled; vanilla fallbacks will be used.")
        if mapping_profile.allows_namespace("hbm") and reg.hbm_count == 0:
            log("WARNING: HBM is enabled by the active catalogs, but the target registry contains no HBM block entries; vanilla fallbacks will be used.")
    elif use_hbm and reg.hbm_count == 0:
        log("WARNING: safe mod architectural replacements are enabled, but the target registry contains no HBM block entries; vanilla fallbacks will be used.")

    provider_namespaces=[]
    provider_target_total=0
    provider_target_registered=0
    if mapping_profile is not None and mapping_profile.catalog_bound:
        provider_namespaces=mapping_profile.to_dict().get("backport_provider_namespaces") or []
        provider_targets={target.target_name for target in mapping_profile.backport_targets}
        provider_target_total=len(provider_targets)
        provider_target_registered=sum(1 for target in provider_targets if reg.resolve(target) is not None)
    return {
        "source_format":reg.source_format, "block_ids":len(reg.ids), "raw_entries":reg.raw_entries,
        "ignored_item_entries":reg.ignored_items, "hbm_entries":reg.hbm_count,
        "provider_namespace_entries":{ns:reg.namespace_count(ns) for ns in provider_namespaces},
        "backport_provider_targets_catalog":provider_target_total,
        "backport_provider_targets_registered":provider_target_registered,
        "etfuturum_entries":etfuturum_entries,
        "etfuturum_native_priority":bool(etfuturum_entries and provider_replacements_enabled),
    }


def boolprop(props,k): return str(props.get(k,"false")).lower()=="true"

def intprop(props,k,default=0,minimum=None,maximum=None):
    try:
        value=int(props.get(k,default))
    except Exception:
        value=int(default)
    if minimum is not None: value=max(int(minimum),value)
    if maximum is not None: value=min(int(maximum),value)
    return value

def stair_meta(props):
    facing={"east":0,"west":1,"south":2,"north":3}.get(props.get("facing"),0)
    if props.get("half") == "top": facing |= 4
    return facing

def slab_meta(base, props):
    return base | (8 if props.get("type") == "top" else 0)

def huge_mushroom_meta(props):
    """Modern mushroom face booleans -> 1.7.10 huge-mushroom metadata.

    Mirrors the converter used by the attached EFR source so mushroom stems and
    cap blocks retain their six-face visual state instead of becoming unrelated
    architectural material.
    """
    up=boolprop(props,"up"); down=boolprop(props,"down")
    north=boolprop(props,"north"); east=boolprop(props,"east")
    south=boolprop(props,"south"); west=boolprop(props,"west")
    if not any((up,down,north,east,south,west)):
        return 0
    if up:
        if east and north: return 3
        if east and south: return 9
        if east: return 6
        if west and north: return 1
        if west and south: return 7
        if west: return 4
        if north: return 2
        if south: return 8
        return 5
    return 14

def flower_pot_content(path, reg):
    spec=FLOWER_POT_CONTENTS.get(str(path).lower())
    if spec is None:
        return None
    item_name,data=spec
    item_id=reg.resolve_item(item_name) if hasattr(reg,"resolve_item") else None
    # Synthetic/test registries often only declare block IDs. In 1.7.10 the
    # corresponding ItemBlock normally shares the numeric ID, so use that only
    # when no item snapshot was supplied at all. Real FML/ItemData templates
    # carry item_ids and therefore never rely on this fallback.
    if item_id is None and not getattr(reg,"item_ids",{}):
        item_id=reg.resolve(item_name)
    if item_id is None:
        return None
    return int(item_id),int(data),item_name

def log_axis_bits(props, wood_block=False):
    if wood_block: return 12
    return {"y":0,"x":4,"z":8}.get(props.get("axis","y"),0)

def sign_wall_meta(props): return {"north":2,"south":3,"west":4,"east":5}.get(props.get("facing"),2)
def torch_wall_meta(props): return {"east":1,"west":2,"south":3,"north":4}.get(props.get("facing"),5)

def direction_meta(props,key="facing",default="up"):
    """ForgeDirection/vanilla side ordinal: down/up/north/south/west/east."""
    return {"down":0,"up":1,"north":2,"south":3,"west":4,"east":5}.get(
        str(props.get(key,default)).lower(),
        {"down":0,"up":1,"north":2,"south":3,"west":4,"east":5}.get(default,1),
    )

def chain_axis_meta(props):
    """EFR BlockChain metadata: Y=0, X=1, Z=2."""
    return {"y":0,"x":1,"z":2}.get(str(props.get("axis","y")).lower(),0)

def loom_meta(props):
    """EFR BlockLoom stores the front as sideOrdinal-2."""
    return {"north":0,"south":1,"west":2,"east":3}.get(str(props.get("facing","north")).lower(),0)

def beetroot_meta(props):
    """Modern beetroot age 0..3 -> EFR/1.7 BlockCrops growth metadata 0..7."""
    return (0,2,4,7)[intprop(props,"age",0,0,3)]

def segmented_ground_meta(props,amount_key):
    amount=intprop(props,amount_key,1,1,4)
    return ((amount-1)<<2) | _horizontal_quadrant(props)

def pink_petals_meta(props):
    # EFR's mature BlockPinkPetals predates the parity bridge. Low two bits are
    # amount-1; high two bits are its renderer rotation. Derive the rotation from
    # vanilla FlowerBedBlock placement (block faces back toward the placer).
    amount=intprop(props,"flower_amount",1,1,4)
    rotation={"north":0,"east":1,"south":3,"west":2}.get(str(props.get("facing","north")).lower(),0)
    return (rotation<<2) | (amount-1)

def grindstone_meta(props):
    face={"floor":0,"wall":1,"ceiling":2}.get(str(props.get("face","floor")).lower(),0)
    return face*4 + _horizontal_quadrant(props)

def _only_default_runtime_props(props, defaults=None, ignored=()):
    """Whether every source property outside represented state is a known default.

    This keeps `backport_exact` conservative. Runtime-only neighbor properties can
    be explicitly ignored by a caller; waterlogged state is never silently called
    exact unless it is false/default.
    """
    defaults={str(k):str(v).lower() for k,v in (defaults or {}).items()}
    ignored={str(x) for x in ignored}
    for key,value in (props or {}).items():
        key=str(key)
        if key in ignored or key in defaults:
            if key in defaults and str(value).lower()!=defaults[key]:
                return False
            continue
        return False
    return True
def gate_meta(props):
    m={"south":0,"west":1,"north":2,"east":3}.get(props.get("facing"),0)
    if boolprop(props,"open"): m|=4
    return m

def door_meta(props):
    if props.get("half") == "upper":
        return 8 | (1 if props.get("hinge") == "right" else 0) | (2 if boolprop(props,"powered") else 0)
    m={"east":0,"south":1,"west":2,"north":3}.get(props.get("facing"),0)
    if boolprop(props,"open"): m|=4
    return m

def trapdoor_meta(props):
    # 1.7.10 BlockTrapDoor metadata uses north=0, south=1, west=2, east=3.
    # The previous table inverted both axes, rotating/mirroring imported trapdoors.
    m={"north":0,"south":1,"west":2,"east":3}.get(props.get("facing"),0)
    if boolprop(props,"open"): m|=4
    if props.get("half") == "top": m|=8
    return m

def rail_meta(props, powered_kind=False):
    shape=props.get("shape","north_south")
    if powered_kind:
        base={"north_south":0,"east_west":1,"ascending_east":2,"ascending_west":3,"ascending_north":4,"ascending_south":5}.get(shape,0)
        if boolprop(props,"powered"): base|=8
        return base
    return {"north_south":0,"east_west":1,"ascending_east":2,"ascending_west":3,"ascending_north":4,"ascending_south":5,
            "south_east":6,"south_west":7,"north_west":8,"north_east":9}.get(shape,0)


def _provider_state_meta(path, props):
    """Best-effort legacy metadata for an exact-name backport-provider block.

    Exact provider matching is intentionally conservative. Common vanilla shape
    encodings are safe to derive from the modern state. Unknown stateful blocks
    remain meta 0 and are labelled backport-close rather than pretending the
    runtime state was translated perfectly.
    """
    p=path.lower()
    if p.endswith("_stairs"):
        return stair_meta(props), True
    if p.endswith("_slab"):
        return slab_meta(0,props), True
    if p.endswith("_door"):
        return door_meta(props), True
    if p.endswith("_trapdoor"):
        return trapdoor_meta(props), True
    if p.endswith("_fence_gate"):
        return gate_meta(props), True
    if p.endswith("_button"):
        return button_meta(props), True
    if p.endswith("_wall_sign"):
        return sign_wall_meta(props), True
    if p.endswith("_sign") or p.endswith("_hanging_sign"):
        try:
            return int(props.get("rotation","0")) & 15, True
        except Exception:
            return 0, False
    if p.endswith(("_log","_stem")):
        return log_axis_bits(props), True
    if p.endswith(("_wood","_hyphae")):
        return log_axis_bits(props,True), True
    if p.endswith("_leaves"):
        # Imported build foliage should not decay immediately in 1.7.10.
        return 4, True
    if not props:
        return 0, True
    return 0, False


def _safe_provider_replacements_enabled(use_hbm, mapping_profile):
    if mapping_profile is not None:
        return bool(mapping_profile.allow_safe_mod_replacements)
    return bool(use_hbm)


def _etfuturum_registered(reg: TargetRegistry) -> bool:
    return reg.namespace_count(ET_FUTURUM_NAMESPACE) > 0


def _etfuturum_registry_name(path: str) -> str:
    return ET_FUTURUM_NAMESPACE + ":" + path


def _horizontal_quadrant(props):
    """EFR Plus parity-model N/E/S/W metadata convention."""
    return {"north": 0, "east": 1, "south": 2, "west": 3}.get(str(props.get("facing", "north")).lower(), 0)


def _glow_lichen_state_mask(props):
    """EFR TileEntityGlowLichen six-face bitmap (ForgeDirection ordinal bits)."""
    mask=0
    for direction,bit in (("down",0),("up",1),("north",2),("south",3),("west",4),("east",5)):
        if boolprop(props,direction):
            mask |= 1 << bit
    return mask


def _etfuturum_state_meta(path, props):
    """Metadata translator for direct EFR/modern identity matches.

    EFR Plus deliberately uses modern registry paths for its parity blocks, but
    1.7.10 still has only four metadata bits. Handle the state layouts that the
    fork exposes explicitly, then fall back to the generic provider translator.
    """
    p=path.lower()
    if p.endswith("_pressure_plate"):
        return (1 if boolprop(props,"powered") else 0), _only_default_runtime_props(
            props,ignored={"powered"}
        )
    if p == "barrier":
        return 0, _only_default_runtime_props(props,{"waterlogged":"false"})
    if (p.endswith("_fence") and not p.endswith("_fence_gate")) or p.endswith("_pane") or p.endswith("_bars"):
        # EFR's 1.7 runtime/model bridge recomputes cardinal connections from
        # neighboring blocks, so the modern connection booleans need no packed
        # metadata. Keep this EFR-specific instead of assuming every provider
        # implements the same runtime contract.
        return 0, _only_default_runtime_props(
            props,{"waterlogged":"false"},ignored={"north","east","south","west"}
        )
    if p.endswith("_wall_hanging_sign"):
        return sign_wall_meta(props), True
    if p.endswith("_bed"):
        return door_bed_meta(props), True
    if p.endswith("_glazed_terracotta"):
        return _horizontal_quadrant(props), True
    if p == "campfire" or p == "soul_campfire":
        return _horizontal_quadrant(props) | (4 if boolprop(props,"lit") else 0), True
    if p == "candle" or (p.endswith("_candle") and not p.endswith("_candle_cake")):
        try:
            count=max(1,min(4,int(props.get("candles","1"))))
        except Exception:
            count=1
        return (count-1) | (4 if boolprop(props,"lit") else 0), True
    if p == "candle_cake" or p.endswith("_candle_cake"):
        return 1 if boolprop(props,"lit") else 0, True
    if p == "wildflowers":
        return segmented_ground_meta(props,"flower_amount"), _only_default_runtime_props(
            props, {"waterlogged":"false"}, ignored={"facing","flower_amount"}
        )
    if p == "leaf_litter":
        return segmented_ground_meta(props,"segment_amount"), _only_default_runtime_props(
            props, {"waterlogged":"false"}, ignored={"facing","segment_amount"}
        )
    if p == "pink_petals":
        return pink_petals_meta(props), _only_default_runtime_props(
            props,{"waterlogged":"false"},ignored={"facing","flower_amount"}
        )
    if p.endswith("_shelf"):
        # EFR Plus' parity shelf model consumes only the low two facing bits.
        # Modern powered/side-chain state has no 1.7.10 equivalent yet.
        exact=_only_default_runtime_props(
            props,
            {"powered":"false","side_chain":"unconnected","waterlogged":"false"},
            ignored={"facing"},
        )
        return _horizontal_quadrant(props), exact
    if p == "end_rod":
        return direction_meta(props), _only_default_runtime_props(props, ignored={"facing"})
    if p == "deepslate":
        return log_axis_bits(props), _only_default_runtime_props(props, ignored={"axis"})
    if p == "muddy_mangrove_roots":
        return log_axis_bits(props), _only_default_runtime_props(props, ignored={"axis"})
    if p == "sweet_berry_bush":
        return intprop(props,"age",0,0,3), _only_default_runtime_props(props, ignored={"age"})
    if p == "beetroots":
        return beetroot_meta(props), _only_default_runtime_props(props, ignored={"age"})
    if p == "composter":
        return intprop(props,"level",0,0,8), _only_default_runtime_props(props, ignored={"level"})
    if p == "light":
        return intprop(props,"level",0,0,15), _only_default_runtime_props(
            props,{"waterlogged":"false"},ignored={"level"}
        )
    if p in {"lantern","soul_lantern"}:
        return (1 if boolprop(props,"hanging") else 0), _only_default_runtime_props(
            props,{"waterlogged":"false"},ignored={"hanging"}
        )
    if p == "barrel":
        return direction_meta(props), _only_default_runtime_props(
            props,{"open":"false"},ignored={"facing"}
        )
    if p in {"beehive","bee_nest"}:
        facing=direction_meta(props,"facing","north")
        honey=intprop(props,"honey_level",0,0,5)
        meta=facing + (6 if honey == 5 else 0)
        # EFR only has an alternate metadata face for the full-honey state. Its
        # TileEntity honeyLevel is synthesized by the writer below for all 0..5.
        exact=_only_default_runtime_props(props,ignored={"facing","honey_level"})
        return meta, exact
    if p in {"blast_furnace","smoker"}:
        # Facing uses the classic furnace side ordinal. Lit identity is handled
        # by _map_etfuturum_first when a registered lit_* block exists.
        exact=_only_default_runtime_props(props,{"lit":"false"},ignored={"facing"})
        return direction_meta(props,"facing","north"), exact
    if p == "loom":
        return loom_meta(props), _only_default_runtime_props(props,ignored={"facing"})
    if p.endswith("lightning_rod"):
        return direction_meta(props), _only_default_runtime_props(
            props,{"powered":"false","waterlogged":"false"},ignored={"facing"}
        )
    if p == "chain":
        return chain_axis_meta(props), _only_default_runtime_props(
            props,{"waterlogged":"false"},ignored={"axis"}
        )
    if p == "grindstone":
        return grindstone_meta(props), _only_default_runtime_props(props,ignored={"face","facing"})
    if p == "scaffolding":
        distance=intprop(props,"distance",0,0,7)
        meta=distance | (8 if boolprop(props,"bottom") else 0)
        return meta, _only_default_runtime_props(
            props,{"waterlogged":"false"},ignored={"distance","bottom"}
        )
    if p == "turtle_egg":
        eggs=intprop(props,"eggs",1,1,4)
        hatch=intprop(props,"hatch",0,0,2)
        return (hatch<<2)|(eggs-1), _only_default_runtime_props(props,ignored={"eggs","hatch"})
    if p.endswith("_coral_wall_fan"):
        return sign_wall_meta(props), _only_default_runtime_props(
            props,{"waterlogged":"false"},ignored={"facing"}
        )
    if p == "glow_lichen":
        mask=_glow_lichen_state_mask(props)
        exact=bool(mask) and _only_default_runtime_props(
            props,{"waterlogged":"false"},ignored={"down","up","north","south","west","east"}
        )
        # The six faces do not fit in metadata; the writer synthesizes the EFR
        # TileEntityGlowLichen State bitmap. Metadata itself is intentionally 0.
        return 0, exact
    if p.endswith("_copper_chain"):
        # Preserve the free metadata bits now even though the attached EFR Plus
        # parity renderer does not yet consume them. A later EFR renderer fix can
        # therefore recover orientation without reconverting the world.
        return chain_axis_meta(props), False
    if p.endswith("_copper_lantern"):
        return (1 if boolprop(props,"hanging") else 0), False
    if p.endswith("_froglight"):
        return log_axis_bits(props), False
    if p.endswith("copper_chest"):
        return direction_meta(props,"facing","north"), _only_default_runtime_props(
            props,{"waterlogged":"false"},ignored={"facing","type"}
        )
    if p == "copper_bulb":
        meta=ET_FUTURUM_COPPER_BULB_BASE_META[p]
        if boolprop(props,"lit"): meta|=4
        return meta, True
    return _provider_state_meta(p,props)


def _etfuturum_alias(path, props):
    """Resolve modern vanilla identities packed into established EFR block IDs.

    Return ``(target_path, meta, state_exact, note)``. The caller still checks
    the selected template registry before accepting the result.
    """
    p=path.lower()

    # Modern coloured/standing/wall banners are one EFR block plus state TE.
    for color,cmeta in COLOR_META.items():
        if p == color+"_banner":
            return "banner",int(props.get("rotation","0")) & 15,True,"EFR banner block + standing/banner colour tile state"
        if p == color+"_wall_banner":
            return "banner",sign_wall_meta(props),True,"EFR banner block + wall/banner colour tile state"

    # EFR keeps every shulker colour in one block. Facing and colour live in
    # TileEntityShulkerBox rather than block metadata.
    if p == "shulker_box":
        return "shulker_box",0,True,"EFR shulker-box tile state"
    for color in COLOR_META:
        if p == color+"_shulker_box":
            return "shulker_box",0,True,"EFR packed dyed shulker-box tile state"

    # Registry-name collisions/renames where a same-named 1.7.10 vanilla block
    # would otherwise be selected even though it has different modern meaning.
    if p == "stone_stairs":
        return "stone_stairs",stair_meta(props),True,"EFR real-stone stair identity (legacy minecraft:stone_stairs is cobblestone)"

    # EFR's extra stone slab block is the actual modern stone slab. The legacy
    # minecraft:stone_slab meta 0 is the modern *smooth* stone slab instead.
    if p in ET_FUTURUM_STONE_SLAB_META:
        base=ET_FUTURUM_STONE_SLAB_META[p]
        if props.get("type") == "double":
            return "double_stone_slab",base,True,"EFR extra vanilla slab subtype (double)"
        return "stone_slab",base | (8 if props.get("type") == "top" else 0),True,"EFR extra vanilla slab subtype"

    if p in ET_FUTURUM_STONE_SLAB_2_META:
        base=ET_FUTURUM_STONE_SLAB_2_META[p]
        if props.get("type") == "double":
            return "double_stone_slab_2",base,True,"EFR bountiful-stone slab subtype (double)"
        return "stone_slab_2",base | (8 if props.get("type") == "top" else 0),True,"EFR bountiful-stone slab subtype"

    if p in ET_FUTURUM_STONE_WALL_META:
        return "stone_wall",ET_FUTURUM_STONE_WALL_META[p],True,"EFR extra vanilla wall subtype"
    if p in ET_FUTURUM_STONE_WALL_2_META:
        return "stone_wall_2",ET_FUTURUM_STONE_WALL_2_META[p],True,"EFR bountiful-stone wall subtype"

    if p in ET_FUTURUM_RAW_ORE_META:
        return "raw_ore_block",ET_FUTURUM_RAW_ORE_META[p],True,"EFR packed raw-ore block subtype"

    if p == "mud_bricks":
        return "packed_mud",1,True,"EFR packed mud-brick subtype"

    if p == "flowering_azalea":
        return "azalea",1,True,"EFR flowering azalea subtype"
    if p == "flowering_azalea_leaves":
        # Low bit = flowering subtype; bit 2 keeps imported map foliage
        # persistent so it does not decay immediately after conversion.
        return "azalea_leaves",5,True,"EFR flowering azalea-leaf subtype; persistent bit set"

    if p in {"cave_vines","cave_vines_plant"}:
        target="cave_vine" if p == "cave_vines" else "cave_vine_plant"
        berries=1 if boolprop(props,"berries") else 0
        exact=_only_default_runtime_props(props,ignored={"berries"})
        # Modern cave-vine age is a growth scheduler state and EFR has no
        # one-to-one metadata slot for it. Preserve the visible berry state but
        # be conservative when age is present/non-default.
        if "age" in props and str(props.get("age","0")) not in {"0","25"}:
            exact=False
        return target,berries,exact,"EFR cave-vine identity and berry/light state"

    if p in {"weeping_vines_plant","weeping_vines"}:
        return "weeping_vines",0,_only_default_runtime_props(props,ignored={"age"}),"EFR unified weeping-vine head/body block"
    if p in {"twisting_vines_plant","twisting_vines"}:
        return "twisting_vines",0,_only_default_runtime_props(props,ignored={"age"}),"EFR unified twisting-vine head/body block"

    if p in ET_FUTURUM_NETHER_ROOT_META:
        return "nether_roots",ET_FUTURUM_NETHER_ROOT_META[p],True,"EFR packed crimson/warped roots subtype"
    if p in ET_FUTURUM_NETHER_FUNGUS_META:
        return "nether_fungus",ET_FUTURUM_NETHER_FUNGUS_META[p],True,"EFR packed crimson/warped fungus subtype"

    if p == "magma_block":
        return "magma",0,True,"EFR legacy registry rename for magma block"
    if p == "wet_sponge":
        return "sponge",1,True,"EFR wet sponge subtype"

    # End-brick / red-nether-brick legacy registry spelling differences.
    if p == "end_stone_brick_stairs":
        return "end_brick_stairs",stair_meta(props),True,"EFR legacy end-brick stair registry identity"
    if p == "end_stone_brick_slab":
        target="double_end_brick_slab" if props.get("type") == "double" else "end_brick_slab"
        return target,(8 if props.get("type") == "top" and target == "end_brick_slab" else 0),True,"EFR legacy end-brick slab registry identity"
    if p == "end_stone_brick_wall":
        return "end_brick_wall",0,True,"EFR legacy end-brick wall registry identity"
    if p == "red_nether_brick_stairs":
        return "red_netherbrick_stairs",stair_meta(props),True,"EFR legacy red-nether-brick stair registry identity"
    if p == "red_nether_brick_slab":
        target="double_red_netherbrick_slab" if props.get("type") == "double" else "red_netherbrick_slab"
        return target,(8 if props.get("type") == "top" and target == "red_netherbrick_slab" else 0),True,"EFR legacy red-nether-brick slab registry identity"

    if p in ET_FUTURUM_BLACKSTONE_META and p != "blackstone":
        return "blackstone",ET_FUTURUM_BLACKSTONE_META[p],True,"EFR packed blackstone subtype"
    if p in ET_FUTURUM_BLACKSTONE_SLAB_META:
        base=ET_FUTURUM_BLACKSTONE_SLAB_META[p]
        target="double_blackstone_slab" if props.get("type") == "double" else "blackstone_slab"
        meta=base if target.startswith("double_") else base | (8 if props.get("type") == "top" else 0)
        return target,meta,True,"EFR packed blackstone slab subtype"
    if p in ET_FUTURUM_BLACKSTONE_WALL_META:
        return "blackstone_wall",ET_FUTURUM_BLACKSTONE_WALL_META[p],True,"EFR packed blackstone wall subtype"

    # EFR supplies stripped variants for the six legacy wood species in two
    # packed BlockLog IDs. This prevents stripped old woods from silently
    # degrading back to ordinary vanilla logs.
    for wood,species in (("oak",0),("spruce",1),("birch",2),("jungle",3)):
        if p == "stripped_"+wood+"_log":
            return "wood_stripped",species | log_axis_bits(props),True,"EFR packed stripped legacy log"
        if p == "stripped_"+wood+"_wood":
            return "wood_stripped",species | 12,True,"EFR packed stripped legacy wood/bark block"
    for wood,species in (("acacia",0),("dark_oak",1)):
        if p == "stripped_"+wood+"_log":
            return "wood2_stripped",species | log_axis_bits(props),True,"EFR packed stripped legacy log"
        if p == "stripped_"+wood+"_wood":
            return "wood2_stripped",species | 12,True,"EFR packed stripped legacy wood/bark block"

    # Pre-flattening wood-family registry names in EFR are reversed. Oak stays
    # vanilla and is handled in map_modern; these are the five non-oak families.
    for wood in ("spruce","birch","jungle","acacia","dark_oak"):
        prefix=wood+"_"
        if p.startswith(prefix):
            rest=p[len(prefix):]
            target_template=ET_FUTURUM_LEGACY_WOOD_PATHS.get(rest)
            if target_template:
                target=target_template.format(wood=wood)
                if rest == "fence":
                    return target,0,True,"EFR legacy wood-fence registry identity"
                if rest == "fence_gate":
                    return target,gate_meta(props),True,"EFR legacy wood fence-gate registry identity"
                if rest == "door":
                    return target,door_meta(props),True,"EFR legacy wood-door registry identity"
                if rest == "trapdoor":
                    return target,trapdoor_meta(props),True,"EFR legacy wood-trapdoor registry identity"
                if rest == "button":
                    return target,button_meta(props),True,"EFR legacy wood-button registry identity"
                if rest == "pressure_plate":
                    return target,(1 if boolprop(props,"powered") else 0),True,"EFR legacy wood pressure-plate registry identity"
                if rest == "sign":
                    return target,int(props.get("rotation","0")) & 15,True,"EFR legacy wood-sign registry identity"
                if rest == "wall_sign":
                    return target,sign_wall_meta(props),True,"EFR legacy wood wall-sign registry identity"

    simple=ET_FUTURUM_SIMPLE_SUBTYPES.get(p)
    if simple is not None:
        return simple[0], simple[1], True, "EFR legacy subtype metadata"

    for color,cmeta in COLOR_META.items():
        if p == color+"_concrete":
            return "concrete",cmeta,True,"EFR packed concrete colour"
        if p == color+"_concrete_powder":
            return "concrete_powder",cmeta,True,"EFR packed concrete-powder colour"

    # Existing EFR prismarine/deepslate/tuff slab and wall blocks pack material
    # variants in their low metadata bits and use bit 3 for a top slab.
    slab_aliases={
        "prismarine_brick_slab":("prismarine_slab",1),
        "dark_prismarine_slab":("prismarine_slab",2),
        "cobbled_deepslate_slab":("deepslate_slab",0),
        "polished_deepslate_slab":("deepslate_slab",1),
        "deepslate_tile_slab":("deepslate_brick_slab",1),
        "polished_tuff_slab":("tuff_slab",1),
        "tuff_brick_slab":("tuff_slab",2),
    }
    if p in slab_aliases:
        target,base=slab_aliases[p]
        return target,slab_meta(base,props),True,"EFR packed slab subtype"

    wall_aliases={
        "prismarine_brick_wall":("prismarine_wall",1),
        "dark_prismarine_wall":("prismarine_wall",2),
        "cobbled_deepslate_wall":("deepslate_wall",0),
        "polished_deepslate_wall":("deepslate_wall",1),
        "deepslate_tile_wall":("deepslate_brick_wall",1),
        "polished_tuff_wall":("tuff_wall",1),
        "tuff_brick_wall":("tuff_wall",2),
    }
    if p in wall_aliases:
        target,meta=wall_aliases[p]
        return target,meta,True,"EFR packed wall subtype"

    stair_aliases={
        "prismarine_brick_stairs":"prismarine_stairs_brick",
        "dark_prismarine_stairs":"prismarine_stairs_dark",
    }
    if p in stair_aliases:
        return stair_aliases[p],stair_meta(props),True,"EFR legacy stair registry identity"

    # EFR's modern wood families predate the parity-shell layer and intentionally
    # share IDs for planks/slabs/fences/leaves/saplings.
    for wood,wmeta in ET_FUTURUM_WOOD_META.items():
        if p == wood+"_planks":
            return "wood_planks",wmeta,True,"EFR packed modern wood planks"
        if p == wood+"_slab":
            return "wood_slab",slab_meta(wmeta,props),True,"EFR packed modern wood slab"
        if p == wood+"_fence":
            return "wood_fence",wmeta,True,"EFR packed modern wood fence"
    if p == "mangrove_leaves":
        return "leaves",4,True,"EFR packed mangrove leaves; persistent bit set"
    if p == "cherry_leaves":
        return "leaves",5,True,"EFR packed cherry leaves; persistent bit set"
    if p == "mangrove_propagule":
        stage=8 if str(props.get("stage","0")) == "1" else 0
        return "sapling",0|stage,True,"EFR packed mangrove propagule"
    if p == "cherry_sapling":
        stage=8 if str(props.get("stage","0")) == "1" else 0
        return "sapling",1|stage,True,"EFR packed cherry sapling"

    # EFR BaseLog stores log/wood/stripped-log/stripped-wood in low bits 0..3.
    for wood,target in (("mangrove","mangrove_log"),("cherry","cherry_log")):
        if p == "stripped_"+wood+"_log":
            return target,2|log_axis_bits(props),True,"EFR packed stripped log"
        if p == wood+"_wood":
            return target,1,True,"EFR packed bark/wood block"
        if p == "stripped_"+wood+"_wood":
            return target,3,True,"EFR packed stripped wood block"
    for wood,target in (("crimson","crimson_stem"),("warped","warped_stem")):
        if p == "stripped_"+wood+"_stem":
            return target,2|log_axis_bits(props),True,"EFR packed stripped stem"
        if p == wood+"_hyphae":
            return target,1,True,"EFR packed hyphae block"
        if p == "stripped_"+wood+"_hyphae":
            return target,3,True,"EFR packed stripped hyphae block"
    if p == "stripped_bamboo_block":
        return "bamboo_block",1|log_axis_bits(props),True,"EFR packed stripped bamboo block"

    # 1.21.9 renamed the vanilla chain identity to iron_chain. EFR's established
    # 1.7.10 implementation remains registered as `chain` and uses axis metadata.
    if p == "iron_chain":
        exact=_only_default_runtime_props(props,{"waterlogged":"false"},ignored={"axis"})
        return "chain",chain_axis_meta(props),exact,"EFR chain axis metadata (modern iron_chain alias)"

    if p in ET_FUTURUM_COPPER_BLOCK_META:
        return "copper_block",ET_FUTURUM_COPPER_BLOCK_META[p],True,"EFR packed copper/cut-copper state"
    if p in ET_FUTURUM_CHISELED_COPPER_META:
        return "chiseled_copper",ET_FUTURUM_CHISELED_COPPER_META[p],True,"EFR packed chiseled-copper oxidation/wax state"
    if p in ET_FUTURUM_COPPER_GRATE_META:
        return "copper_grate",ET_FUTURUM_COPPER_GRATE_META[p],True,"EFR packed copper-grate oxidation/wax state"
    if p in ET_FUTURUM_COPPER_BULB_BASE_META:
        target="powered_copper_bulb" if boolprop(props,"powered") else "copper_bulb"
        meta=ET_FUTURUM_COPPER_BULB_BASE_META[p] | (4 if boolprop(props,"lit") else 0)
        return target,meta,True,"EFR packed copper-bulb oxidation/wax/light state"
    if p in ET_FUTURUM_CUT_COPPER_SLAB_META:
        meta=ET_FUTURUM_CUT_COPPER_SLAB_META[p] | (8 if props.get("type") == "top" else 0)
        return "cut_copper_slab",meta,True,"EFR packed cut-copper slab state"

    return None


def _map_etfuturum_first(name, props, reg: TargetRegistry):
    """Return a first-class Et Futurum mapping proven by the target registry.

    This intentionally does not consult Catalog Workspace or JAR assets. EFR Plus
    may download Mojang assets at launch; block availability is instead proven by
    the Forge 1.7.10 registry snapshot in the selected target/template world.
    """
    if not _etfuturum_registered(reg):
        return None
    p=name.split(":",1)[-1].lower()

    # Furnace-like EFR blocks retain separate lit/unlit 1.7.10 registry IDs.
    # Prefer the lit identity when the modern state says lit and the selected
    # target registry actually contains that concrete block.
    if p in {"blast_furnace","smoker"} and boolprop(props or {},"lit"):
        lit_direct=_etfuturum_registry_name("lit_"+p)
        if reg.resolve(lit_direct) is not None:
            meta=direction_meta(props or {},"facing","north")
            exact=_only_default_runtime_props(props or {},ignored={"facing","lit"})
            quality="backport_exact" if exact else "backport_close"
            note="Et Futurum lit furnace identity detected directly in the selected target registry (Forge 1.7.10)"
            if not exact:
                note += "; exact block identity but some source state is not represented"
            return Mapping(lit_direct,meta&15,quality,note)

    # Resolve source identities that EFR deliberately packs into shared legacy
    # registry IDs *before* same-name/direct matching. This is required for
    # flattened names such as andesite_slab -> stone_slab_2 and for semantic
    # collisions such as modern stone_slab/stone_stairs, whose same-named 1.7.10
    # vanilla blocks mean something different.
    alias=_etfuturum_alias(p,props or {})
    if alias is not None:
        target_path,meta,state_exact,detail=alias
        target=_etfuturum_registry_name(target_path)
        if reg.resolve(target) is not None:
            quality="backport_exact" if state_exact else "backport_close"
            return Mapping(target,meta&15,quality,"Et Futurum target detected in the selected Forge registry; "+detail)

    # Never replace a genuine 1.7.10 vanilla identity merely because EFR also
    # has something similarly named, except for the explicitly documented
    # post-flattening semantic collisions above.
    if reg.resolve("minecraft:"+p) is not None and p not in ET_FUTURUM_VANILLA_COLLISION_OVERRIDES:
        return None

    direct=_etfuturum_registry_name(p)
    if reg.resolve(direct) is not None:
        meta,state_exact=_etfuturum_state_meta(p,props or {})
        quality="backport_exact" if state_exact else "backport_close"
        note="Et Futurum target detected directly in the selected target registry (Forge 1.7.10)"
        if not state_exact:
            note += "; exact block identity but state metadata is only partially translatable"
        return Mapping(direct,meta&15,quality,note)

    return None


def map_modern(name, props, reg: TargetRegistry, use_hbm=True, mapping_profile: MappingProfile | None = None):
    """Return the closest legacy block mapping.

    Exact registered blocks from enabled backport-provider catalogs are considered
    before approximate architectural fallbacks, but only when the target world's
    Forge registry confirms the provider block actually exists. Vanilla blocks
    that already exist in 1.7.10 keep the explicit legacy metadata rules below.
    """
    p=name.split(":",1)[-1]

    def V(target,meta=0,q="exact",note=""): return Mapping(target,meta&15,q,note)
    def H(logical, fallback, meta=0, fbmeta=0, q="close", note=""):
        profile_allows = mapping_profile.allows_namespace("hbm") if mapping_profile is not None else bool(use_hbm)
        if profile_allows and logical in HBM_SAFE_ARCHITECTURAL and reg.has_hbm(logical):
            return V("hbm:"+logical,meta,q,note)
        if mapping_profile is not None and mapping_profile.catalog_bound and not profile_allows:
            reason = "HBM is not enabled in the active Catalog Workspace; vanilla fallback"
        elif not use_hbm or (mapping_profile is not None and not mapping_profile.allow_safe_mod_replacements):
            reason = "Safe mod replacements disabled; vanilla fallback"
        else:
            reason = "HBM %s unavailable in target registry; vanilla fallback" % logical
        fallback_note = ("%s; %s" % (note, reason)) if note else reason
        return V(fallback,fbmeta,"approximate", fallback_note)

    if name in AIR_NAMES: return V("minecraft:air")

    # Et Futurum is the preferred 1.7.10 modern-content provider when its block
    # is actually registered in the selected target/template world. This is
    # target-registry driven, so it works even when EFR Plus downloads Mojang
    # textures/models at runtime and no EFR catalog has been added manually.
    if _safe_provider_replacements_enabled(use_hbm,mapping_profile):
        etfuturum_mapping=_map_etfuturum_first(name,props or {},reg)
        if etfuturum_mapping is not None:
            return etfuturum_mapping

    # Exact pre-flattening aliases that do not require a backport provider.
    # These must run before generic/catalog fallbacks because the modern names
    # either did not exist in 1.7.10 or changed meaning during flattening.
    if p == "smooth_stone_slab":
        if props.get("type") == "double":
            return V("minecraft:double_stone_slab",0,"exact","Modern smooth-stone slab is legacy stone_slab subtype 0")
        return V("minecraft:stone_slab",8 if props.get("type") == "top" else 0,"exact","Modern smooth-stone slab is legacy stone_slab subtype 0")
    if p == "short_grass":
        return V("minecraft:tallgrass",1,"exact","Modern short_grass is legacy tallgrass subtype 1")
    if p == "mushroom_stem":
        return V("minecraft:red_mushroom_block",huge_mushroom_meta(props or {}),"exact","Modern mushroom stem uses legacy huge-mushroom face metadata")
    if p in {"brown_mushroom_block","red_mushroom_block"}:
        return V("minecraft:"+p,huge_mushroom_meta(props or {}),"exact","Preserved legacy huge-mushroom face metadata")

    # Oak kept vanilla registry identities through 1.7.10, but flattening added
    # the explicit oak_ prefix. Normalize those names without downgrading them.
    oak_aliases={
        "oak_fence":("minecraft:fence",0),
        "oak_fence_gate":("minecraft:fence_gate",gate_meta(props or {})),
        "oak_door":("minecraft:wooden_door",door_meta(props or {})),
        "oak_trapdoor":("minecraft:trapdoor",trapdoor_meta(props or {})),
        "oak_button":("minecraft:wooden_button",button_meta(props or {})),
        "oak_pressure_plate":("minecraft:wooden_pressure_plate",1 if boolprop(props or {},"powered") else 0),
        "oak_sign":("minecraft:standing_sign",int((props or {}).get("rotation","0")) & 15),
        "oak_wall_sign":("minecraft:wall_sign",sign_wall_meta(props or {})),
    }
    if p in oak_aliases:
        target,meta=oak_aliases[p]
        return V(target,meta,"exact","Flattened oak registry name normalized to its 1.7.10 identity")

    # Potted modern identities all use the old flower-pot block plus a target
    # item ID/data pair in TileEntityFlowerPot. Real Forge ItemData is used so
    # modded EFR item IDs are never guessed.
    if p in FLOWER_POT_CONTENTS:
        content=flower_pot_content(p,reg)
        if content is not None and reg.resolve("minecraft:flower_pot") is not None:
            _item_id,_data,item_name=content
            quality="exact" if item_name.startswith("minecraft:") else "backport_exact"
            if p == "potted_torchflower":
                quality="backport_close"
            return V("minecraft:flower_pot",0,quality,"Flower-pot content preserved through target ItemData tile state")

    # If no real provider block exists, invisible/editor-only modern blocks are
    # safer omitted than turned into unrelated visible fallback cubes.
    if p in {"barrier","structure_block","structure_void","jigsaw","light","end_gateway"}: return V("minecraft:air",0,"omitted","Modern editor/invisible block omitted")

    # Other catalog backport providers may expose the same modern vanilla registry path in a
    # 1.7.10 namespace. Do not let that shadow a real 1.7.10 vanilla block of the
    # same name, and never trust a catalog target that is absent from the actual
    # template-world registry.
    if mapping_profile is not None and mapping_profile.allow_safe_mod_replacements and reg.resolve("minecraft:"+p) is None:
        for candidate in mapping_profile.backport_candidates(name):
            target=candidate.target_name
            namespace=target.split(":",1)[0] if ":" in target else ""
            if not mapping_profile.allows_namespace(namespace):
                continue
            if reg.resolve(target) is None:
                continue
            meta,state_exact=_provider_state_meta(p,props or {})
            quality="backport_exact" if state_exact else "backport_close"
            note="Exact-name block supplied by backport provider %s" % (candidate.provider or namespace)
            if not state_exact:
                note += "; block identity is exact but this state has no generic legacy metadata translator"
            return V(target,meta,quality,note)

    # Colour families.
    for color,cmeta in COLOR_META.items():
        pref=color+"_"
        if p.startswith(pref):
            rest=p[len(pref):]
            if rest=="wool": return V("minecraft:wool",cmeta)
            if rest=="carpet": return V("minecraft:carpet",cmeta)
            if rest=="stained_glass": return V("minecraft:stained_glass",cmeta)
            if rest=="stained_glass_pane": return V("minecraft:stained_glass_pane",cmeta)
            if rest=="terracotta": return V("minecraft:stained_hardened_clay",cmeta)
            if rest in {"concrete","concrete_powder"}: return H("concrete_colored","minecraft:stained_hardened_clay",cmeta,cmeta,"close","HBM coloured concrete substitutes modern concrete")
            if rest=="glazed_terracotta": return V("minecraft:stained_hardened_clay",cmeta,"approximate","Glazed pattern is unavailable in 1.7.10")
            if rest=="bed": return V("minecraft:bed",door_bed_meta(props),"approximate","1.7.10 beds are red only")
            if rest in {"wall_banner","banner"}: return V("minecraft:air",0,"omitted","Banners do not exist in 1.7.10")
            if rest=="candle": return H("lantern","minecraft:torch",0,5,"approximate","Modern candle replaced by small light source")
            if rest=="shulker_box": return V("minecraft:chest",0,"approximate","Shulker boxes do not exist in 1.7.10; converted to an empty chest shell")

    # Wood families that actually exist in 1.7.10.
    for wood,wmeta in WOOD_META.items():
        pref=wood+"_"
        if p.startswith(pref) or p.startswith("stripped_"+pref):
            stripped=p.startswith("stripped_")
            rest=p[len("stripped_"):] if stripped else p
            rest=rest[len(pref):] if rest.startswith(pref) else rest
            if rest=="planks": return V("minecraft:planks",wmeta)
            if rest=="log":
                if wmeta<4: return V("minecraft:log", wmeta|log_axis_bits(props))
                return V("minecraft:log2", (wmeta-4)|log_axis_bits(props))
            if rest=="wood":
                if wmeta<4: return V("minecraft:log", wmeta|12)
                return V("minecraft:log2", (wmeta-4)|12)
            if rest=="leaves":
                if wmeta<4: return V("minecraft:leaves",wmeta|4)
                return V("minecraft:leaves2",(wmeta-4)|4)
            if rest=="sapling": return V("minecraft:sapling",wmeta)
            if rest=="stairs": return V("minecraft:"+wood+"_stairs",stair_meta(props))
            if rest=="slab":
                if props.get("type") == "double":
                    return V("minecraft:double_wooden_slab",wmeta)
                return V("minecraft:wooden_slab",wmeta | (8 if props.get("type") == "top" else 0))
            if rest=="fence": return V("minecraft:fence",0,"approximate","1.7.10 has only oak wooden fences")
            if rest=="fence_gate": return V("minecraft:fence_gate",gate_meta(props),"approximate","1.7.10 has only oak fence gates")
            if rest=="door": return V("minecraft:wooden_door",door_meta(props),"approximate","1.7.10 has only oak wooden doors")
            if rest=="trapdoor": return V("minecraft:trapdoor",trapdoor_meta(props),"approximate","1.7.10 has only oak wooden trapdoors")
            if rest=="button": return V("minecraft:wooden_button",button_meta(props),"approximate" if wood!="oak" else "exact")
            if rest=="pressure_plate": return V("minecraft:wooden_pressure_plate",1 if boolprop(props,"powered") else 0,"approximate" if wood!="oak" else "exact")
            if rest in {"sign","hanging_sign"}: return V("minecraft:standing_sign",int(props.get("rotation","0"))&15,"approximate")
            if rest=="wall_sign": return V("minecraft:wall_sign",sign_wall_meta(props),"approximate")

    # Modern wood families with no 1.7.10 counterpart.
    modern_wood=None
    for w in ("mangrove","cherry","bamboo","crimson","warped"):
        if p.startswith(w+"_") or p.startswith("stripped_"+w+"_"):
            modern_wood=w; break
    if modern_wood:
        basep=p[len("stripped_"):] if p.startswith("stripped_") else p
        rest=basep[len(modern_wood)+1:]
        # Palette choices intended to keep broad colour/material feel without introducing valuable HBM resources.
        fallback_wood={"mangrove":"dark_oak","cherry":"birch","bamboo":"birch","crimson":"dark_oak","warped":"spruce"}[modern_wood]
        wm=WOOD_META[fallback_wood]
        if modern_wood=="warped" and rest in {"planks","log","hyphae"}:
            logical="frozen_planks" if rest=="planks" else "frozen_log"
            fb="minecraft:planks" if rest=="planks" else ("minecraft:log" if wm<4 else "minecraft:log2")
            fbm=wm if rest=="planks" else ((wm if wm<4 else wm-4)|log_axis_bits(props,rest=="hyphae"))
            return H(logical,fb,0,fbm,"approximate","Warped wood colour approximated with HBM frozen wood")
        if rest=="planks": return V("minecraft:planks",wm,"approximate",modern_wood+" wood unavailable")
        if rest in {"log","stem"}:
            return V("minecraft:log" if wm<4 else "minecraft:log2",(wm if wm<4 else wm-4)|log_axis_bits(props),"approximate",modern_wood+" wood unavailable")
        if rest in {"wood","hyphae"}:
            return V("minecraft:log" if wm<4 else "minecraft:log2",(wm if wm<4 else wm-4)|12,"approximate",modern_wood+" wood unavailable")
        if rest=="leaves": return V("minecraft:leaves" if wm<4 else "minecraft:leaves2",(wm if wm<4 else wm-4)|4,"approximate")
        if rest=="stairs": return V("minecraft:"+fallback_wood+"_stairs",stair_meta(props),"approximate")
        if rest=="slab": return V("minecraft:wooden_slab",slab_meta(wm,props),"approximate")
        if rest=="fence": return V("minecraft:fence",0,"approximate")
        if rest=="fence_gate": return V("minecraft:fence_gate",gate_meta(props),"approximate")
        if rest=="door": return V("minecraft:wooden_door",door_meta(props),"approximate")
        if rest=="trapdoor": return V("minecraft:trapdoor",trapdoor_meta(props),"approximate")
        if rest=="button": return V("minecraft:wooden_button",button_meta(props),"approximate")
        if rest=="pressure_plate": return V("minecraft:wooden_pressure_plate",0,"approximate")
        if rest in {"sign","hanging_sign"}: return V("minecraft:standing_sign",int(props.get("rotation","0"))&15,"approximate")
        if rest=="wall_sign": return V("minecraft:wall_sign",sign_wall_meta(props),"approximate")
        if rest in {"roots","fungus","nylium"}: return V("minecraft:netherrack",0,"approximate")

    # Core terrain / natural blocks.
    direct={
        "bedrock":"bedrock","stone":"stone","grass_block":"grass","gravel":"gravel","clay":"clay",
        "cobblestone":"cobblestone","mossy_cobblestone":"mossy_cobblestone","obsidian":"obsidian",
        "netherrack":"netherrack","soul_sand":"soul_sand","end_stone":"end_stone",
        "snow_block":"snow","ice":"ice","packed_ice":"packed_ice","cactus":"cactus","sponge":"sponge",
        "bookshelf":"bookshelf","glass":"glass","glass_pane":"glass_pane","glowstone":"glowstone",
        "coal_block":"coal_block","iron_block":"iron_block","gold_block":"gold_block","diamond_block":"diamond_block",
        "emerald_block":"emerald_block","lapis_block":"lapis_block","redstone_block":"redstone_block",
        "coal_ore":"coal_ore","iron_ore":"iron_ore","gold_ore":"gold_ore","diamond_ore":"diamond_ore",
        "emerald_ore":"emerald_ore","lapis_ore":"lapis_ore","redstone_ore":"redstone_ore",
        "nether_quartz_ore":"quartz_ore","bricks":"brick_block","nether_bricks":"nether_brick",
        "stone_bricks":"stonebrick","mossy_stone_bricks":"stonebrick","cracked_stone_bricks":"stonebrick",
        "chiseled_stone_bricks":"stonebrick","sandstone":"sandstone","chiseled_sandstone":"sandstone",
        "smooth_sandstone":"sandstone","cut_sandstone":"sandstone","quartz_block":"quartz_block",
        "quartz_pillar":"quartz_block","smooth_quartz":"quartz_block","terracotta":"hardened_clay",
        "crafting_table":"crafting_table","furnace":"furnace","chest":"chest","trapped_chest":"trapped_chest",
        "ender_chest":"ender_chest","enchanting_table":"enchanting_table","brewing_stand":"brewing_stand",
        "cauldron":"cauldron","hopper":"hopper","dispenser":"dispenser","dropper":"dropper","beacon":"beacon",
        "jukebox":"jukebox","note_block":"noteblock","tnt":"tnt","ladder":"ladder","iron_bars":"iron_bars",
        "iron_door":"iron_door","dragon_egg":"dragon_egg","end_portal":"end_portal","end_portal_frame":"end_portal_frame",
        "daylight_detector":"daylight_detector","piston":"piston","sticky_piston":"sticky_piston",
        "flower_pot":"flower_pot","hay_block":"hay_block","mycelium":"mycelium","farmland":"farmland",
        "pumpkin":"pumpkin","carved_pumpkin":"pumpkin","jack_o_lantern":"lit_pumpkin","melon":"melon_block",
        "cobweb":"web","vine":"vine","lily_pad":"waterlily","sugar_cane":"reeds",
        "brown_mushroom":"brown_mushroom","red_mushroom":"red_mushroom","brown_mushroom_block":"brown_mushroom_block",
        "red_mushroom_block":"red_mushroom_block","dragon_wall_head":"skull","creeper_head":"skull","player_head":"skull",
        "player_wall_head":"skull","skeleton_skull":"skull","skeleton_wall_skull":"skull","wither_skeleton_skull":"skull",
        "zombie_head":"skull","zombie_wall_head":"skull","anvil":"anvil","chipped_anvil":"anvil","damaged_anvil":"anvil",
    }
    if p in direct:
        meta=0
        if p=="mossy_stone_bricks": meta=1
        elif p=="cracked_stone_bricks": meta=2
        elif p=="chiseled_stone_bricks": meta=3
        elif p=="chiseled_sandstone": meta=1
        elif p in {"smooth_sandstone","cut_sandstone"}: meta=2
        elif p=="quartz_pillar": meta={"y":2,"x":3,"z":4}.get(props.get("axis","y"),2)
        elif p in {"chipped_anvil","damaged_anvil"}: meta=(4 if p=="chipped_anvil" else 8) | ({"north":0,"south":0,"east":1,"west":1}.get(props.get("facing"),0))
        elif p=="anvil": meta={"north":0,"south":0,"east":1,"west":1}.get(props.get("facing"),0)
        elif p in {"pumpkin","carved_pumpkin","jack_o_lantern"}: meta={"south":0,"west":1,"north":2,"east":3}.get(props.get("facing"),0)
        elif p in {"piston","sticky_piston"}: meta=piston_meta(props)
        elif p=="cauldron": meta=int(props.get("level","0"))&3
        elif p=="farmland": meta=min(7,int(props.get("moisture","0")))
        elif p=="sponge": meta=0
        return V("minecraft:"+direct[p],meta)

    if p=="dirt": return V("minecraft:dirt",0)
    if p=="coarse_dirt": return V("minecraft:dirt",1)
    if p=="podzol": return V("minecraft:dirt",2)
    if p=="rooted_dirt": return V("minecraft:dirt",0,"approximate")
    if p=="dirt_path": return V("minecraft:grass",0,"approximate","Path block did not exist in 1.7.10")
    if p=="sand": return V("minecraft:sand",0)
    if p=="red_sand": return V("minecraft:sand",1)
    if p=="snow": return V("minecraft:snow_layer",max(0,min(7,int(props.get("layers","1"))-1)))
    if p=="powder_snow": return V("minecraft:snow",0,"approximate")
    if p=="water": return V("minecraft:water",min(15,int(props.get("level","0"))))
    if p=="lava": return V("minecraft:lava",min(15,int(props.get("level","0"))))
    if p=="bubble_column": return V("minecraft:water",0,"approximate")
    if p=="fire": return V("minecraft:fire",0)
    if p=="soul_fire": return V("minecraft:fire",0,"approximate")
    if p=="crying_obsidian": return V("minecraft:obsidian",0,"approximate")

    # 1.8+ stone families -> HBM structural stone when available.
    if p in {"deepslate","cobbled_deepslate","infested_deepslate","blackstone","smooth_basalt"}:
        logical="basalt_smooth" if p=="smooth_basalt" else "basalt"
        return H(logical,"minecraft:stone",0,0,"close","Dark modern stone approximated with HBM basalt")
    if p in {"polished_deepslate","polished_blackstone","polished_basalt"}:
        return H("basalt_polished","minecraft:stone",0,0,"close")
    if p in {"deepslate_bricks","cracked_deepslate_bricks","polished_blackstone_bricks","cracked_polished_blackstone_bricks","chiseled_polished_blackstone","chiseled_deepslate"}:
        return H("basalt_brick","minecraft:stonebrick",0,0,"close")
    if p in {"deepslate_tiles","cracked_deepslate_tiles"}:
        return H("basalt_tiles","minecraft:stonebrick",0,0,"close")
    if p in {"andesite","polished_andesite","tuff"}:
        return H("stone_gneiss","minecraft:stone",0,0,"close")
    if p in {"granite","polished_granite"}:
        return V("minecraft:stained_hardened_clay",8,"approximate","Granite unavailable; neutral stone-colour fallback")
    if p in {"diorite","polished_diorite","calcite"}:
        return V("minecraft:quartz_block",0,"approximate","Light modern stone approximated with quartz")
    if p=="dripstone_block": return V("minecraft:stained_hardened_clay",12,"approximate")
    if p=="mud" or p=="packed_mud": return H("mud_block","minecraft:dirt",0,1,"close")
    if p.startswith("mud_brick"): return H("brick_concrete","minecraft:brick_block",0,0,"approximate")
    if p=="moss_block": return V("minecraft:grass",0,"approximate")
    if p=="moss_carpet": return V("minecraft:carpet",13,"approximate")

    # Stairs/slabs/walls for common families.
    stair_targets={
        "cobblestone_stairs":"minecraft:stone_stairs","brick_stairs":"minecraft:brick_stairs",
        "stone_brick_stairs":"minecraft:stone_brick_stairs","mossy_stone_brick_stairs":"minecraft:stone_brick_stairs",
        "mossy_cobblestone_stairs":"minecraft:stone_stairs","nether_brick_stairs":"minecraft:nether_brick_stairs",
        "sandstone_stairs":"minecraft:sandstone_stairs","smooth_sandstone_stairs":"minecraft:sandstone_stairs",
        "quartz_stairs":"minecraft:quartz_stairs","smooth_quartz_stairs":"minecraft:quartz_stairs",
    }
    if p in stair_targets: return V(stair_targets[p],stair_meta(props),"exact" if p in {"cobblestone_stairs","brick_stairs","stone_brick_stairs","nether_brick_stairs","sandstone_stairs","quartz_stairs"} else "approximate")
    if p in {"stone_stairs","andesite_stairs","polished_andesite_stairs","diorite_stairs","polished_diorite_stairs","granite_stairs","polished_granite_stairs"}:
        return H("concrete_smooth_stairs","minecraft:stone_stairs",stair_meta(props),stair_meta(props),"approximate")
    if "deepslate" in p and p.endswith("_stairs") or "blackstone" in p and p.endswith("_stairs"):
        return H("reinforced_stone_stairs","minecraft:stone_brick_stairs",stair_meta(props),stair_meta(props),"approximate")
    if p in {"red_sandstone_stairs","smooth_red_sandstone_stairs"}: return H("brick_red","minecraft:brick_stairs",0,stair_meta(props),"approximate")
    if p in {"purpur_stairs","prismarine_stairs","prismarine_brick_stairs","dark_prismarine_stairs"}: return H("concrete_stairs","minecraft:quartz_stairs",stair_meta(props),stair_meta(props),"approximate")

    slab_base={
        "stone_slab":0,"smooth_stone_slab":0,"sandstone_slab":1,"petrified_oak_slab":2,
        "cobblestone_slab":3,"brick_slab":4,"stone_brick_slab":5,"mossy_stone_brick_slab":5,
        "nether_brick_slab":6,"quartz_slab":7,"smooth_quartz_slab":7,
    }
    if p in slab_base:
        quality="exact" if p in {"sandstone_slab","cobblestone_slab","brick_slab","stone_brick_slab","nether_brick_slab","quartz_slab"} else "approximate"
        if props.get("type") == "double":
            return V("minecraft:double_stone_slab",slab_base[p],quality)
        return V("minecraft:stone_slab",slab_base[p] | (8 if props.get("type") == "top" else 0),quality)
    if p.endswith("_slab"):
        # New stone slabs have no matching HBM slab family; keep half-block geometry using stone slab.
        return V("minecraft:stone_slab",slab_meta(0,props),"approximate","Modern slab texture unavailable; shape preserved")

    if p=="cobblestone_wall": return V("minecraft:cobblestone_wall",0)
    if p=="mossy_cobblestone_wall": return V("minecraft:cobblestone_wall",1)
    if p.endswith("_wall"): return V("minecraft:cobblestone_wall",0,"approximate","Modern wall texture unavailable; wall geometry preserved")

    # Prismarine / purpur / newer masonry.
    if p in {"prismarine","prismarine_bricks"}: return H("concrete_colored","minecraft:stained_hardened_clay",9,9,"approximate","Prismarine colour approximated with cyan architectural material")
    if p=="dark_prismarine": return H("concrete_colored","minecraft:stained_hardened_clay",13,13,"approximate")
    if p in {"purpur_block"}: return H("concrete_colored","minecraft:stained_hardened_clay",10,10,"approximate")
    if p=="end_stone_bricks": return H("brick_light","minecraft:end_stone",0,0,"close")
    if p in {"red_sandstone","smooth_red_sandstone","chiseled_red_sandstone"}: return H("brick_red","minecraft:stained_hardened_clay",0,14,"approximate")
    if p in {"red_nether_bricks","cracked_nether_bricks","chiseled_nether_bricks"}: return H("brick_red","minecraft:nether_brick",0,0,"approximate")
    if p=="quartz_bricks": return V("minecraft:quartz_block",0,"approximate")

    # Lighting and thin/decorative blocks.
    if p in {"torch"}: return V("minecraft:torch",5)
    if p=="wall_torch": return V("minecraft:torch",torch_wall_meta(props))
    if p in {"lantern","soul_lantern"}: return H("lantern","minecraft:torch",0,5,"close")
    if p in {"sea_lantern","ochre_froglight","pearlescent_froglight","verdant_froglight","shroomlight"}: return H("lightstone","minecraft:glowstone",0,0,"close")
    if p in {"campfire","soul_campfire"}: return V("minecraft:fire",0,"approximate","Campfire geometry unavailable")
    if p=="chain": return H("steel_poles","minecraft:iron_bars",0,0,"approximate")
    if p=="scaffolding": return H("steel_scaffold","minecraft:fence",0,0,"close","HBM steel scaffold preserves scaffold-like structure")
    if p=="lightning_rod": return H("steel_poles","minecraft:iron_bars",0,0,"approximate")
    if p=="iron_trapdoor": return H("trapdoor_steel","minecraft:trapdoor",trapdoor_meta(props),trapdoor_meta(props),"close")
    if p=="bell": return H("lantern","minecraft:gold_block",0,0,"approximate")
    if p=="end_rod": return H("steel_poles","minecraft:torch",0,5,"approximate")
    if p=="glow_lichen": return V("minecraft:vine",0,"approximate")

    # Glass variants.
    if p=="tinted_glass": return V("minecraft:stained_glass",15,"approximate")
    if p=="reinforced_deepslate": return H("reinforced_stone","minecraft:obsidian",0,0,"close")

    # Redstone / mechanisms.
    if p=="redstone_wire": return V("minecraft:redstone_wire",min(15,int(props.get("power","0"))))
    if p in {"redstone_torch","redstone_wall_torch"}:
        tgt="minecraft:redstone_torch" if boolprop(props,"lit") else "minecraft:unlit_redstone_torch"
        return V(tgt,5 if p=="redstone_torch" else torch_wall_meta(props))
    if p=="redstone_lamp": return V("minecraft:lit_redstone_lamp" if boolprop(props,"lit") else "minecraft:redstone_lamp",0)
    if p=="repeater":
        tgt="minecraft:powered_repeater" if boolprop(props,"powered") else "minecraft:unpowered_repeater"
        facing={"north":0,"east":1,"south":2,"west":3}.get(props.get("facing"),0)
        return V(tgt,facing | ((max(1,min(4,int(props.get("delay","1"))))-1)<<2))
    if p=="comparator":
        tgt="minecraft:powered_comparator" if boolprop(props,"powered") else "minecraft:unpowered_comparator"
        facing={"north":0,"east":1,"south":2,"west":3}.get(props.get("facing"),0)
        mode=4 if props.get("mode")=="subtract" else 0
        return V(tgt,facing|mode)
    if p=="lever": return V("minecraft:lever",lever_meta(props))
    if p in {"stone_button","polished_blackstone_button"}: return V("minecraft:stone_button",button_meta(props),"exact" if p=="stone_button" else "approximate")
    if p in {"stone_pressure_plate","polished_blackstone_pressure_plate"}: return V("minecraft:stone_pressure_plate",0,"exact" if p=="stone_pressure_plate" else "approximate")
    if p=="heavy_weighted_pressure_plate": return V("minecraft:heavy_weighted_pressure_plate",0)
    if p=="light_weighted_pressure_plate": return V("minecraft:light_weighted_pressure_plate",0)
    if p=="rail": return V("minecraft:rail",rail_meta(props,False))
    if p=="powered_rail": return V("minecraft:golden_rail",rail_meta(props,True))
    if p=="detector_rail": return V("minecraft:detector_rail",rail_meta(props,True))
    if p=="tripwire": return V("minecraft:tripwire",0)
    if p=="tripwire_hook": return V("minecraft:tripwire_hook",tripwire_hook_meta(props))
    if p=="observer": return V("minecraft:dispenser",piston_meta(props)&7,"approximate","Observer did not exist; directional dispenser shell used")
    if p in {"chain_command_block","repeating_command_block"}: return V("minecraft:command_block",0,"approximate")
    if p=="command_block": return V("minecraft:command_block",0)

    # Plants/crops.
    if p=="dandelion": return V("minecraft:yellow_flower",0)
    if p in FLOWER_META: return V("minecraft:red_flower",FLOWER_META[p])
    if p=="cornflower": return V("minecraft:red_flower",3,"approximate")
    if p=="lily_of_the_valley": return V("minecraft:red_flower",6,"approximate")
    if p=="wither_rose": return V("minecraft:red_flower",0,"approximate")
    if p=="torchflower": return V("minecraft:red_flower",5,"approximate")
    if p in DOUBLE_PLANT_META: return V("minecraft:double_plant",DOUBLE_PLANT_META[p] | (8 if props.get("half")=="upper" else 0))
    if p=="grass": return V("minecraft:tallgrass",1)
    if p=="fern": return V("minecraft:tallgrass",2)
    if p=="dead_bush": return V("minecraft:deadbush",0)
    if p=="bamboo" or p=="bamboo_sapling": return V("minecraft:reeds",0,"approximate")
    if p in {"azalea","flowering_azalea","mangrove_propagule"}: return V("minecraft:sapling",0,"approximate")
    if p in {"azalea_leaves","flowering_azalea_leaves"}: return V("minecraft:leaves",4,"approximate")
    if p in {"big_dripleaf","big_dripleaf_stem","small_dripleaf","spore_blossom","hanging_roots","pink_petals","sweet_berry_bush"}: return V("minecraft:tallgrass",1,"approximate")
    if p in {"cave_vines","cave_vines_plant","twisting_vines","twisting_vines_plant","nether_sprouts","crimson_roots","warped_roots"}: return V("minecraft:vine",0,"approximate")
    if p in {"kelp","kelp_plant","seagrass","tall_seagrass","sea_pickle"}: return V("minecraft:water",0,"omitted","Underwater plant has no 1.7.10 equivalent; water preserved")
    if p=="wheat": return V("minecraft:wheat",min(7,int(props.get("age","0"))))
    if p=="carrots": return V("minecraft:carrots",min(7,int(props.get("age","0"))))
    if p=="potatoes": return V("minecraft:potatoes",min(7,int(props.get("age","0"))))
    if p=="beetroots": return V("minecraft:wheat",min(7,int(props.get("age","0"))*2),"approximate")
    if p=="nether_wart": return V("minecraft:nether_wart",min(3,int(props.get("age","0"))))
    if p.startswith("potted_"): return V("minecraft:flower_pot",0,"approximate","Pot contents are not representable without tile-entity conversion")

    # Functional/new workstations: preserve a believable shell, not functionality.
    if p in {"barrel","shulker_box"}: return V("minecraft:chest",0,"approximate")
    if p in {"blast_furnace","smoker"}: return V("minecraft:furnace",furnace_meta(props),"approximate")
    if p in {"cartography_table","smithing_table","loom","stonecutter","lectern","composter","grindstone"}: return V("minecraft:crafting_table",0,"approximate")
    if p=="decorated_pot": return V("minecraft:flower_pot",0,"approximate")
    if p=="bee_nest": return V("minecraft:log",0,"approximate")
    if p=="beehive": return V("minecraft:planks",2,"approximate")
    if p=="honey_block": return V("minecraft:stained_glass",4,"approximate")
    if p=="honeycomb_block": return H("concrete_colored","minecraft:wool",4,4,"approximate")
    if p=="dried_kelp_block": return V("minecraft:wool",13,"approximate")
    if p=="bone_block": return V("minecraft:quartz_block",2,"approximate")
    if p in {"raw_iron_block","raw_gold_block","raw_copper_block"}: return V("minecraft:"+("iron_block" if p=="raw_iron_block" else "gold_block" if p=="raw_gold_block" else "stained_hardened_clay"),0 if p!="raw_copper_block" else 1,"approximate")
    if p in {"copper_ore","deepslate_copper_ore"}: return V("minecraft:iron_ore",0,"approximate","Copper ore has no vanilla 1.7.10 counterpart; non-HBM resource substitution avoided")
    if p.startswith("deepslate_") and p.endswith("_ore"):
        ore=p[len("deepslate_"):-len("_ore")]
        tgt={"coal":"coal_ore","iron":"iron_ore","gold":"gold_ore","diamond":"diamond_ore","emerald":"emerald_ore","lapis":"lapis_ore","redstone":"redstone_ore"}.get(ore,"stone")
        return V("minecraft:"+tgt,0,"approximate")
    if p in {"netherite_block","ancient_debris"}: return H("steel_wall","minecraft:obsidian",0,0,"approximate","Dark industrial material used without adding valuable HBM resource blocks")
    if "copper" in p:
        # Patinated copper uses colour-only safe materials to avoid turning decorative city blocks into valuable HBM copper.
        green=("oxidized" in p or "weathered" in p)
        if p.endswith("_stairs"): return H("concrete_stairs","minecraft:brick_stairs",stair_meta(props),stair_meta(props),"approximate")
        if p.endswith("_slab"): return V("minecraft:stone_slab",slab_meta(0,props),"approximate")
        return H("concrete_colored","minecraft:stained_hardened_clay",9 if green else 1,9 if green else 1,"approximate","Copper colour approximated without resource-block exploit")

    # Exotic modern blocks with deliberately safe visual approximations.
    if p in {"amethyst_block","budding_amethyst"}: return H("concrete_colored","minecraft:stained_hardened_clay",10,10,"approximate","Amethyst has no true 1.7.10/HBM architectural equivalent")
    if "amethyst" in p and ("bud" in p or "cluster" in p): return V("minecraft:stained_glass",10,"approximate","Crystal geometry unavailable")
    if p in {"sculk","sculk_catalyst","sculk_sensor","calibrated_sculk_sensor","sculk_shrieker"}: return H("asphalt","minecraft:wool",0,15,"approximate","Sculk mechanics/texture unavailable")
    if p=="sculk_vein": return V("minecraft:air",0,"omitted")
    if p in {"chorus_flower"}: return V("minecraft:red_flower",2,"approximate")
    if p=="nether_wart_block" or p=="warped_wart_block": return V("minecraft:wool",14 if p=="nether_wart_block" else 9,"approximate")
    if p=="soul_soil": return V("minecraft:soul_sand",0,"approximate")
    if p=="magma_block": return H("basalt","minecraft:netherrack",0,0,"approximate")
    if p=="blue_ice": return V("minecraft:packed_ice",0,"approximate")
    if p=="target": return V("minecraft:wool",0,"approximate")
    if p=="lodestone": return H("steel_wall","minecraft:iron_block",0,0,"approximate")
    if p=="respawn_anchor": return V("minecraft:obsidian",0,"approximate")
    if p=="conduit": return H("lantern","minecraft:glowstone",0,0,"approximate")
    if p=="infested_stone": return V("minecraft:monster_egg",0)
    if p=="infested_stone_bricks": return V("minecraft:monster_egg",2)
    if p=="spawner": return V("minecraft:mob_spawner",0)
    if p=="wet_sponge": return V("minecraft:sponge",0,"approximate","Wet sponge metadata is unavailable")

    # Fallback keeps a visible marker instead of silently deleting unknown solids.
    return H("concrete_smooth","minecraft:stone",0,0,"approximate","No dedicated mapping rule; neutral structural fallback")


def door_bed_meta(props):
    # Bed foot/head + direction; 1.7.10 has no colour state.
    facing={"south":0,"west":1,"north":2,"east":3}.get(props.get("facing"),0)
    if props.get("part")=="head": facing|=8
    if boolprop(props,"occupied"): facing|=4
    return facing

def button_meta(props):
    m={"east":1,"west":2,"south":3,"north":4}.get(props.get("facing"),1)
    if boolprop(props,"powered"): m|=8
    return m

def lever_meta(props):
    # Wall placement is the common case; floor/ceiling are approximated.
    face=props.get("face","wall"); f=props.get("facing","north")
    if face=="wall": m={"east":1,"west":2,"south":3,"north":4}.get(f,4)
    elif face=="floor": m=5 if f in {"north","south"} else 6
    else: m=0 if f in {"north","south"} else 7
    if boolprop(props,"powered"): m|=8
    return m

def piston_meta(props):
    m={"down":0,"up":1,"north":2,"south":3,"west":4,"east":5}.get(props.get("facing"),2)
    if boolprop(props,"extended"): m|=8
    return m

def tripwire_hook_meta(props):
    m={"south":0,"west":1,"north":2,"east":3}.get(props.get("facing"),0)
    if boolprop(props,"attached"): m|=4
    if boolprop(props,"powered"): m|=8
    return m

def furnace_meta(props): return {"north":2,"south":3,"west":4,"east":5}.get(props.get("facing"),2)


def resolve_mapping(mapping: Mapping, reg: TargetRegistry):
    target=mapping.target
    if target.startswith("hbm:"):
        logical=target.split(":",1)[1]
        h=reg.resolve_hbm(logical)
        if h: return h[1], mapping.meta, h[0]
        # Should generally have been handled by H(), but retain fail-safe.
        raise ConversionError("Requested HBM mapping %s is missing in target registry" % logical)
    rid=reg.resolve(target)
    if rid is None:
        # A few old Forge registry names differ from modern naming. Aliases below.
        aliases={
            "minecraft:redstone_lamp":"minecraft:redstone_lamp",
            "minecraft:lit_redstone_lamp":"minecraft:lit_redstone_lamp",
            "minecraft:unlit_redstone_torch":"minecraft:unlit_redstone_torch",
            "minecraft:redstone_torch":"minecraft:redstone_torch",
        }
        rid=reg.resolve(aliases.get(target,target))
    if rid is None: raise ConversionError("Target 1.7.10 registry does not contain %s" % target)
    return rid,mapping.meta,target


def _palette_signature(name, props):
    return (str(name), tuple(sorted((str(k), str(v)) for k,v in (props or {}).items())))


def preflight_source_mappings(
    regions,
    reg: TargetRegistry,
    use_hbm=True,
    y_offset=0,
    log=print,
    mapping_profile: MappingProfile | None = None,
):
    """Resolve every in-range unique source palette mapping before output exists.

    The preflight also estimates vertical cropping and records which reviewed mod
    targets would be used. Palette signatures are resolved once, so repeated
    stone/air palettes across thousands of chunks do not repeat mapping work.
    """
    unique=set(); chunks=0; dvs=collections.Counter()
    source_ymin=999; source_ymax=-999; inrange_ymin=999; inrange_ymax=-999
    unresolved={}; parse_failures=[]; mapped_targets=collections.Counter()
    mapping_quality=collections.defaultdict(set); mod_targets=collections.Counter()
    mapping_cache={}; mapping_impact=collections.Counter(); quality_occurrences=collections.Counter()
    unavailable_provider_impact=collections.Counter()
    source_occurrences=collections.Counter(); block_occurrences_total=0
    stateful_occurrences_total=0; stateful_preserved_occurrences=0
    crop_high_chunks=0; crop_low_chunks=0
    block_entity_types=collections.Counter()
    property_states=0; property_keys=collections.Counter()

    for ri,rp in enumerate(regions,1):
        if ri == 1 or ri == len(regions) or ri % 5 == 0:
            log("Preflight [%d/%d] %s" % (ri,len(regions),rp.name))
        try:
            iterator=RegionReader(rp).chunks()
            for idx,raw in iterator:
                try:
                    c=parse_modern_chunk(raw)
                    chunks+=1; dvs[str(c.get("DataVersion"))]+=1
                    for be in c.get("block_entities") or []:
                        if isinstance(be,dict):
                            block_entity_types[str(be.get("id") or "<unknown>")]+=1
                        else:
                            block_entity_types["<malformed>"]+=1
                    chunk_high=False; chunk_low=False
                    for section in c["sections"]:
                        sy=section.get("Y"); palette=section.get("palette") or []
                        if sy is None or not palette: continue
                        source_ymin=min(source_ymin,sy); source_ymax=max(source_ymax,sy)
                        target_base=sy*16+y_offset
                        has_non_air=any(name not in AIR_NAMES for name,_ in palette)
                        if target_base>255 or target_base+15<0:
                            if has_non_air:
                                if target_base>255: chunk_high=True
                                else: chunk_low=True
                            continue

                        inrange_ymin=min(inrange_ymin,sy); inrange_ymax=max(inrange_ymax,sy)
                        inds=unpack_palette_indices(section.get("data"),len(palette),4096,4)
                        occurrence_counts=np.bincount(inds,minlength=len(palette)) if len(palette) else np.zeros(0,dtype=np.int64)
                        for palette_index,(name,props) in enumerate(palette):
                            sig=_palette_signature(name,props)
                            if sig not in unique:
                                unique.add(sig)
                                if props:
                                    property_states+=1
                                    for prop_key in props:
                                        property_keys[str(prop_key)]+=1
                                try:
                                    mapping=map_modern(name,props,reg,use_hbm,mapping_profile)
                                    _,_,resolved=resolve_mapping(mapping,reg)
                                    mapping_cache[sig]=(mapping,resolved)
                                    mapped_targets[resolved]+=1
                                    mapping_quality[mapping.quality].add(str(name))
                                    if ":" in resolved and not resolved.lower().startswith("minecraft:"):
                                        mod_targets[resolved]+=1
                                except Exception as exc:
                                    unresolved.setdefault(str(exc),[]).append({
                                        "source":str(name), "properties":dict(props or {}),
                                        "region":rp.name, "chunk_index":idx,
                                    })
                            cached=mapping_cache.get(sig)
                            if cached is not None:
                                mapping,resolved=cached
                                count=int(occurrence_counts[palette_index]) if palette_index < len(occurrence_counts) else 0
                                if count and str(name) not in AIR_NAMES:
                                    block_occurrences_total+=count
                                    source_occurrences[str(name)]+=count
                                    quality_occurrences[mapping.quality]+=count
                                    if props:
                                        stateful_occurrences_total+=count
                                        if mapping.quality in {"exact","backport_exact"}:
                                            stateful_preserved_occurrences+=count
                                    mapping_impact[(str(name),str(resolved),str(mapping.quality),str(mapping.note or ""))]+=count
                                    if mapping_profile is not None and mapping_profile.allow_safe_mod_replacements and mapping.quality not in {"backport_exact","backport_close"}:
                                        source_name=str(name)
                                        vanilla_name=source_name if ":" in source_name else "minecraft:"+source_name
                                        if vanilla_name.lower().startswith("minecraft:") and reg.resolve(vanilla_name) is None:
                                            candidates=[c for c in mapping_profile.backport_candidates(vanilla_name) if mapping_profile.allows_namespace(c.target_name.split(":",1)[0] if ":" in c.target_name else "")]
                                            if candidates and not any(reg.resolve(c.target_name) is not None for c in candidates):
                                                targets=" | ".join(c.target_name for c in candidates)
                                                providers=" | ".join(c.provider or c.target_name.split(":",1)[0] for c in candidates)
                                                unavailable_provider_impact[(source_name,targets,providers)]+=count
                    if chunk_high: crop_high_chunks+=1
                    if chunk_low: crop_low_chunks+=1
                except Exception as exc:
                    if len(parse_failures)<20:
                        parse_failures.append({"region":rp.name,"chunk_index":idx,"error":str(exc)})
        except Exception as exc:
            if len(parse_failures)<20:
                parse_failures.append({"region":rp.name,"error":str(exc)})

    if chunks == 0:
        raise ConversionError("Source preflight found no readable chunks")
    if parse_failures:
        sample="; ".join("%s chunk %s: %s" % (x.get("region"),x.get("chunk_index","?"),x.get("error")) for x in parse_failures[:5])
        raise ConversionError(
            "Source preflight could not safely parse one or more chunks. No output world was created. "
            "Examples: %s" % sample
        )
    if unresolved:
        details=[]
        for error,examples in list(unresolved.items())[:8]:
            sources=", ".join(sorted({e["source"] for e in examples[:8]}))
            details.append("%s (source: %s)" % (error,sources))
        raise ConversionError(
            "Source/target mapping preflight found %d unresolved mapping problem(s). "
            "No output world was created. %s" % (len(unresolved),"; ".join(details))
        )

    impact_rows=[]
    for (source_name,target_name,quality,note),count in mapping_impact.most_common():
        if quality in {"exact","backport_exact"}:
            continue
        impact_rows.append({
            "source":source_name, "target":target_name, "quality":quality,
            "count":int(count), "note":note,
        })
        if len(impact_rows)>=40:
            break
    unavailable_provider_rows=[
        {"source":source,"candidate_targets":targets.split(" | "),"providers":providers.split(" | "),"count":int(count)}
        for (source,targets,providers),count in unavailable_provider_impact.most_common(40)
    ]
    quality_percent={
        quality:(100.0*int(count)/block_occurrences_total if block_occurrences_total else 0.0)
        for quality,count in quality_occurrences.items()
    }

    info={
        "chunks":chunks,
        "unique_palette_states":len(unique),
        "data_versions":dict(dvs),
        "source_section_y_min":None if source_ymin==999 else source_ymin,
        "source_section_y_max":None if source_ymax==-999 else source_ymax,
        "in_range_section_y_min":None if inrange_ymin==999 else inrange_ymin,
        "in_range_section_y_max":None if inrange_ymax==-999 else inrange_ymax,
        # Preserve the old field names for callers/reports that used them.
        "section_y_min":None if source_ymin==999 else source_ymin,
        "section_y_max":None if source_ymax==-999 else source_ymax,
        "potential_chunks_cropped_above_255":crop_high_chunks,
        "potential_chunks_cropped_below_0":crop_low_chunks,
        "resolved_target_names":len(mapped_targets),
        "mapping_quality":{k:sorted(v) for k,v in mapping_quality.items()},
        "safe_mod_target_names":sorted(mod_targets),
        "safe_mod_target_count":len(mod_targets),
        "content_policy":CONTENT_POLICY,
        "block_entities_total":sum(block_entity_types.values()),
        "block_entity_types":dict(block_entity_types),
        "lighting_strategy":LIGHTING_STRATEGY,
        "heightmap_strategy":HEIGHTMAP_STRATEGY,
        "block_property_strategy":BLOCK_PROPERTY_STRATEGY,
        "target_relight_required":True,
        "unique_palette_states_with_properties":property_states,
        "property_keys_seen":dict(property_keys),
        "block_occurrences_total":int(block_occurrences_total),
        "stateful_block_occurrences_total":int(stateful_occurrences_total),
        "stateful_block_occurrences_preserved":int(stateful_preserved_occurrences),
        "stateful_block_occurrences_partial":int(max(0,stateful_occurrences_total-stateful_preserved_occurrences)),
        "stateful_block_fidelity_percent":(
            100.0*stateful_preserved_occurrences/stateful_occurrences_total if stateful_occurrences_total else 100.0
        ),
        "mapping_quality_block_occurrences":dict(quality_occurrences),
        "mapping_quality_percent":quality_percent,
        "top_non_exact_mappings":impact_rows,
        "unavailable_backport_candidates":unavailable_provider_rows,
        "top_source_block_occurrences":[
            {"source":name,"count":int(count)} for name,count in source_occurrences.most_common(40)
        ],
    }
    log(
        "Preflight passed: %d chunks; %d unique in-range palette states; DataVersion(s): %s"
        % (chunks,len(unique),", ".join(sorted(dvs)) or "unknown")
    )
    if crop_high_chunks or crop_low_chunks:
        log(
            "Preflight vertical-range notice: %d chunk(s) may crop above Y=255; %d chunk(s) may crop below Y=0."
            % (crop_high_chunks,crop_low_chunks)
        )
    if mod_targets:
        log(
            "Preflight safe-mod usage: %d configured target block name(s) from enabled catalog namespaces."
            % len(mod_targets)
        )
    if block_occurrences_total:
        ordered=("backport_exact","exact","backport_close","close","approximate","omitted")
        parts=[]
        for quality in ordered:
            if quality_occurrences.get(quality):
                parts.append("%s %.2f%%"%(quality,quality_percent.get(quality,0.0)))
        if parts:
            log("Preflight mapping impact by placed blocks: "+"; ".join(parts))
        if stateful_occurrences_total:
            log(
                "Preflight state fidelity: %.2f%% of placed stateful blocks have a verified legacy/EFR state encoding (%d/%d)."
                % (100.0*stateful_preserved_occurrences/stateful_occurrences_total,stateful_preserved_occurrences,stateful_occurrences_total)
            )
        if impact_rows:
            sample=", ".join("%s -> %s (%s, %d)"%(row["source"],row["target"],row["quality"],row["count"]) for row in impact_rows[:8])
            log("Highest-impact non-exact mappings: "+sample)
        if unavailable_provider_rows:
            sample=", ".join(
                "%s -> %s absent from target registry (%d)"%(row["source"],"/".join(row["candidate_targets"]),row["count"])
                for row in unavailable_provider_rows[:8]
            )
            log("Backport candidates present in catalogs but unavailable in the selected template: "+sample)
    return info

# ---------- Legacy NBT writer ----------
def nbt_name(name):
    b=name.encode("utf-8"); return struct.pack(">H",len(b))+b

def tag(t,name,payload): return bytes([t])+nbt_name(name)+payload

def p_byte(v): return struct.pack(">b", ((int(v)+128)%256)-128)
def p_short(v): return struct.pack(">h", ((int(v)+32768)%65536)-32768)
def p_int(v): return struct.pack(">i",int(v))
def p_long(v): return struct.pack(">q",int(v))
def p_string(v):
    b=str(v).encode("utf-8")
    return struct.pack(">H",len(b))+b
def p_byte_array(b): return struct.pack(">i",len(b))+bytes(b)
def p_int_array(vals): return struct.pack(">i",len(vals))+b"".join(struct.pack(">i",int(v)) for v in vals)
def p_list(et,payloads): return bytes([et])+struct.pack(">i",len(payloads))+b"".join(payloads)
def p_compound(named_tags): return b"".join(named_tags)+b"\x00"


def _legacy_tile_entity_base(te_id,x,y,z,extra_tags=()):
    return p_compound([
        tag(8,"id",p_string(te_id)),
        tag(3,"x",p_int(x)), tag(3,"y",p_int(y)), tag(3,"z",p_int(z)),
        *list(extra_tags),
    ])


def _etfuturum_state_tile_entity(target_name, _source_path, props, x, y, z, reg=None):
    """Build minimal EFR TEs required for imported blockstate fidelity.

    These are intentionally *state* tile entities, not a claim that arbitrary
    modern inventories/entities have been translated. Source block entities are
    still audited and reported separately. The generated compounds are limited
    to EFR contracts verified in the attached 1.7.10 source.
    """
    target=str(target_name).lower()
    source_path=str(_source_path).lower()
    if target == "etfuturum:banner":
        base=0
        for color,cmeta in COLOR_META.items():
            if source_path in {color+"_banner",color+"_wall_banner"}:
                base=cmeta
                break
        return _legacy_tile_entity_base(
            "etfuturum.banner",x,y,z,[
                tag(3,"Base",p_int(base)),
                tag(1,"IsStanding",p_byte(0 if source_path.endswith("_wall_banner") else 1)),
            ]
        )
    if target == "etfuturum:shulker_box":
        color=0
        for dye,cmeta in COLOR_META.items():
            if source_path == dye+"_shulker_box":
                color=cmeta+1
                break
        return _legacy_tile_entity_base(
            "etfuturum.shulker_box",x,y,z,[
                tag(1,"Type",p_byte(0)),tag(9,"Items",p_list(10,[])),
                tag(1,"Color",p_byte(color)),tag(1,"Facing",p_byte(direction_meta(props or {},"facing","down"))),
            ]
        )
    if target == "etfuturum:cave_vine":
        # EFR's head block requires a non-updating state TE. Modern age is not a
        # one-to-one match for its random maximum growth length, so use a stable
        # permissive value while preserving visible berries in metadata.
        return _legacy_tile_entity_base(
            "etfuturum.cave_vines",x,y,z,[tag(3,"MaxLength",p_int(27)),tag(1,"TipSheared",p_byte(0))]
        )
    if target == "minecraft:flower_pot" and source_path in FLOWER_POT_CONTENTS and reg is not None:
        content=flower_pot_content(source_path,reg)
        if content is not None:
            item_id,data,_item_name=content
            return _legacy_tile_entity_base(
                "FlowerPot",x,y,z,[tag(3,"Item",p_int(item_id)),tag(3,"Data",p_int(data))]
            )
    if target == "etfuturum:glow_lichen":
        mask=_glow_lichen_state_mask(props or {})
        if not mask:
            return None
        return _legacy_tile_entity_base(
            "etfuturum.glow_lichen",x,y,z,[tag(3,"State",p_int(mask))]
        )
    if target in {"etfuturum:beehive","etfuturum:bee_nest"}:
        honey=intprop(props or {},"honey_level",0,0,5)
        extra=[tag(9,"Bees",p_list(10,[]))]
        if honey:
            extra.append(tag(3,"honeyLevel",p_int(honey)))
        return _legacy_tile_entity_base("etfuturum.hive",x,y,z,extra)
    if target == "etfuturum:barrel":
        return _legacy_tile_entity_base(
            "etfuturum.barrel",x,y,z,[tag(1,"Type",p_byte(0)),tag(9,"Items",p_list(10,[]))]
        )
    if target in {"etfuturum:blast_furnace","etfuturum:lit_blast_furnace"}:
        return _legacy_tile_entity_base(
            "etfuturum.blast_furnace",x,y,z,[
                tag(2,"BurnTime",p_short(0)),tag(2,"CookTime",p_short(0)),tag(9,"Items",p_list(10,[])),
            ]
        )
    if target in {"etfuturum:smoker","etfuturum:lit_smoker"}:
        return _legacy_tile_entity_base(
            "etfuturum.smoker",x,y,z,[
                tag(2,"BurnTime",p_short(0)),tag(2,"CookTime",p_short(0)),tag(9,"Items",p_list(10,[])),
            ]
        )
    if target in {"etfuturum:campfire","etfuturum:soul_campfire"}:
        return _legacy_tile_entity_base(
            "etfuturum:modern_parity_campfire",x,y,z,[tag(9,"CookingItems",p_list(10,[]))]
        )
    if target.startswith("etfuturum:") and target.endswith("copper_chest"):
        return _legacy_tile_entity_base(
            "etfuturum:modern_parity_copper_chest",x,y,z,[tag(9,"Items",p_list(10,[]))]
        )
    return None

def make_section_nbt(y, ids, metas, skylight):
    ids=np.asarray(ids,dtype=np.uint16).reshape(4096)
    low=(ids & 255).astype(np.uint8).tobytes()
    high=((ids >> 8) & 15).astype(np.uint8)
    data=pack_nibbles(np.asarray(metas,dtype=np.uint8).reshape(4096)&15)
    blocklight=bytes(2048)
    tags=[tag(1,"Y",p_byte(y)),tag(7,"Blocks",p_byte_array(low)),tag(7,"Data",p_byte_array(data)),
          tag(7,"BlockLight",p_byte_array(blocklight)),tag(7,"SkyLight",p_byte_array(skylight))]
    if np.any(high): tags.append(tag(7,"Add",p_byte_array(pack_nibbles(high))))
    return p_compound(tags)

def make_chunk_nbt(cx,cz,last_update,sections,heightmap,biomes,tile_entities=None):
    sec_payload=[s for s in sections]
    level_tags=[
        tag(3,"xPos",p_int(cx)), tag(3,"zPos",p_int(cz)), tag(4,"LastUpdate",p_long(last_update or 0)),
        tag(7,"Biomes",p_byte_array(bytes(int(x)&255 for x in biomes))),
        tag(11,"HeightMap",p_int_array([int(x) for x in heightmap])),
        tag(1,"TerrainPopulated",p_byte(1)), tag(1,"LightPopulated",p_byte(0)), tag(1,"V",p_byte(1)),
        tag(4,"InhabitedTime",p_long(0)),
        tag(9,"Sections",p_list(10,sec_payload)),
        tag(9,"Entities",p_list(10,[])), tag(9,"TileEntities",p_list(10,tile_entities or [])),
    ]
    root_payload=p_compound([tag(10,"Level",p_compound(level_tags))])
    return bytes([10])+nbt_name("")+root_payload


class RegionReader:
    def __init__(self,path): self.path=Path(path)
    def chunks(self):
        if self.path.stat().st_size < 8192: return
        with self.path.open("rb") as f:
            hdr=f.read(8192)
            for idx in range(1024):
                v=int.from_bytes(hdr[idx*4:idx*4+4],"big"); off=v>>8; sectors=v&255
                if not off: continue
                f.seek(off*4096); lb=f.read(4)
                if len(lb)!=4: continue
                length=struct.unpack(">I",lb)[0]
                c=f.read(1)[0]; data=f.read(length-1)
                if c==2: raw=zlib.decompress(data)
                elif c==1: raw=gzip.decompress(data)
                elif c==3: raw=data
                else: raise ConversionError("Unsupported chunk compression %d in %s"%(c,self.path.name))
                yield idx,raw


def write_region(path, chunks_by_index):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    loc=bytearray(4096); times=bytearray(4096); body=bytearray(); sector=2; now=int(time.time())
    for idx in sorted(chunks_by_index):
        raw=chunks_by_index[idx]
        comp=zlib.compress(raw,6)
        payload=struct.pack(">I",len(comp)+1)+b"\x02"+comp
        sectors=(len(payload)+4095)//4096
        if sectors>255: raise ConversionError("Converted chunk %d in %s exceeds Anvil 255-sector limit"%(idx,path.name))
        if sector>0xFFFFFF: raise ConversionError("Region file sector offset overflow")
        loc[idx*4:idx*4+3]=sector.to_bytes(3,"big"); loc[idx*4+3]=sectors
        times[idx*4:idx*4+4]=struct.pack(">I",now)
        body.extend(payload); body.extend(bytes(sectors*4096-len(payload))); sector+=sectors
    with path.open("wb") as f: f.write(loc); f.write(times); f.write(body)


def validate_legacy_chunk_nbt(raw: bytes, expected_cx=None, expected_cz=None):
    """Validate the structural invariants required by the legacy 1.7.10 writer."""
    try:
        _,root=parse_nbt(raw)
    except Exception as exc:
        raise ConversionError("Generated legacy chunk NBT is unreadable: %s"%exc) from exc
    if not isinstance(root,dict) or not isinstance(root.get("Level"),dict):
        raise ConversionError("Generated legacy chunk is missing the Level compound")
    level=root["Level"]
    cx=level.get("xPos"); cz=level.get("zPos")
    if expected_cx is not None and cx != expected_cx:
        raise ConversionError("Generated legacy chunk xPos mismatch: expected %s, got %s"%(expected_cx,cx))
    if expected_cz is not None and cz != expected_cz:
        raise ConversionError("Generated legacy chunk zPos mismatch: expected %s, got %s"%(expected_cz,cz))

    biomes=level.get("Biomes")
    heightmap=level.get("HeightMap")
    if not isinstance(biomes,(bytes,bytearray)) or len(biomes)!=256:
        raise ConversionError("Generated legacy chunk Biomes array is not exactly 256 bytes")
    if not isinstance(heightmap,list) or len(heightmap)!=256:
        raise ConversionError("Generated legacy chunk HeightMap is not exactly 256 integers")
    if level.get("LightPopulated") != 0:
        raise ConversionError("Generated legacy chunk must keep LightPopulated=0 for target relight")

    sections=level.get("Sections",[])
    if not isinstance(sections,list):
        raise ConversionError("Generated legacy chunk Sections tag is not a list")
    seen_y=set()
    for sec in sections:
        if not isinstance(sec,dict):
            raise ConversionError("Generated legacy chunk contains a non-compound section")
        sy=sec.get("Y")
        if not isinstance(sy,int) or not (0<=sy<=15) or sy in seen_y:
            raise ConversionError("Generated legacy chunk has invalid/duplicate section Y=%r"%sy)
        seen_y.add(sy)
        expected_lengths={"Blocks":4096,"Data":2048,"BlockLight":2048,"SkyLight":2048}
        for key,length in expected_lengths.items():
            value=sec.get(key)
            if not isinstance(value,(bytes,bytearray)) or len(value)!=length:
                raise ConversionError("Generated section Y=%d has invalid %s length"%(sy,key))
        if "Add" in sec:
            add=sec["Add"]
            if not isinstance(add,(bytes,bytearray)) or len(add)!=2048:
                raise ConversionError("Generated section Y=%d has invalid Add length"%sy)

    if not isinstance(level.get("Entities",[]),list):
        raise ConversionError("Generated legacy chunk Entities tag is not a list")
    if not isinstance(level.get("TileEntities",[]),list):
        raise ConversionError("Generated legacy chunk TileEntities tag is not a list")
    return {"xPos":cx,"zPos":cz,"sections":len(sections)}


def verify_written_region(path: Path, expected_chunks):
    """Round-trip the just-written Anvil region and validate every promoted chunk."""
    expected=set(int(x) for x in expected_chunks)
    seen=set()
    for idx,raw in RegionReader(path).chunks():
        if idx not in expected:
            raise ConversionError("Output region %s contains unexpected chunk index %d"%(Path(path).name,idx))
        info=validate_legacy_chunk_nbt(raw)
        local=(int(info["xPos"])&31)+((int(info["zPos"])&31)*32)
        if local != idx:
            raise ConversionError(
                "Output region %s chunk index %d does not match xPos/zPos-derived index %d"
                %(Path(path).name,idx,local)
            )
        seen.add(idx)
    missing=expected-seen
    if missing:
        raise ConversionError(
            "Output region %s is missing %d written chunk(s): %s"
            %(Path(path).name,len(missing),", ".join(str(x) for x in sorted(missing)[:12]))
        )
    return len(seen)


def choose_biomes(sections):
    # Prefer a section near old sea level; fallback to the nearest section with biome data.
    candidates=[s for s in sections if s.get("biome_palette")]
    if not candidates: return [1]*256
    sec=min(candidates,key=lambda s:abs((s.get("Y") or 0)-4))
    pal=sec["biome_palette"]; dat=sec.get("biome_data")
    inds=unpack_palette_indices(dat,len(pal),64,1)
    out=[]
    # Sample biome y-cell 0 around section base; x/z are 4-block resolution.
    for z in range(16):
        for x in range(16):
            idx=(0*16)+((z>>2)*4)+(x>>2)
            pi=int(inds[idx]) if idx<len(inds) else 0
            b=pal[pi] if 0<=pi<len(pal) else "minecraft:plains"
            out.append(MODERN_BIOME_TO_1710.get(b,1))
    return out


def convert_chunk(raw, reg, use_hbm, y_offset, strip_below_y, stats, mapping_profile: MappingProfile | None = None):
    c=parse_modern_chunk(raw)
    cx,cz=c["xPos"],c["zPos"]
    if cx is None or cz is None: raise ConversionError("Chunk missing xPos/zPos")
    dv=c.get("DataVersion")
    stats["data_versions"][str(dv)]+=1
    for be in c.get("block_entities") or []:
        stats["block_entities_omitted"]+=1
        if isinstance(be,dict):
            stats["block_entity_types_omitted"][str(be.get("id") or "<unknown>")]+=1
        else:
            stats["block_entity_types_omitted"]["<malformed>"]+=1
    height=np.zeros((16,16),dtype=np.int32)
    converted=[]
    state_tile_entities=[]
    sec_by_target={}
    high_crop=False; low_crop=False

    # First pass: convert every source section that intersects legacy 0..255 after offset.
    for s in c["sections"]:
        sy=s.get("Y")
        pal=s.get("palette") or []
        if sy is None or not pal: continue
        target_base=sy*16+y_offset
        if target_base>255 or target_base+15<0:
            if any(n not in AIR_NAMES for n,_ in pal):
                if target_base>255: high_crop=True
                else: low_crop=True
            continue
        # Offset must be section-aligned for direct section conversion.
        if y_offset % 16 != 0:
            raise ConversionError("Vertical offset must be a multiple of 16 in this build")
        ty=sy+(y_offset//16)
        if not (0<=ty<=15): continue
        inds=unpack_palette_indices(s.get("data"),len(pal),4096,4)
        mids=[]; mmeta=[]; state_te_specs={}
        for palette_index,(name,props) in enumerate(pal):
            mp=map_modern(name,props,reg,use_hbm,mapping_profile)
            rid,meta,resolved=resolve_mapping(mp,reg)
            mids.append(rid); mmeta.append(meta)
            resolved_lower=str(resolved).lower()
            if resolved_lower in {
                "etfuturum:glow_lichen","etfuturum:beehive","etfuturum:bee_nest",
                "etfuturum:barrel","etfuturum:blast_furnace","etfuturum:lit_blast_furnace",
                "etfuturum:smoker","etfuturum:lit_smoker","etfuturum:campfire","etfuturum:soul_campfire",
                "etfuturum:banner","etfuturum:shulker_box","etfuturum:cave_vine",
            } or (resolved_lower == "minecraft:flower_pot" and str(name).split(":",1)[-1].lower() in FLOWER_POT_CONTENTS) \
               or (resolved_lower.startswith("etfuturum:") and resolved_lower.endswith("copper_chest")):
                state_te_specs[palette_index]=(str(name).split(":",1)[-1],dict(props or {}),str(resolved))
            key=name
            stats["palette_seen"][key]+=1
            stats["mapping_quality"][mp.quality].add(key)
            if mp.quality in {"approximate","omitted"}:
                stats["mapping_notes"].setdefault(key,{"target":resolved,"meta":meta,"quality":mp.quality,"note":mp.note})
        ids=np.asarray(mids,dtype=np.uint16)[inds]
        metas=np.asarray(mmeta,dtype=np.uint8)[inds]
        # Optional underground stripping: fill with stone, leaving build/surface above threshold.
        if strip_below_y>0:
            world_y=np.arange(ty*16,ty*16+16,dtype=np.int32)[:,None,None]
            mask=(world_y < strip_below_y).reshape(16,1,1)
            arr=ids.reshape(16,16,16); md=metas.reshape(16,16,16)
            stone_id=reg.resolve("minecraft:stone")
            if stone_id is None: raise ConversionError("Target registry missing minecraft:stone")
            arr=np.where(mask,stone_id,arr); md=np.where(mask,0,md); ids=arr.reshape(-1); metas=md.reshape(-1)

        # Some EFR state cannot live in four metadata bits at all (glow lichen),
        # while a few EFR container blocks require a minimal TileEntity to exist
        # after a raw Anvil import. Synthesize only those narrowly verified TEs.
        for palette_index,(source_path,props,resolved) in state_te_specs.items():
            flats=np.flatnonzero(inds == palette_index)
            for flat in flats.tolist():
                ly=int(flat)//256
                rem=int(flat)%256
                lz=rem//16
                lx=rem%16
                world_y=ty*16+ly
                if strip_below_y>0 and world_y < strip_below_y:
                    continue
                payload=_etfuturum_state_tile_entity(
                    resolved,source_path,props,cx*16+lx,world_y,cz*16+lz,reg
                )
                if payload is not None:
                    state_tile_entities.append(payload)
                    stats["block_entities_synthesized"]+=1
                    stats["block_entity_types_synthesized"][
                        "etfuturum.glow_lichen" if str(resolved).lower()=="etfuturum:glow_lichen" else str(resolved).lower()
                    ]+=1
        sec_by_target[ty]=(ids,metas)
        # Heightmap uses highest non-air block as a robust map/surface approximation.
        air_id=reg.resolve("minecraft:air")
        a=ids.reshape(16,16,16)
        solid=a != air_id
        for ly in range(16):
            yy=ty*16+ly+1
            m=solid[ly]
            height=np.where(m,np.maximum(height,yy),height)

    if high_crop: stats["chunks_cropped_above_255"]+=1
    if low_crop: stats["chunks_cropped_below_0"]+=1

    # Build skylight after height is known.
    for ty in sorted(sec_by_target):
        ids,metas=sec_by_target[ty]
        yvals=np.arange(ty*16,ty*16+16,dtype=np.int32)[:,None,None]
        sky=np.where(yvals>=height[None,:,:],15,0).astype(np.uint8).reshape(-1)
        converted.append(make_section_nbt(ty,ids,metas,pack_nibbles(sky)))

    biomes=choose_biomes(c["sections"])
    legacy=make_chunk_nbt(
        cx,cz,c.get("LastUpdate",0),converted,height.reshape(-1).tolist(),biomes,
        tile_entities=state_tile_entities,
    )
    validate_legacy_chunk_nbt(legacy,cx,cz)
    return (cx,cz),legacy


def discover_source_regions(source: Path, tempdir: Path):
    source=source.resolve()
    if source.is_file() and source.suffix.lower()==".mca":
        out=tempdir/"source_region"; out.mkdir(parents=True,exist_ok=True)
        shutil.copy2(source, out/source.name)
        return out
    if source.is_file() and source.suffix.lower()==".zip":
        out=tempdir/"source_region"; out.mkdir(parents=True,exist_ok=True)
        with zipfile.ZipFile(source) as z:
            found=0
            for info in z.infolist():
                if info.is_dir() or not info.filename.lower().endswith(".mca"): continue
                if "/region/" in ("/"+info.filename).lower() or info.filename.lower().startswith("region/") or re.match(r"(^|.*/)r\.-?\d+\.-?\d+\.mca$",info.filename):
                    dest=out/Path(info.filename).name
                    with z.open(info) as fi, dest.open("wb") as fo: shutil.copyfileobj(fi,fo,1024*1024)
                    found+=1
            if not found: raise ConversionError("ZIP contains no .mca region files")
        return out
    if source.is_dir():
        if (source/"region").is_dir(): return source/"region"
        if any(source.glob("r.*.*.mca")): return source
    raise ConversionError("Source must be a modern world folder, region folder, .mca file, or ZIP containing region/*.mca")


def discover_source_entity_regions(source: Path, tempdir: Path):
    """Return a modern entity-region directory when the supplied source exposes one.

    Modern Java worlds store non-block entities separately from terrain region
    files. Region-only inputs cannot prove whether such entity data exists, so
    callers receive None and must report the audit as unavailable instead of
    silently assuming zero entities.
    """
    source=Path(source).resolve()
    if source.is_file() and source.suffix.lower()==".zip":
        out=tempdir/"source_entities"; out.mkdir(parents=True,exist_ok=True)
        found=0
        with zipfile.ZipFile(source) as z:
            for info in z.infolist():
                low=("/"+info.filename).lower()
                if info.is_dir() or not low.endswith(".mca"):
                    continue
                if "/entities/" not in low:
                    continue
                name=Path(info.filename).name
                if not re.match(r"r\.-?\d+\.-?\d+\.mca$",name):
                    continue
                with z.open(info) as fi, (out/name).open("wb") as fo:
                    shutil.copyfileobj(fi,fo,1024*1024)
                found+=1
        return out if found else None

    if source.is_dir():
        if (source/"entities").is_dir():
            return source/"entities"
        if source.name.lower()=="region" and (source.parent/"entities").is_dir():
            return source.parent/"entities"
        return None

    if source.is_file() and source.suffix.lower()==".mca":
        if source.parent.name.lower()=="region" and (source.parent.parent/"entities").is_dir():
            return source.parent.parent/"entities"
    return None


def audit_source_entities(source: Path, tempdir: Path, log=print):
    entity_dir=discover_source_entity_regions(Path(source),tempdir)
    if entity_dir is None:
        return {
            "scan_status":"unavailable",
            "entities_total":None,
            "entity_types":{},
            "entity_regions":0,
            "note":"No modern entities/ region was available from this source input; entity loss cannot be quantified.",
        }

    counts=collections.Counter(); chunks=0; regions=0; failures=[]
    for rp in sorted(entity_dir.glob("r.*.*.mca")):
        if not rp.is_file() or rp.stat().st_size<8192:
            continue
        regions+=1
        try:
            for idx,raw in RegionReader(rp).chunks():
                chunks+=1
                try:
                    _,root=parse_nbt(raw)
                    entities=[]
                    if isinstance(root,dict):
                        entities=root.get("Entities",root.get("entities",[])) or []
                    if not isinstance(entities,list):
                        raise ConversionError("Entity chunk Entities tag is not a list")
                    for ent in entities:
                        if isinstance(ent,dict):
                            counts[str(ent.get("id") or "<unknown>")]+=1
                        else:
                            counts["<malformed>"]+=1
                except Exception as exc:
                    if len(failures)<20:
                        failures.append({"region":rp.name,"chunk_index":idx,"error":str(exc)})
        except Exception as exc:
            if len(failures)<20:
                failures.append({"region":rp.name,"error":str(exc)})

    if failures:
        sample="; ".join(
            "%s chunk %s: %s"%(x.get("region"),x.get("chunk_index","?"),x.get("error"))
            for x in failures[:5]
        )
        raise ConversionError(
            "Modern entity-region audit failed. No output world was created. Examples: %s"%sample
        )

    total=sum(counts.values())
    log("Content audit: %d source entities across %d entity region file(s)."%(total,regions))
    return {
        "scan_status":"scanned",
        "entities_total":total,
        "entity_types":dict(counts),
        "entity_regions":regions,
        "entity_chunks":chunks,
        "note":"Current 1.7.10 backend reports these entities but does not yet translate them.",
    }


def attach_content_audit(preflight: dict, source: Path, tempdir: Path, log=print):
    entity_audit=audit_source_entities(source,tempdir,log)
    content={
        "policy":CONTENT_POLICY,
        "block_entities_total":int(preflight.get("block_entities_total",0) or 0),
        "block_entity_types":dict(preflight.get("block_entity_types") or {}),
        "entity_scan_status":entity_audit.get("scan_status"),
        "entities_total":entity_audit.get("entities_total"),
        "entity_types":dict(entity_audit.get("entity_types") or {}),
        "entity_regions":int(entity_audit.get("entity_regions",0) or 0),
        "entity_chunks":int(entity_audit.get("entity_chunks",0) or 0),
        "note":"Terrain/block states are converted; entities and block entities are currently reported in the loss manifest rather than translated.",
    }
    preflight["content_audit"]=content
    if content["block_entities_total"]:
        log(
            "Content audit warning: %d block entity record(s) will be reported but not translated by this backend."
            % content["block_entities_total"]
        )
    if content["entities_total"]:
        log(
            "Content audit warning: %d entity record(s) will be reported but not translated by this backend."
            % content["entities_total"]
        )
    if content["entity_scan_status"]=="unavailable":
        log("Content audit notice: source entity-region data was not available from this input form.")
    return preflight


def _prepare_staging_output(template: Path, output: Path):
    """Clone the template beside the requested output without exposing partial results."""
    template=template.resolve(); output=output.resolve()
    if template==output:
        raise ConversionError("Output must not be the template/source world")
    if output.exists():
        if not output.is_dir():
            raise ConversionError("Output path already exists and is not a folder: %s"%output)
        if any(output.iterdir()):
            raise ConversionError("Output folder must not already contain files: %s"%output)
        output.rmdir()
    output.parent.mkdir(parents=True,exist_ok=True)
    staging=Path(tempfile.mkdtemp(prefix=".%s.wgmbps-staging-"%output.name,dir=str(output.parent)))
    try:
        shutil.copytree(template,staging,dirs_exist_ok=True)
        (staging/"region").mkdir(parents=True,exist_ok=True)
        return staging
    except Exception:
        shutil.rmtree(staging,ignore_errors=True)
        raise


def _promote_staging_output(staging: Path, output: Path):
    staging=Path(staging); output=Path(output)
    if output.exists():
        raise ConversionError("Refusing to promote over an existing output path: %s"%output)
    os.replace(str(staging),str(output))


def _stable_hash(payload):
    raw=json.dumps(payload,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _source_fingerprint(source: Path):
    source=source.resolve()
    if not source.exists():
        raise ConversionError("Source path does not exist: %s" % source)
    if source.is_file():
        st=source.stat()
        payload={"kind":"file","path":str(source),"size":st.st_size,"mtime_ns":st.st_mtime_ns}
        return _stable_hash(payload)

    region_dir=source/"region" if (source/"region").is_dir() else source
    files=[]
    for p in sorted(region_dir.glob("r.*.*.mca")):
        if not p.is_file(): continue
        st=p.stat()
        files.append(("region",p.name,st.st_size,st.st_mtime_ns))
    if not files:
        raise ConversionError("Source directory contains no Anvil region files: %s" % source)

    entity_dir=None
    if (source/"entities").is_dir():
        entity_dir=source/"entities"
    elif source.name.lower()=="region" and (source.parent/"entities").is_dir():
        entity_dir=source.parent/"entities"
    if entity_dir is not None:
        for p in sorted(entity_dir.glob("r.*.*.mca")):
            if not p.is_file(): continue
            st=p.stat()
            files.append(("entities",p.name,st.st_size,st.st_mtime_ns))

    return _stable_hash({"kind":"world_or_region_dir","path":str(region_dir.resolve()),"files":files})


def _registry_fingerprint(reg: TargetRegistry):
    return _stable_hash({
        "source_format":reg.source_format,
        "ids":sorted((str(k),int(v)) for k,v in reg.ids.items()),
        "aliases":sorted((str(k),str(v)) for k,v in reg.aliases.items()),
    })


def _conversion_fingerprint(
    source: Path,
    template: Path,
    reg: TargetRegistry,
    profile: MappingProfile,
    y_offset: int,
    strip_below_y: int,
):
    level=(template/"level.dat").resolve()
    if not level.is_file():
        raise ConversionError("Target/template world has no level.dat: %s" % template)
    st=level.stat()
    payload={
        "source":_source_fingerprint(source),
        "template_level_dat":{"path":str(level),"size":st.st_size,"mtime_ns":st.st_mtime_ns},
        "target_registry":_registry_fingerprint(reg),
        "mapping_profile":profile.fingerprint_payload(),
        "vertical_offset":int(y_offset),
        "strip_below_y":int(strip_below_y),
    }
    return _stable_hash(payload)


def run_conversion_preflight(
    source,
    template,
    use_hbm=True,
    y_offset=0,
    strip_below_y=0,
    catalog_snapshot=None,
    log=print,
):
    """Perform the exact read-only validation required before conversion.

    No output world is created. The returned fingerprint can be handed to
    ``run_conversion`` so the desktop app does not need to parse every source
    chunk twice when nothing has changed between preflight and Convert.
    """
    if y_offset%16 != 0:
        raise ConversionError("Vertical offset must be a multiple of 16 (e.g. -32, -16, 0, 16)")
    if not (0 <= int(strip_below_y) <= 255):
        raise ConversionError("Strip/fill below target Y must be between 0 and 255")

    source=Path(source); template=Path(template)
    profile=profile_from_catalog_snapshot(catalog_snapshot,bool(use_hbm))
    reg=load_target_registry(template)
    registry_info=validate_target_registry(reg,use_hbm,log,mapping_profile=profile)

    with tempfile.TemporaryDirectory(prefix="wg1710_preflight_") as td:
        src_regions=discover_source_regions(source,Path(td))
        regions=[p for p in sorted(src_regions.glob("r.*.*.mca")) if p.stat().st_size>=8192]
        if not regions:
            raise ConversionError("Source contains no non-empty Anvil region files")
        log("Found %d non-empty region files" % len(regions))
        log("Running read-only source/target conversion preflight...")
        preflight=preflight_source_mappings(
            regions,reg,use_hbm,y_offset,log,mapping_profile=profile
        )
        attach_content_audit(preflight,source,Path(td),log)

    fingerprint=_conversion_fingerprint(source,template,reg,profile,y_offset,strip_below_y)
    result={
        "ready":True,
        "source":str(source),
        "template":str(template),
        "regions":len(regions),
        "settings":{
            "allow_safe_mod_replacements":bool(use_hbm),
            "vertical_offset":int(y_offset),
            "strip_below_y":int(strip_below_y),
        },
        "target_registry":registry_info,
        "mapping_profile":profile.to_dict(),
        "preflight":preflight,
        "fingerprint":fingerprint,
    }
    log("Conversion preflight READY — no output world was created.")
    return result


def run_conversion(source, template, output, use_hbm=True, y_offset=0, strip_below_y=0, log=print, catalog_snapshot=None, verified_preflight=None):
    if np is None:
        raise ConversionError("NumPy is required. Install it with: python3 -m pip install numpy")
    if y_offset%16 != 0:
        raise ConversionError("Vertical offset must be a multiple of 16 (e.g. -32, -16, 0, 16)")

    source=Path(source); template=Path(template); output=Path(output)
    profile=profile_from_catalog_snapshot(catalog_snapshot,bool(use_hbm))

    # Fail closed before creating even a staging clone. The target registry,
    # mapping profile and source palette mapping must all be known-good first.
    reg=load_target_registry(template)
    registry_info=validate_target_registry(reg,use_hbm,log,mapping_profile=profile)
    current_fingerprint=_conversion_fingerprint(source,template,reg,profile,y_offset,strip_below_y)

    report={
        "tool_version":TOOL_VERSION,"source":str(source),"template":str(template),"output":str(output),
        "settings":{
            "use_hbm_architectural":use_hbm,
            "allow_safe_mod_replacements":bool(use_hbm),
            "vertical_offset":y_offset,
            "strip_below_y":strip_below_y,
        },
        "content_policy":CONTENT_POLICY,
        "lighting_strategy":LIGHTING_STRATEGY,
        "heightmap_strategy":HEIGHTMAP_STRATEGY,
        "block_property_strategy":BLOCK_PROPERTY_STRATEGY,
        "target_relight_required":True,
        "mapping_profile":profile.to_dict(),
        "target_registry":registry_info,"preflight":{},
        "preflight_reused":False,
        "output_promoted":False,
        "regions_total":0,"regions_converted":0,"regions_verified":0,
        "chunks_converted":0,"chunks_verified":0,"chunks_failed":0,
        "chunks_cropped_above_255":0,"chunks_cropped_below_0":0,
        "block_entities_omitted":0,
        "block_entity_types_omitted":collections.Counter(),
        "block_entities_synthesized":0,
        "block_entity_types_synthesized":collections.Counter(),
        "entities_omitted":None,
        "entity_types_omitted":{},
        "entity_scan_status":"unknown",
        "data_versions":collections.Counter(),"palette_seen":collections.Counter(),
        "mapping_quality":collections.defaultdict(set),"mapping_notes":{},
        "failure_counts":collections.Counter(),"failures":[],
        "failure_report_json":"",
        "failure_report_txt":"",
    }
    max_failure_examples=200

    def record_failure(entry):
        error=str(entry.get("error","Unknown conversion failure"))
        report["failure_counts"][error]+=1
        if len(report["failures"])<max_failure_examples:
            report["failures"].append(entry)

    def serialise_report():
        serial=dict(report)
        serial["data_versions"]=dict(report["data_versions"])
        serial["palette_seen"]=dict(report["palette_seen"])
        serial["mapping_quality"]={k:sorted(v) for k,v in report["mapping_quality"].items()}
        serial["failure_counts"]=dict(report["failure_counts"])
        serial["block_entity_types_omitted"]=dict(report["block_entity_types_omitted"])
        serial["block_entity_types_synthesized"]=dict(report["block_entity_types_synthesized"])
        return serial

    def report_lines(serial):
        profile_dict=profile.to_dict()
        profile_mods=", ".join(profile_dict.get("enabled_mod_ids") or []) or "none"
        content=(serial.get("preflight") or {}).get("content_audit") or {}
        source_entities=content.get("entities_total")
        entity_text="not scanned from this input form" if source_entities is None else str(source_entities)
        lines=[
            "WG Modern -> 1.7.10 Backport Report", "====================================", "",
            "Output status: %s"%("PROMOTED" if serial.get("output_promoted") else "NOT PROMOTED"),
            "Target registry: %s"%reg.summary(),
            "Mapping profile: %s"%profile_dict.get("mode","unknown"),
            "Enabled catalog mod namespaces: %s"%profile_mods,
            "Verified preflight reused: %s"%("yes" if serial.get("preflight_reused") else "no"),
            "Preflight chunks: %d"%(serial.get("preflight") or {}).get("chunks",0),
            "Preflight unique in-range palette states: %d"%(serial.get("preflight") or {}).get("unique_palette_states",0),
            "Potential crop above Y=255: %d chunk(s)"%(serial.get("preflight") or {}).get("potential_chunks_cropped_above_255",0),
            "Potential crop below Y=0: %d chunk(s)"%(serial.get("preflight") or {}).get("potential_chunks_cropped_below_0",0), "",
            "Content policy: %s"%CONTENT_POLICY,
            "Source block entities detected: %d"%int(content.get("block_entities_total",0) or 0),
            "EFR state/default tile entities synthesized: %d"%int(serial.get("block_entities_synthesized",0) or 0),
            "Block entities omitted/reported during conversion: %d"%int(serial.get("block_entities_omitted",0) or 0),
            "Source entities detected: %s"%entity_text,
            "Entities translated: 0",
            "Block-property strategy: %s"%BLOCK_PROPERTY_STRATEGY,
            "Lighting strategy: %s (legacy chunks emitted with LightPopulated=0)"%LIGHTING_STRATEGY,
            "Heightmap strategy: %s"%HEIGHTMAP_STRATEGY, "",
            "Converted regions: %d / %d"%(serial.get("regions_converted",0),serial.get("regions_total",0)),
            "Round-trip verified regions: %d"%serial.get("regions_verified",0),
            "Converted chunks: %d"%serial.get("chunks_converted",0),
            "Round-trip verified chunks: %d"%serial.get("chunks_verified",0),
            "Failed chunks: %d"%serial.get("chunks_failed",0),
            "Chunks with source blocks cropped above Y=255: %d"%serial.get("chunks_cropped_above_255",0),
            "Chunks with source blocks cropped below Y=0: %d"%serial.get("chunks_cropped_below_0",0), "",
        ]
        preflight=serial.get("preflight") or {}
        occurrence_total=int(preflight.get("block_occurrences_total",0) or 0)
        quality_occurrences=preflight.get("mapping_quality_block_occurrences") or {}
        quality_percent=preflight.get("mapping_quality_percent") or {}
        if occurrence_total:
            lines += ["Mapping impact by placed in-range non-air blocks:"]
            for quality in ("backport_exact","exact","backport_close","close","approximate","omitted"):
                count=int(quality_occurrences.get(quality,0) or 0)
                if count:
                    lines.append("- %s: %d (%.3f%%)"%(quality,count,float(quality_percent.get(quality,0.0) or 0.0)))
            stateful_total=int(preflight.get("stateful_block_occurrences_total",0) or 0)
            stateful_preserved=int(preflight.get("stateful_block_occurrences_preserved",0) or 0)
            if stateful_total:
                lines.append(
                    "- verified state fidelity: %d / %d stateful placed blocks (%.3f%%)"
                    % (stateful_preserved,stateful_total,float(preflight.get("stateful_block_fidelity_percent",0.0) or 0.0))
                )
            impact=preflight.get("top_non_exact_mappings") or []
            if impact:
                lines += ["", "Highest-impact non-exact mappings:"]
                for row in impact[:25]:
                    lines.append("- %d × %s -> %s [%s]%s"%(
                        int(row.get("count",0) or 0), row.get("source","?"), row.get("target","?"),
                        row.get("quality","?"), (" — "+str(row.get("note"))) if row.get("note") else ""
                    ))
            unavailable=preflight.get("unavailable_backport_candidates") or []
            if unavailable:
                lines += ["", "Backport-provider candidates unavailable in target/template registry:"]
                for row in unavailable[:25]:
                    lines.append("- %d × %s: catalog candidate(s) %s not registered in target world%s"%(
                        int(row.get("count",0) or 0), row.get("source","?"),
                        ", ".join(row.get("candidate_targets") or []),
                        (" (provider: "+", ".join(row.get("providers") or [])+")") if row.get("providers") else ""
                    ))
            lines += [""]
        lines += ["Approximate/omitted modern blocks:"]
        for name in sorted(report["mapping_notes"]):
            e=report["mapping_notes"][name]
            lines.append("- %s -> %s:%d [%s]%s"%(
                name,e["target"],e["meta"],e["quality"],(" — "+e["note"]) if e["note"] else ""
            ))
        if report["block_entity_types_omitted"]:
            lines += ["", "Block-entity loss manifest:"]
            for be,count in report["block_entity_types_omitted"].most_common():
                lines.append("- %d × %s"%(count,be))
        if report["block_entity_types_synthesized"]:
            lines += ["", "Synthesized EFR state/default tile entities:"]
            for be,count in report["block_entity_types_synthesized"].most_common():
                lines.append("- %d × %s"%(count,be))
        if source_entities is not None and content.get("entity_types"):
            lines += ["", "Entity loss manifest (preflight audit):"]
            for ent,count in sorted(content.get("entity_types",{}).items(),key=lambda kv:(-kv[1],kv[0])):
                lines.append("- %d × %s"%(count,ent))
        if report["failure_counts"]:
            lines += ["", "Failure summary:"]
            for error,count in report["failure_counts"].most_common():
                lines.append("- %d × %s"%(count,error))
        if report["failures"]:
            lines += ["", "Failure examples (maximum %d):"%max_failure_examples]
            lines += ["- "+json.dumps(x,sort_keys=True) for x in report["failures"]]
        return lines

    def write_reports(directory: Path, json_name="WG_BACKPORT_REPORT.json", txt_name="WG_BACKPORT_REPORT.txt"):
        directory=Path(directory); directory.mkdir(parents=True,exist_ok=True)
        serial=serialise_report()
        (directory/json_name).write_text(json.dumps(serial,indent=2,sort_keys=True),encoding="utf-8")
        (directory/txt_name).write_text("\n".join(report_lines(serial))+"\n",encoding="utf-8")
        return serial,directory/json_name,directory/txt_name

    staging=None
    try:
        with tempfile.TemporaryDirectory(prefix="wg1710_") as td:
            td=Path(td)
            src_regions=discover_source_regions(source,td)
            regions=[p for p in sorted(src_regions.glob("r.*.*.mca")) if p.stat().st_size>=8192]
            report["regions_total"]=len(regions)
            if not regions:
                raise ConversionError("Source contains no non-empty Anvil region files")
            log("Found %d non-empty region files"%len(regions))

            reusable=(
                isinstance(verified_preflight,dict)
                and bool(verified_preflight.get("ready"))
                and verified_preflight.get("fingerprint") == current_fingerprint
                and isinstance(verified_preflight.get("preflight"),dict)
            )
            if reusable:
                report["preflight"]=dict(verified_preflight["preflight"])
                report["preflight_reused"]=True
                log("Reusing the verified read-only preflight; source, template, settings and active catalogs are unchanged.")
            else:
                if verified_preflight:
                    log("Stored preflight no longer matches the current conversion inputs; running it again before output staging.")
                else:
                    log("Running source/target mapping preflight before output staging...")
                report["preflight"]=preflight_source_mappings(
                    regions,reg,use_hbm,y_offset,log,mapping_profile=profile
                )
                attach_content_audit(report["preflight"],source,td,log)

            content=(report["preflight"] or {}).get("content_audit") or {}
            report["entities_omitted"]=content.get("entities_total")
            report["entity_types_omitted"]=dict(content.get("entity_types") or {})
            report["entity_scan_status"]=str(content.get("entity_scan_status") or "unavailable")

            # Conversion writes only into a hidden sibling staging world. The
            # requested output path is promoted only after zero chunk failures
            # and a full region round-trip structural verification.
            staging=_prepare_staging_output(template,output)
            log("Preflight is green; created a hidden staging clone and starting conversion.")

            for ri,rp in enumerate(regions,1):
                chunks={}
                log("[%d/%d] %s"%(ri,len(regions),rp.name))
                try:
                    for idx,raw in RegionReader(rp).chunks():
                        try:
                            (cx,cz),legacy=convert_chunk(
                                raw,reg,use_hbm,y_offset,strip_below_y,report,mapping_profile=profile
                            )
                            local=(cx&31)+((cz&31)*32)
                            chunks[local]=legacy
                            report["chunks_converted"]+=1
                        except Exception as exc:
                            report["chunks_failed"]+=1
                            record_failure({"region":rp.name,"chunk_index":idx,"error":str(exc)})
                            if report["chunks_failed"]<=20:
                                log("  chunk %d FAILED: %s"%(idx,exc))
                    if chunks:
                        out_region=staging/"region"/rp.name
                        write_region(out_region,chunks)
                        verified=verify_written_region(out_region,chunks.keys())
                        report["regions_converted"]+=1
                        report["regions_verified"]+=1
                        report["chunks_verified"]+=verified
                except Exception as exc:
                    # Region-level writer/verification problems make the staged
                    # world non-promotable even if individual chunk conversion
                    # happened to succeed.
                    record_failure({"region":rp.name,"error":str(exc)})
                    report["chunks_failed"]+=max(1,len(chunks))
                    log("  REGION FAILED/UNVERIFIED: %s"%exc)

            if report["chunks_failed"] or report["regions_verified"] != report["regions_converted"] or report["chunks_verified"] != report["chunks_converted"]:
                report["output_promoted"]=False
                fail_json=output.parent/(output.name+".WG_BACKPORT_FAILED_REPORT.json")
                fail_txt=output.parent/(output.name+".WG_BACKPORT_FAILED_REPORT.txt")
                report["failure_report_json"]=str(fail_json)
                report["failure_report_txt"]=str(fail_txt)
                serial=serialise_report()
                fail_json.write_text(json.dumps(serial,indent=2,sort_keys=True),encoding="utf-8")
                fail_txt.write_text("\n".join(report_lines(serial))+"\n",encoding="utf-8")
                shutil.rmtree(staging,ignore_errors=True)
                staging=None
                log("Conversion was NOT promoted: the requested output world remains absent.")
                log("Failure report: %s"%fail_txt)
                return serial

            report["output_promoted"]=True
            serial,_,_=write_reports(staging)
            _promote_staging_output(staging,output)
            staging=None
            log("Staged world passed round-trip verification and was promoted to: %s"%output)
            log("Finished: %d chunks, 0 failures"%report["chunks_converted"])
            log("Report: %s"%(output/"WG_BACKPORT_REPORT.txt"))
            return serial
    finally:
        if staging is not None and Path(staging).exists():
            shutil.rmtree(staging,ignore_errors=True)


# ---------- Map analyzer used without a target world ----------
def analyze_source(source, out_json, log=print):
    counts=collections.Counter(); dvs=collections.Counter(); ymin=999; ymax=-999; chunks=0; nonempty_regions=0
    with tempfile.TemporaryDirectory(prefix="wgscan_") as td:
        region_dir=discover_source_regions(Path(source),Path(td))
        for rp in sorted(region_dir.glob("r.*.*.mca")):
            if rp.stat().st_size<8192: continue
            nonempty_regions+=1
            log("Scanning "+rp.name)
            for _,raw in RegionReader(rp).chunks():
                c=parse_modern_chunk(raw); chunks+=1; dvs[str(c.get("DataVersion"))]+=1
                for s in c["sections"]:
                    if s.get("palette"):
                        y=s.get("Y"); ymin=min(ymin,y); ymax=max(ymax,y)
                        for n,_ in s["palette"]: counts[n]+=1
    rep={"regions":nonempty_regions,"chunks":chunks,"data_versions":dict(dvs),"section_y_min":ymin,"section_y_max":ymax,"block_palette_occurrences":dict(sorted(counts.items()))}
    Path(out_json).write_text(json.dumps(rep,indent=2),encoding="utf-8")
    return rep


def cli(argv=None):
    ap=argparse.ArgumentParser(description="Backport modern Minecraft Anvil map chunks to Forge Minecraft 1.7.10")
    sub=ap.add_subparsers(dest="cmd")
    c=sub.add_parser("convert",help="convert into a cloned 1.7.10 template world")
    c.add_argument("--source",required=True,help="modern world folder, region folder, or region ZIP")
    c.add_argument("--template",required=True,help="saved Forge 1.7.10 world created with the exact target modpack/RTG")
    c.add_argument("--output",required=True,help="new/empty output world folder")
    c.add_argument("--no-hbm",action="store_true",help="do not use safe HBM architectural substitutes")
    c.add_argument("--y-offset",type=int,default=0,help="vertical shift, multiple of 16; default 0")
    c.add_argument("--strip-below-y",type=int,default=0,help="replace blocks below this target Y with stone; default 0 (preserve)")
    a=sub.add_parser("analyze",help="inventory modern source blocks without converting")
    a.add_argument("--source",required=True); a.add_argument("--output",required=True)
    args=ap.parse_args(argv)
    if args.cmd=="convert":
        run_conversion(args.source,args.template,args.output,not args.no_hbm,args.y_offset,args.strip_below_y)
    elif args.cmd=="analyze": analyze_source(args.source,args.output)
    else: ap.print_help()


def gui():
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox
    except Exception as e:
        print("Tk GUI unavailable:",e); cli(); return
    root=tk.Tk(); root.title("WG Modern -> Minecraft 1.7.10 Map Backporter"); root.geometry("860x620")
    src=tk.StringVar(); tmpl=tk.StringVar(); out=tk.StringVar(); hbm=tk.BooleanVar(value=True); yoff=tk.StringVar(value="0"); strip=tk.StringVar(value="0")
    def row(label,var,r,kind="dir"):
        tk.Label(root,text=label,anchor="w").grid(row=r,column=0,sticky="w",padx=8,pady=5)
        tk.Entry(root,textvariable=var,width=78).grid(row=r,column=1,sticky="ew",padx=8,pady=5)
        def browse():
            if kind=="source":
                p=filedialog.askopenfilename(title=label,filetypes=[("ZIP or world data","*.zip"),("All files","*")])
                if not p: p=filedialog.askdirectory(title=label)
            else: p=filedialog.askdirectory(title=label)
            if p: var.set(p)
        tk.Button(root,text="Browse…",command=browse).grid(row=r,column=2,padx=8,pady=5)
    root.columnconfigure(1,weight=1)
    tk.Label(root,text="Backports modern region chunks into a cloned Forge 1.7.10 + HBM/RTG template world.",font=("TkDefaultFont",11,"bold")).grid(row=0,column=0,columnspan=3,sticky="w",padx=8,pady=(10,8))
    row("Modern map / region ZIP",src,1,"source"); row("1.7.10 template world",tmpl,2); row("Empty output folder",out,3)
    tk.Checkbutton(root,text="Use HBM architectural replacements (recommended; never uses HBM machines/ores)",variable=hbm).grid(row=4,column=0,columnspan=3,sticky="w",padx=8,pady=4)
    f=tk.Frame(root); f.grid(row=5,column=0,columnspan=3,sticky="w",padx=8,pady=4)
    tk.Label(f,text="Vertical offset (multiple of 16):").pack(side="left"); tk.Entry(f,textvariable=yoff,width=8).pack(side="left",padx=6)
    tk.Label(f,text="Strip/fill below Y (0 = preserve):").pack(side="left",padx=(18,0)); tk.Entry(f,textvariable=strip,width=8).pack(side="left",padx=6)
    text=tk.Text(root,height=24,wrap="word"); text.grid(row=7,column=0,columnspan=3,sticky="nsew",padx=8,pady=8); root.rowconfigure(7,weight=1)
    def log(s):
        def u(): text.insert("end",str(s)+"\n"); text.see("end")
        root.after(0,u)
    def start():
        try:
            yi=int(yoff.get()); sb=int(strip.get())
        except Exception: messagebox.showerror("Invalid setting","Vertical offset and strip Y must be integers."); return
        if not src.get() or not tmpl.get() or not out.get(): messagebox.showerror("Missing path","Select source, template world, and output folder."); return
        btn.config(state="disabled")
        def worker():
            try:
                run_conversion(src.get(),tmpl.get(),out.get(),hbm.get(),yi,sb,log)
                root.after(0,lambda:messagebox.showinfo("Backport complete","Conversion finished. Check WG_BACKPORT_REPORT.txt in the output world before using it."))
            except Exception as e:
                log(traceback.format_exc()); root.after(0,lambda:messagebox.showerror("Conversion failed",str(e)))
            finally: root.after(0,lambda:btn.config(state="normal"))
        threading.Thread(target=worker,daemon=True).start()
    btn=tk.Button(root,text="Convert map",command=start,height=2); btn.grid(row=6,column=0,columnspan=3,pady=6)
    root.mainloop()

if __name__=="__main__":
    if len(sys.argv)==1: gui()
    else: cli()
