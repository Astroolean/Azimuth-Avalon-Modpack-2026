"""Build and sign Azimuth's local random-skin add-on before Avalon starts."""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import stat
from tempfile import NamedTemporaryFile, mkdtemp
from azimuth_app.cosmetics import azimuth_skins
from azimuth_app.cosmetics import skin_settings

from azimuth_app.core.backups import source_type
from azimuth_app.integrations.hunter_signing import SigningToolsMissing, ensure_signed
from azimuth_app.cosmetics.native_color_build import _pbo_bytes
from azimuth_app.core.worlds import atomic_text

SOURCE = Path("Custom/GunSkins/RandomSkins/azimuthrandomskins")
PREFIX = "azimuthrandomskins"
MOD_NAME = "@AzimuthRandomSkins"
SIGNING_STATE = "RandomSkinSigning"
MAX_SOURCE_FILES = 32
# Includes the model-backed exterior mappings and installed publisher adapters.
MAX_SOURCE_BYTES = 512 * 1024
MAX_PACK_BYTES = 1024 * 1024
SCRIPT_PATH = re.compile(r"scripts/4_world/(?:[a-z0-9_-]+/)*[a-z0-9_-]+\.c\Z")
PACK_FILE = re.compile(r"(?:mod\.cpp|Addons/azimuthrandomskins\.pbo(?:\.AzimuthHunter_[a-f0-9]{16}\.bisign)?|Keys/AzimuthHunter_[a-f0-9]{16}\.bikey)\Z")
MOD_METADATA = 'name = "Azimuth Random Weapon Finishes";\nauthor = "Ashton Kenney";\noverview = "Random cosmetic finishes on compatible Avalon weapons.";\n'


def _ordinary(path: Path, *, directory: bool = False) -> None:
    if not (stat.S_ISDIR if directory else stat.S_ISREG)(source_type(path)):
        raise ValueError(f"Expected an ordinary random-skin {'folder' if directory else 'file'}: {path.name}.")


def _source_files(source: Path) -> dict[str, bytes]:
    """Accept only this add-on's bounded text config and world scripts."""
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
            if relative not in {"config.cpp"} and not SCRIPT_PATH.fullmatch(relative):
                raise ValueError(f"Unexpected random-skin source file: {relative}.")
            if len(files) >= MAX_SOURCE_FILES:
                raise ValueError("The random-skin source contains too many files.")
            _ordinary(path)
            with path.open("rb") as stream:
                content = stream.read(MAX_SOURCE_BYTES - total + 1)
            total += len(content)
            if total > MAX_SOURCE_BYTES or not content or b"\0" in content:
                raise ValueError(f"Random-skin source must be bounded, nonempty text: {relative}.")
            content.decode("utf-8-sig")
            files[relative] = content
    if "config.cpp" not in files or not any(SCRIPT_PATH.fullmatch(name) for name in files):
        raise ValueError("Random Weapon Finishes requires config.cpp and at least one script.")
    config = files["config.cpp"].decode("utf-8-sig")
    if re.search(r"\bclass\s+CfgAzimuthRandomSkins\b", config):
        raise ValueError("Random skin choices must be generated from the selected catalog, not authored in source.")
    if f'{PREFIX}/scripts/4_world' not in config.replace("\\", "/"):
        raise ValueError("Random Weapon Finishes must register its world-script folder.")
    return files


def _config_string(value: str) -> str:
    if not isinstance(value, str) or any(ord(character) < 32 for character in value):
        raise ValueError("Random skin labels and paths must be plain single-line text.")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_config(original: str, catalog: dict | None = None, choices: dict | None = None) -> str:
    """Append a selected-only mapping with permanent catalog IDs, never list offsets."""
    if re.search(r"\bclass\s+CfgAzimuthRandomSkins\b", original):
        raise ValueError("Random skin choices are already present in the source configuration.")
    enabled, chance, selected = False, 0, []
    vehicle_enabled, vehicle_chance = False, 0
    if catalog is not None or choices is not None:
        if catalog is None or choices is None:
            raise ValueError("A random-skin build needs both its catalog and validated choices.")
        choices = skin_settings.validate(choices, catalog)
        selected = skin_settings.selected_catalog(catalog, choices["selected_skin_ids"])["skins"]
        enabled = choices["random_skins_enabled"] and bool(selected)
        chance = choices["random_skin_chance_percent"]
        vehicle_enabled = choices["vehicle_wraps_enabled"] and bool(selected)
        vehicle_chance = choices["vehicle_wrap_chance_percent"]
    identities = [skin["random_id"] for skin in selected]
    if (any(type(identity) is not int or not 1 <= identity <= 128 for identity in identities)
            or len(identities) != len(set(identities))):
        raise ValueError("Selected random skins need unique stable IDs from 1 to 128.")
    textures, labels = [], []
    for skin in selected:
        if skin["texture"] != f"azimuthskins/data/{skin['id']}_co.paa":
            raise ValueError("A random skin texture must come from its matching selected skin asset.")
        textures.append(_config_string(skin["texture"]))
        labels.append(_config_string(skin["label"]))
    return original.rstrip() + f'''

// Generated from Skins/Camos. Empty selections retain a disabled reset-capable addon.
class CfgAzimuthRandomSkins
{{
    enabled = {int(enabled)};
    chancePercent = {chance};
    vehicleEnabled = {int(vehicle_enabled)};
    vehicleChancePercent = {vehicle_chance};
    skinIds[] = {{{", ".join(map(str, identities))}}};
    skinTextures[] = {{{", ".join(textures)}}};
    skinNames[] = {{{", ".join(labels)}}};
}};
'''


