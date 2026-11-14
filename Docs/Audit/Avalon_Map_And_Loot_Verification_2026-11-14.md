# Avalon · Map & loot-placement verification

**Evidence date:** 14 November 2026  
**Basis:** Original Workshop Avalon map (Workshop ID `3225634444`), its supplied `metro.Avalon` mission and a temporary Azimuth-generated economy fixture using the latest R23 application.  
**Mode:** Read-only static analysis. **No original Avalon map, player data, installed Workshop assets, or application source modified.**

## Verified original map records

| Check | Result |
|:---|---:|
| Original placement files checked against publisher archive | **10/10 SHA-256 matches** |
| Building group positions | **4,261**, all finite |
| Distinct placed building classes | **407** |
| Usable, unambiguous placed group prototypes with loot containers | **387** |
| Placed buildings belonging to usable loot groups | **4,176** |
| Valid category/tag containers among usable groups | **676** |
| Natural-resource/cluster placement records | **225,015** across 6 XML files |
| Cluster prototype classes represented | **50 / 50** |
| Generated-mission event position records | **593**, all finite |
| Generated-mission referenced CE extension files present | **31/31** |
| CE types in generated economy | **5,310** |
| Positive ordinary-loot types | **1,679** |
| CE XML/contained-loot warnings | **0/0** |

`cfgeconomycore.xml` references all 31 existing generated economy extension files. The 11 publisher-origin map placement files used for prototypes, positions, and cluster placement were byte-identical to the uploaded map. This check covers **static XML structure and coordinates**; it does not simulate the world or the CE runtime.

## Static loot-placement outcomes

| Ordinary-loot category | Count | Interpretation |
|:---|---:|:---|
| Full category/usage/tier match to placed loot prototype | **1,282** | Static route supported; live population not measured |
| Placed category/usage group; region tier still unresolved | **320** | Actual area flags/engine CE need native confirmation |
| No complete static prototype match | **64** | May be other systems, dispatch proxies, publisher scripts or genuine gaps |
| Item has no explicit category for this checker | **13** | Static result is inconclusive without runtime/category resolution |
| **Total** | **1,679** | All ordinary loot classes accounted for in this classification |

Of the **64** unmatched classes, **48** are Hunter Mods (`HM_...`) entries, and **7** are vehicle wheels using `lootdispatch` category; the remaining 9 are food/miscellaneous classes. This is a **diagnostic breakdown**, not a verdict that any specific class is impossible to obtain. The 13 no-category cases include guitar tabs and candy canes. Full names and original flags appear in [Unresolved static placement CSV](Avalon_Static_Placement_Unresolved_2026-11-14.csv).

## Important existing map ambiguities

The publisher prototype XML contains **9** class names with duplicate prototype declarations, of which **8** are used by placed buildings. Our conservative checker excludes these duplicated placed prototypes from certainty because it cannot establish which duplicate DayZ will use. There are also **10** placed group classes with no matching prototype in this standalone check. These findings are *pre-existing authored map evidence*, not changes caused by Azimuth or proof that DayZ rejects the map.

Map `env/` territory data and DayZ's built-in economy also supply animal/infected/loot events. For that reason, a base `db/events.xml` name without a direct `cfgeventspawns.xml` entry is **not** by itself a missing event. Vehicle events can instead originate in the 31 registered Azimuth extension files. The event table was checked for finite coordinates; it was **not** used to fabricate spawn sites for the airship, large boats, or special aircraft.

## Remaining native qualification

DayZ uses actual terrain area-flag regions, object footprints, container occupancy, collision/clearance, event scheduling and publisher script behavior that cannot be completely derived from these XML files alone. The map's `areaflags.map` is present in the publisher package, but its complete regional runtime interpretation was **not independently certified**. Actual loot appearance, CE respawn cycles, scripted containers, water depth and aircraft landing clearance therefore require a real DayZ server session.

**Correct claim for GitHub:** "The Avalon economy and map placements passed a comprehensive source-backed offline configuration audit; full live-server behavior is not certified by the offline audit."

Source: Bohemia Interactive's [Central Economy setup for custom terrains](https://community.bohemia.net/wiki/DayZ:Central_Economy_setup_for_custom_terrains).
