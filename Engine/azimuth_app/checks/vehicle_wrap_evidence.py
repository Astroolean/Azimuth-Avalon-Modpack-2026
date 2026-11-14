"""Export installed vehicle config evidence without changing or launching DayZ.

The archive contains PBO metadata/configs and bounded publisher setup files.
It does not certify texture mapping or copy models, textures, saves or logs.
Compressed config members remain compressed and are labelled accordingly.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import struct
from tempfile import NamedTemporaryFile
from uuid import uuid4
from zipfile import ZipFile, ZIP_DEFLATED

from azimuth_app.client.client_launch import find_client


WORKSHOP_IDS = (
    "3225634444", "2291785437", "2116157322", "2971190303", "3834034916",
    "3827497320", "2963230374", "3830162428", "2880254695", "3411055412",
    "3354681846", "2651195301", "3387855369", "3456445155", "3011551250",
    "3125203834", "3639695989", "2971993210", "3706032797", "3810689113",
)
MAX_PBOS = 2048
MAX_DIRECTORY_CHILDREN = 8192
MAX_WALK_DEPTH = 4
MAX_WALK_DIRECTORIES = 512
MAX_STEAM_LIBRARIES = 32
MAX_ENTRIES = 20000
MAX_TOTAL_ENTRIES = 50000
MAX_STRING_BYTES = 4096
MAX_HEADER_BYTES = 4 * 1024 * 1024
MAX_TOTAL_HEADER_BYTES = 64 * 1024 * 1024
MAX_CONFIG_BYTES = 2 * 1024 * 1024
MAX_TOTAL_CONFIG_BYTES = 32 * 1024 * 1024
MAX_PUBLISHER_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_PUBLISHER_BYTES = 8 * 1024 * 1024
MAX_PUBLISHER_FILES = 128
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
ENTRY = struct.Struct("<5I")
VERS = 0x56657273
CPRS = 0x43707273
ASSET_EXTENSIONS = {".p3d", ".paa", ".pac", ".rvmat", ".bin", ".cpp", ".c"}
VEHICLE_WORDS = (
    "vehicle", "motorbike", "motorcycle", "boat", "offroad", "hatchback", "sedan", "truck",
    "rtrzr", "rzr", "bicycle", "old_bike", "ladybicycle", "an2", "an-2", "biplane",
    "merlin", "gyro", "vodnik", "tractor", "landrover", "uaz", "rhib", "zodiac", "mh6", "uh1h",
)
PUBLISHER_FOLDERS = {"extras", "serverfiles", "server files", "info"}
PRIVATE_FOLDERS = {"profile", "profiles", "backup", "backups", "log", "logs", "keys", "storage", "save", "saves", "userdata", "private", "credentials", "secrets"}
PUBLISHER_XML = re.compile(r"(?:(?:[a-z0-9_. -]+)[_. -])?(?:types|events|cfgspawnabletypes)(?:[_. -][a-z0-9_. -]+)?\.xml\Z")
PUBLISHER_README = re.compile(r"readme(?:[ _-][a-z0-9 _-]+)?(?:\.txt|\.md)?\Z")


def _ordinary(path: Path, directory: bool = False) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    return kind(info.st_mode) and not getattr(info, "st_file_attributes", 0) & 0x400


def _relative_name(value: str) -> str:
    value = value.replace("\\", "/")
    if not value or any(ord(c) < 32 for c in value) or ":" in value:
        raise ValueError("Invalid resource name")
    parts = value.split("/")
    if any(p in ("", ".", "..") for p in parts) or PurePosixPath(value).is_absolute():
        raise ValueError("Resource name leaves its archive")
    return value


def _children(path: Path) -> list[Path]:
    if not _ordinary(path, True):
        return []
    found = []
    with os.scandir(path) as entries:
        for entry in entries:
            if len(found) >= MAX_DIRECTORY_CHILDREN:
                raise ValueError("Directory exceeds the scan limit")
            found.append(Path(entry.path))
    return sorted(found, key=lambda p: p.name.casefold())


def _case_child(path: Path, name: str) -> Path | None:
    matches = [p for p in _children(path) if p.name.casefold() == name.casefold()]
    return matches[0] if len(matches) == 1 else None


def _resolve_root(path: Path) -> Path | None:
    # Explicit Steam roots may themselves be junctions. Resolve that root once;
    # never follow arbitrary child reparse points during the bounded walk.
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    return resolved if _ordinary(resolved, True) else None


def _discover(libraries: list[Path], installation: Path | None) -> tuple[list[tuple[str, Path, bool]], list[tuple[str, Path]], list[str]]:
    if len(libraries) > MAX_STEAM_LIBRARIES:
        raise ValueError("Steam library count exceeds the scan limit")
    roots: list[tuple[str, Path, bool]] = []
    publisher_roots: list[tuple[str, Path]] = []
    notes: list[str] = []
    seen: set[str] = set()
    publisher_seen: set[str] = set()

    def add(label: str, folder: Path | None, native: bool) -> bool:
        resolved = _resolve_root(folder) if folder else None
        addons = _case_child(resolved, "addons") if resolved else None
        addons = _resolve_root(addons) if addons else None
        if addons is None:
            return False
        key = os.path.normcase(str(addons))
        if key not in seen:
            seen.add(key)
            roots.append((label, addons, native))
        return True

    client = find_client(libraries)
    if not add("dayz_client", client, True):
        notes.append("DayZ client Addons folder was not located.")
    if installation is not None and not add("dayz_server", installation, True):
        notes.append("DayZ server Addons folder was not located.")
    for item_id in WORKSHOP_IDS:
        located = False
        for index, library in enumerate(libraries):
            folder = Path(library) / "steamapps/workshop/content/221100" / item_id
            try:
                label = f"workshop_{item_id}_library_{index + 1}"
                publisher = _resolve_root(folder)
                if publisher is not None:
                    publisher_key = os.path.normcase(str(publisher))
                    if publisher_key not in publisher_seen:
                        publisher_seen.add(publisher_key)
                        publisher_roots.append((label, publisher))
                located = add(label, folder, False) or located
            except (OSError, ValueError):
                notes.append(f"Workshop {item_id} Addons folder could not be inspected.")
        if not located:
            notes.append(f"Workshop {item_id} Addons folder was not located.")
    return roots, publisher_roots, notes


@dataclass(frozen=True)
class Member:
    name: str
    method: int
    original_size: int
    stored_size: int
    offset: int


class HeaderReader:
    def __init__(self, stream, limit: int | None = None):
        self.stream = stream
        self.count = 0
        self.digest = sha256()
        self.limit = MAX_HEADER_BYTES if limit is None else min(MAX_HEADER_BYTES, limit)

    def take(self, count: int) -> bytes:
        if self.count + count > self.limit:
            raise ValueError("PBO header exceeds the scan limit")
        data = self.stream.read(count)
        self.count += len(data)
        self.digest.update(data)
        if len(data) != count:
            raise ValueError("Truncated PBO header")
        return data

    def string(self) -> str:
        value = bytearray()
        for _ in range(MAX_STRING_BYTES):
            char = self.take(1)
            if char == b"\0":
                try:
                    return value.decode("utf-8")
                except UnicodeDecodeError:
                    return value.decode("cp1252")
            value.extend(char)
        raise ValueError("PBO resource name exceeds the scan limit")


def _directory(stream, size: int, header_limit: int | None = None) -> tuple[list[Member], str, int, str]:
    reader = HeaderReader(stream, header_limit)
    prefix = ""
    rows = []
    seen = set()
    properties_seen = False
    while True:
        name = reader.string()
        method, original, reserved, timestamp, stored = ENTRY.unpack(reader.take(ENTRY.size))
        if name == "":
            if method == VERS and not rows and not properties_seen:
                properties_seen = True
                for _ in range(256):
                    key = reader.string()
                    if not key:
                        break
                    value = reader.string()
                    if key.casefold() == "prefix" and value:
                        prefix = _relative_name(value.strip("\\/"))
                else:
                    raise ValueError("Too many PBO properties")
                continue
            if any((method, original, reserved, timestamp, stored)):
                raise ValueError("Invalid PBO terminator")
            break
        if len(rows) >= MAX_ENTRIES:
            raise ValueError("PBO member count exceeds the scan limit")
        name = _relative_name(name)
        key = name.casefold()
        if key in seen:
            raise ValueError("Duplicate PBO resource name")
        seen.add(key)
        rows.append((name, method, original, stored))
    offset = reader.count
    members = []
    for name, method, original, stored in rows:
        if stored > size - offset:
            raise ValueError("PBO member exceeds archive bounds")
        members.append(Member(name, method, original, stored, offset))
        offset += stored
    return members, prefix, reader.count, reader.digest.hexdigest()


def _vehicle_word(value: str) -> bool:
    folded = value.casefold()
    return any(word in folded for word in VEHICLE_WORDS)


def _is_config(member: Member) -> bool:
    return PurePosixPath(member.name).name.casefold() in {"config.bin", "config.cpp"}


def _scan_pbo(path: Path, relative: str, native: bool, config_budget: int, header_budget: int | None = None) -> tuple[dict, list[tuple[str, bytes]], int]:
    if not _ordinary(path):
        raise ValueError("PBO is not an ordinary file")
    before = path.stat()
    with path.open("rb") as stream:
        members, prefix, header_size, header_hash = _directory(stream, before.st_size, header_budget)
        # The Workshop roots are explicit vehicle candidates. A publisher
        # can place vehicle classes in a generically named config-only addon.
        relevant = not native or _vehicle_word(relative) or _vehicle_word(prefix) or any(_vehicle_word(m.name) for m in members)
        # Scripts can establish inheritance even when their addon is named simply
        # scripts.pbo. Only its config is copied; script paths remain metadata.
        if native and "script" in path.stem.casefold():
            relevant = True
        record = {"archive": relative, "pbo_bytes": before.st_size, "header_sha256": header_hash,
                  "virtual_prefix": prefix, "status": "indexed" if relevant else "no_vehicle_resources",
                  "members": [], "configs": []}
        payloads = []
        used = 0
        if relevant:
            for member in members:
                if PurePosixPath(member.name).suffix.casefold() in ASSET_EXTENSIONS:
                    record["members"].append({"path": member.name, "stored_bytes": member.stored_size,
                                              "original_bytes": member.original_size,
                                              "method_hex": f"{member.method:08x}"})
                if not _is_config(member):
                    continue
                config = {"path": member.name, "stored_bytes": member.stored_size,
                          "original_bytes": member.original_size, "method_hex": f"{member.method:08x}"}
                record["configs"].append(config)
                if max(member.original_size, member.stored_size) > MAX_CONFIG_BYTES:
                    config["status"] = "over_entry_limit"
                    continue
                if member.stored_size > config_budget - used:
                    config["status"] = "over_total_limit"
                    continue
                stream.seek(member.offset)
                data = stream.read(member.stored_size)
                if len(data) != member.stored_size:
                    raise ValueError("PBO changed or truncated during config read")
                storage_name = member.name if member.method == 0 else member.name + ".stored"
                config.update(status="copied", storage_name=storage_name, sha256=sha256(data).hexdigest(),
                              encoding="raw" if member.method == 0 else "stored_compressed_or_unknown",
                              compression="none" if member.method == 0 else "Cprs" if member.method == CPRS else "unknown")
                payloads.append((storage_name, data))
                used += len(data)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("PBO changed during inspection")
    return record, payloads, header_size


def _walk_pbos(root: Path, native: bool):
    pending = [(root, 0)]
    visited = 0
    while pending:
        if visited >= MAX_WALK_DIRECTORIES:
            raise ValueError("Directory count exceeds the scan limit")
        visited += 1
        folder, depth = pending.pop()
        candidates = sorted(_children(folder), key=lambda p: (not (_vehicle_word(p.name) or "script" in p.stem.casefold()), p.name.casefold()))
        for path in candidates:
            if _ordinary(path, True) and depth < MAX_WALK_DEPTH:
                pending.append((path, depth + 1))
            elif _ordinary(path) and path.suffix.casefold() == ".pbo":
                if not native or _vehicle_word(path.name) or "script" in path.stem.casefold():
                    yield path


def _publisher_name(name: str) -> bool:
    folded = name.casefold()
    return bool(PUBLISHER_XML.fullmatch(folded) or PUBLISHER_README.fullmatch(folded))


def _private_folder(name: str) -> bool:
    folded = name.casefold()
    return folded in PRIVATE_FOLDERS or folded.startswith("storage_") or folded.startswith(".")


def _publisher_files(root: Path):
    """Inspect top-level publisher files and only the documented setup folders."""
    children = _children(root)
    for path in children:
        if _ordinary(path) and _publisher_name(path.name):
            yield path
    pending = [(p, 0) for p in children if p.name.casefold() in PUBLISHER_FOLDERS and _ordinary(p, True)]
    visited = 0
    while pending:
        if visited >= MAX_WALK_DIRECTORIES:
            raise ValueError("Publisher directory count exceeds the scan limit")
        visited += 1
        folder, depth = pending.pop()
        for path in _children(folder):
            if _ordinary(path, True) and depth < MAX_WALK_DEPTH and not _private_folder(path.name):
                pending.append((path, depth + 1))
            elif _ordinary(path) and _publisher_name(path.name):
                yield path


def _export_publisher(archive: ZipFile, roots: list[tuple[str, Path]], notes: list[str]) -> tuple[list[dict], int]:
    records: list[dict] = []
    total = 0
    exported = set()
    for label, root in roots:
        try:
            for path in _publisher_files(root):
                if len(records) >= MAX_PUBLISHER_FILES:
                    notes.append("Publisher setup export reached its bounded file limit; remaining files were not inspected.")
                    return records, total
                relative = _relative_name(path.relative_to(root).as_posix())
                record = {"source": label, "path": relative}
                records.append(record)
                destination = f"publisher/{label}/{relative}"
                if destination.casefold() in exported:
                    record["status"] = "duplicate_export_name"
                    continue
                try:
                    before = path.stat()
                    if before.st_size > MAX_PUBLISHER_FILE_BYTES:
                        record["status"] = "over_entry_limit"
                        continue
                    if before.st_size > MAX_TOTAL_PUBLISHER_BYTES - total:
                        record["status"] = "over_total_limit"
                        continue
                    with path.open("rb") as stream:
                        data = stream.read(MAX_PUBLISHER_FILE_BYTES + 1)
                    after = path.stat()
                    if len(data) != before.st_size or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                        raise ValueError("Publisher file changed during inspection")
                    archive.writestr(destination, data)
                    exported.add(destination.casefold())
                    total += len(data)
                    record.update(status="copied", export_path=destination, bytes=len(data), sha256=sha256(data).hexdigest())
                except (OSError, ValueError) as error:
                    record.update(status="could_not_inspect", error=type(error).__name__)
        except (OSError, ValueError):
            notes.append(f"Some publisher setup files in {label} could not be enumerated.")
    return records, total


def _output_directory(root: Path) -> Path:
    folder = root.resolve(strict=True)
    for name in ("Docs", "Audit", "Exports"):
        folder = folder / name
        if not folder.exists():
            folder.mkdir()
        if not _ordinary(folder, True):
            raise ValueError("Evidence output must use ordinary folders")
    return folder


def export_vehicle_wrap_evidence(root: Path, libraries: list[Path], installation: Path | None = None) -> Path:
    """Return a new local evidence ZIP; never start, stop or change the server."""
    roots, publisher_roots, notes = _discover([Path(p) for p in libraries], installation)
    folder = _output_directory(Path(root))
    created = datetime.now(timezone.utc)
    target = folder / f"Azimuth_Vehicle_Wrap_Evidence_{created:%Y%m%d_%H%M%S}_UTC_{uuid4().hex[:12]}.zip"
    manifest = {"schema_version": 1, "created_utc": created.isoformat(),
                "purpose": "Installed config and asset-name evidence for vehicle wrap compatibility research",
                "actions": "Read-only inspection of already installed files; no downloads, subscriptions, enabled mods or server changes",
                "validation": "NOT a native render, compilation, model-UV or gameplay verification",
                "integrity": "Header digests and copied-config digests only; whole-PBO checksum validation was not performed",
                "contents": "PBO directory metadata, stored config.bin/config.cpp, and bounded publisher economy XML/README files; no models, textures, saves, logs or keys",
                "compression_note": "A .stored config retains its exact compressed/unknown PBO payload. Do not treat it as a decoded config.",
                "sources": [label for label, _, _ in roots], "publisher_sources": [label for label, _ in publisher_roots],
                "candidate_workshop_ids": list(WORKSHOP_IDS),
                "notes": notes, "archives": []}
    temporary = None
    config_bytes = headers = total_entries = scanned = 0
    try:
        with NamedTemporaryFile(prefix=".vehicle-evidence-", suffix=".tmp", dir=folder, delete=False) as handle:
            temporary = Path(handle.name)
        with ZipFile(temporary, "w", ZIP_DEFLATED, compresslevel=6) as archive:
            stop = False
            for label, source, native in roots:
                if stop:
                    break
                try:
                    for path in _walk_pbos(source, native):
                        if scanned >= MAX_PBOS or headers >= MAX_TOTAL_HEADER_BYTES or total_entries >= MAX_TOTAL_ENTRIES:
                            manifest["notes"].append("Evidence scan reached its bounded archive/header/member limit; remaining files were not inspected.")
                            stop = True
                            break
                        scanned += 1
                        relative = _relative_name(path.relative_to(source).as_posix())
                        try:
                            record, payloads, header_bytes = _scan_pbo(path, relative, native, MAX_TOTAL_CONFIG_BYTES - config_bytes,
                                                                     MAX_TOTAL_HEADER_BYTES - headers)
                            headers += header_bytes
                            if len(record["members"]) > MAX_TOTAL_ENTRIES - total_entries:
                                record["members"] = []
                                record["configs"] = []
                                record["status"] = "over_total_member_limit"
                                payloads = []
                                stop = True
                            total_entries += len(record["members"])
                            record["source"] = label
                            archive_id = f"{label}/{scanned:04d}"
                            for name, data in payloads:
                                archive.writestr(f"configs/{archive_id}/{name}", data)
                                config_bytes += len(data)
                            for config in record["configs"]:
                                if config.get("status") == "copied":
                                    config["export_path"] = f"configs/{archive_id}/{config['storage_name']}"
                        except (OSError, ValueError, UnicodeError, struct.error) as error:
                            # Do not expose machine paths or user names from an
                            # exception string. The archive's relative name is enough.
                            record = {"source": label, "archive": relative, "status": "could_not_inspect", "error": type(error).__name__}
                            headers += min(MAX_HEADER_BYTES, MAX_TOTAL_HEADER_BYTES - headers)
                        manifest["archives"].append(record)
                        if stop:
                            break
                except (OSError, ValueError):
                    manifest["notes"].append(f"Some files in {label} could not be enumerated.")
            manifest["publisher_files"], publisher_bytes = _export_publisher(archive, publisher_roots, notes)
            manifest["counts"] = {"pbo_files_checked": scanned, "indexed_asset_entries": total_entries,
                                  "copied_config_bytes": config_bytes, "copied_publisher_bytes": publisher_bytes}
            content = (json.dumps(manifest, indent=2, ensure_ascii=True) + "\n").encode("utf-8")
            if len(content) > MAX_MANIFEST_BYTES:
                raise ValueError("Evidence manifest exceeds its size limit")
            archive.writestr("Vehicle_Wrap_Evidence.json", content)
        if target.exists():
            raise FileExistsError("Evidence output already exists")
        os.replace(temporary, target)
        temporary = None
        return target
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
