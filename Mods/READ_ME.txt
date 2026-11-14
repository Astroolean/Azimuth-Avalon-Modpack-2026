DROP IN MODS HERE

1. Stop the server.
2. Drop each complete, extracted mod folder directly into Mods.
   Example: Mods\@Example\Addons\example.pbo
3. Start the server again.

The dashboard loads the folders automatically and copies public .bikey files
from each mod's Keys folder to DayZ Server's keys folder. Share the required
client mods with everyone joining. Put a mod in ServerOnly only if its author
specifies server-side loading.

Optional: If a mod requires a specific load order, create load-order.txt in
this folder (or inside ServerOnly for that folder). Enter one exact mod folder
name per line, dependencies first. Folders you omit follow alphabetically.
Some mods require additional settings or mission changes in their own guide.

To remove a mod, stop the server and move its folder out of Mods. Removing a
mod from an existing world can affect items added by that mod.
