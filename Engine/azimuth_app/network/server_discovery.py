"""Read-only discovery of the owner's Steam DayZ Server installation.

Known Steam registry roots, library catalogs and app 223350 are inspected.
There is no drive crawl, network lookup, download, game launch or settings
change. Returned paths are for local launch planning; the caller must redact
them before exporting a report. File existence is not publisher verification.
"""
from __future__ import annotations

import ctypes
import ntpath
import os
from pathlib import Path
import re
import stat
import struct
import time
from typing import Callable, Iterable

APP_ID = "223350"
MAX_ROOTS = 12
MAX_LIBRARIES = 32
MAX_CATALOG_BYTES = 1024 * 1024
MAX_MANIFEST_BYTES = 256 * 1024
MAX_TOKENS = 60_000
MAX_TOKEN_CHARS = 8192
MAX_DEPTH = 16
DISCOVERY_SECONDS = 12.0
REQUIRED_MISSION_FILES = ("init.c", "db/types.xml")
_DEVICE_NAMES = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} | {
    prefix + digit for prefix in ("COM", "LPT") for digit in "123456789\u00b9\u00b2\u00b3"
}


class DiscoveryDataError(ValueError):
    pass


def parse_keyvalues(text: str, *, max_bytes: int = MAX_CATALOG_BYTES) -> dict:
    """Bounded Steam text KeyValues subset; directives and duplicate keys fail.

    Quotes, braces, line comments and literal/backslash-escaped paths are
    supported. Only escaped quote and backslash are decoded. Unknown escapes
    stay literal, so a Windows folder starting with n or t is not corrupted.
    No #include or #base directive is followed.
    """
    if not isinstance(text, str) or len(text.encode("utf-8")) > max_bytes:
        raise DiscoveryDataError("SIZE_LIMIT")
    tokens: list[tuple[str, str]] = []
    pos, length = 0, len(text)
    while pos < length:
        char = text[pos]
        if char.isspace() or (pos == 0 and char == "\ufeff"):
            pos += 1
            continue
        if text.startswith("//", pos):
            end = text.find("\n", pos)
            pos = length if end < 0 else end + 1
            continue
        if len(tokens) >= MAX_TOKENS:
            raise DiscoveryDataError("TOKEN_LIMIT")
        if char in "{}":
            tokens.append((char, char))
            pos += 1
            continue
        if char == '"':
            pos += 1
            value = []
            while pos < length and text[pos] != '"':
                if len(value) >= MAX_TOKEN_CHARS:
                    raise DiscoveryDataError("TOKEN_LENGTH_LIMIT")
                if text[pos] == "\\" and pos + 1 < length and text[pos + 1] in ('"', "\\"):
                    pos += 1
                value.append(text[pos])
                pos += 1
            if pos >= length:
                raise DiscoveryDataError("UNTERMINATED_QUOTE")
            pos += 1
            tokens.append(("text", "".join(value)))
            continue
        start = pos
        while pos < length and not text[pos].isspace() and text[pos] not in '{}"':
            if text.startswith("//", pos):
                break
            pos += 1
            if pos - start > MAX_TOKEN_CHARS:
                raise DiscoveryDataError("TOKEN_LENGTH_LIMIT")
        if pos == start:
            raise DiscoveryDataError("INVALID_TOKEN")
        tokens.append(("text", text[start:pos]))

    cursor = 0

    def pairs(depth: int, nested: bool) -> dict:
        nonlocal cursor
        if depth > MAX_DEPTH:
            raise DiscoveryDataError("DEPTH_LIMIT")
        output = {}
        while cursor < len(tokens):
            kind, key = tokens[cursor]
            if kind == "}":
                if not nested:
                    raise DiscoveryDataError("UNEXPECTED_CLOSE")
                cursor += 1
                return output
            if kind != "text" or not key or len(key) > 256 or key.startswith("#"):
                raise DiscoveryDataError("UNSUPPORTED_KEY_OR_DIRECTIVE")
            folded = key.casefold()
            if folded in output:
                raise DiscoveryDataError("DUPLICATE_KEY")
            cursor += 1
            if cursor >= len(tokens):
                raise DiscoveryDataError("MISSING_VALUE")
            kind, value = tokens[cursor]
            cursor += 1
            if kind == "{":
                output[folded] = pairs(depth + 1, True)
            elif kind == "text":
                output[folded] = value
            else:
                raise DiscoveryDataError("MISSING_VALUE")
        if nested:
            raise DiscoveryDataError("UNCLOSED_OBJECT")
        return output

    return pairs(0, False)


