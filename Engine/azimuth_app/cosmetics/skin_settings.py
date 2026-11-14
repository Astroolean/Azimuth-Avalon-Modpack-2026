"""Persistent choices for Azimuth's local cosmetic library and showcase kit."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re
import stat
import uuid

from azimuth_app.core.backups import source_type
from azimuth_app.core.worlds import atomic_text

SETTINGS_PATH = Path("Config/GunSkins/settings.json")
APPLIED_PATH = Path("Config/GunSkins/applied.json")
SHOWCASE_RECEIPT_PATH = Path("Worlds/avalon/Profiles/AzimuthSkins/showcase_state.json")
RELIC_SKIN_IDS = (
    "cathedralash", "deadfrequency", "lasttrain", "floodline", "blackbox",
    "ghostanatomy", "evidencelocker", "funeralporcelain", "mothkeeper", "mycelium",
    "redactedatlas", "foundrybloom", "deadchannel", "rationzero",
)
CARTOON_SKIN_IDS = (
    "bobsburgers", "thesimpsons", "familyguy", "southpark", "americandad",
    "rickandmorty", "futurama", "kingofthehill", "bojackhorseman", "archer",
)
RELIC_CATALOG_REVISION = "field_relics_14"
CATALOG_REVISION = "adult_animation_10"
DEFAULT_SELECTED = ("neon420", "cosmickush420", "deeporbit", *RELIC_SKIN_IDS, *CARTOON_SKIN_IDS)
GENERATION = re.compile(r"[a-f0-9]{32}\Z")
MAX_JSON = 128 * 1024
INITIAL_GENERATION = "00000000000000000000000000000002"
INITIAL_VEHICLE_TEST_GENERATION = "00000000000000000000000000000003"
STEAM_INDIVIDUAL_BASE = (1 << 56) | (1 << 52) | (1 << 32)


def _catalog_ids(catalog: dict) -> list[str]:
    return [skin["id"] for skin in catalog["skins"]]


def _read(path: Path) -> dict | None:
    if not path.exists() and not path.is_symlink():
        return None
    if not stat.S_ISREG(source_type(path)):
        raise ValueError(f"Expected an ordinary skin settings file: {path.name}.")
    with path.open("rb") as stream:
        raw = stream.read(MAX_JSON + 1)
    if len(raw) > MAX_JSON:
        raise ValueError("The skin settings file is too large.")
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except (ValueError, UnicodeError) as error:
        raise ValueError(f"Could not read {path.name}; restore its valid settings before continuing.") from error
    if not isinstance(value, dict):
        raise ValueError("Skin settings must be a JSON object.")
    return value


def _write(root: Path, relative: Path, value: dict) -> None:
    root = root.resolve()
    path = root / relative
    for folder in reversed(path.parent.parents):
        if folder == root or folder.is_relative_to(root):
            if folder.exists() or folder.is_symlink():
                if not stat.S_ISDIR(source_type(folder)):
                    raise ValueError("Expected an ordinary skin settings folder.")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not stat.S_ISDIR(source_type(path.parent)):
        raise ValueError("Expected an ordinary skin settings folder.")
    if path.exists() or path.is_symlink():
        if not stat.S_ISREG(source_type(path)):
            raise ValueError("Expected an ordinary skin settings file.")
    atomic_text(path, json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def validate(value: dict, catalog: dict) -> dict:
    if value.get("schema_version") != 1:
        raise ValueError("Unsupported skin settings version.")
    ids = value.get("selected_skin_ids")
    available = _catalog_ids(catalog)
    if (not isinstance(ids, list) or any(not isinstance(identity, str) for identity in ids)
            or len(ids) != len(set(ids)) or not set(ids) <= set(available)):
        raise ValueError("The selected skins contain an unknown or duplicate finish.")
    enabled = value.get("test_kit_enabled")
    generation = value.get("test_kit_generation")
    steam_id = value.get("target_steam_id", "")
    cleanup_generation = value.get("cleanup_generation", "")
    random_enabled = value.get("random_skins_enabled", True)
    random_chance = value.get("random_skin_chance_percent", 20)
    vehicle_enabled = value.get("vehicle_wraps_enabled", True)
    vehicle_chance = value.get("vehicle_wrap_chance_percent", 100)
    vehicle_test_enabled = value.get("vehicle_test_enabled", True)
    vehicle_test_generation = value.get("vehicle_test_generation", INITIAL_VEHICLE_TEST_GENERATION)
    revision = value.get("catalog_revision", CATALOG_REVISION)
    if type(enabled) is not bool:
        raise ValueError("The showcase kit choice must be on or off.")
    if not isinstance(generation, str) or not GENERATION.fullmatch(generation):
        raise ValueError("The showcase kit generation is invalid.")
    if not isinstance(steam_id, str) or (steam_id and (
            not re.fullmatch(r"[0-9]{17}", steam_id)
            or not STEAM_INDIVIDUAL_BASE < int(steam_id) <= STEAM_INDIVIDUAL_BASE + 0xFFFFFFFF)):
        raise ValueError("The showcase recipient must be a valid SteamID64.")
    if not isinstance(cleanup_generation, str) or (cleanup_generation and not GENERATION.fullmatch(cleanup_generation)):
        raise ValueError("The showcase cleanup generation is invalid.")
    if enabled and not ids:
        raise ValueError("Select at least one skin before enabling the showcase kit.")
    if type(random_enabled) is not bool:
        raise ValueError("Random weapon finishes must be on or off.")
    if type(random_chance) is not int or not 0 <= random_chance <= 100:
        raise ValueError("The random weapon skin chance must be a whole percentage from 0 to 100.")
    if type(vehicle_enabled) is not bool:
        raise ValueError("Random vehicle wraps must be on or off.")
    if type(vehicle_chance) is not int or not 0 <= vehicle_chance <= 100:
        raise ValueError("The random vehicle wrap chance must be a whole percentage from 0 to 100.")
    if type(vehicle_test_enabled) is not bool:
        raise ValueError("The one-time test car choice must be on or off.")
    if not isinstance(vehicle_test_generation, str) or not GENERATION.fullmatch(vehicle_test_generation):
        raise ValueError("The test car request generation is invalid.")
    if revision != CATALOG_REVISION:
        raise ValueError("Unsupported skin library revision.")
    return {
        "schema_version": 1,
        "selected_skin_ids": [identity for identity in available if identity in ids],
        "test_kit_enabled": enabled,
        "test_kit_generation": generation,
        "target_steam_id": steam_id,
        "cleanup_generation": cleanup_generation,
        "random_skins_enabled": random_enabled,
        "random_skin_chance_percent": random_chance,
        "vehicle_wraps_enabled": vehicle_enabled,
        "vehicle_wrap_chance_percent": vehicle_chance,
        "vehicle_test_enabled": vehicle_test_enabled,
        "vehicle_test_generation": vehicle_test_generation,
        "catalog_revision": revision,
    }


def _migrate_approved_finishes(value: dict, catalog: dict) -> dict:
    """Add approved collections once, preserving every earlier checkbox choice."""
    revision = value.get("catalog_revision")
    if revision == CATALOG_REVISION:
        return value
    if "catalog_revision" in value and revision != RELIC_CATALOG_REVISION:
        raise ValueError("Unsupported skin library revision.")
    # Validate legacy choices before adding anything. A broken settings file
    # must never be repaired by silently discarding user choices.
    migrated = validate({**value, "catalog_revision": CATALOG_REVISION}, catalog)
    approved = set(CARTOON_SKIN_IDS)
    if "catalog_revision" not in value:
        approved.update(RELIC_SKIN_IDS)
    selected = set(migrated["selected_skin_ids"])
    migrated["selected_skin_ids"] = [identity for identity in _catalog_ids(catalog)
                                      if identity in selected or identity in approved]
    return migrated


def load(root: Path, catalog: dict) -> dict:
    """Read choices without writes; an unopened installation has stable defaults."""
    value = _read(root / SETTINGS_PATH)
    if value is None:
        selected = [identity for identity in DEFAULT_SELECTED if identity in _catalog_ids(catalog)]
        value = {
            "schema_version": 1,
            "selected_skin_ids": selected,
            "test_kit_enabled": bool(selected),
            "test_kit_generation": INITIAL_GENERATION,
            "target_steam_id": "",
            "cleanup_generation": "",
            "catalog_revision": CATALOG_REVISION,
        }
    else:
        value = _migrate_approved_finishes(value, catalog)
    return validate(value, catalog)


def require_safe_unload(root: Path, selected_skin_ids: list[str]) -> None:
    """Validate the receipt before changing choices; keep saved classes loaded.

    Selection controls new cosmetic choices, not the lifetime of saved entity
    classes. prepare() retains historical classes even when the selection is
    empty, so an old or unreachable chest is no longer an uninstall deadlock.
    """
    showcase_receipt(root)


def showcase_receipt(root: Path) -> dict | None:
    try:
        receipt = _read(root / SHOWCASE_RECEIPT_PATH)
    except (OSError, ValueError) as error:
        raise ValueError("Could not check the showcase receipt. Restore its valid contents before changing finishes.") from error
    if receipt is None:
        return None
    if (receipt.get("schema_version") != 1 or not isinstance(receipt.get("chests"), list)
            or not isinstance(receipt.get("weapons"), list)
            or any(not isinstance(item, dict) for key in ("weapons", "chests") for item in receipt[key])):
        raise ValueError("The showcase receipt is invalid. Restore its valid contents before changing finishes.")
    return receipt


def retained_classes(root: Path, catalog: dict) -> tuple[list[str], bool]:
    """Read proven class history without changing settings or retiring receipts."""
    available = _catalog_ids(catalog)
    retained: set[str] = set()
    keep_chest = False
    applied = _read(root / APPLIED_PATH)
    if applied is not None:
        if (applied.get("schema_version") != 1 or "selected_skin_ids" not in applied
                or type(applied.get("showcase_chest_supported", False)) is not bool):
            raise ValueError("Could not read the applied skin class history.")
        for key in ("selected_skin_ids", "retained_skin_ids"):
            ids = applied.get(key, [])
            if (not isinstance(ids, list) or any(not isinstance(identity, str) for identity in ids)
                    or len(ids) != len(set(ids)) or not set(ids) <= set(available)):
                raise ValueError("Could not read the applied skin class history.")
            retained.update(ids)
        keep_chest = bool(retained) or applied.get("showcase_chest_supported", False) is True
    receipt = showcase_receipt(root)
    if receipt is not None:
        classes = {variant["class_name"]: variant["skin_id"]
                   for weapon in catalog["weapons"] for variant in weapon["variants"]}
        classes.update({name.replace("Azimuth_M4A1_", "Azimuth_Showcase_M4A1_", 1): identity
                        for name, identity in list(classes.items()) if name.startswith("Azimuth_M4A1_")})
        for record in receipt["weapons"]:
            name = record.get("class_name")
            if not isinstance(name, str) or name not in classes:
                raise ValueError("The showcase receipt references an unknown rifle class. Restore its catalog before rebuilding skins.")
            retained.add(classes[name])
        keep_chest = keep_chest or bool(receipt["chests"] or receipt["weapons"])
    return [identity for identity in available if identity in retained], keep_chest


def ensure_persisted(root: Path, catalog: dict) -> dict:
    before = _read(root / SETTINGS_PATH)
    value = load(root, catalog)
    require_safe_unload(root, value["selected_skin_ids"])
    if before is None:
        value["test_kit_generation"] = uuid.uuid4().hex
    if before is None or "vehicle_test_generation" not in before:
        value["vehicle_test_generation"] = uuid.uuid4().hex
    if before != value:
        _write(root, SETTINGS_PATH, value)
    return value


def save(root: Path, catalog: dict, selected_skin_ids: list[str], test_kit_enabled: bool,
         *, target_steam_id: str | None = None, rearm: bool = False,
         random_skins_enabled: bool | None = None,
         random_skin_chance_percent: int | None = None,
         vehicle_wraps_enabled: bool | None = None,
         vehicle_wrap_chance_percent: int | None = None,
         vehicle_test_enabled: bool | None = None) -> dict:
    """Caller must hold the manager lock and require a stopped server/client."""
    before = _read(root / SETTINGS_PATH)
    previous = load(root, catalog)
    value = {
        **previous,
        "selected_skin_ids": selected_skin_ids,
        "test_kit_enabled": test_kit_enabled,
    }
    if target_steam_id is not None:
        value["target_steam_id"] = target_steam_id
    if random_skins_enabled is not None:
        value["random_skins_enabled"] = random_skins_enabled
    if random_skin_chance_percent is not None:
        value["random_skin_chance_percent"] = random_skin_chance_percent
    if vehicle_wraps_enabled is not None:
        value["vehicle_wraps_enabled"] = vehicle_wraps_enabled
    if vehicle_wrap_chance_percent is not None:
        value["vehicle_wrap_chance_percent"] = vehicle_wrap_chance_percent
    if vehicle_test_enabled is not None:
        value["vehicle_test_enabled"] = vehicle_test_enabled
    if rearm:
        if not test_kit_enabled:
            raise ValueError("Enable the showcase kit before preparing another delivery.")
        # An explicit new kit request may supersede an unreachable old cleanup.
        # Receipt records remain intact so a later cleanup can still find it.
        value["cleanup_generation"] = ""
    value = validate(value, catalog)
    require_safe_unload(root, value["selected_skin_ids"])
    changed_for_delivery = (value["selected_skin_ids"] != previous["selected_skin_ids"]
                            or not previous["test_kit_enabled"]
                            or value["target_steam_id"] != previous["target_steam_id"])
    if rearm or (value["test_kit_enabled"] and changed_for_delivery) or before is None:
        value["test_kit_generation"] = uuid.uuid4().hex
    if before is None or "vehicle_test_generation" not in before:
        value["vehicle_test_generation"] = uuid.uuid4().hex
    if before != value:
        _write(root, SETTINGS_PATH, value)
    return value


def request_cleanup(root: Path, catalog: dict) -> dict:
    before = _read(root / SETTINGS_PATH)
    value = load(root, catalog)
    value["test_kit_enabled"] = False
    value["cleanup_generation"] = uuid.uuid4().hex
    if before is None or "vehicle_test_generation" not in before:
        value["vehicle_test_generation"] = uuid.uuid4().hex
    _write(root, SETTINGS_PATH, value)
    return value


def selected_catalog(catalog: dict, selected_skin_ids: list[str]) -> dict:
    if (len(selected_skin_ids) != len(set(selected_skin_ids))
            or not set(selected_skin_ids) <= set(_catalog_ids(catalog))):
        raise ValueError("The selected skins contain an unknown or duplicate finish.")
    selected = set(selected_skin_ids)
    result = deepcopy(catalog)
    result["skins"] = [skin for skin in result["skins"] if skin["id"] in selected]
    for weapon in result["weapons"]:
        weapon["variants"] = [variant for variant in weapon["variants"] if variant["skin_id"] in selected]
    result["weapons"] = [weapon for weapon in result["weapons"] if weapon["variants"]]
    return result


def record_applied(root: Path, selected_skin_ids: list[str], *, retained_skin_ids: list[str] = (),
                   showcase_chest_supported: bool = False) -> None:
    value = {"schema_version": 1, "selected_skin_ids": list(selected_skin_ids),
             "retained_skin_ids": list(dict.fromkeys([*retained_skin_ids, *selected_skin_ids])),
             "showcase_chest_supported": bool(showcase_chest_supported or selected_skin_ids)}
    if _read(root / APPLIED_PATH) != value:
        _write(root, APPLIED_PATH, value)


def applied_ids(root: Path, catalog: dict) -> list[str]:
    value = _read(root / APPLIED_PATH)
    if value is None:
        return []
    ids = value.get("selected_skin_ids")
    if value.get("schema_version") != 1 or not isinstance(ids, list) or any(not isinstance(x, str) for x in ids):
        raise ValueError("Could not read the applied skin choices.")
    return [identity for identity in _catalog_ids(catalog) if identity in ids]
