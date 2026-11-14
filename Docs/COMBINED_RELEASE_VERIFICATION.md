# Azimuth R23 — Complete combined release verification

**Built:** 14 November 2026  
**Inputs:** `Azimuth_DayZ_R23_Avalon_Workshop_Verified_Loot_Economy_FULL.zip` + `Azimuth_GitHub_Complete_Ready_To_Upload_R3_Avalon_Map_Loot_Audit.zip`.

## What is included

- Entire working Azimuth Windows application and its current launcher / custom-addon sources.
- The last qualified Avalon loot-economy integration, publisher fingerprints, guides and records.
- Original GitHub presentation README and full artwork atlas.
- Additional GitHub-facing Avalon static map analysis and placement evidence.

## Preservation rule

**Every original file from the latest full application ZIP is preserved byte-for-byte, except for documentation-only files `Docs/START_HERE.md` and `Docs/Audit/README.md`, which are expanded for the combined release.** No gameplay, Python source, custom scripts, launcher, asset, mod, or save data is changed by this merge.

## Economy and map verification

The preceding source-backed offline qualification recorded **5,310 effective economy classes, including 1,679 ordinary-loot classes**, zero XML/contained-loot warnings and 24 economy regression assertions. The separate Avalon static map audit recorded **4,261 valid building placements**, **225,015 natural/cluster placements**, **593 explicit event coordinates**, **31/31 referenced economy imports present**, and a class-by-class placement confidence classification.

These are **static/offline** results, not an exhaustive live server guarantee. Some loot still depends on tier flags, actual CE cycles, publisher scripts, area flags and live container occupancy. Air/water vehicles may need native placement checks. The detailed source of each number is in `Docs/Audit/`.

## Release validation

Checks run against the combined application staging tree:

| Check | Result |
|---|---|
| Combined ZIP archive CRC and one-folder layout | **PASS** |
| Runnable application code and installed assets compared with baseline ZIP | **PASS — no runnable or gameplay source changed** |
| Python source parse/compile (in-memory) | **82/82 PASS** |
| Built-in skin/PBO source checks | **PASS** |
| Economy regression re-run with merged code, Avalon map fixture and publisher files | **24/24 PASS** |
| Generated loot/XML checks | **PASS — 5,310 classes, 1,679 ordinary, zero reported warnings** |
| Repeat-start economy data equality | **PASS** |
| README relative file/image references | **100/100 resolve** |
| Standalone playthrough checker without running DayZ | **REVIEW (no installed live mission staged; expected in an isolated release folder)** |

**The combined package has not been executed as a DayZ game/server on Windows.** The playthrough checker needs a prepared installed mission to inspect. Windows process startup, mod key synchronization and live loot/vehicle behavior require the installed game; they must not be described as certified by offline tests.

## Third-party content

The supplied Workshop mods are inspected as evidence but are not packaged. Third-party rights and publisher licenses must be reviewed before public redistribution of non-original media.
