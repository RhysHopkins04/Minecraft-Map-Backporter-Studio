# Backporter modern state fixes: local handoff

Changes are uncommitted in the original checkout. No commit, push, tag or version bump.

## Completed

A narrow minecraft:poweder_snow alias preserves the observed typo. Current local EFR encodings are used for every amethyst stage/facing, pointed-dripstone thickness/direction, tall-seagrass half, sea-pickle count/wet state, and coral plant/fan/wall-fan facing/wet state.

Kelp ages 0–25 use compact two-ID metadata when the target registry contains etfuturum:kelp_age_16. Older registries retain the legacy etfuturum:modern_parity_kelp_state tile entity with BYTE Age. Refresh the registry from the updated EFR runtime before large-map conversion; the legacy fallback still creates one TE per head and defeats the scale improvement.

Unsupported waterlogged states are explicitly downgraded from exact. Missing supported wet aquatic blocks fall back to water; missing dry aquatic blocks fall back to air.

## Validation and local builds

218 pytest checks passed, including actual serialized Blocks/Add/Data, BYTE Age NBT, offset coordinates, negative chunks, cropping, Anvil save/reload, fallback and compact-path no-TE contracts. See backporter-modern-tests.log. Wheel and source distribution built successfully; see backporter-modern-build.log.

Local installable artifacts (version unchanged):
- release/modern-state-fixes/wg_map_backporter_studio-0.4.2-py3-none-any.whl
- release/modern-state-fixes/wg_map_backporter_studio-0.4.2.tar.gz

Run the normal pytest suite with the repository dependencies installed. New tests are tests/test_modern_aquatic_states.py.

## Acceptance and limitations

1. Refresh the registry from the new EFR jar.
2. Convert a small sample; check block states and offset positions in Minecraft.
3. Save, stop and restart; check kelp ages and wet/dry states again.

Automated serialized-state tests passed; graphical Minecraft client checks and a complete 63-million-head conversion were not run. General waterlogging is unsupported and must not be called exact.

## Suggested commit

Title: Preserve modern aquatic and crystal states in EFR conversion

Description: Correct EFR metadata for amethyst, pointed dripstone, tall seagrass, pickles and coral, preserve the observed powder-snow typo through a narrow alias, and support compact kelp ages with legacy BYTE Age fallback. Mark unsupported waterlogged mappings approximate. Add serialized chunk/NBT, coordinate-offset, save/reload and fallback coverage; all 218 tests and wheel/source builds pass.
