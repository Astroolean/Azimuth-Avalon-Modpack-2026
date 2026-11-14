> **Complete combined R23 release (14 November 2026):** This archive includes the runnable Azimuth application and the GitHub README/artwork together. You do not need a separate documentation ZIP. The original DayZ game and Workshop mods remain external dependencies. [Read the full project overview](../README.md) or [the Avalon map and loot audit](Audit/Avalon_Map_And_Loot_Verification_2026-11-14.md).

# Azimuth

DayZ Server Manager · R23 · Created by Ashton Kenney

**This is the complete Azimuth application ZIP, including its code, skins and bundled assets. Run `START_SERVER.bat` to open it.**

## Use the complete ZIP

1. In the older manager, stop Avalon and wait for DayZ Server to exit. Use **Backups → Back up now** and confirm that an archive was saved. Older releases did not automatically back up when you clicked Stop. Close DayZ and exit Azimuth.
2. Extract the ZIP's complete `Azimuth` folder to your usual location and allow the packaged application files to be replaced. The result must contain `Azimuth/START_SERVER.bat`, not `Azimuth/Azimuth/START_SERVER.bat`. There is no installer or updater program.
3. Keep your existing `Worlds`, `Config`, `Mods` and `Backups` folders. Do not delete the installation first. Your world, character, saved skin choices, go-kart/Gunter receipts and installed Workshop downloads stay in place.
4. Run `START_SERVER.bat` and click **Play Avalon**. Finish any Steam download or installation prompt. Keep DayZ and DayZ Server on the same current build; the manager now checks the pair before joining.

You do not need a new character, world reset, respawn, or a new round of manual model tests. Your existing settings remain the starting point. The go-kart showcase is optional and keeps its original one-time receipt; updating does not queue another vehicle.

Steam, DayZ, DayZ Server, DayZ Tools and the required Workshop downloads remain necessary. Changed generated mods are rebuilt and signed through the existing Windows setup flow.

## What this repair changes

- Unchecking finishes preserves the classes and assets needed by previously installed fixed-skin rifles and recorded test chests. Checked finishes still control new random choices. Missing/out-of-range test items can defer cleanup without permanently locking your choices.
- Stop now requests a clean shutdown and a final backup. Backup verification, shutdown evidence, schedule selection and restore handling have been strengthened. Invalid FPS backups are rejected before graphics changes.
- An-2 door validation, bicycle attachment fallback, the Humvee event bounds and missing ordinary loot routes are repaired. BeeHive companion recovery reads actual installed publisher definitions rather than inventing class names. The checker now catches the invalid roots, numbers, positions and attachment probabilities found in the audit.
- Vehicle compatibility contains the audited shared-default overrides, selects matching green Kamaz panels and corrects the three verified Cybertruck material references. Workshop failure displays and obsolete upload instructions are corrected.

Details and remaining limits are in [Full Audit Repairs](Audit/Full_Audit_Repairs.txt).

## Vehicle assets integrated

The supplied vehicle collection and full LM aircraft archive have now been inspected. No further export is required to use this release. Fourteen additional Workshop packs now contain 580 configured variants, including 12 LM aircraft. The normal Workshop page handles their installed dependencies.

This repair adds precise exterior selections from the supplied models, corrects missing material references, removes an unavailable optional ATV handlebar from new spawn recipes, and integrates LM aircraft attachments, placement and wrap cleanup. Your existing car, go-kart delivery receipt and saved choices are retained.

## Automatic vehicle placement setup

A server-only survey checks candidate Avalon locations for large vessels, the airship and separate additional-helicopter sites. It creates no vehicles and does not remove existing ones. Only a complete report matching this mission/request can supply new locations; valid dedicated locations already configured for the world are preserved.

**Play Avalon** displays **Checking Avalon vehicle placement sites** while waiting for up to two minutes. If validated sites or generated fishing setup need importing, it can perform one clean backup/restart before joining. If the survey takes longer, play continues with existing transport; accepted new locations are applied at a later server start. No manual coordinate survey is required.

Finding no suitable site is an explicit limitation, not permission to place a large vehicle at an unsafe small-boat or helicopter location. Reports are under `Worlds/avalon/Profiles/Azimuth`.

## Stop, back up and restore

In this repaired manager, **Stop** leaves Azimuth paused while the shutdown and final backup finish. Check **Backups** or **Activity** for the saved archive. If clean shutdown cannot be confirmed, it reports **Backup delayed**; it does not pretend a backup succeeded or force-kill the process. **Back up now** remains available for an empty online server or a cleanly stopped world.

To restore, close DayZ, stop the server, and wait for backup work to finish. In **Backups**, select an archive and choose **Restore selected**. The confirmation explains that it replaces the active world's characters, world/profile state, archived world settings and saved skin choices. It retains a safety copy, creates a safety ZIP when current save files exist, keeps current connection settings, and leaves the server stopped. The archive must match the selected world.

## Appearance coverage

The audited inventory contains 738 vehicle variants: 703 have an exterior mapping, 33 keep their stock body, and two support fitted panels only. A mapping can cover only an isolated exterior section; it does not guarantee a complete-body wrap. Airship banners remain paintable while its main hull stays stock. Humvee door Multi-material wrapping remains unresolved.

Some publisher models combine paint, windows and interior faces in the same section or expose no body section. Those are kept stock to avoid painting glass and interiors. Exact classes and limits are listed in [Vehicle coverage](Guides/VEHICLE_WRAP_COVERAGE.txt). Horse bodies and unsupported train systems are outside this wrap mechanism.

The supplied Gunter screenshot confirms that car visually. This release was checked through actual model/configuration inspection, Python checks and extracted-code tests. Native Windows DayZ compilation, rendering, flight and multiplayer were not executed here. You are not being asked to test every model.

## Files and optional checks

All paths are relative to the main `Azimuth` folder.

| Folder | Purpose |
| --- | --- |
| `Docs/Guides` | Current controls, transportation and coverage guides |
| `Docs/Audit` | Repair notes and clearly retained earlier audit reports |
| `Docs/Reference` | Layout reference and historical release notes |
| `Engine/azimuth_app` | Application code grouped by responsibility |
| `Engine/Archive` | Verified recovery archives from the old layout |
| `Custom`, `Visuals`, `WorldSetup` | Bundled sources, artwork and setup data |
| `Worlds`, `Config`, `Mods` | Existing world data, choices and installed/generated mods |
| `Backups` / `Worlds/<world>/Backups` | World backups and pre-restore safety copies |

After a normal stop, `Tools/VERIFY_PLAYTHROUGH.bat` checks installed mission/configuration data and `Tools/VERIFY_GUN_SKINS.bat` checks bundled assets and temporary generated packs. These are optional diagnostics, not native gameplay certification or a manual test assignment.

If setup needs attention, the Activity message and **Open logs folder** identify the next issue. Repeated failed starts and an unresponsive startup pause automatic restarting.