def _drive_type(root: str) -> int | None:
    try:
        fn = ctypes.WinDLL("kernel32.dll", use_last_error=True, winmode=0x800).GetDriveTypeW
        fn.argtypes, fn.restype = [ctypes.c_wchar_p], ctypes.c_uint32
        return int(fn(root))
    except (AttributeError, OSError):
        return None


def _local_root(value: str | Path) -> Path | None:
    text = str(value)
    if not text or len(text) > 2048 or any(ord(c) < 32 for c in text):
        return None
    if os.name == "nt":
        text = text.replace("/", "\\")
        drive, tail = ntpath.splitdrive(text)
        if not re.fullmatch(r"[A-Za-z]:", drive) or not tail.startswith("\\"):
            return None
        if any(c in tail for c in '<>:"|?*'):
            return None
        parts = [part for part in tail.split("\\") if part]
        if any(p in (".", "..") or p.endswith((".", " ")) or p.split(".", 1)[0].upper() in _DEVICE_NAMES for p in parts):
            return None
        if _drive_type(drive + "\\") not in (2, 3):
            return None
        return Path(ntpath.normpath(drive.upper() + tail))
    # Non-Windows paths are permitted only for injected development fixtures;
    # the public discovery status still states that native Windows did not run.
    path = Path(text)
    if not path.is_absolute() or ".." in path.parts or text.startswith("//"):
        return None
    return path


def _state(path: Path, directory: bool) -> str:
    """Inspect known path components without following symlinks/junctions."""
    if len(path.parts) > 64:
        return "PATH_LIMIT"
    parts = list(reversed(path.parents)) + [path]
    for index, current in enumerate(parts):
        try:
            info = current.lstat()
        except FileNotFoundError:
            return "MISSING"
        except OSError:
            return "UNREADABLE"
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            return "LINK_OR_REPARSE_POINT"
        last = index == len(parts) - 1
        if not last and not stat.S_ISDIR(info.st_mode):
            return "WRONG_TYPE"
        if last and not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
            return "WRONG_TYPE"
    return "PRESENT"


def _read_vdf(path: Path, limit: int) -> tuple[str, dict | None]:
    state = _state(path, False)
    if state != "PRESENT":
        return state, None
    try:
        with path.open("rb") as handle:
            raw = handle.read(limit + 1)
        if len(raw) > limit:
            return "SIZE_LIMIT", None
        return "PRESENT", parse_keyvalues(raw.decode("utf-8-sig"), max_bytes=limit)
    except (UnicodeError, DiscoveryDataError):
        return "INVALID_KEYVALUES", None
    except OSError:
        return "UNREADABLE", None


def _registry_steam_roots() -> list[str]:
    if os.name != "nt":
        return []
    import winreg
    output = []
    requests = [
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath", 0),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Valve\Steam", "InstallPath", winreg.KEY_WOW64_64KEY),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Valve\Steam", "InstallPath", winreg.KEY_WOW64_32KEY),
    ]
    for hive, name, value_name, view in requests:
        try:
            with winreg.OpenKey(hive, name, 0, winreg.KEY_READ | view) as handle:
                value, kind = winreg.QueryValueEx(handle, value_name)
            if isinstance(value, str) and kind in (winreg.REG_SZ, winreg.REG_EXPAND_SZ):
                output.append(os.path.expandvars(value) if kind == winreg.REG_EXPAND_SZ else value)
        except OSError:
            continue
    return output


def _numeric(value: object) -> str | None:
    return value if isinstance(value, str) and re.fullmatch(r"[0-9]{1,24}", value) else None


