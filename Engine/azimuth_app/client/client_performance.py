"""Apply reversible video presets to an existing DayZ client settings profile."""
from __future__ import annotations

import csv
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
from uuid import UUID

VISIBILITY_LIMITS = {"viewDistance": 1600, "preferredObjectViewDistance": 1000}
VISUAL_SETTINGS = {
    "viewDistance": 5000,
    "preferredObjectViewDistance": 3000,
    "TexQuality": 3,
    "shadowQuality": 3,
    "shadowZDistance": 200,
    "sceneComplexity": 1000000,
}
# Earlier Azimuth versions also changed water. Restore those original values
# without continuing to force water to the old low setting.
VISUAL_RESTORE_SETTINGS = {**VISUAL_SETTINGS, "WaterQuality": 0}
QUALITY_CFG_SETTINGS = {"MSAA": 4, "AToC": 1, "FXAA": 2, "AnisoFilter": 2}
AA_CFG_SETTINGS = {key: value for key, value in QUALITY_CFG_SETTINGS.items() if key != "AnisoFilter"}
MAX_PROFILE_BYTES = 2 * 1024 * 1024
ACTIVE_CLIENTS = {"dayz_x64.exe", "dayz.exe", "dayz_be.exe", "dayzlauncher.exe", "dayzdiag_x64.exe"}
HIGH_TEXTURE_FILTERING = 2


def documents_directory() -> Path:
    """Use Windows' actual Documents location, including redirected folders."""
    if os.name == "nt":
        folder = ctypes.c_wchar_p()
        folder_id = (ctypes.c_ubyte * 16).from_buffer_copy(
            UUID("FDD39AD0-238F-46AF-ADB4-6C85480369C7").bytes_le
        )
        shell = ctypes.WinDLL("shell32", winmode=0x800)
        ole = ctypes.WinDLL("ole32", winmode=0x800)
        get_path = shell.SHGetKnownFolderPath
        get_path.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.HANDLE,
                             ctypes.POINTER(ctypes.c_wchar_p)]
        get_path.restype = ctypes.c_long
        free = ole.CoTaskMemFree
        free.argtypes = [ctypes.c_void_p]
        free.restype = None
        try:
            if get_path(ctypes.byref(folder_id), 0, None, ctypes.byref(folder)) == 0 and folder.value:
                return Path(folder.value)
        finally:
            if folder:
                free(ctypes.cast(folder, ctypes.c_void_p))
    return Path.home() / "Documents"


def find_profiles() -> list[Path]:
    root = documents_directory()
    candidates = []
    for folder in (root / "DayZ", root / "DayZ Other Profiles"):
        if folder.is_dir():
            candidates.extend(path for path in folder.glob("**/*_settings.DayZProfile") if path.is_file())
    return sorted(set(candidates), key=lambda path: str(path).casefold())


