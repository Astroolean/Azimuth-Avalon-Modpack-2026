# Azimuth / Avalon — Workshop-backed loot economy qualification

**Date:** 14 November 2026  
**Basis:** The last user-confirmed working Azimuth R23 Go-Kart + Parts Crate package, the original Avalon map mission, and all 49 uploaded Workshop ZIPs.  
**Scope:** Economy configuration only. No Workshop PBOs, gameplay mods, original vehicle configs, player saves or existing launcher scripts are changed.

## Results

| Metric | Before | After |
|---|---:|---:|
| Effective CE item/entity class definitions | 4,887 | 5,310 |
| Positive ordinary-loot classes | 1,388 | 1,679 |
| Economy XML / contained-loot validation warnings | 0 | 0 |

- All **1,350** positive publisher-authored ordinary-loot rows for the audited active Workshop mods resolve to natural CE classes; **0** remain missing by that cross-check.
- **76** Workshop mod folders; **391** PBO files, including four originally split, SHA-256-verified PBO archives.
- **375** PBOs were enumerated with the strict local parser; **16** require different-header handling and **29** config entries require additional decoding. Those are reader limitations, not proof of game bugs.
- **12,048** compiled class declarations were inventoried across readable configs. These include abstract and technical classes, **not just world-loot items**.
- **24** release regression assertions passed, including exact preservation of all **304** previous baseline files outside the two economy integration modules, and stable repeat-start regeneration.

## Repairs added

1. Import source-verified RaG BaseItems loot rows missing from Azimuth while preserving all authored crafted, event-only, and zero-population restrictions. Keep previously curated building kits and supplies unchanged.
2. Restore author-listed BeeHive equipment and empty honey-glass loot in addition to the existing honey-making tools and production kits.
3. Restore source-verified RaG vehicle spares and Maharlika wooden-boat engine, without turning vehicle cosmetic bodies or unverified parts into ordinary ground loot.
4. Correct the missing Avalon container category or unusable placement restriction for two RaG BaseItems kits, the GAZ-69 trunk and the SchoolBus hood. Keep low-rate normal wheel-spare behavior intact.
5. Import only when SHA-256 of publisher XML matches the inspected Workshop release **and** the class was confirmed concrete in the examined mod PBO. Changed publisher files trigger a notice, not guessed replacements. Existing verified generated rows remain available if a publisher file changes.
6. Keep the previous Azimuth loot balance, original map, native vehicle recipes, player data, random skins, go-kart parts crate, compatibility patches and one-click launcher unchanged.

## Verification details

- Economy regenerated against a temporary original Avalon mission with all active Workshop assets represented in the fixture.
- CE output passed XML and duplicate checks. Source names, ordinary-loot classification and placed category/usage locations were cross-referenced against the map.
- Re-running the generator left all audited generated economy XML files and transport status records byte-identical. All item nominal targets remained stable.
- A simulated updated publisher XML was rejected by fingerprint validation without changing the previously registered verified classes.
- **397** naturally positive classes cannot be certified against every author-specified static group filter by the limited prototype-only checker. Many are vanilla items controlled by tier regions, map-zone flags or event systems; this number is **not a confirmed count of inaccessible loot**.

## Remaining native tests (cannot be honestly certified offline)

The server must run on your Windows PC to confirm natural loot appearance across regions over normal CE cycles, real spawn capacity, equipment attachment behavior and vehicle handling. Test crafted base-building, honey production, fishing, school-bus and GAZ-69 repairs, aircraft and large-water spawn locations. Large ships and airships are held without unsafe coordinates unless you provide authored placements. Native DayZ server log validation remains the final gate; **a mathematically flawless spawn economy cannot be guaranteed from files alone.**

## Files in this qualification

- `Avalon_Effective_Item_Loot_Audit_2026-11-14.csv`: one row per effective CE class with nominal target, category, source and placement confidence.
- `Avalon_Workshop_Publisher_Loot_Crosscheck_2026-11-14.csv`: every standalone publisher XML type and whether the activated economy covers it.
- `Avalon_Workshop_Mod_Coverage_2026-11-14.csv`: each uploaded mod folder, PBO/config coverage, and active status.
- `release_audit_summary.json`: precise machine-readable counts and limits.

**Installation:** Replace the existing Azimuth application files with the full replacement ZIP as usual. Do not delete any running server's mission storage or player save data. Allow the launcher to regenerate only its Azimuth-owned CE extensions at startup.
