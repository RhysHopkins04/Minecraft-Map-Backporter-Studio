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

try:
    import numpy as np
except Exception:
    np = None

TOOL_VERSION = "0.2.0"
AIR_NAMES = {"minecraft:air", "minecraft:cave_air", "minecraft:void_air"}

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
    out={"xPos":None,"zPos":None,"LastUpdate":0,"DataVersion":None,"sections":[]}
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


class TargetRegistry:
    def __init__(self, ids):
        self.ids=dict(ids)
        self._lower={k.lower():v for k,v in self.ids.items()}
        self._hbm_by_logical={}
        for k,v in self.ids.items():
            if k.lower().startswith("hbm:"):
                suffix=k.split(":",1)[1]
                logical=suffix[5:] if suffix.startswith("tile.") else suffix
                self._hbm_by_logical[logical.lower()]=(k,v)

    def resolve(self, name):
        if name in self.ids: return self.ids[name]
        return self._lower.get(name.lower())

    def resolve_hbm(self, logical):
        return self._hbm_by_logical.get(logical.lower())

    def has_hbm(self, logical):
        return logical.lower() in self._hbm_by_logical


def load_target_registry(world: Path):
    level=world/"level.dat"
    if not level.is_file(): raise ConversionError("Target/template world has no level.dat: %s" % world)
    with gzip.open(level,"rb") as f: raw=f.read()
    _,root=parse_nbt(raw)
    fml=root.get("FML",{})
    registries=fml.get("Registries",{})
    blocks=registries.get("fml:blocks",{})
    ids={}
    for e in blocks.get("ids",[]) if isinstance(blocks,dict) else []:
        if isinstance(e,dict) and "K" in e and "V" in e: ids[str(e["K"])]=int(e["V"])
    if not ids:
        # Early 1.7 compatibility format.
        for e in fml.get("ItemData",[]) if isinstance(fml,dict) else []:
            if not isinstance(e,dict): continue
            if "K" in e and "V" in e: ids[str(e["K"])]=int(e["V"])
            elif "ItemName" in e and "ItemId" in e: ids[str(e["ItemName"])]=int(e["ItemId"])
    if not ids:
        raise ConversionError(
            "Could not find Forge's fml:blocks ID registry in target level.dat. "
            "Create/open the template world once in the exact Forge 1.7.10 modpack (with HBM/RTG), save, quit, then select it again."
        )
    return TargetRegistry(ids)


def boolprop(props,k): return str(props.get(k,"false")).lower()=="true"

def stair_meta(props):
    facing={"east":0,"west":1,"south":2,"north":3}.get(props.get("facing"),0)
    if props.get("half") == "top": facing |= 4
    return facing

def slab_meta(base, props):
    return base | (8 if props.get("type") == "top" else 0)

def log_axis_bits(props, wood_block=False):
    if wood_block: return 12
    return {"y":0,"x":4,"z":8}.get(props.get("axis","y"),0)

def sign_wall_meta(props): return {"north":2,"south":3,"west":4,"east":5}.get(props.get("facing"),2)
def torch_wall_meta(props): return {"east":1,"west":2,"south":3,"north":4}.get(props.get("facing"),5)
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
    m={"south":0,"north":1,"east":2,"west":3}.get(props.get("facing"),0)
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


