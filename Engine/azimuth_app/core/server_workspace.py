"""Copy an isolated mission from the local DayZ Server installation."""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import stat


MISSION_NAME = "dayzOffline.chernarusplus"
MAX_COPY_FILES = 5000
MAX_COPY_BYTES = 128 * 1024 * 1024
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_ENTRIES = 10_000
MAX_MISSION_DEPTH = 16
MISSION_SUFFIXES = frozenset((".xml", ".json", ".c"))
EXCLUDED_DIRECTORIES = frozenset((
    "logs", "log", "profiles", "artifacts", "reports", "backups", "backup",
    "cache", "screenshots", ".git", "__pycache__",
))
_DEVICE_NAMES = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} | {
    prefix + digit for prefix in ("COM", "LPT") for digit in "123456789\u00b9\u00b2\u00b3"
}


class WorkspaceError(ValueError):
    """A bounded, path-free diagnostic code safe to include in a report."""


def _fail(code: str) -> None:
    raise WorkspaceError(code)


def _is_link(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _check_name(name: str) -> None:
    if (not name or name in (".", "..") or len(name) > 255
            or name.endswith((".", " "))
            or any(ord(char) < 32 or char in '<>:"/\\|?*' for char in name)
            or name.split(".", 1)[0].upper() in _DEVICE_NAMES):
        _fail("UNSAFE_MISSION_NAME")


def _check_existing(path: Path, directory: bool) -> os.stat_result:
    """Check all components with lstat; never resolve a junction or symlink."""
    if len(path.parts) > 64:
        _fail("PATH_DEPTH_LIMIT")
    for current in [*reversed(path.parents), path]:
        info = current.lstat()
        if _is_link(info):
            _fail("LINK_OR_REPARSE_POINT")
        wanted_dir = current != path or directory
        if not (stat.S_ISDIR(info.st_mode) if wanted_dir else stat.S_ISREG(info.st_mode)):
            _fail("WRONG_PATH_TYPE")
    return info


def _inside(path: Path, parent: Path) -> bool:
    try:
        return os.path.commonpath((os.path.normcase(str(path)), os.path.normcase(str(parent)))) == os.path.normcase(str(parent))
    except ValueError:
        return False


def _mkdir_checked(path: Path) -> None:
    if len(path.parts) > 64:
        _fail("PATH_DEPTH_LIMIT")
    for current in [*reversed(path.parents), path]:
        try:
            info = current.lstat()
        except FileNotFoundError:
            current.mkdir(mode=0o700)
            info = current.lstat()
        if _is_link(info):
            _fail("LINK_OR_REPARSE_POINT")
        if not stat.S_ISDIR(info.st_mode):
            _fail("WRONG_PATH_TYPE")


def _signature(info: os.stat_result) -> tuple:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


def _mission_plan(source: Path) -> tuple[list[tuple[Path, tuple]], dict]:
    """Preflight before creating the run. Pruned subtrees are never traversed."""
    files: list[tuple[Path, tuple]] = []
    counts = {"copied_files": 0, "copied_bytes": 0, "skipped_storage_entries": 0,
              "skipped_other_entries": 0}
    entries_seen = 0
    pending = [(source, 0)]
    while pending:
        directory, depth = pending.pop()
        _check_existing(directory, True)
        if depth > MAX_MISSION_DEPTH:
            _fail("MISSION_DEPTH_LIMIT")
        folded_names: set[str] = set()
        with os.scandir(directory) as entries:
            for entry in entries:
                entries_seen += 1
                if entries_seen > MAX_ENTRIES:
                    _fail("MISSION_ENTRY_LIMIT")
                _check_name(entry.name)
                folded = entry.name.casefold()
                if folded in folded_names:
                    _fail("CASE_COLLIDING_MISSION_NAMES")
                folded_names.add(folded)
                path = directory / entry.name
                # Windows DirEntry.stat() caches zero inode/device/link count;
                # lstat() is needed for meaningful identity and hardlink checks.
                info = path.lstat()
                if _is_link(info):
                    _fail("LINK_OR_REPARSE_POINT")
                if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                    _fail("SPECIAL_MISSION_FILE")
                if folded.startswith("storage_"):
                    counts["skipped_storage_entries"] += 1
                    continue
                if stat.S_ISDIR(info.st_mode):
                    if folded in EXCLUDED_DIRECTORIES or folded.startswith("."):
                        counts["skipped_other_entries"] += 1
                    else:
                        pending.append((path, depth + 1))
                    continue
                if path.suffix.casefold() not in MISSION_SUFFIXES:
                    counts["skipped_other_entries"] += 1
                    continue
                # A mission hard link can alias a file outside the mission.
                if getattr(info, "st_nlink", 1) != 1:
                    _fail("HARD_LINKED_MISSION_FILE")
                if not 0 <= info.st_size <= MAX_FILE_BYTES:
                    _fail("MISSION_FILE_SIZE_LIMIT")
                counts["copied_files"] += 1
                counts["copied_bytes"] += info.st_size
                if counts["copied_files"] > MAX_COPY_FILES:
                    _fail("MISSION_FILE_COUNT_LIMIT")
                if counts["copied_bytes"] > MAX_COPY_BYTES:
                    _fail("MISSION_TOTAL_SIZE_LIMIT")
                files.append((path.relative_to(source), _signature(info)))
    paths = {relative.as_posix().casefold() for relative, _ in files}
    if not {"init.c", "db/types.xml"}.issubset(paths):
        _fail("REQUIRED_MISSION_FILES_MISSING")
    return sorted(files, key=lambda item: item[0].as_posix().casefold()), counts


def _open_no_follow(path: Path):
    """Open a final component without following it, including on Windows."""
    if os.name == "nt":
        import msvcrt
        kernel = ctypes.WinDLL("kernel32.dll", use_last_error=True, winmode=0x800)
        create = kernel.CreateFileW
        create.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                           ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
                           ctypes.c_void_p]
        create.restype = ctypes.c_void_p
        # OPEN_EXISTING, OPEN_REPARSE_POINT, SEQUENTIAL_SCAN. Share reads only:
        # an already-open writer is a reason to retry after the update ends.
        handle = create(str(path), 0x80000000, 1, None, 3, 0x00200000 | 0x08000000, None)
        if handle in (None, ctypes.c_void_p(-1).value):
            raise OSError(ctypes.get_last_error(), "SOURCE_OPEN_FAILED")
        try:
            descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        except BaseException:
            close = kernel.CloseHandle
            close.argtypes, close.restype = [ctypes.c_void_p], ctypes.c_int32
            close(handle)
            raise
    else:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    return os.fdopen(descriptor, "rb")


