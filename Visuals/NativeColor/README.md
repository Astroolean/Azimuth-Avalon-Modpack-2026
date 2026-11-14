# Avalon Looks source

The `azimuthlooks` folder is the PBO source root and virtual prefix. This DayZ client addon adds a warm tint and a small contrast adjustment outdoors during Avalon's daylight hours, between 08:00 and 17:00 game time.

The grade uses its own low-priority post-process requester. It suspends when DayZ starts a recognized vision effect, including blood loss, aiming optics, night vision, contamination, or underground eye adjustment. The requester stops when the mission ends.

Before enabling a build, compare the same Avalon scene with and without the addon on DayZ PC 1.30.164289. Check the daylight grade, ungraded dusk and night, blood loss, gas, optics, flashbangs, unconsciousness, tunnels, reconnecting, and loading a different map. Compare black levels in a dark room and at night. Script signature checks do not measure the rendered result.

[DayZ mod structure and PBO signing](https://community.bistudio.com/wiki/DayZ:Modding_Structure) · [Bohemia's requester registration API](https://github.com/BohemiaInteractive/DayZ-Script-Diff/blob/main/scripts/3_game/ppemanager/pperequesterbank.c) · [Vanilla colorization and contrast usage](https://diff.yadz.app/files/3_Game/PPEManager/Requesters/ppersandstorm.c/)
