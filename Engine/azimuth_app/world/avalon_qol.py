"""Prepare Avalon's map overlay and native new-character loadout."""
from azimuth_app.paths import APP_ROOT
import json
from pathlib import Path
from xml.etree import ElementTree

from azimuth_app.economy.avalon_economy import register_bytes
from azimuth_app.core.backups import source_type
from azimuth_app.economy.hunter_economy import _read_xml, _write_core
from azimuth_app.cosmetics.native_color_build import build_script_pbo
from azimuth_app.core.worlds import atomic_text

GAMEPLAY_NAME = "cfggameplay.json"
KIT_PATH = Path("Hunter_Mods/HM_Starter_Kit/HM_Settings_Starter_Kit.json")
STARTER_PATH = Path("azimuth_loadouts/avalon_starter.json")
STARTER_NAME = "Avalon Shore Kit"
MAP_SETTINGS = {
    "ignoreMapOwnership": True,
    "ignoreNavItemsOwnership": True,
    "displayPlayerPosition": True,
}
STARTER_CLOTHING = {
    "Body": "Hoodie_Grey",
    "Legs": "Jeans_BlueDark",
    "Feet": "AthleticShoes_Black",
    "Back": "Slingbag_Black",
}
STARTER_SUPPLIES = ("Apple", "SodaCan_Cola", "BandageDressing", "StoneKnife", "Matchbox", "Chemlight_Green")
COMFORT_SPRINT = ("sprintStaminaModifierErc", "sprintStaminaModifierCro")
COMFORT_PLACEMENT = ("disableIsClippingRoofCheck", "disableIsCollidingAngleCheck")
LOGOUT_SOURCE = APP_ROOT / "WorldSetup" / "avalon" / "AzimuthLogout"
LOGOUT_PREFIX = "azimuthlogout"
LOGOUT_GLOBALS = b'<variables>\n    <var name="TimeLogout" type="0" value="0"/>\n</variables>\n'


def prepare_logout(mission: Path, config: Path) -> Path:
    """Remove the logout countdown and load the matching server-only rule."""
    mod = config / "Logout" / "@AzimuthLogout"
    build_script_pbo(LOGOUT_SOURCE, mod, LOGOUT_PREFIX)
    if (mission / "cfgeconomycore.xml").exists():
        register_bytes(mission, "azimuth_logout", {"globals": ("globals.xml", LOGOUT_GLOBALS)})
    else:
        path = mission / "db" / "globals.xml"
        if path.exists():
            source_type(path)
            variables = _read_xml(path, "variables", preserve_comments=True)
        else:
            variables = ElementTree.Element("variables")
        entry = variables.find("var[@name='TimeLogout']")
        if entry is None:
            ElementTree.SubElement(variables, "var", name="TimeLogout", type="0", value="0")
            _write_core(path, variables)
        elif entry.get("value") != "0":
            entry.set("value", "0")
            _write_core(path, variables)
    return mod


def _load(path: Path, default: dict) -> tuple[dict, bytes | None]:
    if not path.exists():
        return default, None
    try:
        original = path.read_bytes()
        data = json.loads(original.decode("utf-8-sig"))
    except (OSError, UnicodeError, ValueError) as error:
        raise ValueError(f"Could not read Avalon configuration {path.name}: {error}") from error
    if not isinstance(data, dict):
        raise ValueError(f"Expected a JSON object in Avalon configuration {path.name}.")
    return data, original


def _write(path: Path, values: dict) -> None:
    atomic_text(path, json.dumps(values, indent=4, ensure_ascii=False) + "\n")


def _section(config: dict, key: str) -> dict:
    section = config.setdefault(key, {})
    if not isinstance(section, dict):
        raise ValueError(f"Avalon cfggameplay.json {key} must be a JSON object.")
    return section


def _configure_gameplay(config: dict) -> tuple[bool, bool]:
    map_data = _section(config, "MapData")
    player = _section(config, "PlayerData")
    ui = config.get("UIData")
    if "UIData" in config and not isinstance(ui, dict):
        raise ValueError("Avalon cfggameplay.json UIData must be a JSON object.")
    needs_2d_map = ui is not None and ui.get("use3DMap") in (True, 1)
    needs_map_settings = any(map_data.get(key) is not value for key, value in MAP_SETTINGS.items())
    old_presets = player.get("spawnGearPresetFiles")
    new_presets = [STARTER_PATH.as_posix()]
    needs_starter = old_presets != new_presets
    if needs_2d_map:
        ui["use3DMap"] = False
    map_data.update(MAP_SETTINGS)
    player["spawnGearPresetFiles"] = new_presets
    return needs_map_settings or needs_2d_map or needs_starter, bool(old_presets) and needs_starter


