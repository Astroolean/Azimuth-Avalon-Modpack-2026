"""Manage a private PC DayZ server using the locally installed Steam runtime."""
from __future__ import annotations

from contextlib import suppress, contextmanager
from dataclasses import replace
from datetime import datetime
import filecmp
import json
import os
from pathlib import Path
import queue
import re
import secrets
import shutil
import socket
import subprocess
import tempfile
from threading import RLock
import time
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from azimuth_app.economy import avalon_loot_balance
from azimuth_app.economy import avalon_economy_repair
from azimuth_app.world import avalon_qol
from azimuth_app.world import avalon_workshop
from azimuth_app.cosmetics import azimuth_skins
from azimuth_app.cosmetics import skin_settings
from azimuth_app.cosmetics import skin_showcase
from azimuth_app.cosmetics import weapon_inspect
from azimuth_app.cosmetics import photo_mode
from azimuth_app.cosmetics import random_skin_mode
from azimuth_app.cosmetics import vehicle_showcase
from azimuth_app.core.backups import backup_time, latest_backup, write_backup, recover_restore
from azimuth_app.client.client_launch import stage_client_mods
from azimuth_app.client.client_performance import VISUAL_SETTINGS, ensure_client_closed
from azimuth_app.economy.hunter_economy import apply_economy, remove_economy
from azimuth_app.integrations.hunter_signing import ensure_signed
from azimuth_app.cosmetics.native_color_build import SOURCE as NATIVE_COLOR_SOURCE, build_native_color
from azimuth_app.integrations import solitude_signatures
from azimuth_app.integrations.workshop_keys import repair_vehicle_keys

from azimuth_app.network.server_discovery import discover_installations, inspect_installation
from azimuth_app.network.server_query import query_info
from azimuth_app.transport.transport_version import require_dayz130
from azimuth_app.transport import vehicle_workshop, additional_vehicles, transport_survey
from azimuth_app.core.server_workspace import _copy_file, _mission_plan, _quote
from azimuth_app.core.worlds import (
    CATALOG, Worlds, atomic_text, children, has_mission, mission_candidate, mission_sources,
    unpack_mission, workshop_mods, workshop_paths,
)

DEFAULTS = {
    "hostname": "FreeDayZ Private Server",
    "password": "freedayzme",
    "max_players": 8,
    "game_port": 2302,
    "query_port": 27016,
}
HUNTER_PACK = "@HunterMods"
NATIVE_COLOR_MARKER = "native-color-preview.enabled"
CF_WORKSHOP_ID = "1559212036"
AVALON_TERRAIN_ID = CATALOG["avalon"].workshop_ids[0]
AVALON_STEAM_ITEMS = avalon_workshop.ITEM_IDS | {CF_WORKSHOP_ID, AVALON_TERRAIN_ID}
JOIN_PASSWORD_REVISION = 1
BACKUP_INTERVAL = 6 * 60 * 60
BACKUP_IDLE = 60
BACKUP_RETRY = 5 * 60
STOP_TIMEOUT = 60
CLEAN_EXIT_MARKER = b"termination successfully completed"
NO_SCHEDULE = '<?xml version="1.0" encoding="UTF-8"?>\n<messages />\n'


write_text = atomic_text


def remove_mission_shutdown(path: Path) -> Path | None:
    """Keep valid creator messages or preserve a broken file beside an empty schedule."""
    if not path.is_file():
        return None
    original = path.read_bytes()
    try:
        parser = ElementTree.XMLParser(target=ElementTree.TreeBuilder(insert_comments=True, insert_pis=True))
        messages = ElementTree.fromstring(original, parser=parser)
    except (ElementTree.ParseError, LookupError, ValueError):
        messages = None
    if messages is None or messages.tag != "messages":
        preserved = None
        try:
            with tempfile.NamedTemporaryFile(prefix="messages.original-", suffix=".bak",
                                             dir=path.parent, delete=False) as archive:
                preserved = Path(archive.name)
                archive.write(original)
            write_text(path, NO_SCHEDULE)
        except OSError:
            if preserved is not None:
                preserved.unlink(missing_ok=True)
            raise
        return preserved

    shutdowns = [message for message in messages.findall("message")
                 if (message.findtext("shutdown") or "").strip() == "1"]
    if shutdowns:
        for message in shutdowns:
            messages.remove(message)
        write_text(path, ElementTree.tostring(messages, encoding="unicode", xml_declaration=True) + "\n")
    return None


def get_lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
            connection.connect(("192.0.2.1", 9))
            return connection.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def ensure_udp_available(port: int) -> None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as listener:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            listener.bind(("0.0.0.0", port))
    except OSError as error:
        raise RuntimeError(f"UDP port {port} is unavailable. Close any earlier DayZ server or change the ports in Settings.") from error


@contextmanager
def reserve_udp_ports(ports):
    """Keep another local server from opening these ports during a restore."""
    listeners = []
    try:
        for port in ports:
            listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            listeners.append(listener)
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                listener.bind(("0.0.0.0", port))
            except OSError as error:
                raise RuntimeError(f"UDP port {port} is in use. Close any earlier DayZ server before restoring world files.") from error
        yield
    finally:
        for listener in listeners:
            listener.close()


def valid_settings(settings: dict) -> None:
    name, password = settings["hostname"], settings["password"]
    if not isinstance(name, str) or not re.fullmatch(r'[ -~]{1,64}', name) or not name.strip() or any(c in name for c in '"\\'):
        raise ValueError("Use 1 to 64 printable characters for the server name, without quotes or backslashes.")
    if not isinstance(password, str) or not re.fullmatch(r"[A-Za-z0-9_.!@#$%+\-]{4,32}", password):
        raise ValueError("Use a join password with 4 to 32 letters, numbers, or these symbols: _.!@#$%+-")
    for key, minimum, maximum, label in (
        ("max_players", 1, 60, "Player limit"),
        ("game_port", 1024, 65533, "Game port"),
        ("query_port", 1024, 65535, "Query port"),
    ):
        if type(settings[key]) is not int or not minimum <= settings[key] <= maximum:
            raise ValueError(f"{label} must be between {minimum} and {maximum}.")
    if settings["query_port"] in {settings["game_port"], settings["game_port"] + 2}:
        raise ValueError("Use a query port different from the game port and game port + 2.")


