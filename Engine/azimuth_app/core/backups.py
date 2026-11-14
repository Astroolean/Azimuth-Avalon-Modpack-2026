"""Create verified ZIP archives of the stopped server's persistent files."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
import time
from xml.etree import ElementTree
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile

from azimuth_app.core.server_workspace import _check_existing

NAME = re.compile(r"FreeDayZ_Backup_(\d{8}T\d{6}\.\d{6}Z)\.zip")
TIME_FORMAT = "%Y%m%dT%H%M%S.%fZ"
PROFILE_LOGS = {".log", ".rpt", ".adm", ".mdmp", ".dmp", ".dump"}
EXTRA_FILES = (
    "Config/settings.json", "Config/serverDZ.cfg",
    "Config/avalon-workshop.json",
    "Config/native-color-preview.enabled",
    "Mods/load-order.txt", "Mods/ServerOnly/load-order.txt",
)
MANAGER_SKIN_FILES = (
    "Config/GunSkins/settings.json", "Config/GunSkins/applied.json",
)
MAX_BACKUP_ENTRIES = 200_000
MAX_BACKUP_BYTES = 64 * 1024 * 1024 * 1024
MAX_MISSION_XML_BYTES = 16 * 1024 * 1024
_DEVICE_NAMES = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} | {
    prefix + digit for prefix in ("COM", "LPT") for digit in "123456789¹²³"
}


def validate_archive(path: Path, *, mission_matches=None) -> tuple[str, ...]:
    """Check portable paths, required mission/save content, and every member CRC.

    This verifies a restorable file layout, not the native game's save format.
    mission_matches, when supplied, binds the archive to the selected world.
    """
    if not stat.S_ISREG(source_type(path)):
        raise ValueError("Choose a regular Azimuth backup ZIP.")
    try:
        with ZipFile(path) as archive:
            members = archive.infolist()
            if len(members) > MAX_BACKUP_ENTRIES or sum(row.file_size for row in members) > MAX_BACKUP_BYTES:
                raise ValueError("The backup exceeds the supported archive size.")
            names, files = set(), {}
            allowed = set(EXTRA_FILES) | {"ManagerSettings/" + name for name in MANAGER_SKIN_FILES}
            for member in members:
                name = member.filename
                parts = PurePosixPath(name).parts
                if (not parts or name.startswith("/") or "/".join(parts) != name.rstrip("/")
                        or any(part in (".", "..") or part.endswith((".", " "))
                               or part.split(".", 1)[0].upper() in _DEVICE_NAMES
                               or any(ord(c) < 32 or c in '<>:"\\|?*' for c in part) for part in parts)
                        or name.rstrip("/").casefold() in names):
                    raise ValueError("The backup contains an invalid or repeated file path.")
                mode = stat.S_IFMT(member.external_attr >> 16)
                if mode not in (0, stat.S_IFREG, stat.S_IFDIR) or (mode == stat.S_IFDIR and not member.is_dir()):
                    raise ValueError("The backup contains a linked or special file.")
                names.add(name.rstrip("/").casefold())
                if member.is_dir():
                    continue
                if not (name.startswith(("World/Mission/", "World/Saves/", "Profiles/")) or name in allowed):
                    raise ValueError("The backup contains files outside the supported world data.")
                files[name] = member
            folded = {name.casefold(): row for name, row in files.items()}
            complete = []
            for name, member in files.items():
                parts = PurePosixPath(name).parts
                if len(parts) == 4 and parts[:2] == ("World", "Mission") and parts[3].casefold() == "init.c":
                    types = folded.get(("/".join(parts[:3]) + "/db/types.xml").casefold())
                    if types is None or member.file_size == 0 or not 0 < types.file_size <= MAX_MISSION_XML_BYTES:
                        continue
                    if mission_matches is not None and not mission_matches(parts[2]):
                        raise ValueError("The backup mission belongs to a different world.")
                    raw = archive.read(types)
                    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
                        raise ValueError("The backup contains unsupported mission XML.")
                    if ElementTree.fromstring(raw).tag != "types":
                        raise ValueError("The backup mission types XML is invalid.")
                    complete.append(parts[2])
            if len(complete) != 1 or not any(name.startswith("World/Saves/") and row.file_size > 0 for name, row in files.items()):
                raise ValueError("The backup needs one complete mission and nonempty world save data.")
            # A file cannot also be the parent directory of another entry.
            for name in files:
                if any(parent.as_posix().casefold() in folded for parent in PurePosixPath(name).parents if parent.parts):
                    raise ValueError("The backup contains conflicting file and directory paths.")
            if archive.testzip() is not None:
                raise ValueError("The backup failed its ZIP integrity check.")
            return tuple(files)
    except (BadZipFile, RuntimeError, NotImplementedError, ElementTree.ParseError) as error:
        raise ValueError("The backup could not be read as a complete, valid ZIP.") from error


RESTORE_FILES = frozenset(("World", "Profiles", "Config/avalon-workshop.json",
                           "Config/native-color-preview.enabled", "Config/launch-session.json",
                           "Mods/load-order.txt", "Mods/ServerOnly/load-order.txt"))


def recover_restore(root: Path, manager_root: Path) -> bool:
    """Roll back an interrupted component swap before any server can launch.

    Both displaced originals and a partial restored attempt are retained.
    Only the fixed set of restoration targets can be named by this journal.
    """
    journal = root / "Config/restore-transaction.json"
    if not journal.exists():
        return False
    try:
        _check_existing(journal, False)
        if not stat.S_ISREG(source_type(journal)) or journal.stat().st_size > 64 * 1024:
            raise ValueError("Invalid restore journal")
        data = json.loads(journal.read_text(encoding="utf-8"))
        folder = data.get("originals")
        if (data.get("version") != 1 or data.get("phase") not in {"publishing", "committed"}
                or not isinstance(folder, str) or not re.fullmatch(r"BeforeRestore-[0-9]+", folder)
                or not isinstance(data.get("operations"), list)):
            raise ValueError("Invalid restore journal")
        operations, seen = [], set()
        originals = root / "Backups" / folder
        _check_existing(originals, True)
        if not stat.S_ISDIR(source_type(originals)):
            raise ValueError("Missing restore originals")
        for operation in data["operations"]:
            relative = operation.get("path")
            existed = operation.get("existed")
            if not isinstance(relative, str) or relative in seen or type(existed) is not bool:
                raise ValueError("Invalid restore operation")
            seen.add(relative)
            if relative in RESTORE_FILES:
                target = root / relative
            elif relative in {"ManagerSettings/" + name for name in MANAGER_SKIN_FILES}:
                target = manager_root / relative.removeprefix("ManagerSettings/")
            else:
                raise ValueError("Invalid restore target")
            operations.append((relative, target, existed))
        if data["phase"] == "committed":
            journal.unlink()
            return False
        for relative, target, existed in reversed(operations):
            original = originals / relative
            if existed and not original.exists():
                # Either publication never reached this target, or a previous
                # recovery already returned its original to the live path.
                if not target.exists():
                    raise OSError(f"Restore original is missing for {relative}.")
                continue
            if target.exists() or target.is_symlink():
                _check_existing(target, target.is_dir())
                failed = originals / "InterruptedAttempt" / relative
                failed.parent.mkdir(parents=True, exist_ok=True)
                if failed.exists():
                    raise OSError(f"An interrupted restore copy already exists for {relative}.")
                os.replace(target, failed)
            if existed:
                _check_existing(original, original.is_dir())
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(original, target)
        journal.unlink()
        return True
    except (OSError, ValueError, TypeError, AttributeError) as error:
        raise RuntimeError(f"An interrupted restore needs recovery before launch. Preserved originals are in Backups. {error}") from error


def backup_time(path: Path) -> float:
    match = NAME.fullmatch(path.name)
    if match is None:
        raise ValueError(f"Unexpected backup filename: {path.name}")
    return datetime.strptime(match[1], TIME_FORMAT).replace(tzinfo=timezone.utc).timestamp()


def latest_backup(root: Path) -> Path | None:
    directory = root / "Backups"
    if not directory.is_dir():
        return None
    for candidate in sorted(directory.glob("FreeDayZ_Backup_*.zip"), reverse=True):
        if candidate.is_file() and not candidate.is_symlink():
            try:
                if backup_time(candidate) > time.time() + 300:
                    continue
                validate_archive(candidate)
            except (OSError, ValueError, OverflowError):
                continue
            return candidate
    return None


def source_type(path: Path) -> int:
    info = path.lstat()
    if path.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
        raise OSError(f"Backup cannot follow a linked file or directory: {path.name}")
    return info.st_mode


def add_tree(archive: ZipFile, root: Path, folder: Path, skip_logs: bool = False) -> None:
    if not folder.exists():
        return
    if not stat.S_ISDIR(source_type(folder)):
        raise OSError(f"Expected a directory: {folder.relative_to(root)}")

    def onerror(error: OSError) -> None:
        raise error

    for directory, children, files in os.walk(folder, onerror=onerror):
        parent = Path(directory)
        for child in children:
            source_type(parent / child)
        for name in files:
            path = parent / name
            if skip_logs and path.suffix.casefold() in PROFILE_LOGS:
                continue
            if not stat.S_ISREG(source_type(path)):
                raise OSError(f"Expected an ordinary backup file: {path.relative_to(root)}")
            archive.write(path, path.relative_to(root).as_posix())


def add_file(archive: ZipFile, root: Path, relative: str, *, prefix: str = "") -> None:
    """Include an optional settings file without following directory links."""
    path = root / relative
    if not path.exists() and not path.is_symlink():
        return
    if not stat.S_ISDIR(source_type(root)):
        raise OSError("Expected an ordinary backup source directory.")
    for parent in path.parents:
        if parent == root:
            break
        if not stat.S_ISDIR(source_type(parent)):
            raise OSError(f"Expected a backup directory: {parent.relative_to(root)}")
    if not stat.S_ISREG(source_type(path)):
        raise OSError(f"Expected an ordinary backup file: {relative}")
    archive.write(path, prefix + relative)


def write_backup(root: Path, now: float, *, manager_root: Path | None = None) -> Path:
    """Publish a checked archive atomically. The caller holds the server lock."""
    world = root / "World"
    if not (world / "Mission").is_dir() or not (world / "Saves").is_dir():
        raise OSError("A server world is required before creating a backup.")
    source_type(world)
    target_dir = root / "Backups"
    target_dir.mkdir(exist_ok=True)
    source_type(target_dir)
    stamp = datetime.fromtimestamp(now, timezone.utc).strftime(TIME_FORMAT)
    target = target_dir / f"FreeDayZ_Backup_{stamp}.zip"
    if target.exists():
        raise FileExistsError(f"A backup already exists for {stamp}.")
    with tempfile.NamedTemporaryFile(prefix=".backup-", suffix=".part",
                                     dir=target_dir, delete=False) as stream:
        temporary = Path(stream.name)
    try:
        with ZipFile(temporary, "w", compression=ZIP_DEFLATED, compresslevel=3,
                     strict_timestamps=False) as archive:
            add_tree(archive, root, world / "Mission")
            add_tree(archive, root, world / "Saves")
            add_tree(archive, root, root / "Profiles", skip_logs=True)
            for relative in EXTRA_FILES:
                add_file(archive, root, relative)
            if manager_root is not None:
                for relative in MANAGER_SKIN_FILES:
                    add_file(archive, manager_root, relative, prefix="ManagerSettings/")
            if not any(name.startswith("World/Saves/") for name in archive.namelist()):
                raise OSError("DayZ has not written any world save files yet.")
        validate_archive(temporary)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target