def ensure_client_closed() -> None:
    if os.name != "nt":
        return
    tasklist = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "tasklist.exe"
    try:
        result = subprocess.run(
            [str(tasklist), "/FO", "CSV", "/NH"],
            capture_output=True, text=True, errors="replace", timeout=8, check=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError("Azimuth could not check whether DayZ is closed. Try again.") from error
    if any(row and row[0].casefold() in ACTIVE_CLIENTS for row in csv.reader(result.stdout.splitlines())):
        raise RuntimeError("Close the DayZ game and DayZ Launcher before changing your video settings.")


def load_profile(profile: Path) -> bytes:
    if not profile.name.casefold().endswith("_settings.dayzprofile"):
        raise ValueError("Select a file ending in _settings.DayZProfile.")
    return _read_settings_file(profile, "This DayZ profile")


def _read_settings_file(path: Path, label: str) -> bytes:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ValueError(f"{label} must be an ordinary file.")
    with path.open("rb") as stream:
        content = stream.read(MAX_PROFILE_BYTES + 1)
    if len(content) > MAX_PROFILE_BYTES or b"\0" in content:
        raise ValueError(f"{label} is larger than expected or uses an unsupported format.")
    return content


def assignment(key: str) -> re.Pattern[bytes]:
    return re.compile(
        rb"(?m)^(?:\xef\xbb\xbf)?[ \t]*" + key.encode("ascii")
        + rb"[ \t]*=[ \t]*(?P<number>[0-9]+(?:\.[0-9]+)?)[ \t]*;"
    )


def _numeric_fields(content: bytes, settings: dict[str, int], label: str,
                    *, insensitive: bool = False) -> dict[str, re.Match[bytes]]:
    result = {}
    for key in settings:
        flags = re.MULTILINE | (re.IGNORECASE if insensitive else 0)
        starts = list(re.finditer(rb"^(?:\xef\xbb\xbf)?[ \t]*" + key.encode("ascii") + rb"[ \t]*=", content, flags))
        if len(starts) > 1:
            raise ValueError(f"{label} has more than one {key} value.")
        if starts:
            numeric = re.compile(assignment(key).pattern, flags).match(content, starts[0].start())
            if numeric is None:
                raise ValueError(f"{label} has an invalid numeric {key} value.")
            result[key] = numeric
    return result


def _profile_fields(content: bytes, settings: dict[str, int]) -> dict[str, re.Match[bytes]]:
    result = _numeric_fields(content, settings, "The DayZ profile")
    if not VISIBILITY_LIMITS.keys() & result.keys():
        raise ValueError("This DayZ profile has no recognized visibility settings. Run DayZ once and save its video settings.")
    return result


def visibility_fields(content: bytes) -> dict[str, re.Match[bytes]]:
    return _profile_fields(content, VISIBILITY_LIMITS)


def replace_values(content: bytes, replacements: list[tuple[re.Match[bytes], bytes]]) -> bytes:
    for match, value in sorted(replacements, key=lambda item: item[0].start("number"), reverse=True):
        start, end = match.span("number")
        content = content[:start] + value + content[end:]
    return content


def backup_file(profile: Path) -> Path:
    return profile.with_name(profile.name + ".azimuth-fps.bak")


def visual_backup_file(profile: Path) -> Path:
    return profile.with_name(profile.name + ".azimuth-visual.bak")


def _replace_settings_file(path: Path, expected: bytes, content: bytes, label: str) -> None:
    if expected == content:
        return
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".azimuth-video-",
                                         delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
        ensure_client_closed()
        if _read_settings_file(path, label) != expected:
            raise RuntimeError("DayZ settings changed during the edit. Close DayZ and try again.")
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def replace_profile(profile: Path, expected: bytes, content: bytes) -> None:
    _replace_settings_file(profile, expected, content, "This DayZ profile")


def _texture_filter_field(content: bytes) -> re.Match[bytes] | None:
    return _numeric_fields(content, {"AnisoFilter": HIGH_TEXTURE_FILTERING}, "DayZ.cfg", insensitive=True).get("AnisoFilter")


def _texture_filter_files() -> tuple[Path, Path]:
    config = documents_directory() / "DayZ" / "DayZ.cfg"
    return config, config.with_name("DayZ.cfg.azimuth-filter.bak")


def apply_texture_filtering() -> bool:
    """Raise an existing DayZ.cfg texture filtering setting to High."""
    config, backup = _texture_filter_files()
    if not os.path.lexists(config):
        return False
    ensure_client_closed()
    content = _read_settings_file(config, "DayZ.cfg")
    match = _texture_filter_field(content)
    if match is None or float(match.group("number")) >= HIGH_TEXTURE_FILTERING:
        return False
    created = False
    try:
        with backup.open("xb") as stream:
            created = True
            stream.write(content)
    except FileExistsError:
        original = _read_settings_file(backup, "The Azimuth texture filtering backup")
        if _texture_filter_field(original) is None:
            raise ValueError("The Azimuth texture filtering backup is missing its original setting.")
    except OSError:
        if created:
            backup.unlink(missing_ok=True)
        raise
    updated = replace_values(content, [(match, str(HIGH_TEXTURE_FILTERING).encode("ascii"))])
    _replace_settings_file(config, content, updated, "DayZ.cfg")
    return True


def restore_texture_filtering() -> bool:
    config, backup = _texture_filter_files()
    if not os.path.lexists(backup) or not os.path.lexists(config):
        return False
    ensure_client_closed()
    content = _read_settings_file(config, "DayZ.cfg")
    original = _read_settings_file(backup, "The Azimuth texture filtering backup")
    current_field = _texture_filter_field(content)
    saved_field = _texture_filter_field(original)
    if current_field is None or saved_field is None:
        return False
    updated = replace_values(content, [(current_field, saved_field.group("number"))])
    _replace_settings_file(config, content, updated, "DayZ.cfg")
    return updated != content


def _apply_changes(profile: Path, backup: Path, prior_backup: Path, content: bytes,
                   changes: list[tuple[re.Match[bytes], bytes]],
                   settings: dict[str, int]) -> Path | None:
    if not changes:
        return None
    label = "The Azimuth video backup"
    original = _optional_settings(backup, label)
    current = _profile_fields(content, settings)
    changed_spans = {match.span("number") for match, _ in changes}
    changed = [key for key, match in current.items() if match.span("number") in changed_spans]
    baseline = content
    if original is None:
        prior = _optional_settings(prior_backup, label)
        original_visibility = visibility_fields(prior) if prior is not None else {}
        baseline = replace_values(content, [
            (match, original_visibility[key].group("number"))
            for key, match in visibility_fields(content).items()
            if key in original_visibility
        ])
    # Validate an existing baseline before changing the profile. If DayZ added
    # a field since that baseline, remember its current value once as well.
    remembered = _remember_fields(backup, original, baseline, current, changed,
                                  lambda value: _profile_fields(value, settings), label)
    _apply_transaction([remembered] if remembered is not None else [], [
        _FileEdit(profile, content, replace_values(content, changes), "This DayZ profile"),
    ])
    return backup


def apply_preset(profile: Path) -> Path | None:
    ensure_client_closed()
    content = load_profile(profile)
    current = visibility_fields(content)
    changes = [(match, str(VISIBILITY_LIMITS[key]).encode("ascii"))
               for key, match in current.items()
               if float(match.group("number")) > VISIBILITY_LIMITS[key]]
    return _apply_changes(profile, backup_file(profile), visual_backup_file(profile), content, changes,
                          VISIBILITY_LIMITS)


def apply_visual_preset(profile: Path, *, cap_visibility: bool = False) -> Path | None:
    """Raise saved profile detail, preserving higher quality and water choices."""
    ensure_client_closed()
    content = load_profile(profile)
    current = _profile_fields(content, VISUAL_SETTINGS)
    changes = []
    for key, match in current.items():
        target = VISUAL_SETTINGS[key]
        value = float(match.group("number"))
        if cap_visibility and key in VISIBILITY_LIMITS:
            target = min(value, VISIBILITY_LIMITS[key])
        elif value >= target:
            continue
        if value != target:
            changes.append((match, str(target).encode("ascii")))
    return _apply_changes(profile, visual_backup_file(profile), backup_file(profile), content, changes,
                          VISUAL_RESTORE_SETTINGS)


def _restore_settings(profile: Path, backup: Path, settings: dict[str, int], label: str) -> Path:
    ensure_client_closed()
    content = load_profile(profile)
    try:
        original = _read_settings_file(backup, f"The Azimuth {label} backup")
    except FileNotFoundError as error:
        raise ValueError(f"There is no Azimuth {label} backup for this profile.") from error
    saved_fields = _profile_fields(original, settings)
    current = _profile_fields(content, settings)
    changes = [(match, saved_fields[key].group("number")) for key, match in current.items()
               if key in saved_fields]
    if not changes:
        raise ValueError("The current DayZ profile no longer contains the saved video settings.")
    replace_profile(profile, content, replace_values(content, changes))
    return backup


def restore_profile(profile: Path) -> Path:
    return _restore_settings(profile, backup_file(profile), VISIBILITY_LIMITS, "FPS")


def restore_visual_profile(profile: Path) -> Path:
    backup = visual_backup_file(profile)
    if not os.path.lexists(backup):
        return restore_profile(profile)
    return _restore_settings(profile, backup, VISUAL_RESTORE_SETTINGS, "Visuals")


@dataclass(frozen=True)
class _FileEdit:
    path: Path
    expected: bytes | None
    content: bytes
    label: str


def _optional_settings(path: Path, label: str) -> bytes | None:
    if not os.path.lexists(path):
        return None
    return _read_settings_file(path, label)


def _cfg_fields(content: bytes, settings: dict[str, int] = QUALITY_CFG_SETTINGS) -> dict[str, re.Match[bytes]]:
    return _numeric_fields(content, settings, "DayZ.cfg", insensitive=True)


def _cfg_backup_fields(content: bytes, settings: dict[str, int]) -> dict[str, re.Match[bytes]]:
    fields = _cfg_fields(content, settings)
    if not fields:
        raise ValueError("The Azimuth graphics backup is missing its original settings.")
    return fields


def _remember_fields(path: Path, original: bytes | None, baseline: bytes,
                     current: dict[str, re.Match[bytes]], changed: list[str], reader,
                     label: str) -> _FileEdit | None:
    if original is None:
        return _FileEdit(path, None, baseline, label)
    saved = reader(original)
    # A later DayZ version may add a field that was absent in the first backup.
    # Capture its current value once, while retaining every earlier baseline.
    missing = [key for key in changed if key not in saved]
    if not missing:
        return None
    newline = b"\r\n" if b"\r\n" in original else b"\n"
    updated = original + (b"" if original.endswith(b"\n") else newline)
    for key in missing:
        updated += key.encode("ascii") + b"=" + current[key].group("number") + b";" + newline
    reader(updated)
    return _FileEdit(path, original, updated, label)


def _perform_edit(edit: _FileEdit) -> None:
    if edit.expected is not None:
        _replace_settings_file(edit.path, edit.expected, edit.content, edit.label)
        return
    created = False
    try:
        ensure_client_closed()
        with edit.path.open("xb") as stream:
            created = True
            stream.write(edit.content)
    except OSError:
        if created:
            edit.path.unlink(missing_ok=True)
        raise


def _undo_edit(edit: _FileEdit) -> None:
    current = _optional_settings(edit.path, edit.label)
    if current == edit.expected:
        return
    if current != edit.content:
        raise RuntimeError(f"{edit.path.name} changed again; it was not overwritten during rollback.")
    if edit.expected is None:
        edit.path.unlink()
    else:
        _replace_settings_file(edit.path, edit.content, edit.expected, edit.label)


def _apply_transaction(backups: list[_FileEdit], targets: list[_FileEdit]) -> None:
    completed_backups, completed_targets = [], []
    try:
        # Each publish checks the client and current bytes again. Preflight has
        # already validated both destinations and every required original value.
        for edit in backups:
            _perform_edit(edit)
            completed_backups.append(edit)
        for edit in targets:
            _perform_edit(edit)
            completed_targets.append(edit)
    except (OSError, RuntimeError, ValueError) as error:
        rollback_errors = []
        for edit in reversed(completed_targets):
            try:
                _undo_edit(edit)
            except (OSError, RuntimeError, ValueError) as rollback_error:
                rollback_errors.append(str(rollback_error))
        if not rollback_errors:
            for edit in reversed(completed_backups):
                try:
                    _undo_edit(edit)
                except (OSError, RuntimeError, ValueError) as rollback_error:
                    rollback_errors.append(str(rollback_error))
        if rollback_errors:
            raise RuntimeError(f"{error} Some settings could not be rolled back safely. "
                               "Their graphics backups were retained. " + " ".join(rollback_errors)) from error
        raise


def _quality_profile_changes(content: bytes, cap_visibility: bool) -> tuple[dict, list, list]:
    current = _profile_fields(content, VISUAL_SETTINGS)
    changes, changed = [], []
    for key, match in current.items():
        value = float(match.group("number"))
        target = min(value, VISIBILITY_LIMITS[key]) if cap_visibility and key in VISIBILITY_LIMITS else max(value, VISUAL_SETTINGS[key])
        if value != target:
            changes.append((match, str(int(target)).encode("ascii")))
            changed.append(key)
    return current, changes, changed


def apply_quality_preset(profile: Path, *, cap_visibility: bool = False) -> dict:
    """Apply saved client quality fields together, with independent AA originals.

    Only existing numeric fields are edited. Missing keys are returned so the UI
    can distinguish a partial application from a fully configured preset.
    """
    ensure_client_closed()
    content = load_profile(profile)
    profile_fields, profile_changes, changed = _quality_profile_changes(content, cap_visibility)
    missing = [key for key in VISUAL_SETTINGS if key not in profile_fields]
    config, filter_backup = _texture_filter_files()
    quality_backup = config.with_name("DayZ.cfg.azimuth-quality.bak")
    cfg = _optional_settings(config, "DayZ.cfg")
    cfg_fields = _cfg_fields(cfg) if cfg is not None else {}
    missing.extend(key for key in QUALITY_CFG_SETTINGS if key not in cfg_fields)
    cfg_changed = [key for key, match in cfg_fields.items() if float(match.group("number")) < QUALITY_CFG_SETTINGS[key]]
    cfg_changes = [(cfg_fields[key], str(QUALITY_CFG_SETTINGS[key]).encode("ascii")) for key in cfg_changed]

    backups, targets = [], []
    if profile_changes:
        destination = visual_backup_file(profile)
        original = _optional_settings(destination, "The Azimuth Visuals backup")
        baseline = content
        if original is None:
            prior = _optional_settings(backup_file(profile), "The Azimuth FPS backup")
            saved_visibility = visibility_fields(prior) if prior is not None else {}
            baseline = replace_values(content, [(match, saved_visibility[key].group("number"))
                                                for key, match in profile_fields.items() if key in saved_visibility])
        edit = _remember_fields(destination, original, baseline, profile_fields, changed,
                                lambda value: _profile_fields(value, VISUAL_RESTORE_SETTINGS), "The Azimuth Visuals backup")
        if edit is not None:
            backups.append(edit)
        targets.append(_FileEdit(profile, content, replace_values(content, profile_changes), "This DayZ profile"))
    if cfg_changes:
        aa_changed = [key for key in cfg_changed if key in AA_CFG_SETTINGS]
        if aa_changed:
            original = _optional_settings(quality_backup, "The Azimuth image-quality backup")
            edit = _remember_fields(quality_backup, original, cfg, cfg_fields, aa_changed,
                                    lambda value: _cfg_backup_fields(value, AA_CFG_SETTINGS), "The Azimuth image-quality backup")
            if edit is not None:
                backups.append(edit)
        if "AnisoFilter" in cfg_changed:
            original = _optional_settings(filter_backup, "The Azimuth texture filtering backup")
            edit = _remember_fields(filter_backup, original, cfg, cfg_fields, ["AnisoFilter"],
                                    lambda value: _cfg_backup_fields(value, {"AnisoFilter": 2}), "The Azimuth texture filtering backup")
            if edit is not None:
                backups.append(edit)
        targets.append(_FileEdit(config, cfg, replace_values(cfg, cfg_changes), "DayZ.cfg"))
    _apply_transaction(backups, targets)
    return {"changed": bool(targets), "changed_fields": changed + cfg_changed,
            "missing_fields": missing, "config_missing": cfg is None}


def restore_quality_preset(profile: Path) -> dict:
    """Restore only managed fields, retaining later unrelated in-game edits."""
    ensure_client_closed()
    content = load_profile(profile)
    fields = _profile_fields(content, VISUAL_RESTORE_SETTINGS)
    visual = _optional_settings(visual_backup_file(profile), "The Azimuth Visuals backup")
    fps = _optional_settings(backup_file(profile), "The Azimuth FPS backup") if visual is None else None
    saved = _profile_fields(visual, VISUAL_RESTORE_SETTINGS) if visual is not None else (visibility_fields(fps) if fps is not None else {})
    changed = [key for key in fields if key in saved and fields[key].group("number") != saved[key].group("number")]
    missing = [key for key in saved if key not in fields]
    targets = []
    if changed:
        updated = replace_values(content, [(fields[key], saved[key].group("number")) for key in changed])
        targets.append(_FileEdit(profile, content, updated, "This DayZ profile"))

    config, filter_backup = _texture_filter_files()
    quality_backup = config.with_name("DayZ.cfg.azimuth-quality.bak")
    cfg = _optional_settings(config, "DayZ.cfg")
    cfg_fields = _cfg_fields(cfg) if cfg is not None else {}
    original_aa = _optional_settings(quality_backup, "The Azimuth image-quality backup")
    original_filter = _optional_settings(filter_backup, "The Azimuth texture filtering backup")
    cfg_saved = _cfg_backup_fields(original_aa, AA_CFG_SETTINGS) if original_aa is not None else {}
    if original_filter is not None:
        cfg_saved.update(_cfg_backup_fields(original_filter, {"AnisoFilter": 2}))
    missing.extend(key for key in cfg_saved if key not in cfg_fields)
    cfg_changed = [key for key in cfg_saved if key in cfg_fields and cfg_fields[key].group("number") != cfg_saved[key].group("number")]
    if cfg_changed:
        updated = replace_values(cfg, [(cfg_fields[key], cfg_saved[key].group("number")) for key in cfg_changed])
        targets.append(_FileEdit(config, cfg, updated, "DayZ.cfg"))
    _apply_transaction([], targets)
    return {"changed": bool(targets), "changed_fields": changed + cfg_changed,
            "missing_fields": missing, "config_missing": cfg is None}
