from __future__ import annotations
import json
import struct
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wgmap_backporter_studio.core.jar_analyzer import analyze_jar
from wgmap_backporter_studio.core.modpack_analyzer import analyze_modpack
from wgmap_backporter_studio.core.version_targets import TARGET_BY_VERSION
from wgmap_backporter_studio.core import legacy1710_engine
from wgmap_backporter_studio.app import packaged_self_test


def _minimal_block_holder_class() -> bytes:
    """Build a tiny valid class with one public static Minecraft Block field."""
    cp = []

    def utf8(value: str):
        raw = value.encode("utf-8")
        cp.append(b"\x01" + struct.pack(">H", len(raw)) + raw)
        return len(cp)

    def class_ref(name_index: int):
        cp.append(b"\x07" + struct.pack(">H", name_index))
        return len(cp)

    this_name = utf8("demo/ModBlocks")
    this_class = class_ref(this_name)
    super_name = utf8("java/lang/Object")
    super_class = class_ref(super_name)
    field_name = utf8("demo_brick")
    field_desc = utf8("Lnet/minecraft/block/Block;")

    out = bytearray()
    out += struct.pack(">IHHH", 0xCAFEBABE, 0, 52, len(cp) + 1)
    for entry in cp:
        out += entry
    out += struct.pack(">HHH", 0x0021, this_class, super_class)  # public + super
    out += struct.pack(">H", 0)  # interfaces
    out += struct.pack(">H", 1)  # fields
    out += struct.pack(">HHHH", 0x0009, field_name, field_desc, 0)  # public static
    out += struct.pack(">H", 0)  # methods
    out += struct.pack(">H", 0)  # class attributes
    return bytes(out)


def make_fake_jar(path: Path):
    mcmod = [{"modid":"demo","name":"Demo Blocks","version":"1.0","mcversion":"1.7.10"}]
    png = bytes.fromhex("89504E470D0A1A0A0000000D49484452000000010000000108060000001F15C4890000000D49444154789C63606060F80F0001040100F805FF640000000049454E44AE426082")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("mcmod.info", json.dumps(mcmod))
        # Put Chinese first deliberately: analyzer language priority must still
        # choose en_US rather than ZIP member order.
        z.writestr("assets/demo/lang/zh_CN.lang", "tile.demo_brick.name=演示砖\n")
        z.writestr("assets/demo/lang/en_US.lang", "tile.demo_brick.name=Demo Brick\n")
        z.writestr("assets/demo/textures/blocks/demo_brick.png", png)
        z.writestr("assets/demo/textures/blocks/particle/noise.png", png)
        z.writestr("assets/demo/models/blocks/demo_brick.obj", "o DemoBrick\n")
        z.writestr("demo/ModBlocks.class", _minimal_block_holder_class())


def test_forge1710_itemdata_registry():
    fml = {
        "ItemData": [
            {"K":"\x01minecraft:air","V":0},
            {"K":"\x01minecraft:stone","V":1},
            {"K":"\x01minecraft:grass","V":2},
            {"K":"\x01minecraft:dirt","V":3},
            {"K":"\x01minecraft:cobblestone","V":4},
            {"K":"\x01minecraft:planks","V":5},
            {"K":"\x01minecraft:bedrock","V":7},
            {"K":"\x01minecraft:water","V":9},
            {"K":"\x01hbm:concrete","V":901},
            {"K":"\x02hbm:some_item","V":5001},
        ]
    }
    reg = legacy1710_engine._target_registry_from_fml(fml)
    assert reg.source_format == "Forge 1.7.10 FML/ItemData"
    assert reg.resolve("minecraft:stone") == 1
    assert reg.resolve("minecraft:bedrock") == 7
    assert reg.resolve_hbm("concrete") == ("hbm:concrete", 901)
    assert "\x01minecraft:stone" not in reg.ids
    assert "\x02hbm:some_item" not in reg.ids
    assert len(reg.ids) == 9
    assert reg.ignored_items == 1
    legacy1710_engine.validate_target_registry(reg, True, log=lambda _: None)

    try:
        legacy1710_engine._target_registry_from_fml({"ItemData":[{"K":"\x02minecraft:stick","V":280}]})
    except legacy1710_engine.ConversionError as exc:
        assert "U+0001 block entries" in str(exc)
    else:
        raise AssertionError("item-only Forge ItemData must not be accepted as a block registry")


def main():
    assert TARGET_BY_VERSION["1.7.10"].backend == "legacy1710"
    assert hasattr(legacy1710_engine, "run_conversion")
    test_forge1710_itemdata_registry()
    assert packaged_self_test() == 0
    with tempfile.TemporaryDirectory() as td:
        td = Path(td); jar = td / "demo.jar"; make_fake_jar(jar)
        cat = analyze_jar(jar)
        assert cat.mod_ids == ["demo"]
        assert len(cat.blocks) == 1, "legacy class evidence should exclude unrelated textures/blocks assets"
        block = cat.blocks[0]
        assert block.registry_hint == "demo:demo_brick"
        assert block.display_name == "Demo Brick"
        assert block.localization_locale == "en_us"
        assert block.confidence == "high"
        assert block.candidate_kind == "registered block candidate"
        assert block.model_paths == ["assets/demo/models/blocks/demo_brick.obj"]
        assert cat.analysis_stats["legacy_static_block_fields"] == 1
        assert cat.analysis_stats["packaged_model_assets"] == 1
        pack = td / "pack"; (pack / "mods").mkdir(parents=True); (pack / "mods" / "demo.jar").write_bytes(jar.read_bytes())
        rep = analyze_modpack(pack)
        assert rep.local_jars == 1 and rep.mods[0].block_candidates >= 1
    print("core smoke tests passed")

if __name__ == "__main__": main()