def map_modern(name, props, reg: TargetRegistry, use_hbm=True):
    """Return closest legacy block mapping. No HBM machines/ores are used."""
    p=name.split(":",1)[-1]

    def V(target,meta=0,q="exact",note=""): return Mapping(target,meta&15,q,note)
    def H(logical, fallback, meta=0, fbmeta=0, q="close", note=""):
        if use_hbm and logical in HBM_SAFE_ARCHITECTURAL and reg.has_hbm(logical):
            return V("hbm:"+logical,meta,q,note)
        return V(fallback,fbmeta,"approximate", note or ("HBM %s unavailable; vanilla fallback"%logical))

    if name in AIR_NAMES: return V("minecraft:air")
    # Invisible/editor-only modern blocks are safer omitted than turned into visible cubes.
    if p in {"barrier","structure_block","jigsaw","light","end_gateway"}: return V("minecraft:air",0,"omitted","Modern editor/invisible block omitted")

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
            if rest=="slab": return V("minecraft:wooden_slab",slab_meta(wmeta,props))
            if rest=="fence": return V("minecraft:fence",0,"approximate","1.7.10 has only oak wooden fences")
            if rest=="fence_gate": return V("minecraft:fence_gate",gate_meta(props),"approximate","1.7.10 has only oak fence gates")
            if rest=="door": return V("minecraft:wooden_door",door_meta(props),"approximate","1.7.10 has only oak wooden doors")
            if rest=="trapdoor": return V("minecraft:trapdoor",trapdoor_meta(props),"approximate","1.7.10 has only oak wooden trapdoors")
            if rest=="button": return V("minecraft:wooden_button",button_meta(props),"approximate" if wood!="oak" else "exact")
            if rest=="pressure_plate": return V("minecraft:wooden_pressure_plate",0,"approximate" if wood!="oak" else "exact")
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
    if p in slab_base: return V("minecraft:stone_slab",slab_meta(slab_base[p],props),"exact" if p in {"stone_slab","sandstone_slab","cobblestone_slab","brick_slab","stone_brick_slab","nether_brick_slab","quartz_slab"} else "approximate")
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


# ---------- Legacy NBT writer ----------
def nbt_name(name):
    b=name.encode("utf-8"); return struct.pack(">H",len(b))+b

def tag(t,name,payload): return bytes([t])+nbt_name(name)+payload

def p_byte(v): return struct.pack(">b", ((int(v)+128)%256)-128)
def p_int(v): return struct.pack(">i",int(v))
def p_long(v): return struct.pack(">q",int(v))
def p_byte_array(b): return struct.pack(">i",len(b))+bytes(b)
def p_int_array(vals): return struct.pack(">i",len(vals))+b"".join(struct.pack(">i",int(v)) for v in vals)
def p_list(et,payloads): return bytes([et])+struct.pack(">i",len(payloads))+b"".join(payloads)
def p_compound(named_tags): return b"".join(named_tags)+b"\x00"

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