class Server:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.config = self.root / "Config"
        self.config.mkdir(parents=True, exist_ok=True)
        self.settings_path = self.config / "settings.json"
        self.installation_path = self.config / "server-location.txt"
        self.worlds = Worlds(self.root)
        self.world_setup_dir = self.worlds.setup
        self._last_world_notice = ""
        self._steam_libraries: list[Path] = []

        self.lock = RLock()
        self.events: queue.Queue[str] = queue.Queue()
        self.installation: Path | None = None
        self.process: subprocess.Popen | None = None
        self.loaded_client_mods: tuple[Path, ...] = ()
        self._loaded_server_version: str | None = None
        self._loaded_workshop_files = ()
        self.settings = DEFAULTS.copy()
        self._admin_password = secrets.token_hex(18)
        self._stopping = False
        self._seen_online = False
        self._last_exit = None
        self._launch_lock = None
        if self.settings_path.exists():
            try:
                saved = json.loads(self.settings_path.read_text(encoding="utf-8-sig"))
                if not isinstance(saved, dict) or "password" not in saved:
                    raise ValueError("Server settings must contain a join password.")
                self.settings.update({key: saved[key] for key in self.settings if key in saved})
                migrate_password = saved.get("join_password_revision") != JOIN_PASSWORD_REVISION
                if migrate_password:
                    self.settings["password"] = DEFAULTS["password"]
                valid_settings(self.settings)
                admin_password = saved.get("admin_password", "")
                if re.fullmatch(r"[A-Fa-f0-9]{36}", admin_password):
                    self._admin_password = admin_password
                if migrate_password:
                    self._save_settings(self.settings)
            except (AttributeError, TypeError, ValueError, OSError) as error:
                raise RuntimeError(f"Could not read Config/settings.json: {error}") from error
        else:
            self._save_settings(self.settings)
        self._load_world_paths()

    @property
    def active_world_id(self) -> str:
        return self.worlds.active_id

    @property
    def active_world(self):
        return self.worlds.get(self.active_world_id)

    def world_options(self) -> list[tuple[str, str]]:
        return [(world.id, world.label) for world in CATALOG.values()]

    def _world_with_dependencies(self, world_id: str, *, include_pack: bool | None = None):
        world = self.worlds.get(world_id)
        if world_id != "avalon":
            return world
        paths = self.worlds.paths(world_id)
        if include_pack is None:
            include_pack = avalon_workshop.enabled(paths.config)
        items = tuple(zip(world.workshop_ids, world.aliases))
        if include_pack or self._hunter_pack(world.id, paths):
            items = ((CF_WORKSHOP_ID, "CF"), *items)
        if include_pack:
            items += avalon_workshop.ITEMS
        items = tuple(dict(items).items())
        return replace(world, workshop_ids=tuple(item_id for item_id, _ in items),
                       aliases=tuple(alias for _, alias in items))

    def world_workshop_ids(self, world_id: str | None = None) -> tuple[str, ...]:
        return self._world_with_dependencies(world_id or self.active_world_id).workshop_ids

    def _hunter_pack(self, world_id: str, paths) -> Path | None:
        if world_id == "avalon":
            pack = paths.mods / HUNTER_PACK
            if pack.is_dir():
                return pack
        return None

    def _configure_hunter_pack(self, world_id: str, paths, mission: Path) -> None:
        pack = self._hunter_pack(world_id, paths)
        if pack is not None:
            ensure_signed(pack, self._steam_libraries, self.root)
            count = apply_economy(mission, pack)
            self.events.put(f"Hunter Mods ready for Avalon. Registered {count} loot types.")
        elif world_id == "avalon":
            remove_economy(mission)

    def avalon_pack_status(self) -> tuple[bool, set[str]]:
        with self.lock:
            paths = self.worlds.paths("avalon")
            world = self._world_with_dependencies("avalon", include_pack=True)
            libraries = self.steam_libraries(self.installation, refresh=True)
            installed = {item_id for item_id, _, path in
                         workshop_paths(world, paths.mission_source, paths.mods, libraries,
                                        AVALON_STEAM_ITEMS) if path is not None}
            return avalon_workshop.version(paths.config) >= avalon_workshop.PACK_VERSION, installed

    @staticmethod
    def _workshop_file_state(folders: list[Path]) -> tuple:
        state = []
        for folder in folders:
            addons = children(folder).get("addons")
            if addons is None:
                continue
            for path in children(addons).values():
                if path.suffix.casefold() in {".pbo", ".bisign"} and path.is_file():
                    info = path.stat()
                    state.append((path, info.st_size, info.st_mtime_ns))
        return tuple(state)

    def avalon_pack_restart_needed(self) -> bool:
        with self.lock:
            if self.active_world_id != "avalon" or not self._running() or not self.installation:
                return False
            current = self._workshop_folders(self.installation, self.active_world, self.world_paths)
            return self._loaded_workshop_files != self._workshop_file_state(current)

    def avalon_first_run_pending(self) -> bool:
        with self.lock:
            if self.active_world_id != "avalon" or not avalon_workshop.enabled(self.world_paths.config):
                return False
            parent = self.world / "Mission"
            return parent.is_dir() and any((avalon_workshop.fishing_setup_pending(mission, self.profiles)
                                           or transport_survey.setup_pending(mission, self.profiles))
                                          for mission in parent.iterdir() if has_mission(mission))

    def avalon_survey_running(self) -> bool:
        with self.lock:
            if self.active_world_id != "avalon" or not avalon_workshop.enabled(self.world_paths.config):
                return False
            parent = self.world / "Mission"
            return parent.is_dir() and any(transport_survey.survey_running(mission, self.profiles)
                                          for mission in parent.iterdir() if has_mission(mission))

    def avalon_color_preview_enabled(self) -> bool:
        return (self.worlds.paths("avalon").config / NATIVE_COLOR_MARKER).is_file()

    def _avalon_color_preview(self) -> Path:
        return self.worlds.paths("avalon").config / "NativeColorPreview" / "@AzimuthLooks"

    def _prepare_avalon_color_preview(self, installation: Path) -> Path:
        preview = self._avalon_color_preview()
        try:
            build_native_color(NATIVE_COLOR_SOURCE, preview)
        except (OSError, ValueError) as error:
            raise RuntimeError("Could not build the Avalon color preview. "
                               f"Restore the package's Visuals files or disable the preview in Client. {error}") from error
        ensure_signed(preview, self.steam_libraries(installation, refresh=True), self.root,
                      state_name="NativeColorSigning")
        return preview

    def avalon_color_preview_restart_needed(self) -> bool:
        with self.lock:
            return (self.active_world_id == "avalon" and self._running()
                    and self.avalon_color_preview_enabled() != (self._avalon_color_preview() in self.loaded_client_mods))

    def set_avalon_color_preview(self, enabled: bool, *, prepare_for_play: bool = False) -> None:
        with self.lock:
            running_avalon = self.active_world_id == "avalon" and self._running()
            can_stage = prepare_for_play and enabled and self._avalon_color_preview() not in self.loaded_client_mods
            if self.backing_up or (running_avalon and not can_stage):
                raise RuntimeError("Stop Avalon before changing the color preview.")
            marker = self.worlds.paths("avalon").config / NATIVE_COLOR_MARKER
            if not enabled:
                marker.unlink(missing_ok=True)
                self.events.put("Avalon color preview disabled.")
                return
            installation = self.find_installation()
            if installation is None:
                raise RuntimeError("Install DayZ Server before preparing the color preview.")
            self._prepare_avalon_color_preview(installation)
            write_text(marker, "enabled\n")
            self.events.put("Avalon color preview enabled for the next launch.")

    def backup_avalon_before_change(self) -> Path | None:
        with self.lock:
            if self.backing_up or (self.active_world_id == "avalon" and self._running()):
                raise RuntimeError("Wait for Avalon to stop before taking its upgrade backup.")
            paths = self.worlds.paths("avalon")
            self._recover_interrupted_restore(paths)
            saves = paths.world / "Saves"
            if saves.is_dir() and any(path.is_file() for path in saves.rglob("*")):
                return write_backup(paths.root, time.time(), manager_root=self.root)
            return None

    def enable_avalon_pack(self) -> Path | None:
        """Preflight the complete Workshop list, archive an existing world, and opt in."""
        with self.lock:
            paths = self.worlds.paths("avalon")
            if self.backing_up or (self.active_world_id == "avalon" and self._running()):
                raise RuntimeError("Wait for Avalon to stop and the backup to finish before enabling the pack.")
            self._recover_interrupted_restore(paths)
            if avalon_workshop.version(paths.config) >= avalon_workshop.PACK_VERSION:
                return None
            installation = self.find_installation()
            if installation is None:
                raise RuntimeError("Install DayZ Server in Steam before enabling the pack.")
            require_dayz130(installation / "DayZServer_x64.exe", "DayZ Server")
            world = self._world_with_dependencies("avalon", include_pack=True)
            libraries = self.steam_libraries(installation, refresh=True)
            resolved = workshop_mods(world, paths.mission_source, paths.mods, libraries, AVALON_STEAM_ITEMS)
            mission = self._stage_mission(installation, world, paths, resolved[:2])
            self._check_avalon_animation_mods(paths)
            self._sync_avalon_pack_keys(installation, resolved)
            backup = self.backup_avalon_before_change()
            mods = dict(zip(world.workshop_ids, resolved, strict=True))
            with tempfile.TemporaryDirectory(prefix="azimuth-avalon-") as temp:
                staging = Path(temp)
                staged_mission = staging / mission.name
                staged_mission.mkdir()
                plan, _ = _mission_plan(mission)
                for relative, expected in plan:
                    target = staged_mission / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    _copy_file(mission / relative, target, expected)
                staged_profiles = staging / "Profiles"
                staged_profiles.mkdir()
                if backup is not None:
                    with ZipFile(backup) as archive:
                        profiles = [name for name in archive.namelist() if name.startswith("Profiles/")]
                        archive.extractall(staging, profiles)
                hunter = self._hunter_pack(world.id, paths)
                if hunter is not None:
                    apply_economy(staged_mission, hunter)
                avalon_qol.prepare(staged_mission, staged_profiles, hunter is not None)
                avalon_qol.prepare_comfort(staged_mission)
                notices = avalon_workshop.prepare(staged_mission, staged_profiles, mods)
                repair_notices = avalon_economy_repair.prepare(staged_mission)
                avalon_loot_balance.prepare(staged_mission, staged_profiles, notices + repair_notices)
            avalon_workshop.enable(paths.config)
            return backup

    def _configure_world(self, world, paths, mission: Path, workshop: list[Path]) -> None:
        self._configure_hunter_pack(world.id, paths, mission)
        if world.id != "avalon":
            return
        avalon_qol.prepare(mission, paths.profiles, self._hunter_pack(world.id, paths) is not None)
        notices = ()
        active_vehicle_ids = ()
        if avalon_workshop.enabled(paths.config):
            avalon_qol.prepare_comfort(mission)
            base_ids = self._world_with_dependencies(world.id).workshop_ids
            mods = dict(zip(base_ids, workshop[:len(base_ids)], strict=True))
            notices = avalon_workshop.prepare(mission, paths.profiles, mods)
            for notice in notices:
                self.events.put(notice)
            extra = {folder.name: folder for folder in workshop[len(base_ids):]}
            active_vehicle_ids = tuple(mods) + tuple(extra)
            extra_notices = additional_vehicles.prepare(mission, paths.profiles, extra)
            notices += tuple(extra_notices)
            for notice in extra_notices:
                self.events.put(notice)
            _selected, download_notices = vehicle_workshop.resolve(paths, self._steam_libraries, base_ids)
            for notice in download_notices:
                self.events.put(notice)
        skin_count, skin_notices = azimuth_skins.prepare(self.root, paths.mods, mission, self._steam_libraries)
        if skin_count:
            self.events.put(f"Azimuth gun skins ready. Registered {skin_count} cosmetic weapon variants.")
        for notice in skin_notices:
            self.events.put(notice)
        for notice in skin_showcase.prepare(self.root, paths.mods, paths.profiles):
            self.events.put(notice)
        inspect_mod = weapon_inspect.prepare(self.root, paths.mods, self._steam_libraries)
        if inspect_mod is not None:
            self.events.put("Weapon inspection ready. In first person, hold a gun and press I. Rebind in Options > Controls > Azimuth.")
        photo_mod = photo_mode.prepare(self.root, paths.mods, self._steam_libraries)
        if photo_mod is not None:
            self.events.put("Screenshot freecam ready. Hold your weapon and press P. Press P again to return to your character.")
        random_mod = random_skin_mode.prepare(self.root, paths.mods, self._steam_libraries,
                                             active_vehicle_ids=active_vehicle_ids)
        if random_mod is not None:
            self.events.put("Random finishes prepared for compatible weapons, parts, clothing and vehicle body panels. Manage their separate chances in Skins/Camos.")
        for notice in vehicle_showcase.prepare(self.root, paths.mods, paths.profiles):
            self.events.put(notice)
        repair_notices = avalon_economy_repair.prepare(mission)
        for notice in repair_notices:
            self.events.put(notice)
        self.events.put(avalon_loot_balance.prepare(mission, paths.profiles, notices + repair_notices))

    def skin_status(self) -> dict:
        with self.lock:
            catalog = azimuth_skins.load_catalog(self.root / azimuth_skins.SOURCE_FOLDER / "catalog.json")
            choices = skin_settings.load(self.root, catalog)
            selected = skin_settings.selected_catalog(catalog, choices["selected_skin_ids"])
            profiles = self.worlds.paths("avalon").profiles
            state = {}
            try:
                state = skin_showcase.read_state(profiles)
                kit_message = skin_showcase.status_text(choices, state)
            except (OSError, ValueError) as error:
                kit_message = f"Showcase status could not be read: {error}"
            try:
                vehicle_state = vehicle_showcase.read_state(profiles)
                vehicle_message = vehicle_showcase.status_text(choices, vehicle_state)
            except (OSError, ValueError) as error:
                vehicle_message = f"Go-kart status could not be read: {error}"
            applied = skin_settings.applied_ids(self.root, catalog)
            message = "Checked finishes are used for new random choices. Existing fixed-skin guns and test chests stay supported."
            if set(applied) != set(choices["selected_skin_ids"]):
                message = "Saved choices will be applied when Avalon starts."
            return {
                "skins": catalog["skins"], "weapons": selected["weapons"],
                "selected_ids": choices["selected_skin_ids"], "applied_ids": applied,
                "showcase_enabled": choices["test_kit_enabled"], "kit_status": kit_message,
                "showcase_count": len(skin_showcase.config_for(catalog, choices)["weapon_classes"]),
                "random_skins_enabled": choices["random_skins_enabled"],
                "random_skin_chance_percent": choices["random_skin_chance_percent"],
                "vehicle_wraps_enabled": choices["vehicle_wraps_enabled"],
                "vehicle_wrap_chance_percent": choices["vehicle_wrap_chance_percent"],
                "vehicle_test_enabled": choices["vehicle_test_enabled"],
                "vehicle_test_status": vehicle_message,
                "cleanup_pending": skin_showcase.cleanup_pending(choices, state),
                "cleanup_deferred": state.get("status") == "cleanup_deferred",
                "message": message,
            }

    def _require_skin_changes_stopped(self) -> None:
        if self._running() or self.backing_up:
            raise RuntimeError("Stop the server and let its backup finish before changing skins or the test kit.")
        try:
            ensure_client_closed()
        except RuntimeError as error:
            raise RuntimeError(str(error).replace("video settings", "skins")) from error

    def save_skin_settings(self, selected_ids: list[str], showcase_enabled: bool, *,
                           random_skins_enabled: bool | None = None,
                           random_skin_chance_percent: int | None = None,
                           vehicle_wraps_enabled: bool | None = None,
                           vehicle_wrap_chance_percent: int | None = None,
                           vehicle_test_enabled: bool | None = None) -> dict:
        with self.lock:
            self._require_skin_changes_stopped()
            catalog = azimuth_skins.load_catalog(self.root / azimuth_skins.SOURCE_FOLDER / "catalog.json")
            skin_settings.save(self.root, catalog, list(selected_ids), showcase_enabled,
                               random_skins_enabled=random_skins_enabled,
                               random_skin_chance_percent=random_skin_chance_percent,
                               vehicle_wraps_enabled=vehicle_wraps_enabled,
                               vehicle_wrap_chance_percent=vehicle_wrap_chance_percent,
                               vehicle_test_enabled=vehicle_test_enabled)
            return self.skin_status()

    def reissue_skin_kit(self) -> dict:
        with self.lock:
            self._require_skin_changes_stopped()
            catalog = azimuth_skins.load_catalog(self.root / azimuth_skins.SOURCE_FOLDER / "catalog.json")
            choices = skin_settings.load(self.root, catalog)
            skin_settings.save(self.root, catalog, choices["selected_skin_ids"], True, rearm=True)
            return self.skin_status()

    def remove_skin_test_kit(self) -> dict:
        with self.lock:
            self._require_skin_changes_stopped()
            catalog = azimuth_skins.load_catalog(self.root / azimuth_skins.SOURCE_FOLDER / "catalog.json")
            skin_settings.request_cleanup(self.root, catalog)
            return self.skin_status()

    @staticmethod
    def _is_solitude_copy(folder: Path) -> bool:
        addons = children(folder).get("addons")
        return (folder.name.lstrip("@") == solitude_signatures.WORKSHOP_ID
                or (addons is not None and solitude_signatures.MAIN_PBO in children(addons)))

    def _sync_avalon_pack_keys(self, installation: Path, workshop: list[Path],
                               local: list[Path] | None = None) -> None:
        solitude = next((folder for folder in workshop if folder.name == solitude_signatures.WORKSHOP_ID), None)
        self._sync_keys(installation, [folder for folder in workshop if folder != solitude] + (local or []))
        if solitude is not None:
            added = solitude_signatures.prepare(solitude, installation / "keys", self.steam_libraries(installation))
            self.events.put(f"Solitude Storage Workshop signatures passed. Installed {added} public key(s).")
        # Workshop items sometimes place keys outside the top-level Keys folder.
        # DayZ can silently omit those server mods even when -mod= lists them;
        # reconstruct ONLY public keys from each original verified .bisign.
        repaired = repair_vehicle_keys(workshop, installation / "keys",
                                       self.steam_libraries(installation), vehicle_workshop.ITEM_IDS)
        for detail in repaired:
            self.events.put(f"Avalon Workshop signature repaired: {detail}")

    @staticmethod
    def _check_avalon_animation_mods(paths) -> None:
        blocked_ids = {"2545980712", "2793893086"}
        blocked_names = {"customanimations", "dayzexpansionanimations", "expansionanimations"}
        for parent in (paths.mods, paths.mods / "ServerOnly"):
            for folder in children(parent).values():
                if not folder.is_dir():
                    continue
                name = re.sub(r"[^a-z0-9]", "", folder.name.casefold())
                if name in blocked_ids or name in blocked_names:
                    raise ValueError(f"Move {folder.name} out of Avalon Mods before loading Survivor Animations.")

    def world_source_url(self, world_id: str | None = None) -> str:
        return self.worlds.get(world_id or self.active_world_id).source_url

    def world_summary(self) -> str:
        return self._last_world_notice or f"Selected: {self.active_world.label}"

    def reserve_stopped_ports(self):
        port = self.settings["game_port"]
        return reserve_udp_ports((port, port + 2, self.settings["query_port"]))

    def _recover_interrupted_restore(self, paths) -> bool:
        if not (paths.config / "restore-transaction.json").exists():
            return False
        with self.reserve_stopped_ports():
            return recover_restore(paths.root, self.root)

    def _load_world_paths(self, paths=None, *, recover: bool = True) -> None:
        self.world_paths = paths or self.worlds.paths(self.active_world_id)
        if recover and self._recover_interrupted_restore(self.world_paths):
            self.events.put("An interrupted restore was rolled back. Original world and player files were retained.")
        self.profiles = self.world_paths.profiles
        self.mods_dir = self.world_paths.mods
        self.world = self.world_paths.world
        self.backups_dir = self.world_paths.backups
        for path in (self.world_paths.config, self.profiles, self.mods_dir, self.world, self.backups_dir):
            path.mkdir(parents=True, exist_ok=True)
        self._backup_due_path = self.world_paths.config / "backup-first-due.txt"
        self._last_world_notice = ""
        self._last_exit = None
        self._seen_online = False
        recent_backup = latest_backup(self.world_paths.root)
        self._backup_last = backup_time(recent_backup) if recent_backup else None
        self._backup_due = self._backup_last + BACKUP_INTERVAL if self._backup_last is not None else None
        if self._backup_due is None and self._backup_due_path.is_file():
            try:
                self._backup_due = int(self._backup_due_path.read_text(encoding="ascii").strip())
                if self._backup_due <= 0:
                    raise ValueError
            except (OSError, ValueError):
                self._backup_due = None
                self.events.put("Backup schedule will be recreated when the server starts.")
        self._backup_state = "Backups every 6 hours"
        self._backup_pending = False
        self._backup_restart = False
        self._backup_writing = False
        self._backup_stop_deadline = 0.0
        self._backup_idle_since = None
        self._backup_retry = 0.0
        # Keep the baseline across dashboard restarts. An older clean RPT cannot
        # certify a later process that exited before it could write a report.
        self._launch_reports = {}
        self._launch_provenance = None
        self._launch_state_path = self.world_paths.config / "launch-session.json"
        if self._launch_state_path.is_file():
            try:
                saved = json.loads(self._launch_state_path.read_text(encoding="utf-8"))
                if not isinstance(saved, dict) or saved.get("version") != 1 or saved.get("kind") not in {"launch", "restore"}:
                    raise ValueError("Invalid launch provenance")
                reports = saved.get("reports")
                if not isinstance(reports, list):
                    raise ValueError("Invalid report baseline")
                baseline = {}
                for row in reports:
                    if (not isinstance(row, list) or len(row) != 3 or not isinstance(row[0], str)
                            or Path(row[0]).name != row[0] or any(c in row[0] for c in '/\\:')
                            or Path(row[0]).suffix.casefold() != ".rpt"
                            or any(type(value) is not int or value < 0 for value in row[1:])):
                        raise ValueError("Invalid report baseline")
                    baseline[self.profiles / row[0]] = (row[1], row[2])
                self._launch_reports = baseline
                self._launch_provenance = saved["kind"]
            except (OSError, ValueError, TypeError):
                self.events.put("Saved shutdown evidence was invalid. A new clean server run is needed before a verified stop backup.")

    def _save_launch_provenance(self, kind: str = "launch") -> None:
        if kind not in {"launch", "restore"}:
            raise ValueError("Invalid launch provenance")
        reports = [[path.name, modified, size] for path, (modified, size) in self._launch_reports.items()]
        write_text(self._launch_state_path, json.dumps({"version": 1, "kind": kind, "reports": reports}, indent=2) + "\n")
        self._launch_provenance = kind

    def select_world(self, world_id: str) -> None:
        with self.lock:
            if self._running() or self.backing_up:
                raise RuntimeError("Wait for the active world to stop before switching.")
            if world_id != self.active_world_id:
                paths = self.worlds.paths(world_id)
                for path in (paths.config, paths.profiles, paths.mods, paths.world, paths.backups):
                    path.mkdir(parents=True, exist_ok=True)
                previous = self.active_world_id
                self.worlds.select(world_id)
                try:
                    self._load_world_paths(paths)
                except (OSError, ValueError):
                    self.worlds.select(previous)
                    self._load_world_paths()
                    raise

    def steam_libraries(self, installation: Path | None = None, *, refresh: bool = False) -> list[Path]:
        with self.lock:
            if refresh or not self._steam_libraries:
                discovered = discover_installations()
                for value in discovered.get("library_paths", []):
                    library = Path(value)
                    if library not in self._steam_libraries:
                        self._steam_libraries.append(library)
            if (installation is not None and installation.parent.name.casefold() == "common"
                    and installation.parent.parent.name.casefold() == "steamapps"):
                library = installation.parent.parent.parent
                if library not in self._steam_libraries:
                    self._steam_libraries.append(library)
            return list(self._steam_libraries)

    def _workshop_folders(self, installation: Path, world, paths) -> list[Path]:
        if not world.workshop_ids:
            return []
        resolved = self._world_with_dependencies(world.id)
        steam_only = AVALON_STEAM_ITEMS if world.id == "avalon" and avalon_workshop.enabled(paths.config) else frozenset()
        libraries = self.steam_libraries(installation)
        folders = workshop_mods(resolved, paths.mission_source, paths.mods, libraries, steam_only)
        if world.id == "avalon":
            extra = {}
            if avalon_workshop.enabled(paths.config):
                extra, _notices = vehicle_workshop.resolve(paths, libraries, resolved.workshop_ids)
            additional_vehicles.require_saved_packs(paths.profiles, extra)
            folders.extend(extra.values())
        return folders

    def _stage_mission(self, installation: Path, world, paths, workshop: list[Path]) -> Path:
        parent = paths.world / "Mission"
        if parent.is_dir():
            candidates = sorted(parent.iterdir(), key=lambda path: path.name.casefold())
            for candidate in candidates:
                if mission_candidate(candidate.name, world) and has_mission(candidate):
                    return candidate
            if any(parent.iterdir()):
                raise RuntimeError(f"{world.label}: the saved mission folder is incomplete. Restore its files before starting.")
        parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="mission-", dir=parent) as temp:
            stage = Path(temp)
            sources = [installation / "mpmissions" / world.template] if not world.workshop_ids else []
            if world.workshop_ids:
                for folder in (paths.mission_source, *reversed(workshop)):
                    sources.extend(mission_sources(folder, world))
            for source in sources:
                if source.is_file():
                    try:
                        source = unpack_mission(source, stage, world)
                    except BadZipFile as error:
                        raise ValueError(f"Could not open mission ZIP {source.name}. Extract a fresh copy into WorldSetup/{world.id}.") from error
                if source is None or not has_mission(source):
                    continue
                mission = parent / source.name
                plan, _ = _mission_plan(source)
                staged = stage / "world"
                staged.mkdir()
                for relative, expected in plan:
                    target = staged / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    _copy_file(source / relative, target, expected)
                preserved = None
                if not world.workshop_ids:
                    write_text(staged / "db" / "messages.xml", NO_SCHEDULE)
                else:
                    db = children(staged)["db"]
                    messages = children(db).get("messages.xml", db / "messages.xml")
                    preserved = remove_mission_shutdown(messages)
                staged.rename(mission)
                if preserved is not None:
                    self.events.put(f"{world.label}: optional server messages were invalid. "
                                    f"Azimuth preserved the original as {preserved.name} and imported the mission.")
                return mission
        raise RuntimeError(
            f"{world.label}: mission files are missing. Use Mission source and place the extracted mission "
            f"folder in WorldSetup/{world.id}, then click Start server."
        )

    def prepare_world(self, world_id: str) -> None:
        with self.lock:
            world = self.worlds.get(world_id)
            paths = self.worlds.paths(world_id)
            self._recover_interrupted_restore(paths)
            installation = self.find_installation()
            if installation is None:
                raise RuntimeError("DayZ Server is missing. Click Install in Steam first.")
            workshop = self._workshop_folders(installation, world, paths)
            pack = world.id == "avalon" and avalon_workshop.enabled(paths.config)
            if pack:
                self._check_avalon_animation_mods(paths)
            mission = self._stage_mission(installation, world, paths, workshop[:2] if pack else workshop)
            self._configure_world(world, paths, mission, workshop)
            for path in (paths.mods, paths.mods / "ServerOnly"):
                path.mkdir(parents=True, exist_ok=True)
            folders = self._mod_folders(paths.mods) + self._mod_folders(paths.mods / "ServerOnly")
            if pack:
                self._sync_avalon_pack_keys(installation, workshop, [folder for folder in folders
                                                                    if not self._is_solitude_copy(folder)])
            else:
                self._sync_keys(installation, workshop + folders)

    def _save_settings(self, settings: dict) -> None:
        write_text(self.settings_path, json.dumps(
            {
                **settings,
                "admin_password": self._admin_password,
                "join_password_revision": JOIN_PASSWORD_REVISION,
            },
            indent=2,
        ) + "\n")

    def _release_launch_lock(self) -> None:
        if self._launch_lock is not None:
            self._launch_lock.close()
            self._launch_lock = None

    def _reserve_launch(self) -> None:
        if os.name != "nt" or self._launch_lock is not None:
            return
        import msvcrt

        handle = (self.config / "server.lock").open("a+b")
        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            handle.close()
            raise RuntimeError("Another dashboard is already managing this server.") from error
        self._launch_lock = handle

    def _running(self) -> bool:
        if self.process is None:
            return False
        code = self.process.poll()
        if code is None:
            return True
        self._last_exit = code
        self.process = None
        self.loaded_client_mods = ()
        self._loaded_server_version = None
        self._stopping = False
        if not self._backup_pending:
            self._release_launch_lock()
        self.events.put(f"DayZ server exited with code {code}. Logs are in Profiles.")
        return False

    def update_settings(self, changes: dict) -> None:
        with self.lock:
            if self._running():
                raise RuntimeError("Stop the server before editing settings.")
            settings = {**self.settings, **changes}
            if settings.keys() != self.settings.keys():
                raise ValueError("Unrecognized server setting.")
            valid_settings(settings)
            self._save_settings(settings)
            self.settings = settings
            self.events.put("Server settings saved.")

    def set_installation(self, path: Path) -> Path:
        with self.lock:
            if self._running():
                raise RuntimeError("Stop the server before changing its installation folder.")
            candidate = inspect_installation(path)
            if candidate["status"] != "READY":
                raise ValueError("Choose the DayZ Server folder that contains DayZServer_x64.exe and mpmissions.")
            result = Path(candidate["install_dir"])
            write_text(self.installation_path, str(result) + "\n")
            self.installation = result
            self.events.put("Official DayZ Server installation found.")
            return result

    def find_installation(self) -> Path | None:
        with self.lock:
            if self._running():
                return self.installation
            if self.installation_path.is_file():
                configured = Path(self.installation_path.read_text(encoding="utf-8").strip())
                if inspect_installation(configured)["status"] == "READY":
                    self.installation = configured
                    return configured
            self.installation = None
            roots = [self.root / "Server", self.root]
            for folder in roots:
                if inspect_installation(folder)["status"] == "READY":
                    return self.set_installation(folder)
            result = discover_installations()
            for candidate in result["candidates"]:
                if candidate["status"] == "READY":
                    return self.set_installation(Path(candidate["install_dir"]))
            return None

    def _prepare_mission(self, installation: Path) -> Path:
        world = self.active_world
        workshop = self._workshop_folders(installation, world, self.world_paths)
        return self._stage_mission(installation, world, self.world_paths, workshop)

    def _mod_folders(self, root: Path) -> list[Path]:
        mods = {}
        for folder in sorted(root.iterdir(), key=lambda item: item.name.casefold()):
            if folder.name.casefold() == "serveronly":
                continue
            if not folder.is_dir():
                if folder.suffix.casefold() in {".zip", ".7z", ".rar"}:
                    raise ValueError(f"Extract {folder.name} before starting. Drop the extracted mod folder here.")
                continue
            addons = children(folder).get("addons")
            if addons is None or not any(p.is_file() and p.suffix.casefold() == ".pbo" for p in children(addons).values()):
                raise ValueError(f"{folder.name} needs an Addons folder with a .pbo file.")
            if ";" in str(folder):
                raise ValueError(f"Remove semicolons from the mod path for {folder.name}.")
            mods[folder.name.casefold()] = folder

        order = root / "load-order.txt"
        ordered = []
        if order.is_file():
            for line in order.read_text(encoding="utf-8-sig").splitlines():
                name = line.strip()
                if not name or name.startswith("#"):
                    continue
                folder = mods.pop(name.casefold(), None)
                if folder is None:
                    raise ValueError(f"{order.parent.name}/load-order.txt contains a missing or repeated folder: {name}")
                ordered.append(folder)
        return ordered + list(mods.values())

    def _sync_keys(self, installation: Path, folders: list[Path]) -> None:
        key_dir = installation / "keys"
        keys = {}
        for folder in folders:
            source = children(folder).get("keys")
            if source is not None:
                for key in children(source).values():
                    if not key.is_file() or key.suffix.casefold() != ".bikey":
                        continue
                    folded = key.name.casefold()
                    previous = keys.get(folded)
                    if previous and not filecmp.cmp(previous, key, shallow=False):
                        raise ValueError(f"Two mods supply different keys named {key.name}.")
                    keys[folded] = key
        if not keys:
            return
        key_dir.mkdir(exist_ok=True)
        existing = {key.name.casefold(): key for key in key_dir.iterdir()}
        for name, key in keys.items():
            if name in existing and not filecmp.cmp(existing[name], key, shallow=False):
                raise ValueError(f"A mod key conflicts with {existing[name].name} in the DayZ Server keys folder.")
        for name, key in keys.items():
            if name not in existing:
                target = key_dir / key.name
                try:
                    output = target.open("xb")
                except FileExistsError:
                    if not filecmp.cmp(target, key, shallow=False):
                        raise ValueError(f"A mod key conflicts with {key.name}.")
                    continue
                try:
                    with output, key.open("rb") as original:
                        shutil.copyfileobj(original, output)
                except OSError:
                    target.unlink(missing_ok=True)
                    raise

    def _collect_mods(self, installation: Path, server_only: list[Path] | None = None) -> list[Path]:
        folders = self._mod_folders(self.mods_dir)
        try:
            self._sync_keys(installation, folders + (server_only or []))
        except PermissionError as error:
            raise RuntimeError(f"Windows denied access to the DayZ Server keys folder at {installation / 'keys'}.") from error
        return folders

    def _write_config(self, template: str | None = None, *, enable_gameplay: bool = False) -> None:
        settings = self.settings
        lines = [
            f"hostname = {_quote(settings['hostname'])};",
            f"password = {_quote(settings['password'])};",
            f"passwordAdmin = {_quote(self._admin_password)};",
            f"maxPlayers = {settings['max_players']};",
            "BattlEye = 1;",
            "verifySignatures = 2;",
            "forceSameBuild = 1;",
            "allowFilePatching = 0;",
            "guaranteedUpdates = 1;",
            "instanceId = 1;",
            f"steamQueryPort = {settings['query_port']};",
            'timeStampFormat = "Full";',
            'logFile = "server_console.log";',
            *((
                'enableCfgGameplayFile = 1;',
                f'defaultVisibility = {VISUAL_SETTINGS["viewDistance"]};',
                f'defaultObjectViewDistance = {VISUAL_SETTINGS["preferredObjectViewDistance"]};',
            ) if enable_gameplay else ()),
            "class Missions",
            "{",
            "    class DayZ",
            "    {",
            f"        template = {_quote(template or self.active_world.template)};",
            "    };",
            "};",
            "",
        ]
        write_text(self.world_paths.config / "serverDZ.cfg", "\n".join(lines))

    def _launch_arguments(self, installation: Path, mission: Path,
                          mods: list[Path], server_only: list[Path] | None = None) -> list[str]:
        arguments = [
            str(installation / "DayZServer_x64.exe"),
            f"-config={self.world_paths.config / 'serverDZ.cfg'}",
            f"-profiles={self.profiles}",
            f"-mission={mission}",
            f"-storage={self.world / 'Saves'}",
            f"-port={self.settings['game_port']}",
            "-doLogs", "-adminLog", "-limitFPS=60", "-freezeCheck",
        ]
        if mods:
            arguments.append("-mod=" + ";".join(map(str, mods)))
        if server_only:
            arguments.append("-serverMod=" + ";".join(map(str, server_only)))
        return arguments

    def _launch_command(self, installation: Path, mission: Path, mods: list[Path],
                        server_only: list[Path]) -> list[str] | str:
        if os.name != "nt" or self.active_world_id != "avalon":
            return self._launch_arguments(installation, mission, mods, server_only)

        sources = list(dict.fromkeys(mods + server_only))
        aliases = stage_client_mods(installation, sources)
        relative = {source: alias.relative_to(installation) for source, alias in zip(sources, aliases, strict=True)}
        arguments = self._launch_arguments(installation, mission,
                                           [relative[source] for source in mods],
                                           [relative[source] for source in server_only])
        parameters = (f'"{argument}"' if argument.startswith(("-mod=", "-serverMod="))
                      else subprocess.list2cmdline([argument]) for argument in arguments[1:])
        return " ".join((f'"{arguments[0]}"', *parameters))

    def start(self) -> None:
        with self.lock:
            if self._running():
                return
            if (self.world_paths.config / "restore-transaction.json").exists():
                self._load_world_paths()
            installation = self.find_installation()
            if installation is None:
                self._last_world_notice = "DayZ Server is missing. Click Install in Steam, then click Start server when it finishes."
                raise RuntimeError(self._last_world_notice)
            self._reserve_launch()
            try:
                game_port = self.settings["game_port"]
                for port in (game_port, game_port + 2, self.settings["query_port"]):
                    ensure_udp_available(port)
                world = self.active_world
                workshop = self._workshop_folders(installation, world, self.world_paths)
                pack = world.id == "avalon" and avalon_workshop.enabled(self.world_paths.config)
                server_version = None
                if pack:
                    server_version = require_dayz130(installation / "DayZServer_x64.exe", "DayZ Server")
                    self._check_avalon_animation_mods(self.world_paths)
                mission = self._stage_mission(installation, world, self.world_paths, workshop[:2] if pack else workshop)
                logout_mod = avalon_qol.prepare_logout(mission, self.world_paths.config) if world.id == "avalon" else None
                self._configure_world(world, self.world_paths, mission, workshop)
                survey_mod = transport_survey.prepare_survey(mission, self.profiles, self.world_paths.config) if pack else None
                (self.world / "Saves").mkdir(exist_ok=True)
                server_only_dir = self.mods_dir / "ServerOnly"
                server_only_dir.mkdir(exist_ok=True)
                server_only = self._mod_folders(server_only_dir)
                local_mods = self._mod_folders(self.mods_dir)
                if world.id == "avalon" and self.avalon_color_preview_enabled():
                    local_mods.append(self._prepare_avalon_color_preview(installation))
                if pack:
                    local_mods = [folder for folder in local_mods if not self._is_solitude_copy(folder)]
                    server_only = [folder for folder in server_only if not self._is_solitude_copy(folder)]
                    self._sync_avalon_pack_keys(installation, workshop, local_mods + server_only)
                else:
                    self._sync_keys(installation, workshop + local_mods + server_only)
                if logout_mod is not None:
                    server_only.append(logout_mod)
                if survey_mod is not None:
                    server_only.append(survey_mod)
                mods = list(dict.fromkeys(workshop + local_mods))
                workshop_files = self._workshop_file_state(workshop)
                command = self._launch_command(installation, mission, mods, server_only)
                self._write_config(mission.name, enable_gameplay=world.id == "avalon")
                if self.world_paths.config != self.config:
                    write_text(self.world_paths.config / "settings.json",
                               self.settings_path.read_text(encoding="utf-8"))
                self._launch_reports = self._report_signatures()
                self._save_launch_provenance()
                if self._backup_due is None:
                    due = int(time.time()) + BACKUP_INTERVAL
                    write_text(self._backup_due_path, f"{due}\n")
                    self._backup_due = due
                self.process = subprocess.Popen(
                    command, executable=str(installation / "DayZServer_x64.exe"),
                    cwd=installation,
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
                )
            except Exception as error:
                self._last_world_notice = str(error)
                self._release_launch_lock()
                raise
            self._last_world_notice = ""
            self.loaded_client_mods = tuple(mods)
            self._loaded_server_version = server_version
            self._loaded_workshop_files = workshop_files
            self._last_exit = None
            self._backup_idle_since = None
            self._seen_online = False
            self._stopping = False
            count = len(mods) + len(server_only)
            self.events.put(f"Started DayZ process {self.process.pid} with {count} mod(s). Waiting for server response.")

    def stop(self) -> bool:
        with self.lock:
            if not self._running():
                return False
            if self._stopping:
                return True
            if os.name != "nt":
                raise RuntimeError("This server can be stopped from its Windows console.")
            try:
                result = subprocess.run(
                    [str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "taskkill.exe"),
                     "/PID", str(self.process.pid)],
                    capture_output=True, text=True, timeout=8,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            except (OSError, subprocess.TimeoutExpired) as error:
                raise RuntimeError("Windows could not request shutdown. Use the DayZ server console window.") from error
            if result.returncode and self._running():
                raise RuntimeError("Windows declined the shutdown request. Use the DayZ server console window.")
            if self._running():
                self._stopping = True
                self.events.put("Shutdown requested. Waiting for the DayZ process to exit.")
            return True

    def request_stop_backup(self, *, restart: bool = False) -> bool:
        """Request graceful shutdown and retain ownership until its archive ends."""
        with self.lock:
            if not self._running():
                return False
            if self.backing_up:
                raise RuntimeError("Wait for the current backup to finish.")
            self._backup_pending = True
            self._backup_restart = restart
            self._backup_stop_deadline = time.monotonic() + STOP_TIMEOUT
            self._backup_state = "Stopping for backup"
            try:
                return self.stop()
            except (OSError, RuntimeError):
                self._backup_pending = False
                self._backup_restart = False
                if not self._running():
                    self._release_launch_lock()
                self._backup_failed("Backup postponed because shutdown could not be requested.")
                raise

    def status(self) -> dict:
        with self.lock:
            data = {"state": "stopped", "players": None, "max_players": self.settings["max_players"],
                    "map": self.active_world.label, "version": None}
            if not self._running():
                if self._last_exit not in (None, 0):
                    data["state"] = "error"
                return data
            process = self.process
            host, port = self.settings["hostname"], self.settings["query_port"]
            terrain = self.active_world.template.rsplit(".", 1)[-1].casefold()
        response = query_info(port, timeout=0.55)
        info = response.get("info")
        matches = bool(info and info["name"] == host and info.get("map", terrain).casefold() == terrain)
        with self.lock:
            if self.process is not process or not self._running():
                return data
            if self._stopping:
                data["state"] = "stopping"
            elif matches:
                data["state"] = "online"
                if not self._seen_online:
                    self.events.put("Server is online. You can now join in DayZ.")
                    self._seen_online = True
            else:
                data["state"] = "running" if self._seen_online else "starting"
            if matches:
                data.update(players=info["players"], max_players=info["max_players"], version=info["version"])
                data["query_ms"] = response.get("elapsed_ms")
            return data

    @property
    def backing_up(self) -> bool:
        return self._backup_pending or self._backup_writing

    def backup_status(self) -> dict:
        def display(stamp: float | None) -> str | None:
            return datetime.fromtimestamp(stamp).strftime("%b %d, %H:%M") if stamp is not None else None

        return {
            "last": display(self._backup_last),
            "next": display(self._backup_due),
            "state": self._backup_state,
        }

    def _report_signatures(self) -> dict[Path, tuple[int, int]]:
        reports = {}
        for path in self.profiles.iterdir():
            if path.suffix.casefold() != ".rpt" or not path.is_file():
                continue
            with suppress(OSError):
                info = path.stat()
                reports[path] = (info.st_mtime_ns, info.st_size)
        return reports

    def _backup_clean_exit(self) -> bool:
        if self._launch_provenance == "restore":
            return True
        if self._launch_provenance != "launch":
            return False
        reports = self._report_signatures()
        for path, (modified, size) in sorted(reports.items(), key=lambda item: item[1][0], reverse=True):
            previous = self._launch_reports.get(path)
            if previous == (modified, size):
                continue
            start = previous[1] if previous and size > previous[1] else 0
            try:
                with path.open("rb") as report:
                    report.seek(max(start, size - 128 * 1024))
                    tail = report.read().replace(b"\x00", b"").lower()
                    return CLEAN_EXIT_MARKER in tail
            except OSError:
                # An unreadable newer session must not be mistaken for an
                # older session that shut down successfully.
                return False
        return False

    def _backup_failed(self, message: str) -> None:
        self._backup_state = "Backup delayed"
        self._backup_retry = time.monotonic() + BACKUP_RETRY
        self._backup_idle_since = None
        self.events.put(message)

    def _archive_stopped_server(self, now: float, restart: bool) -> None:
        self._backup_writing = True
        try:
            self._reserve_launch()
            if (self.world_paths.config / "restore-transaction.json").exists():
                self._load_world_paths()
                self._backup_writing = True
            if not self._backup_clean_exit():
                raise RuntimeError("DayZ's clean shutdown could not be confirmed in Profiles.")
            if not restart:
                port = self.settings["game_port"]
                for candidate in (port, port + 2, self.settings["query_port"]):
                    ensure_udp_available(candidate)
            self._backup_state = "Writing backup"
            self.events.put("Creating a ZIP from the stopped server.")
            path = write_backup(self.world_paths.root, now,
                                manager_root=self.root if self.active_world_id == "avalon" else None)
            self._backup_last = backup_time(path)
            self._backup_due = self._backup_last + BACKUP_INTERVAL
            self._backup_state = "Backups every 6 hours"
            self._backup_retry = 0.0
            self.events.put(f"Backup saved in Backups: {path.name}")
        except (OSError, RuntimeError, ValueError, BadZipFile) as error:
            self._backup_failed(f"Backup postponed: {error}")
        finally:
            self._backup_pending = False
            self._backup_restart = False
            if not restart:
                self._release_launch_lock()

        try:
            if restart:
                self.events.put("Restarting DayZ after backup maintenance.")
                self.start()
        except Exception as error:
            self._release_launch_lock()
            self.events.put(f"Automatic restart failed: {error} Click Start server to retry.")
        finally:
            self._backup_writing = False

    def backup_tick(self, status: dict) -> None:
        """Run the backup schedule in the dashboard's background polling worker."""
        with self.lock:
            now, monotonic = time.time(), time.monotonic()
            running = self._running()

            if self._backup_pending:
                if not running:
                    self._archive_stopped_server(now, restart=self._backup_restart)
                elif monotonic >= self._backup_stop_deadline:
                    self._backup_pending = False
                    self._backup_restart = False
                    self._backup_failed("Backup postponed because DayZ did not finish shutting down.")
                return

            if self._backup_due is None:
                return
            if now < self._backup_due or monotonic < self._backup_retry:
                return
            if not running:
                self._archive_stopped_server(now, restart=False)
                return
            if self._stopping:
                return
            if status.get("state") != "online" or status.get("players") != 0:
                self._backup_idle_since = None
                self._backup_state = "Backup due · waiting for empty server"
                return
            if self._backup_idle_since is None:
                self._backup_idle_since = monotonic
            if monotonic - self._backup_idle_since < BACKUP_IDLE:
                self._backup_state = "Backup due · checking server is empty"
                return

            try:
                self.request_stop_backup(restart=True)
            except (OSError, RuntimeError) as error:
                self._backup_failed(f"Backup postponed: {error}")

    def recent_logs(self) -> list[str]:
        with suppress(OSError):
            reports = self._report_signatures()
            if self._launch_provenance == "launch":
                reports = {path: signature for path, signature in reports.items()
                           if self._launch_reports.get(path) != signature}
                if not reports:
                    return ["No new report was written for the latest server launch. Earlier reports remain in Profiles."]
            logs = list(reports)
            if logs:
                latest = max(logs, key=lambda p: reports[p][0])
                with latest.open("rb") as file:
                    size = latest.stat().st_size
                    previous = self._launch_reports.get(latest) if self._launch_provenance == "launch" else None
                    start = previous[1] if previous and size > previous[1] else 0
                    file.seek(max(start, size - 16000))
                    return file.read(16000).decode("utf-8-sig", "replace").splitlines()[-100:]
        return []