def _installation_under_library(library: Path, value: object) -> Path | None:
    if not isinstance(value, str) or not value or len(value) > 1024:
        return None
    if any(ord(c) < 32 for c in value) or any(c in value for c in '<>:"|?*'):
        return None
    parts = value.replace("\\", "/").split("/")
    if any(not p or p in (".", "..") or p.endswith((".", " ")) or p.split(".", 1)[0].upper() in _DEVICE_NAMES for p in parts):
        return None
    return library / "steamapps" / "common" / Path(*parts)


class _FixedVersion(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint32) for name in
                ("signature", "struct_version", "file_ms", "file_ls", "product_ms", "product_ls",
                 "flags_mask", "flags", "os", "file_type", "subtype", "date_ms", "date_ls")]


def read_file_version(path: Path) -> dict:
    """Read fixed version resources; do not treat this as an in-game build check."""
    if os.name != "nt":
        return {"status": "NOT_RUN", "code": "WINDOWS_REQUIRED"}
    try:
        dll = ctypes.WinDLL("version.dll", use_last_error=True, winmode=0x800)
        size_fn = dll.GetFileVersionInfoSizeW
        size_fn.argtypes, size_fn.restype = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_uint32)], ctypes.c_uint32
        get_fn = dll.GetFileVersionInfoW
        get_fn.argtypes, get_fn.restype = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p], ctypes.c_int32
        query_fn = dll.VerQueryValueW
        query_fn.argtypes, query_fn.restype = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_uint32)], ctypes.c_int32
        ignored = ctypes.c_uint32()
        size = size_fn(str(path), ctypes.byref(ignored))
        if not 1 <= size <= 4 * 1024 * 1024:
            return {"status": "UNAVAILABLE", "code": ctypes.get_last_error() if not size else "RESOURCE_LIMIT"}
        buffer = ctypes.create_string_buffer(size)
        if not get_fn(str(path), 0, size, buffer):
            return {"status": "UNAVAILABLE", "code": ctypes.get_last_error()}
        pointer, length = ctypes.c_void_p(), ctypes.c_uint32()
        if not query_fn(buffer, "\\", ctypes.byref(pointer), ctypes.byref(length)):
            return {"status": "UNAVAILABLE", "code": ctypes.get_last_error()}
        base = ctypes.addressof(buffer)
        if length.value < ctypes.sizeof(_FixedVersion) or not pointer.value or not base <= pointer.value <= base + size - ctypes.sizeof(_FixedVersion):
            return {"status": "UNAVAILABLE", "code": "INVALID_VERSION_RESOURCE"}
        fixed = _FixedVersion.from_buffer_copy(ctypes.string_at(pointer, ctypes.sizeof(_FixedVersion)))
        if fixed.signature != 0xFEEF04BD:
            return {"status": "UNAVAILABLE", "code": "INVALID_VERSION_SIGNATURE"}
        version = lambda ms, ls: f"{ms >> 16}.{ms & 65535}.{ls >> 16}.{ls & 65535}"
        return {"status": "OBSERVED", "file_version": version(fixed.file_ms, fixed.file_ls),
                "product_version": version(fixed.product_ms, fixed.product_ls),
                "source": "Windows fixed file version resource; not an in-game build verification"}
    except (OSError, AttributeError, ValueError):
        return {"status": "UNAVAILABLE", "code": "VERSION_API_UNAVAILABLE"}


