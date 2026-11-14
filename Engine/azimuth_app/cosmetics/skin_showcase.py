"""Prepare the local player's temporary showcase kit without editing character saves."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import stat

from azimuth_app.cosmetics import azimuth_skins
from azimuth_app.cosmetics import skin_settings
from azimuth_app.core.backups import source_type
from azimuth_app.cosmetics.native_color_build import build_script_pbo
from azimuth_app.core.worlds import atomic_text

SOURCE = Path("Custom/GunSkins/Showcase/azimuthshowcase")
MOD_NAME = "@AzimuthSkinShowcase"
PREFIX = "azimuthshowcase"
PROFILE_FOLDER = "AzimuthSkins"
MAX_STATE = 256 * 1024
STEAM_BASE = (1 << 56) | (1 << 52) | (1 << 32)
ATTACHMENTS = ("M4_OEBttstck", "M4_PlasticHndgrd", "M4_CarryHandleOptic")


def account_to_steam_id(account: int) -> str:
    if type(account) is not int or not 0 < account <= 0xFFFFFFFF:
        raise ValueError("Steam has no active individual account.")
    return str(STEAM_BASE | account)


def active_steam_id() -> str:
    """Read the account actively signed in to Steam under this Windows user."""
    if os.name != "nt":
        return ""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam\ActiveProcess") as key:
            account, kind = winreg.QueryValueEx(key, "ActiveUser")
            pid, _ = winreg.QueryValueEx(key, "pid")
        if kind != winreg.REG_DWORD or type(pid) is not int or pid <= 0:
            return ""
        return account_to_steam_id(account)
    except (OSError, ValueError):
        return ""


def resolve_recipient(root: Path, catalog: dict, settings: dict) -> dict:
    if settings["target_steam_id"]:
        return settings
    if not settings["test_kit_enabled"] and not settings["cleanup_generation"]:
        return settings
    identity = active_steam_id()
    if not identity and os.name == "nt":
        from azimuth_app.client.steam_session import ensure_steam_running, session_processes
        ensure_steam_running(session_processes())
        identity = active_steam_id()
    if not identity:
        raise RuntimeError("Open Steam and sign in, then click Play Avalon so your showcase kit can be assigned to you.")
    return skin_settings.save(
        root, catalog, settings["selected_skin_ids"], settings["test_kit_enabled"], target_steam_id=identity,
    )


def config_for(catalog: dict, settings: dict) -> dict:
    selected = skin_settings.selected_catalog(catalog, settings["selected_skin_ids"])
    weapons = [azimuth_skins.showcase_class(variant["class_name"])
               for weapon in selected["weapons"] if weapon["base_class"] == "M4A1"
               for variant in weapon["variants"]][:3]
    return {
        "schema_version": 1,
        "target_steam_id": settings["target_steam_id"],
        "test_kit_enabled": settings["test_kit_enabled"],
        "test_kit_generation": settings["test_kit_generation"],
        "cleanup_generation": settings["cleanup_generation"],
        "weapon_classes": weapons,
        "attachment_classes": list(ATTACHMENTS),
    }


def _profile_path(profiles: Path, filename: str) -> Path:
    folder = profiles / PROFILE_FOLDER
    for part in (profiles, folder):
        if part.exists() or part.is_symlink():
            if not stat.S_ISDIR(source_type(part)):
                raise ValueError("Expected an ordinary showcase profile folder.")
    path = folder / filename
    if path.exists() or path.is_symlink():
        if not stat.S_ISREG(source_type(path)):
            raise ValueError("Expected an ordinary showcase profile file.")
    return path


def read_state(profiles: Path) -> dict:
    path = _profile_path(profiles, "showcase_state.json")
    if not path.is_file():
        return {}
    with path.open("rb") as stream:
        raw = stream.read(MAX_STATE + 1)
    if len(raw) > MAX_STATE:
        raise ValueError("The showcase status file is too large.")
    state = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(state, dict):
        raise ValueError("The showcase status file is invalid.")
    return state


def cleanup_pending(settings: dict, state: dict) -> bool:
    generation = settings["cleanup_generation"]
    return bool(generation and (state.get("target_steam_id") != settings["target_steam_id"]
                or state.get("cleanup_generation") != generation or state.get("cleanup_pending")))


def status_text(settings: dict, state: dict) -> str:
    same_target = bool(settings["target_steam_id"]) and state.get("target_steam_id") == settings["target_steam_id"]
    cleanup = settings["cleanup_generation"]
    if cleanup and (not same_target or state.get("cleanup_generation") != cleanup):
        return "Test-kit removal queued. Start Avalon and join with the kit nearby."
    if cleanup and state.get("cleanup_pending"):
        return str(state.get("message") or "Some test items are out of reach. Bring them nearby and join again.")
    if settings["test_kit_enabled"]:
        if same_target and state.get("delivered_generation") == settings["test_kit_generation"]:
            return str(state.get("message") or "Kit delivered. Rejoining will not duplicate it.")
        count = min(3, len(settings["selected_skin_ids"]))
        return f"Ready to give {count} showcase M4A1{'s' if count != 1 else ''} on your next join."
    if cleanup and same_target and state.get("cleanup_generation") == cleanup:
        return str(state.get("message") or "Showcase cleanup completed. New test kits are off.")
    return "Showcase delivery is off. Your normal weapons are unchanged."


def _remove_generated_mod(pack: Path) -> None:
    if not pack.exists() and not pack.is_symlink():
        return
    if not stat.S_ISDIR(source_type(pack)):
        raise ValueError("The showcase mod folder is not an ordinary folder.")
    allowed = {f"Addons/{PREFIX}.pbo", "mod.cpp"}
    for directory, folders, names in os.walk(pack, followlinks=False):
        for name in folders:
            if not stat.S_ISDIR(source_type(Path(directory) / name)):
                raise ValueError("The showcase mod contains a linked folder.")
        for name in names:
            path = Path(directory) / name
            if not stat.S_ISREG(source_type(path)) or path.relative_to(pack).as_posix() not in allowed:
                raise ValueError("The showcase mod contains custom files. Preserve them before disabling all skins.")
    shutil.rmtree(pack)


def prepare(root: Path, mods_root: Path, profiles: Path) -> tuple[str, ...]:
    pack = mods_root / "ServerOnly" / MOD_NAME
    bundle = root / azimuth_skins.SOURCE_FOLDER
    if not bundle.exists() and not bundle.is_symlink():
        _remove_generated_mod(pack)
        return ()
    catalog = azimuth_skins.load_catalog(bundle / "catalog.json")
    settings = skin_settings.ensure_persisted(root, catalog)
    receipt = skin_settings.showcase_receipt(root)
    recorded_items = bool(receipt and (receipt["weapons"] or receipt["chests"]))
    if not settings["selected_skin_ids"] and not recorded_items and not settings["cleanup_generation"]:
        _remove_generated_mod(pack)
        return ()
    settings = resolve_recipient(root, catalog, settings)
    configuration = config_for(catalog, settings)
    path = _profile_path(profiles, "showcase.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(configuration, indent=2) + "\n"
    if not path.is_file() or path.read_text(encoding="utf-8-sig") != payload:
        atomic_text(path, payload)
    build_script_pbo(root / SOURCE, pack, PREFIX)
    atomic_text(pack / "mod.cpp", 'name = "Azimuth Skin Showcase";\nauthor = "Ashton Kenney";\n')
    if settings["cleanup_generation"] and not settings["test_kit_enabled"]:
        return ("Showcase cleanup is queued for your next join. Ordinary guns are not targeted.",)
    if settings["test_kit_enabled"]:
        return (f"Showcase kit ready: {len(configuration['weapon_classes'])} M4A1s for your Steam account on join.",)
    return ()