def _fixed_attributes(health: float) -> dict:
    return {"healthMin": health, "healthMax": health, "quantityMin": 1.0, "quantityMax": 1.0}


def _spawn_preset() -> dict:
    return {
        "name": STARTER_NAME,
        "spawnWeight": 1,
        "attachmentSlotItemSets": [
            {
                "slotName": slot,
                "discreteItemSets": [{
                    "itemType": item,
                    "spawnWeight": 1,
                    "attributes": _fixed_attributes(0.8),
                    "quickBarSlot": -1,
                }],
            }
            for slot, item in STARTER_CLOTHING.items()
        ],
        "discreteUnsortedItemSets": [{
            "name": "Shore supplies",
            "spawnWeight": 1,
            "attributes": _fixed_attributes(1.0),
            "simpleChildrenUseDefaultAttributes": False,
            "simpleChildrenTypes": list(STARTER_SUPPLIES),
        }],
    }


def _disabled_hunter_kit() -> dict:
    # Hunter's notifications can appear even with empty loot lists.
    return {
        "notificationTitle": STARTER_NAME,
        "notificationMessageAvailable": "New characters receive their supplies automatically.",
        "notificationMessageClaimed": "Your starter supplies are issued when you spawn as a new character.",
        "notificationMessageAlreadyClaimed": "Your starter supplies are issued when you spawn as a new character.",
        "alwaysGiveKitOnRespawn": 0,
        "starterKitList": [],
        "alwaysGiveVIPKitOnRespawn": 0,
        "vipSteamIdList": [],
        "starterVIPKitList": [],
    }


def _save_original(path: Path, original: bytes) -> None:
    try:
        backup = path.open("xb")
    except FileExistsError:
        return
    try:
        with backup:
            backup.write(original)
    except OSError:
        path.unlink(missing_ok=True)
        raise


def prepare(mission: Path, profiles: Path, hunter: bool) -> None:
    """Apply the map overlay and publish one native spawn preset before boot."""
    gameplay_path = mission / GAMEPLAY_NAME
    gameplay, original_gameplay = _load(gameplay_path, {"version": 130})
    gameplay_changed, had_other_presets = _configure_gameplay(gameplay)
    starter_path = mission / STARTER_PATH
    previous_starter, original_starter = _load(starter_path, {})
    starter = _spawn_preset()
    hunter_path = profiles / KIT_PATH
    previous_hunter, original_hunter = _load(hunter_path, {}) if hunter else ({}, None)
    hunter_settings = {**previous_hunter, **_disabled_hunter_kit()} if hunter else {}

    if original_gameplay is not None and had_other_presets:
        _save_original(gameplay_path.with_suffix(".before-native-starter.json"), original_gameplay)
    if starter != previous_starter:
        if original_starter is not None:
            _save_original(starter_path.with_suffix(".before-native-starter.json"), original_starter)
        starter_path.parent.mkdir(parents=True, exist_ok=True)
        _write(starter_path, starter)
    if hunter_settings != previous_hunter:
        if original_hunter is not None:
            _save_original(hunter_path.with_suffix(".before-native-starter.json"), original_hunter)
        hunter_path.parent.mkdir(parents=True, exist_ok=True)
        _write(hunter_path, hunter_settings)
    if original_gameplay is None or gameplay_changed:
        _write(gameplay_path, gameplay)


def prepare_comfort(mission: Path) -> None:
    """Apply the opt-in comfort settings without overriding custom stamina."""
    path = mission / GAMEPLAY_NAME
    config, original = _load(path, {"version": 130})
    stamina = _section(_section(config, "PlayerData"), "StaminaData")
    hologram = _section(_section(config, "BaseBuildingData"), "HologramData")
    changed = original is None
    for key in COMFORT_SPRINT:
        current = stamina.get(key, 1.0)
        if type(current) in (int, float) and current == 1.0:
            stamina[key] = 0.8
            changed = True
    for key in COMFORT_PLACEMENT:
        current = hologram.get(key, False)
        if current is False or (type(current) is int and current == 0):
            hologram[key] = True
            changed = True
    if changed:
        _write(path, config)
