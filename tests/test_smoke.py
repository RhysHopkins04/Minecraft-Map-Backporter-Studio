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


def main():
    assert TARGET_BY_VERSION["1.7.10"].backend == "legacy1710"
    assert hasattr(legacy1710_engine, "run_conversion")
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
