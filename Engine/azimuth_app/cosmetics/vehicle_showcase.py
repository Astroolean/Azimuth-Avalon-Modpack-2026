"""Prepare one account-bound go-kart; preserve the original Gunter and receipt."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re

from azimuth_app.cosmetics import azimuth_skins
from azimuth_app.cosmetics import skin_settings
from azimuth_app.cosmetics import skin_showcase
from azimuth_app.cosmetics.native_color_build import build_script_pbo
from azimuth_app.core.worlds import atomic_text

SOURCE = Path("Custom/GunSkins/VehicleShowcase/azimuthvehicleshowcase")
MOD_NAME = "@AzimuthVehicleShowcase"
PREFIX = "azimuthvehicleshowcase"
LEGACY_STATE_FILE = "vehicle_showcase_state.json"
STATE_FILE = "vehicle_gokart_showcase_state.json"
CONFIG_FILE = "vehicle_showcase.json"
MAX_STATE = 16 * 1024
CAR_CLASS = "MBM_GoKart_Blue"
REQUEST_KIND = "mbm_gokart_showcase_v1"
GENERATION = re.compile(r"[a-f0-9]{32}\Z")
STATUSES = {"preparing", "delivered", "failed", "identity_pending"}


def ready(settings: dict) -> bool:
    """Only test at guaranteed wrap odds; ordinary play keeps its own chance."""
    return bool(settings["selected_skin_ids"] and settings["vehicle_wraps_enabled"]
                and settings["vehicle_wrap_chance_percent"] == 100)


def resolve_recipient(root: Path, catalog: dict, settings: dict) -> dict:
    if settings["target_steam_id"] or not settings["vehicle_test_enabled"] or not ready(settings):
        return settings
    identity = skin_showcase.active_steam_id()
    if not identity and os.name == "nt":
        from azimuth_app.client.steam_session import ensure_steam_running, session_processes
        ensure_steam_running(session_processes())
        identity = skin_showcase.active_steam_id()
    if not identity:
        raise RuntimeError("Open Steam and sign in, then click Play Avalon so the one-time test go-kart can be assigned to you.")
    return skin_settings.save(root, catalog, settings["selected_skin_ids"], settings["test_kit_enabled"],
                              target_steam_id=identity)


def request_generation(settings: dict) -> str:
    """Derive a stable independent request without rewriting saved choices.

    The old Gunter receipt keeps its filename and token. Changing the checkbox,
    selected finish, or chance never creates another go-kart request.
    """
    generation = settings["vehicle_test_generation"]
    if not isinstance(generation, str) or not GENERATION.fullmatch(generation):
        raise ValueError("The saved test-vehicle request is invalid; no vehicle will be created.")
    return hashlib.sha256(f"{REQUEST_KIND}:{generation}".encode("ascii")).hexdigest()[:32]


def config_for(settings: dict) -> dict:
    return {
        "schema_version": 2,
        "vehicle_class": CAR_CLASS,
        "target_steam_id": settings["target_steam_id"],
        "vehicle_test_enabled": bool(settings["vehicle_test_enabled"] and ready(settings)),
        "vehicle_test_generation": request_generation(settings),
    }


def read_state(profiles: Path) -> dict:
    path = skin_showcase._profile_path(profiles, STATE_FILE)
    if not path.is_file():
        return {}
    with path.open("rb") as stream:
        raw = stream.read(MAX_STATE + 1)
    if len(raw) > MAX_STATE:
        raise ValueError("The go-kart receipt is too large.")
    try:
        state = json.loads(raw.decode("utf-8-sig"))
    except (ValueError, UnicodeError) as error:
        raise ValueError("The go-kart receipt could not be read; no new go-kart will be created.") from error
    if (not isinstance(state, dict) or state.get("schema_version") != 2
            or state.get("class_name") != CAR_CLASS
            or not isinstance(state.get("target_steam_id"), str)
            or not re.fullmatch(r"[0-9]{17}", state["target_steam_id"])
            or not isinstance(state.get("reserved_generation"), str)
            or not GENERATION.fullmatch(state["reserved_generation"])
            or state.get("status") not in STATUSES
            or not isinstance(state.get("message"), str)
            or not isinstance(state.get("persistent_id", ""), str)):
        raise ValueError("The go-kart receipt is invalid; no new go-kart will be created.")
    return state


def status_text(settings: dict, state: dict) -> str:
    if state:
        if state.get("target_steam_id") != settings["target_steam_id"]:
            return "The existing go-kart receipt belongs to another Steam account. No go-kart will be added."
        if state.get("reserved_generation") == request_generation(settings):
            return str(state.get("message") or "The go-kart request was already used. Rejoining will not duplicate it.")
    if not settings["vehicle_test_enabled"]:
        return "Go-kart delivery is off. Existing vehicles stay in your world."
    if not ready(settings):
        return "Go-kart paused: select a finish and set vehicle wraps to On / 100% for the camo test."
    return "One MBM Go-Kart and a nearby parts crate queued. Your existing Gunter stays in your world. Stand still outdoors on clear, reasonably flat ground for 10 seconds. Placement keeps retrying while connected; a notification confirms delivery."


def prepare(root: Path, mods_root: Path, profiles: Path) -> tuple[str, ...]:
    bundle = root / azimuth_skins.SOURCE_FOLDER
    if not bundle.exists() and not bundle.is_symlink():
        return ()
    catalog = azimuth_skins.load_catalog(bundle / "catalog.json")
    settings = skin_settings.ensure_persisted(root, catalog)
    settings = resolve_recipient(root, catalog, settings)
    configuration = config_for(settings)
    path = skin_showcase._profile_path(profiles, CONFIG_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(configuration, indent=2) + "\n"
    if not path.is_file() or path.read_text(encoding="utf-8-sig") != payload:
        atomic_text(path, payload)
    pack = mods_root / "ServerOnly" / MOD_NAME
    build_script_pbo(root / SOURCE, pack, PREFIX)
    atomic_text(pack / "mod.cpp", 'name = "Azimuth One-Time Test Go-Kart";\nauthor = "Ashton Kenney";\n')
    try:
        state = read_state(profiles)
    except ValueError as error:
        return (str(error),)
    return (status_text(settings, state),)
