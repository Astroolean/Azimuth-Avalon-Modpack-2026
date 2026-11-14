"""Find the Steam DayZ client and join Azimuth through its BattlEye launcher."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
import subprocess

from azimuth_app.network.server_discovery import MAX_MANIFEST_BYTES, _installation_under_library, _read_vdf, discover_installations
from azimuth_app.client.steam_session import ensure_steam_running, session_processes
from azimuth_app.client.windows_client_launch import launch_as_desktop_user, launch_with_windows_consent

DAYZ_CLIENT_APP_ID = "221100"
CLIENT_PROCESSES = {"dayz_be.exe", "dayz_x64.exe", "dayzdiag_x64.exe", "dayz.exe", "dayzlauncher.exe"}
CLIENT_MOD_FOLDER = "!Azimuth"


def find_client(libraries: list[Path]) -> Path | None:
    """Return a complete client installation from one of Steam's local libraries."""
    def search(roots: list[Path]) -> Path | None:
        for library in roots:
            manifest = library / "steamapps" / f"appmanifest_{DAYZ_CLIENT_APP_ID}.acf"
            _, metadata = _read_vdf(manifest, MAX_MANIFEST_BYTES)
            app = (metadata or {}).get("appstate")
            if isinstance(app, dict) and app.get("appid") == DAYZ_CLIENT_APP_ID:
                installed = _installation_under_library(library, app.get("installdir"))
                if installed and _is_client_installation(installed):
                    return installed
            conventional = library / "steamapps" / "common" / "DayZ"
            if _is_client_installation(conventional):
                return conventional
        return None

    installed = search(libraries)
    if installed is not None:
        return installed
    discovered = discover_installations(extra_steam_roots=libraries)
    return search([Path(path) for path in discovered.get("library_paths", [])])


def _is_client_installation(folder: Path) -> bool:
    return (folder / "DayZ_BE.exe").is_file() and (folder / "DayZ_x64.exe").is_file()


def _client_arguments(install: Path, mods: list[Path], port: int, password: str,
                      *, relative_mods: bool = False) -> list[str]:
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("Choose a valid DayZ game port before playing.")
    mod_paths = []
    for mod in mods:
        if not mod.is_absolute() or not mod.is_dir() or any(c in str(mod) for c in ';"\r\n\x00'):
            raise ValueError(f"DayZ cannot load the client mod folder {mod.name}.")
        mod_paths.append(str(mod.relative_to(install) if relative_mods else mod))

    arguments = [
        str(install / "DayZ_BE.exe"), "0", "1", "1", "-exe", "DayZ_x64.exe", "-dologs",
    ]
    if mod_paths:
        arguments.append("-mod=" + ";".join(mod_paths))
    arguments.extend(("-connect=127.0.0.1", f"-port={port}"))
    if password:
        arguments.append(f"-password={password}")
    return arguments


def _client_parameters(arguments: list[str]) -> str:
    # Match the official launcher's whole-argument quoting for the mod list.
    return " ".join(f'"{argument}"' if argument.startswith("-mod=")
                    else subprocess.list2cmdline([argument]) for argument in arguments[1:])


def _client_command_line(arguments: list[str]) -> str:
    return f'"{arguments[0]}" {_client_parameters(arguments)}'.rstrip()


def _create_junction(source: Path, destination: Path) -> None:
    import _winapi

    _winapi.CreateJunction(str(source), str(destination))


def _ordinary_directory(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISDIR(info.st_mode) and not getattr(info, "st_file_attributes", 0) & 0x400


def _usable_mod(mod: Path) -> bool:
    addons = next((entry for entry in mod.iterdir() if entry.name.casefold() == "addons"), None)
    return addons is not None and addons.is_dir() and any(
        entry.is_file() and entry.suffix.casefold() == ".pbo" for entry in addons.iterdir())


def stage_client_mods(install: Path, mods: list[Path]) -> list[Path]:
    """Link the original mod content under short, stable names in a DayZ installation."""
    if not mods:
        return []
    folder = install / CLIENT_MOD_FOLDER
    try:
        folder.mkdir(exist_ok=True)
        if not _ordinary_directory(folder):
            raise RuntimeError(f"DayZ's {CLIENT_MOD_FOLDER} must be an ordinary folder.")
    except OSError as error:
        raise RuntimeError(f"Azimuth could not prepare {CLIENT_MOD_FOLDER} in {install}.") from error

    aliases = []
    for mod in mods:
        try:
            source = mod.resolve(strict=True)
            if not source.is_dir() or not _usable_mod(source):
                raise ValueError(f"{mod.name} has no PBOs in its Addons folder.")
            digest = hashlib.sha256(os.path.normcase(str(source)).encode("utf-8")).hexdigest()[:16]
            alias = folder / f"@{digest}"
            if not os.path.lexists(alias):
                try:
                    _create_junction(source, alias)
                except FileExistsError:
                    pass
            if not alias.samefile(source):
                raise RuntimeError(f"Azimuth found a conflicting mod link for {mod.name}.")
            aliases.append(alias)
        except OSError as error:
            raise RuntimeError(f"Azimuth could not link {mod.name} into {install}. {error.strerror or error}") from error
    return aliases


def _client_environment() -> dict[str, str]:
    app_id = {"SteamAppId": DAYZ_CLIENT_APP_ID, "SteamGameId": DAYZ_CLIENT_APP_ID}
    id_keys = {key.casefold() for key in app_id}
    return {**{key: value for key, value in os.environ.items() if key.casefold() not in id_keys}, **app_id}


def launch_client(libraries: list[Path], mods: list[Path], port: int, password: str) -> list[Path]:
    """Start the owned Steam client using the server's ordered client mod list."""
    if os.name != "nt":
        raise RuntimeError("Play Avalon launches the Steam DayZ client on Windows.")
    install = find_client(libraries)
    if install is None:
        raise RuntimeError("Steam DayZ is missing or incomplete. Install or verify DayZ in Steam and try again.")
    processes = session_processes()
    if CLIENT_PROCESSES.intersection(processes.values()):
        raise RuntimeError("Close DayZ and DayZ Launcher before using Play Avalon.")
    steam = ensure_steam_running(processes)
    aliases = stage_client_mods(install, mods)
    consent_needed = steam.steam_elevated and not steam.azimuth_elevated
    arguments = _client_arguments(install, aliases, port, password, relative_mods=consent_needed)
    executable = install / "DayZ_BE.exe"
    try:
        if consent_needed:
            # ShellExecute inherits its own environment. Retail DayZ provides its
            # Steam App ID in the game directory used as the working directory.
            try:
                with (install / "steam_appid.txt").open("rb") as appid:
                    payload = appid.read(33)
                    valid_appid = len(payload) <= 32 and payload.strip() == DAYZ_CLIENT_APP_ID.encode("ascii")
            except OSError:
                valid_appid = False
            if not valid_appid:
                raise RuntimeError("DayZ's Steam app ID file is missing or invalid. Verify DayZ in Steam, then retry.")
            launch_with_windows_consent(executable, _client_parameters(arguments), install)
        elif steam.azimuth_elevated and not steam.steam_elevated:
            launch_as_desktop_user(executable, _client_command_line(arguments), install, _client_environment())
        else:
            subprocess.Popen(_client_command_line(arguments), executable=str(executable),
                             cwd=install, env=_client_environment())
    except OSError:
        raise RuntimeError("Windows could not start DayZ through BattlEye at Steam's access level.") from None
    return aliases
