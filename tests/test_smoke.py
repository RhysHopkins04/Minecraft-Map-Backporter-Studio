from __future__ import annotations
import json
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


def make_fake_jar(path: Path):
    mcmod = [{"modid":"demo","name":"Demo Blocks","version":"1.0","mcversion":"1.7.10"}]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("mcmod.info", json.dumps(mcmod))
        z.writestr("assets/demo/lang/en_US.lang", "tile.demo_brick.name=Demo Brick\n")
        # tiny valid 1x1 PNG
        z.writestr("assets/demo/textures/blocks/demo_brick.png", bytes.fromhex("89504E470D0A1A0A0000000D49484452000000010000000108060000001F15C4890000000D49444154789C63606060F80F0001040100F805FF640000000049454E44AE426082"))


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
        assert any(b.registry_hint == "demo:demo_brick" for b in cat.blocks)
        pack = td / "pack"; (pack / "mods").mkdir(parents=True); (pack / "mods" / "demo.jar").write_bytes(jar.read_bytes())
        rep = analyze_modpack(pack)
        assert rep.local_jars == 1 and rep.mods[0].block_candidates >= 1
    print("core smoke tests passed")

if __name__ == "__main__": main()
