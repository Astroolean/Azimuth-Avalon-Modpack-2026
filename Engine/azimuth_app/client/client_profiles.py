"""Select a DayZ graphics profile using the player's choice and local evidence."""
from __future__ import annotations

import os
from pathlib import Path
import re
import stat
import time

from azimuth_app.client.client_performance import (
    MAX_PROFILE_BYTES,
    backup_file,
    load_profile,
    visual_backup_file,
    visibility_fields,
)

PROFILE_SUFFIX = "_settings.DayZProfile"
MAX_REPORT_HEADER = 512 * 1024
REPORT_MAX_AGE_SECONDS = 7 * 24 * 60 * 60
_CLIENT_REPORT = re.compile(r"DayZ_x64_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.rpt", re.I)
_INPUT_PROFILE = re.compile(
    rb'^[ \t]*\d{1,2}:\d{2}:\d{2}(?:\.\d+)?[ \t]+'
    rb'\[Inputs\] (?:Loading|Exporting) inputs cfg "([^"\r\n]+\.core\.xml)"[ \t]*\r?$',
    re.I | re.M,
)


def _profile_is_usable(profile: Path) -> bool:
    try:
        visibility_fields(load_profile(profile))
    except (OSError, ValueError):
        return False
    return True


def _ordinary_file(path: Path) -> os.stat_result | None:
    try:
        info = path.lstat()
    except OSError:
        return None
    if stat.S_ISREG(info.st_mode) and not getattr(info, "st_file_attributes", 0) & 0x400:
        return info
    return None


def _is_graphics_backup(backup: Path) -> bool:
    info = _ordinary_file(backup)
    if info is None or info.st_size > MAX_PROFILE_BYTES:
        return False
    try:
        with backup.open("rb") as stream:
            content = stream.read(MAX_PROFILE_BYTES + 1)
        if len(content) > MAX_PROFILE_BYTES or b"\0" in content:
            return False
        visibility_fields(content)
    except (OSError, ValueError):
        return False
    return True


def _decode_report_path(value: bytes) -> str:
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        return value.decode("mbcs" if os.name == "nt" else "utf-8", errors="replace")


def _recent_client_profile() -> Path | None:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        return None
    try:
        folder = Path(local_app_data) / "DayZ"
        latest = max((path for path in folder.iterdir() if _CLIENT_REPORT.fullmatch(path.name)),
                     key=lambda path: path.name.casefold(), default=None)
        if latest is None:
            return None
        info = _ordinary_file(latest)
        if info is None or not 0 <= time.time() - info.st_mtime <= REPORT_MAX_AGE_SECONDS:
            return None
        with latest.open("rb") as stream:
            header = stream.read(MAX_REPORT_HEADER)
    except OSError:
        return None
    # RPT command-line flags may include credentials. Only use the game's actual
    # input-file path, which also avoids confusing the multiplayer name with it.
    input_profiles = set()
    for match in _INPUT_PROFILE.finditer(header):
        input_path = Path(_decode_report_path(match.group(1)))
        if input_path.is_absolute():
            name = input_path.name[:-len(".core.xml")]
            if name:
                input_profiles.add(input_path.with_name(name + PROFILE_SUFFIX))
    return next(iter(input_profiles)) if len(input_profiles) == 1 else None


def choose_profile(profiles: list[Path], remembered: Path | None = None) -> Path | None:
    """Reuse a saved path or identify a client profile from local evidence."""
    if remembered is not None and remembered.is_absolute() and _profile_is_usable(remembered):
        return remembered
    candidates = [path for path in dict.fromkeys(profiles) if _profile_is_usable(path)]

    recent_profile = _recent_client_profile()
    if recent_profile is not None and _profile_is_usable(recent_profile):
        return recent_profile

    # DayZ Server can generate this same named profile in Documents. A saved
    # choice, client log, or Azimuth backup can establish that it was used here.
    previously_selected = [path for path in candidates if any(
        _is_graphics_backup(backup) for backup in (backup_file(path), visual_backup_file(path)))]
    if len(previously_selected) == 1:
        return previously_selected[0]

    client_candidates = [path for path in candidates if tuple(part.casefold() for part in path.parts[-3:])
                         != ("dayz other profiles", "server", "server_settings.dayzprofile")]
    return client_candidates[0] if len(client_candidates) == 1 and not previously_selected else None
