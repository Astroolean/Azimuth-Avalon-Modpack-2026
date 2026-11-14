"""Build and sign Azimuth's local photo-mode add-on before Avalon starts."""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import stat
from tempfile import NamedTemporaryFile, mkdtemp
from xml.etree import ElementTree as ET

from azimuth_app.core.backups import source_type
from azimuth_app.integrations.hunter_signing import SigningToolsMissing, ensure_signed
from azimuth_app.cosmetics.native_color_build import _pbo_bytes
from azimuth_app.core.worlds import atomic_text

SOURCE = Path("Custom/PhotoMode/azimuthphoto")
PREFIX = "azimuthphoto"
MOD_NAME = "@AzimuthPhotoMode"
SIGNING_STATE = "PhotoModeSigning"
MAX_SOURCE_FILES = 32
MAX_SOURCE_BYTES = 256 * 1024
MAX_PACK_BYTES = 1024 * 1024
SCRIPT_PATH = re.compile(r"scripts/(?:3_game|4_world|5_mission)/(?:[a-z0-9_-]+/)*[a-z0-9_-]+\.c\Z")
PACK_FILE = re.compile(r"(?:mod\.cpp|Addons/azimuthphoto\.pbo(?:\.AzimuthHunter_[a-f0-9]{16}\.bisign)?|Keys/AzimuthHunter_[a-f0-9]{16}\.bikey)\Z")
MOD_METADATA = 'name = "Azimuth Photo Mode";\nauthor = "Azimuth";\noverview = "Photo camera controls for Avalon.";\n'


def _ordinary(path: Path, *, directory: bool = False) -> None:
    if not (stat.S_ISDIR if directory else stat.S_ISREG)(source_type(path)):
        raise ValueError(f"Expected an ordinary photo-mode {'folder' if directory else 'file'}: {path.name}.")


def source_files(source: Path) -> dict[str, bytes]:
    """Accept only this add-on's text config, scripts, inputs and optional labels."""
    _ordinary(source, directory=True)
    files, total = {}, 0

    def walk_error(error):
        raise error

    for directory, folders, names in os.walk(source, followlinks=False, onerror=walk_error):
        parent = Path(directory)
        for name in folders:
            _ordinary(parent / name, directory=True)
        for name in names:
            path = parent / name
            relative = path.relative_to(source).as_posix()
            if relative not in {"config.cpp", "inputs.xml", "stringtable.csv"} and not SCRIPT_PATH.fullmatch(relative):
                raise ValueError(f"Unexpected photo-mode source file: {relative}.")
            if len(files) >= MAX_SOURCE_FILES:
                raise ValueError("The photo-mode source contains too many files.")
            _ordinary(path)
            with path.open("rb") as stream:
                content = stream.read(MAX_SOURCE_BYTES - total + 1)
            total += len(content)
            if total > MAX_SOURCE_BYTES or not content or b"\0" in content:
                raise ValueError(f"Photo-mode source must be bounded, nonempty text: {relative}.")
            content.decode("utf-8-sig")
            files[relative] = content
    if "config.cpp" not in files or not any(SCRIPT_PATH.fullmatch(name) for name in files):
        raise ValueError("Photo Mode requires config.cpp and at least one script.")
    config = files["config.cpp"].decode("utf-8-sig").replace("\\", "/")
    input_references = re.findall(r'\binputs\s*=\s*"([^"]+)"\s*;', config)
    if "inputs.xml" in files:
        if input_references != [f"{PREFIX}/inputs.xml"]:
            raise ValueError("Photo Mode config.cpp must register azimuthphoto/inputs.xml exactly once.")
        if b"<!DOCTYPE" in files["inputs.xml"].upper() or b"<!ENTITY" in files["inputs.xml"].upper():
            raise ValueError("Photo Mode inputs.xml contains an unsupported XML declaration.")
        try:
            ET.fromstring(files["inputs.xml"])
        except ET.ParseError as error:
            raise ValueError(f"Photo Mode inputs.xml is invalid: {error}") from error
    elif input_references:
        raise ValueError("Photo Mode inputs.xml is referenced but missing.")
    return files