def _copy_file(source: Path, target: Path, expected: tuple) -> None:
    before = _check_existing(source, False)
    if _signature(before) != expected or getattr(before, "st_nlink", 1) != 1:
        _fail("MISSION_CHANGED_DURING_COPY")
    _mkdir_checked(target.parent)
    with _open_no_follow(source) as reader:
        opened = os.fstat(reader.fileno())
        if _is_link(opened) or not stat.S_ISREG(opened.st_mode) or _signature(opened) != expected:
            _fail("MISSION_CHANGED_DURING_COPY")
        # A new file is required; copying never follows or replaces a target.
        with target.open("xb") as writer:
            copied = 0
            while True:
                block = reader.read(min(64 * 1024, MAX_FILE_BYTES + 1 - copied))
                if not block:
                    break
                copied += len(block)
                if copied > MAX_FILE_BYTES or copied > expected[2]:
                    _fail("MISSION_CHANGED_DURING_COPY")
                writer.write(block)
        if copied != expected[2] or _signature(os.fstat(reader.fileno())) != expected:
            _fail("MISSION_CHANGED_DURING_COPY")


def _quote(value: str) -> str:
    if not isinstance(value, str) or any(ord(char) < 32 for char in value):
        _fail("UNSAFE_CONFIG_STRING")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
