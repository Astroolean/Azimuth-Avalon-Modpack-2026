"""Set up Airdrop-Upgraded for solo Avalon using player-called supply drops."""

import json
from pathlib import Path
import stat

from azimuth_app.economy.avalon_economy import register_bytes
from azimuth_app.economy.avalon_loot_audit import read_economy
from azimuth_app.core.backups import source_type
from azimuth_app.core.worlds import atomic_text


ITEMS = (("1870524790", "Airdrop-Upgraded"),)
ECONOMY_SLUG = "airdrop-upgraded"
FLARE_TYPE = "AirdropFlare"
CONFIG_LIMIT = 64 * 1024
FLARE_TYPES = b'''<?xml version="1.0" encoding="UTF-8"?>
<types>
    <type name="AirdropFlare">
        <nominal>12</nominal>
        <lifetime>14400</lifetime>
        <restock>1800</restock>
        <min>6</min>
        <quantmin>-1</quantmin>
        <quantmax>-1</quantmax>
        <cost>100</cost>
        <flags count_in_cargo="0" count_in_hoarder="0" count_in_map="1" count_in_player="0" crafted="0" deloot="0" />
        <category name="tools" />
        <usage name="Town" />
        <usage name="Village" />
        <usage name="Police" />
        <usage name="Military" />
    </type>
</types>
'''


def _vpp_map_defaults() -> dict:
    # Even with map export off, the publisher deserializes a fixed RGB array.
    # These are the defaults it wrote after the observed missing-array error.
    return {
        "ExportMap": 0, "TitleMode": 0, "MapColor": [0, 0, 0],
        "MapIcon": "", "IsActive": 0, "Is3DActive": 0,
    }


def _solo_settings() -> dict:
    # The mod derives map bounds from the loaded terrain. This release has no
    # surveyed Avalon landing zones, so only the reserved flare entry is seeded.
    # The current guide documents |TOKEN| substitution. Its sample JSON is for
    # 1.25.03.10 and uses the older bracket form.
    return {
        "Controls": {
            "Version": "1.26.01.04",
            "Description": "Avalon solo supply drops",
            "Interval": 60,
            "Variance": 10,
            "FlightHours": [0, 0, 23, 59],
            "Mode": 2,
            "AD_LogManager": 1,
            "AD_LogAircraft": 1,
            "AD_LogContainer": 1,
            "MinimumPlayers": 1,
            "MaxBackupDays": 7,
            "MaxLogDays": 7,
            "SmokeTrails": 1,
        },
        "Map": {"MapName": "avalon"},
        "Aircraft": {
            "AirSpeedKIAS": 150, "StartAltMSL": 2400, "DropAGL": 152,
            "DropOffset": -10, "DropAccuracy": 20, "TerrainFollowing": 0.5,
        },
        "Messages": {
            "Mode": 3, "Duration": 15, "Proximity": 1000, "ImperialUnits": 0,
            "TitlePostfixMode": 0,
            "Dispatched_S": "Supply aircraft dispatched to |DROPZONE| from the |CARDINAL|.",
            "Dispatched_C": "Supply aircraft dispatched to your flare from the |CARDINAL|.",
            "Proximity_S": "Supply aircraft is approaching |DROPZONE| from the |CARDINAL|.",
            "Proximity_C": "Supply aircraft is approaching your flare from the |CARDINAL|.",
            "Released_S": "Supplies dropped near |DROPZONE|.",
            "Released_C": "Supplies dropped near your flare.",
        },
        "Container": {
            "TriggerAGL": 0.1, "FallRate": 3.5, "StandUpTimer": 0.1,
            "SpawnMin": 2.0, "SpawnMax": 5.0, "SpawnOffset": 0.01,
            "WindStrength": 0.1, "Lifespan": 45,
        },
        "Locations": [{
            "Title": "Avalon Infected",
            "Zombies": ["ZmbM_CitizenBFat_Blue", "ZmbF_CitizenANormal_Beige",
                        "ZmbM_FarmerFat_Blue", "ZmbF_VillagerOld_Green"],
        }],
        "DropZones": [{
            "Title": "Player Called", "Location": "Avalon Infected", "DropType": "Survival Supplies",
            "x": 0.0, "z": 0.0, "Zombies": 3, "Radius": 35, "DropAccuracy": 20,
        }],
        "DropTypes": [{
            "Title": "Survival Supplies", "Container": "AirdropContainer", "Quantity": 6,
            "AddFlare": 60, "SpawnMin": 2.0, "SpawnMax": 5.0, "SpawnOffset": 0.01,
            "ItemCondition": {"MinCondition": 65, "MaxCondition": 100, "Samples": 1},
            "Lifespan": 45,
            "Items": ["BandageDressing", "Canteen", "DuctTape", "HuntingKnife", "SardinesCan",
                      "TacticalBaconCan", "TetracyclineAntibiotics", "WaterBottle", "NailBox",
                      "Hammer", "HandSaw", "Hatchet", "WoodenPlank"],
        }],
        "VPP_Map": _vpp_map_defaults(),
    }