def _write_pbo(files: dict[str, bytes], pack: Path) -> Path:
    payload = _pbo_bytes(files, PREFIX)
    pack.mkdir(parents=True, exist_ok=True)
    _ordinary(pack, directory=True)
    addons = pack / "Addons"
    addons.mkdir(exist_ok=True)
    _ordinary(addons, directory=True)
    target = addons / f"{PREFIX}.pbo"
    if os.path.lexists(target):
        _ordinary(target)
        if target.stat().st_size == len(payload) and target.read_bytes() == payload:
            return target
    temporary = None
    try:
        with NamedTemporaryFile(dir=addons, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return target


def build_pbo(source: Path, pack: Path) -> Path:
    """Pack validated source without claiming to compile or sign DayZ scripts."""
    return _write_pbo(source_files(source), pack)


def _pack_files(pack: Path) -> dict[str, bytes] | None:
    if not os.path.lexists(pack):
        return None
    _ordinary(pack, directory=True)
    files, total = {}, 0
    for directory, folders, names in os.walk(pack, followlinks=False):
        parent = Path(directory)
        for name in folders:
            path = parent / name
            if path.relative_to(pack).as_posix() not in {"Addons", "Keys"}:
                raise ValueError("The photo-mode mod contains a custom folder; preserve it before updating.")
            _ordinary(path, directory=True)
        for name in names:
            path = parent / name
            relative = path.relative_to(pack).as_posix()
            if not PACK_FILE.fullmatch(relative):
                raise ValueError(f"The photo-mode mod contains a custom file: {relative}. Preserve it before updating.")
            _ordinary(path)
            with path.open("rb") as stream:
                content = stream.read(MAX_PACK_BYTES - total + 1)
            total += len(content)
            if total > MAX_PACK_BYTES:
                raise ValueError("The installed photo-mode mod exceeds its expected size.")
            files[relative] = content
    return files


def _require_signed(files: dict[str, bytes]) -> None:
    signatures = [name.removeprefix(f"Addons/{PREFIX}.pbo.").removesuffix(".bisign")
                  for name, content in files.items()
                  if name.startswith(f"Addons/{PREFIX}.pbo.") and name.endswith(".bisign") and content]
    if not files.get(f"Addons/{PREFIX}.pbo") or not any(files.get(f"Keys/{key}.bikey") for key in signatures):
        raise RuntimeError("DayZ Tools did not produce a signed photo-mode PBO and its matching public key.")


def prepare(root: Path, mods_root: Path, steam_libraries: list[Path]) -> Path | None:
    """Publish only a signed client/server mod. Caller must hold a stopped-server lock."""
    source = root / SOURCE
    pack = mods_root / MOD_NAME
    if not os.path.lexists(source):
        if os.path.lexists(pack):
            raise ValueError("Photo Mode source is missing. Restore Custom/PhotoMode, or remove its generated @AzimuthPhotoMode mod to uninstall.")
        return None
    for parent in (root / "Custom", root / "Custom/PhotoMode", source):
        _ordinary(parent, directory=True)
    files = source_files(source)
    mods_root.mkdir(parents=True, exist_ok=True)
    _ordinary(mods_root, directory=True)
    before = _pack_files(pack)
    # A sibling of Mods stays on the same filesystem without becoming a mod
    # discovered by the server. Existing binaries remain available until signed.
    temporary = Path(mkdtemp(prefix=".azimuth-photo-", dir=mods_root.parent))
    keep_recovery = False
    staged = temporary / MOD_NAME
    previous = temporary / "previous"
    try:
        if before is not None:
            shutil.copytree(pack, staged)
        _write_pbo(files, staged)
        try:
            ensure_signed(staged, steam_libraries, root, state_name=SIGNING_STATE)
        except SigningToolsMissing as error:
            raise SigningToolsMissing("Photo Mode needs signing. In Steam, install the free DayZ Tools "
                                      "(Library > Tools > DayZ Tools), then click Play Avalon again.") from error
        atomic_text(staged / "mod.cpp", MOD_METADATA)
        published = _pack_files(staged)
        _require_signed(published)
        if _pack_files(pack) != before:
            raise RuntimeError("The photo-mode mod changed during preparation; its files were not replaced.")
        if published == before:
            return pack
        if before is not None:
            pack.rename(previous)
        try:
            staged.rename(pack)
        except OSError as error:
            if before is not None:
                try:
                    if os.path.lexists(pack):
                        raise OSError("Another folder appeared at the mod destination.")
                    previous.rename(pack)
                except OSError as recovery_error:
                    keep_recovery = True
                    raise RuntimeError(f"Photo Mode could not be published. Its previous files are preserved at {previous}. "
                                       f"{recovery_error}") from error
            raise
        return pack
    finally:
        if not keep_recovery:
            shutil.rmtree(temporary, ignore_errors=True)