def source_files(source: Path, catalog: dict | None = None, choices: dict | None = None,
                 *, root: Path | None = None, active_vehicle_ids=()) -> dict[str, bytes]:
    files = _source_files(source)
    original = files["config.cpp"].decode("utf-8-sig")
    active_vehicle_ids = tuple(active_vehicle_ids)
    if active_vehicle_ids:
        if root is None:
            raise ValueError("Vehicle compatibility needs the Azimuth root.")
        from azimuth_app.transport.vehicle_slot_compat import apply_config
        original = apply_config(original, root, active_vehicle_ids)
        from azimuth_app.cosmetics import publisher_wrap_lifecycle
        original = publisher_wrap_lifecycle.apply_config(original, active_vehicle_ids)
        files.update(publisher_wrap_lifecycle.script_files(active_vehicle_ids))
    files["config.cpp"] = render_config(original, catalog, choices).encode("utf-8")
    if sum(map(len, files.values())) > MAX_SOURCE_BYTES:
        raise ValueError("The generated random-skin source exceeds its size limit.")
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


def build_pbo(source: Path, pack: Path, catalog: dict | None = None, choices: dict | None = None) -> Path:
    """Pack validated source without claiming to compile or sign DayZ scripts."""
    return _write_pbo(source_files(source, catalog, choices), pack)


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
                raise ValueError("The random-skin mod contains a custom folder; preserve it before updating.")
            _ordinary(path, directory=True)
        for name in names:
            path = parent / name
            relative = path.relative_to(pack).as_posix()
            if not PACK_FILE.fullmatch(relative):
                raise ValueError(f"The random-skin mod contains a custom file: {relative}. Preserve it before updating.")
            _ordinary(path)
            with path.open("rb") as stream:
                content = stream.read(MAX_PACK_BYTES - total + 1)
            total += len(content)
            if total > MAX_PACK_BYTES:
                raise ValueError("The installed random-skin mod exceeds its expected size.")
            files[relative] = content
    return files


def _require_signed(files: dict[str, bytes]) -> None:
    signatures = [name.removeprefix(f"Addons/{PREFIX}.pbo.").removesuffix(".bisign")
                  for name, content in files.items()
                  if name.startswith(f"Addons/{PREFIX}.pbo.") and name.endswith(".bisign") and content]
    if not files.get(f"Addons/{PREFIX}.pbo") or not any(files.get(f"Keys/{key}.bikey") for key in signatures):
        raise RuntimeError("DayZ Tools did not produce a signed random-skin PBO and its matching public key.")


def prepare(root: Path, mods_root: Path, steam_libraries: list[Path], *, active_vehicle_ids=()) -> Path | None:
    """Publish only a signed client/server mod. Caller must hold a stopped-server lock."""
    source = root / SOURCE
    pack = mods_root / MOD_NAME
    if not os.path.lexists(source):
        if os.path.lexists(pack):
            raise ValueError("Random Weapon Finishes source is missing. Restore Custom/GunSkins/RandomSkins, or remove its generated @AzimuthRandomSkins mod to uninstall.")
        return None
    for parent in (root / "Custom", root / "Custom/GunSkins", root / "Custom/GunSkins/RandomSkins", source):
        _ordinary(parent, directory=True)
    catalog = azimuth_skins.load_catalog(root / azimuth_skins.SOURCE_FOLDER / "catalog.json")
    choices = skin_settings.ensure_persisted(root, catalog)
    files = source_files(source, catalog, choices, root=root, active_vehicle_ids=active_vehicle_ids)
    mods_root.mkdir(parents=True, exist_ok=True)
    _ordinary(mods_root, directory=True)
    before = _pack_files(pack)
    # A sibling of Mods stays on the same filesystem without becoming a mod
    # discovered by the server. Existing binaries remain available until signed.
    temporary = Path(mkdtemp(prefix=".azimuth-random-skins-", dir=mods_root.parent))
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
            raise SigningToolsMissing("Random Weapon Finishes needs signing. In Steam, install the free DayZ Tools "
                                      "(Library > Tools > DayZ Tools), then click Play Avalon again.") from error
        atomic_text(staged / "mod.cpp", MOD_METADATA)
        published = _pack_files(staged)
        _require_signed(published)
        if _pack_files(pack) != before:
            raise RuntimeError("The random-skin mod changed during preparation; its files were not replaced.")
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
                    raise RuntimeError(f"Random Weapon Finishes could not be published. Its previous files are preserved at {previous}. "
                                       f"{recovery_error}") from error
            raise
        return pack
    finally:
        if not keep_recovery:
            shutil.rmtree(temporary, ignore_errors=True)