def inspect_installation(folder: str | Path, manifest_path: str | Path | None = None,
                         *, version_reader: Callable[[Path], dict] | None = None) -> dict:
    """Validate only known required paths; no executable is run.

    The folder picker can pass either the installation directory or its
    DayZServer_x64.exe. A valid appmanifest is optional for a supplied folder.
    READY means required paths are present, not that the server has started.
    """
    directory = _local_root(folder)
    result = {"status": "INCOMPLETE", "app_id": int(APP_ID), "install_dir": None,
              "executable": None, "mission_dir": None, "build_id": None,
              "manifest_state": "NOT_SUPPLIED", "manifest_path": None,
              "missing": [], "file_version": {"status": "NOT_CHECKED"}}
    if directory is None:
        result["missing"] = ["LOCAL_ABSOLUTE_FOLDER_REQUIRED"]
        return result
    if directory.name.casefold() == "dayzserver_x64.exe":
        directory = directory.parent
    result["install_dir"] = str(directory)
    executable = directory / "DayZServer_x64.exe"
    mission = directory / "mpmissions" / "dayzOffline.chernarusplus"
    result.update(executable=str(executable), mission_dir=str(mission))
    checks = ((directory, True, "INSTALL_FOLDER"), (executable, False, "SERVER_EXECUTABLE"),
              (mission, True, "CHERNARUS_MISSION"),
              (mission / "init.c", False, "MISSION_INIT"),
              (mission / "db" / "types.xml", False, "MISSION_TYPES"),
              (directory / "addons", True, "ADDONS_FOLDER"))
    for target, is_directory, label in checks:
        state = _state(target, is_directory)
        if state != "PRESENT":
            result["missing"].append(label + ":" + state)
    if manifest_path is not None:
        manifest = _local_root(manifest_path)
        if manifest is None:
            result["manifest_state"] = "INVALID_PATH"
        else:
            result["manifest_path"] = str(manifest)
            state, parsed = _read_vdf(manifest, MAX_MANIFEST_BYTES)
            result["manifest_state"] = state
            if parsed is not None:
                app = parsed.get("appstate")
                if not isinstance(app, dict) or app.get("appid") != APP_ID:
                    result["manifest_state"] = "APP_ID_MISMATCH"
                else:
                    # Metadata belongs to the manifest's named installation,
                    # not any conventional fallback that happens to exist in
                    # the same library. A stale DayZServer folder must not
                    # inherit another installation's Steam build evidence.
                    expected = _installation_under_library(manifest.parent.parent, app.get("installdir"))
                    if manifest.parent.name.casefold() != "steamapps" or manifest.name.casefold() != "appmanifest_223350.acf":
                        result["manifest_state"] = "MANIFEST_LOCATION_UNVERIFIED"
                    elif expected is None:
                        result["manifest_state"] = "INVALID_INSTALL_DIR"
                    elif os.path.normcase(str(expected)) != os.path.normcase(str(directory)):
                        result["manifest_state"] = "INSTALL_DIR_MISMATCH"
                    else:
                        result["build_id"] = _numeric(app.get("buildid"))
                        result["steam_state_flags"] = _numeric(app.get("stateflags"))
                        result["manifest_state"] = "OBSERVED"
    if _state(executable, False) == "PRESENT":
        result["file_version"] = (version_reader or read_file_version)(executable)
    if not result["missing"]:
        result["status"] = "READY"
    result["readiness_scope"] = "Required files present; server startup and game build remain untested"
    return result