def _profile_config(path: Path) -> str | None:
    """Fill missing fixed-array settings and preserve populated user choices."""
    if not path.exists() and not path.is_symlink():
        return json.dumps(_solo_settings(), indent=2, allow_nan=False) + "\n"
    if not stat.S_ISREG(source_type(path)):
        raise ValueError("Avalon AirdropSettings.json must be a regular file.")
    try:
        with path.open("rb") as stream:
            source = stream.read(CONFIG_LIMIT + 1)
        if len(source) > CONFIG_LIMIT:
            raise ValueError("settings exceed the game's JSON size limit")
        config = json.loads(source.decode("utf-8-sig"))
        controls = config.get("Controls") if isinstance(config, dict) else None
        if not isinstance(controls, dict):
            raise ValueError("the Controls object is missing")
        changed = False
        if "VPP_Map" not in config:
            config["VPP_Map"] = _vpp_map_defaults()
            changed = True
        vpp_map = config["VPP_Map"]
        if not isinstance(vpp_map, dict):
            raise ValueError("VPP_Map must be an object")
        if "MapColor" not in vpp_map:
            vpp_map["MapColor"] = [0, 0, 0]
            changed = True
        color = vpp_map["MapColor"]
        if (not isinstance(color, list) or len(color) != 3
                or any(type(value) is not int or not 0 <= value <= 255 for value in color)):
            raise ValueError("VPP_Map.MapColor must contain exactly three RGB integers from 0 to 255")
        # The author specifically identifies AD_LogContainer and advises
        # turning the other explicitly disabled logging switches back on.
        fields = [key for key in ("AD_LogContainer", "AD_LogManager", "AD_LogAircraft", "ADU_LogMessage")
                  if key in controls and controls[key] in (0, "0")]
        if fields:
            for key in fields:
                controls[key] = 1
            changed = True
        if changed:
            return json.dumps(config, indent=2, allow_nan=False) + "\n"
    except (UnicodeError, ValueError) as error:
        raise ValueError(f"Invalid Avalon AirdropSettings.json: {error}.") from error
    return None


def prepare(mission: Path, profiles: Path, mods: dict[str, Path]) -> tuple[str, ...]:
    """Prepare the per-world profile and enable flares through Avalon's economy."""
    item_id, name = ITEMS[0]
    if item_id not in mods or not mods[item_id].is_dir():
        raise ValueError(f"The {name} Workshop files are missing.")

    mission = mission.resolve()
    settings_folder = profiles / "Airdrop"
    settings_path = settings_folder / "AirdropSettings.json"
    if settings_folder.exists() or settings_folder.is_symlink():
        if not stat.S_ISDIR(source_type(settings_folder)):
            raise ValueError("The Avalon Airdrop profile path must be a directory.")
    config = _profile_config(settings_path)
    economy = read_economy(mission, exclude_folders=(f"azimuth_workshop/{ECONOMY_SLUG}",))
    custom_flare = economy.types.get(FLARE_TYPE.casefold())
    if custom_flare is None:
        register_bytes(mission, ECONOMY_SLUG, {"types": ("airdrop_flares.xml", FLARE_TYPES)})
    if config is not None:
        settings_folder.mkdir(parents=True, exist_ok=True)
        atomic_text(settings_path, config)
    if custom_flare is None or custom_flare.natural:
        return ()
    reason = {"crafted": "marked as crafted", "event": "assigned to an event",
              "zero": "nominal is zero", "unknown": "no positive nominal is configured"}[custom_flare.spawn_kind]
    source = custom_flare.source.relative_to(mission).as_posix()
    return (f"Airdrop-Upgraded: Avalon’s own flare entry in {source} is {reason}. "
            "Check that flares are obtainable before relying on player-called drops.",)