def make_chunk_nbt(cx,cz,last_update,sections,heightmap,biomes):
    sec_payload=[s for s in sections]
    level_tags=[
        tag(3,"xPos",p_int(cx)), tag(3,"zPos",p_int(cz)), tag(4,"LastUpdate",p_long(last_update or 0)),
        tag(7,"Biomes",p_byte_array(bytes(int(x)&255 for x in biomes))),
        tag(11,"HeightMap",p_int_array([int(x) for x in heightmap])),
        tag(1,"TerrainPopulated",p_byte(1)), tag(1,"LightPopulated",p_byte(0)), tag(1,"V",p_byte(1)),
        tag(4,"InhabitedTime",p_long(0)),
        tag(9,"Sections",p_list(10,sec_payload)),
        tag(9,"Entities",p_list(10,[])), tag(9,"TileEntities",p_list(10,[])),
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


def convert_chunk(raw, reg, use_hbm, y_offset, strip_below_y, stats):
    c=parse_modern_chunk(raw)
    cx,cz=c["xPos"],c["zPos"]
    if cx is None or cz is None: raise ConversionError("Chunk missing xPos/zPos")
    dv=c.get("DataVersion")
    stats["data_versions"][str(dv)]+=1
    height=np.zeros((16,16),dtype=np.int32)
    converted=[]
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
        mids=[]; mmeta=[]
        for name,props in pal:
            mp=map_modern(name,props,reg,use_hbm)
            rid,meta,resolved=resolve_mapping(mp,reg)
            mids.append(rid); mmeta.append(meta)
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
    return (cx,cz),make_chunk_nbt(cx,cz,c.get("LastUpdate",0),converted,height.reshape(-1).tolist(),biomes)


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


def ensure_output(template: Path, output: Path):
    template=template.resolve(); output=output.resolve()
    if template==output: raise ConversionError("Output must not be the template/source world")
    if output.exists():
        if any(output.iterdir()): raise ConversionError("Output folder must not already contain files: %s"%output)
        output.rmdir()
    shutil.copytree(template,output)
    (output/"region").mkdir(parents=True,exist_ok=True)


def run_conversion(source, template, output, use_hbm=True, y_offset=0, strip_below_y=0, log=print):
    if np is None: raise ConversionError("NumPy is required. Install it with: python3 -m pip install numpy")
    if y_offset%16 != 0: raise ConversionError("Vertical offset must be a multiple of 16 (e.g. -32, -16, 0, 16)")
    source=Path(source); template=Path(template); output=Path(output)
    reg=load_target_registry(template)
    hbm_count=sum(1 for k in reg.ids if k.lower().startswith("hbm:"))
    log("Target registry: %d block IDs (%d HBM entries)"%(len(reg.ids),hbm_count))
    ensure_output(template,output)
    report={
        "tool_version":TOOL_VERSION,"source":str(source),"template":str(template),"output":str(output),
        "settings":{"use_hbm_architectural":use_hbm,"vertical_offset":y_offset,"strip_below_y":strip_below_y},
        "regions_total":0,"regions_converted":0,"chunks_converted":0,"chunks_failed":0,
        "chunks_cropped_above_255":0,"chunks_cropped_below_0":0,
        "data_versions":collections.Counter(),"palette_seen":collections.Counter(),
        "mapping_quality":collections.defaultdict(set),"mapping_notes":{},"failures":[]
    }
    with tempfile.TemporaryDirectory(prefix="wg1710_") as td:
        src_regions=discover_source_regions(source,Path(td))
        regions=[p for p in sorted(src_regions.glob("r.*.*.mca")) if p.stat().st_size>=8192]
        report["regions_total"]=len(regions)
        log("Found %d non-empty region files"%len(regions))
        for ri,rp in enumerate(regions,1):
            chunks={}
            log("[%d/%d] %s"%(ri,len(regions),rp.name))
            try:
                for idx,raw in RegionReader(rp).chunks():
                    try:
                        (cx,cz),legacy=convert_chunk(raw,reg,use_hbm,y_offset,strip_below_y,report)
                        local=(cx&31)+((cz&31)*32)
                        chunks[local]=legacy; report["chunks_converted"]+=1
                    except Exception as e:
                        report["chunks_failed"]+=1
                        report["failures"].append({"region":rp.name,"chunk_index":idx,"error":str(e)})
                        if len(report["failures"])<=20: log("  chunk %d FAILED: %s"%(idx,e))
                if chunks:
                    write_region(output/"region"/rp.name,chunks)
                    report["regions_converted"]+=1
            except Exception as e:
                report["failures"].append({"region":rp.name,"error":str(e)})
                log("  REGION FAILED: %s"%e)
    # JSON-serializable summary.
    serial=dict(report)
    serial["data_versions"]=dict(report["data_versions"])
    serial["palette_seen"]=dict(report["palette_seen"])
    serial["mapping_quality"]={k:sorted(v) for k,v in report["mapping_quality"].items()}
    (output/"WG_BACKPORT_REPORT.json").write_text(json.dumps(serial,indent=2,sort_keys=True),encoding="utf-8")
    lines=[
        "WG Modern -> 1.7.10 Backport Report", "====================================", "",
        "Converted regions: %d / %d"%(report["regions_converted"],report["regions_total"]),
        "Converted chunks: %d"%report["chunks_converted"], "Failed chunks: %d"%report["chunks_failed"],
        "Chunks with source blocks cropped above Y=255: %d"%report["chunks_cropped_above_255"],
        "Chunks with source blocks cropped below Y=0: %d"%report["chunks_cropped_below_0"], "",
        "Approximate/omitted modern blocks:",
    ]
    for name in sorted(report["mapping_notes"]):
        e=report["mapping_notes"][name]
        lines.append("- %s -> %s:%d [%s]%s"%(name,e["target"],e["meta"],e["quality"],(" — "+e["note"]) if e["note"] else ""))
    if report["failures"]:
        lines += ["", "Failures:"]+["- "+json.dumps(x,sort_keys=True) for x in report["failures"][:200]]
    (output/"WG_BACKPORT_REPORT.txt").write_text("\n".join(lines)+"\n",encoding="utf-8")
    log("Finished: %d chunks, %d failures"%(report["chunks_converted"],report["chunks_failed"]))
    log("Report: %s"%(output/"WG_BACKPORT_REPORT.txt"))
    return serial


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
