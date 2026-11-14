# Code layout

`START_SERVER.bat` opens `Engine/launch.py`. The bootstrap routes to the dashboard or an optional read-only verification command. Python 3.10 or newer, 64-bit, with Tcl/Tk is required on Windows.

Application modules live in `Engine/azimuth_app` and use explicit package imports.

| Package | Responsibility |
| --- | --- |
| `core` | Server lifecycle, controller, world selection, backups and layout migration |
| `ui` | Control-panel pages, theme and process display |
| `client` | Game launching, Steam session checks, profiles and graphics settings |
| `economy` | Mission XML, loot configuration, balance and audits |
| `transport` | Land vehicles, aircraft, horses and game-version requirements |
| `world` | Avalon atmosphere, building, immersion, airdrops and other world setup |
| `cosmetics` | Skin catalog, random finishes, showcases, weapon inspect and photo mode |
| `integrations` | Signing and compatibility work for supported third-party mods |
| `network` | Server discovery and queries |
| `checks` | Read-only playthrough and skin verification |

`paths.py` supplies installation and asset roots independently of the working directory. `Engine/Assets` holds panel artwork. The native mod sources remain under `Custom`, `Visuals` and `WorldSetup` so their established game paths are preserved.

The layout migration runs after the dashboard claims its existing instance lock and before the server controller is constructed. Its explicit manifest names only files from the former flat layout. Those files are copied into a verified ZIP under `Engine/Archive` before cleanup. Current package files, unknown custom files and runtime world data are outside that manifest.

The optional tools call `Engine/launch.py check-playthrough` and `Engine/launch.py check-skins`. Neither performs the layout migration or starts DayZ.