def discover_installations(extra_steam_roots: Iterable[str | Path] | None = None,
                           *, registry_reader: Callable[[], Iterable[str]] | None = None,
                           environ: dict | None = None,
                           version_reader: Callable[[Path], dict] | None = None) -> dict:
    """Return READY candidates first; missing catalogs never trigger a drive scan.

    extra_steam_roots names Steam/library roots, not arbitrary parent folders.
    Supply a server directory to inspect_installation instead. Discovery has a
    budget between bounded operations; it cannot interrupt a blocked OS I/O call.
    """
    result = {"status": "NOT_FOUND", "native_windows": os.name == "nt", "candidates": [],
              "diagnostics": [], "roots_checked": 0, "libraries_checked": 0}
    end = time.monotonic() + DISCOVERY_SECONDS
    environment = os.environ if environ is None else environ
    roots = list(extra_steam_roots or [])[:MAX_ROOTS]
    try:
        roots += list((registry_reader or _registry_steam_roots)())[:MAX_ROOTS]
    except (OSError, ImportError):
        result["diagnostics"].append({"operation": "steam_registry", "code": "UNAVAILABLE"})
    for key in ("ProgramFiles(x86)", "ProgramFiles"):
        value = environment.get(key)
        if value:
            roots.append(Path(value) / "Steam")
    libraries: list[Path] = []
    seen: set[str] = set()

    def issue(operation: str, code: str) -> None:
        item = {"operation": operation, "code": code}
        if item not in result["diagnostics"] and len(result["diagnostics"]) < 40:
            result["diagnostics"].append(item)

    def add_library(raw: str | Path) -> Path | None:
        candidate = _local_root(raw)
        if candidate is None:
            issue("library_path", "NONLOCAL_OR_INVALID_PATH")
            return None
        key = os.path.normcase(str(candidate))
        if key in seen:
            return candidate
        if len(libraries) >= MAX_LIBRARIES:
            issue("libraries", "LIMIT_REACHED")
            return None
        state = _state(candidate / "steamapps", True)
        if state == "MISSING":
            return None
        if state != "PRESENT":
            issue("library_path", state)
            return None
        seen.add(key)
        libraries.append(candidate)
        return candidate

    roots_seen = set()
    for raw_root in roots:
        if len(roots_seen) >= MAX_ROOTS or time.monotonic() >= end:
            break
        root = add_library(raw_root)
        if root is None:
            continue
        key = os.path.normcase(str(root))
        if key in roots_seen:
            continue
        roots_seen.add(key)
        result["roots_checked"] += 1
        for catalog in (root / "steamapps" / "libraryfolders.vdf", root / "config" / "libraryfolders.vdf"):
            state, parsed = _read_vdf(catalog, MAX_CATALOG_BYTES)
            if state == "MISSING":
                continue
            if state != "PRESENT":
                issue("library_catalog", state)
                continue
            folders = parsed.get("libraryfolders")
            if not isinstance(folders, dict):
                issue("library_catalog", "INVALID_STRUCTURE")
                continue
            for index, item in folders.items():
                if time.monotonic() >= end:
                    break
                if not index.isascii() or not index.isdigit():
                    continue
                path = item.get("path") if isinstance(item, dict) else item
                if isinstance(path, str):
                    add_library(path)

    result["library_paths"] = [str(library) for library in libraries]
    installs_seen = set()
    for library in libraries:
        if time.monotonic() >= end:
            issue("discovery", "TIME_BUDGET_REACHED")
            break
        result["libraries_checked"] += 1
        manifest = library / "steamapps" / ("appmanifest_" + APP_ID + ".acf")
        state, parsed = _read_vdf(manifest, MAX_MANIFEST_BYTES)
        possible = []
        if parsed is not None:
            app = parsed.get("appstate")
            if not isinstance(app, dict) or app.get("appid") != APP_ID:
                issue("server_manifest", "APP_ID_MISMATCH")
            else:
                path = _installation_under_library(library, app.get("installdir"))
                if path is None:
                    issue("server_manifest", "INVALID_INSTALL_DIR")
                else:
                    possible.append(path)
        elif state != "MISSING":
            issue("server_manifest", state)
        # One known conventional folder is checked even if a manifest is
        # missing/corrupt. This is not a scan of common or the user's drives.
        fallback = library / "steamapps" / "common" / "DayZServer"
        if _state(fallback, True) == "PRESENT":
            possible.append(fallback)
        for installation in possible:
            key = os.path.normcase(str(installation))
            if key in installs_seen:
                continue
            installs_seen.add(key)
            candidate = inspect_installation(installation, manifest, version_reader=version_reader)
            result["candidates"].append(candidate)
    result["candidates"].sort(key=lambda row: row["status"] != "READY")
    if any(row["status"] == "READY" for row in result["candidates"]):
        result["status"] = "FOUND"
    elif result["candidates"]:
        result["status"] = "INCOMPLETE"
    if not roots_seen and os.name != "nt" and not extra_steam_roots:
        result["status"] = "UNSUPPORTED_PLATFORM"
    result["limitations"] = [
        "Only known Steam roots, their bounded library catalogs, and app 223350 are inspected.",
        "Missing results do not establish that DayZ Server is absent elsewhere; the user can choose its actual installation folder.",
        "Junction/symlink paths are not traversed; select the real local installation directory.",
        "A Steam build ID and file resource version are not a verified in-game server build or compatibility result.",
    ]
    return result
