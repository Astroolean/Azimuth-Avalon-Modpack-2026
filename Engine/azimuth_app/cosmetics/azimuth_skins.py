"""Build and register Azimuth's optional cosmetic gun-skin add-on for Avalon."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha1, sha256
import json
import os
from pathlib import Path
import re
import stat
import shutil
import struct
from tempfile import NamedTemporaryFile, mkdtemp
from xml.etree import ElementTree as ET

from azimuth_app.economy.avalon_economy import _check_type_collisions, register_bytes
from azimuth_app.economy.avalon_loot_audit import _effective, _references, _xml, TypeLayer, read_economy
from azimuth_app.core.backups import source_type
from azimuth_app.economy.hunter_economy import _read_xml, _write_core
from azimuth_app.integrations.hunter_signing import SigningToolsMissing, ensure_signed
from azimuth_app.cosmetics.native_color_build import _pbo_bytes
from azimuth_app.core.worlds import atomic_text
from azimuth_app.cosmetics import skin_settings

SOURCE_FOLDER = Path("Custom/GunSkins")
PREFIX = "azimuthskins"
MOD_NAME = "@AzimuthSkins"
ECONOMY_SLUG = "azimuth_skins"
ECONOMY_FOLDER = f"azimuth_workshop/{ECONOMY_SLUG}"
MAX_FILES = 258
MAX_BYTES = 256 * 1024 * 1024
MAX_PACK_BYTES = MAX_BYTES + 1024 * 1024
PACK_FILE = re.compile(r"(?:mod\.cpp|Addons/azimuthskins\.pbo(?:\.AzimuthHunter_[a-f0-9]{16}\.bisign)?|Keys/AzimuthHunter_[a-f0-9]{16}\.bikey)\Z")
IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,95}\Z")
SLUG = re.compile(r"[a-z][a-z0-9_]{0,39}\Z")
SHOWCASE_CHEST = "Azimuth_Skins_ShowcaseChest"
SKIN_DESCRIPTIONS = {
    "neon420": "Green cannabis leaves and 420 details over dark woodland camo.",
    "bluedream420": "Cyan cannabis leaves and 420 details over midnight blue camo.",
    "cosmickush420": "Blue and purple galaxy clouds with cannabis leaves and 420 details.",
    "deeporbit": "Rich blue and violet nebula clouds scattered with stars.",
    "arcticpulse": "Sharp camouflage shapes in cobalt, ice blue and white.",
    "eventhorizon": "Dark navy space with blue auroras and fine constellations.",
}


def _ordinary(path: Path, directory: bool = False) -> None:
    if not (stat.S_ISDIR if directory else stat.S_ISREG)(source_type(path)):
        raise ValueError(f"Expected an ordinary gun-skin {'folder' if directory else 'file'}: {path.name}.")


def load_catalog(path: Path) -> dict:
    """Read only the bounded, local catalog supplied with this add-on."""
    _ordinary(path)
    with path.open("rb") as stream:
        raw = stream.read(128 * 1024 + 1)
    if len(raw) > 128 * 1024:
        raise ValueError("The gun-skin catalog is too large.")
    catalog = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(catalog, dict) or catalog.get("schema_version") != 1:
        raise ValueError("Unsupported gun-skin catalog version.")
    skins, weapons = catalog.get("skins"), catalog.get("weapons")
    if not isinstance(skins, list) or not 1 <= len(skins) <= 128 or not isinstance(weapons, list) or not 1 <= len(weapons) <= 8:
        raise ValueError("The gun-skin catalog has no supported skins or weapons.")
    skin_ids, classes, bases, random_ids = set(), set(), set(), set()
    for position, skin in enumerate(skins, 1):
        if not isinstance(skin, dict):
            raise ValueError("Invalid gun-skin entry.")
        identity, texture = skin.get("id"), skin.get("texture")
        if not isinstance(identity, str) or not SLUG.fullmatch(identity) or identity in skin_ids:
            raise ValueError("Invalid or duplicate gun-skin identifier.")
        expected = f"{PREFIX}/data/{identity}_co.paa"
        if texture != expected:
            raise ValueError(f"Gun-skin texture must be {expected}.")
        label = skin.get("label")
        if not isinstance(label, str) or not label.strip() or len(label) > 100:
            raise ValueError("Every finish needs a short display name.")
        description = skin.get("description", SKIN_DESCRIPTIONS.get(identity, "Custom weapon-body finish."))
        if not isinstance(description, str) or len(description) > 500:
            raise ValueError("The skin description is invalid.")
        preview = skin.get("preview", f"Custom/GunSkins/Previews/{identity}_co.png")
        if preview != f"Custom/GunSkins/Previews/{identity}_co.png":
            raise ValueError("The skin preview must use its matching bundled PNG.")
        skin["description"] = description
        skin["preview"] = preview
        # Published IDs never depend on the currently selected subset. The
        # positional fallback supports the original six-entry catalog only.
        random_id = skin.get("random_id", position)
        if type(random_id) is not int or not 1 <= random_id <= 128 or random_id in random_ids:
            raise ValueError("Every finish needs a unique stable random skin ID from 1 to 128.")
        skin["random_id"] = random_id
        random_ids.add(random_id)
        skin_ids.add(identity)
    for weapon in weapons:
        if not isinstance(weapon, dict):
            raise ValueError("Invalid gun-skin weapon entry.")
        base, variants = weapon.get("base_class"), weapon.get("variants")
        if not isinstance(base, str) or not IDENTIFIER.fullmatch(base) or base.casefold() in bases:
            raise ValueError("Invalid or duplicate gun-skin base weapon.")
        bases.add(base.casefold())
        if not isinstance(variants, list) or len(variants) != len(skin_ids):
            raise ValueError(f"Every skin must have one {base} variant.")
        found = set()
        for variant in variants:
            if not isinstance(variant, dict):
                raise ValueError("Invalid gun-skin variant.")
            name, skin_id = variant.get("class_name"), variant.get("skin_id")
            if (not isinstance(name, str) or not IDENTIFIER.fullmatch(name)
                    or not name.startswith("Azimuth_") or name.casefold() in classes
                    or skin_id not in skin_ids or skin_id in found):
                raise ValueError("Invalid or duplicate gun-skin class.")
            classes.add(name.casefold())
            found.add(skin_id)
    if len(classes) > 1024:
        raise ValueError("The gun-skin catalog contains too many variants.")
    return catalog


def source_files(source: Path, catalog: dict, selected_ids: list[str] | None = None) -> dict[str, bytes]:
    _ordinary(source, True)
    allowed = {"config.cpp", *(skin["texture"].removeprefix(f"{PREFIX}/") for skin in catalog["skins"])}
    files, total = {}, 0
    def walk_error(error):
        raise error
    for directory, folders, names in os.walk(source, followlinks=False, onerror=walk_error):
        folder = Path(directory)
        for child in folders:
            _ordinary(folder / child, True)
        for name in names:
            path = folder / name
            _ordinary(path)
            relative = path.relative_to(source).as_posix()
            if relative not in allowed or len(files) >= MAX_FILES:
                raise ValueError(f"Unexpected gun-skin source file: {relative}.")
            with path.open("rb") as stream:
                data = stream.read(MAX_BYTES - total + 1)
            total += len(data)
            if not data or total > MAX_BYTES:
                raise ValueError("The gun-skin source is empty or exceeds its size limit.")
            if relative.endswith(".paa") and (len(data) < 32 or data[:2] not in (b"\x01\xff", b"\x05\xff", b"\x44\x44", b"\x55\x15", b"\x88\x88")):
                raise ValueError(f"Expected a converted DayZ PAA texture at {relative}.")
            files[relative] = data
    if files.keys() != allowed:
        raise ValueError("Gun-skin files are missing: " + ", ".join(sorted(allowed - files.keys())))
    config = files["config.cpp"].decode("utf-8-sig")
    if "\0" in config:
        raise ValueError("Gun-skin config.cpp must be plain text.")
    for weapon in catalog["weapons"]:
        for variant in weapon["variants"]:
            if not re.search(r"\bclass\s+" + re.escape(variant["class_name"]) + r"\s*:\s*" + re.escape(weapon["base_class"]) + r"\s*\{", config):
                raise ValueError(f"The gun-skin config does not define {variant['class_name']} on {weapon['base_class']}.")
    for skin in catalog["skins"]:
        virtual = skin["texture"]
        if virtual not in config.replace("\\", "/"):
            raise ValueError(f"The gun-skin config does not reference {skin['texture']}.")
    if selected_ids is not None:
        selected = skin_settings.selected_catalog(catalog, selected_ids)
        names = {skin["texture"].removeprefix(f"{PREFIX}/") for skin in selected["skins"]}
        files = {name: value for name, value in files.items() if name in names}
        files["config.cpp"] = render_config(config, catalog, selected).encode("utf-8")
    return files


def _class_end(text: str, opening: int) -> int:
    """Match a config class brace while ignoring comments and quoted strings."""
    depth, index, quoted = 0, opening, False
    while index < len(text):
        character = text[index]
        if quoted:
            if character == "\\":
                index += 2
                continue
            if character == '"':
                quoted = False
        elif character == '"':
            quoted = True
        elif text.startswith("//", index):
            newline = text.find("\n", index)
            index = len(text) if newline < 0 else newline
            continue
        elif text.startswith("/*", index):
            end = text.find("*/", index + 2)
            if end < 0:
                raise ValueError("The gun-skin configuration contains an unfinished comment.")
            index = end + 2
            continue
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if not depth:
                end = index + 1
                while end < len(text) and text[end].isspace():
                    end += 1
                if end >= len(text) or text[end] != ";":
                    raise ValueError("A gun-skin configuration class is missing its terminator.")
                return end + 1
        index += 1
    raise ValueError("The gun-skin configuration contains an unfinished class.")


def showcase_class(class_name: str) -> str:
    if not class_name.startswith("Azimuth_M4A1_"):
        raise ValueError("Showcase rifles must use an M4A1 finish.")
    return class_name.replace("Azimuth_M4A1_", "Azimuth_Showcase_M4A1_", 1)


def render_config(original: str, catalog: dict, selected: dict) -> str:
    """Copy selected authored definitions; do not load unchecked variants/assets."""
    addons = re.search(r"requiredAddons\[\]\s*=\s*\{([^}]+)\}\s*;", original)
    if addons is None:
        raise ValueError("The skin configuration is missing its dependencies.")
    dependencies = re.findall(r'"([A-Za-z0-9_]+)"', addons.group(1))
    if not dependencies:
        raise ValueError("The skin configuration has invalid dependencies.")
    dependencies = list(dict.fromkeys([*dependencies, "DZ_Gear_Camping", "DZ_Gear_Containers"]))
    definitions, names, base_names, showcase = [], [], [], []
    labels = {skin["id"]: skin["label"] for skin in selected["skins"]}
    for weapon in selected["weapons"]:
        base = weapon["base_class"]
        base_names.append(base)
        for variant in weapon["variants"]:
            name = variant["class_name"]
            matches = list(re.finditer(r"\bclass\s+" + re.escape(name) + r"\s*:\s*" + re.escape(base) + r"\s*\{", original))
            if len(matches) != 1:
                raise ValueError(f"Expected exactly one definition for {name}.")
            start = matches[0].start()
            opening = matches[0].end() - 1
            definitions.append(original[start:_class_end(original, opening)])
            names.append(name)
            if base == "M4A1":
                test_name = showcase_class(name)
                display = ("M4A1 | " + labels[variant["skin_id"]] + " (Showcase)").replace("\\", "\\\\").replace('"', '\\"')
                showcase.append(f'    class {test_name}: {name}\n    {{\n        scope = 2;\n        displayName = "{display}";\n    }};')
                names.append(test_name)
    names_text = ", ".join('"' + name + '"' for name in names)
    deps_text = ", ".join('"' + name + '"' for name in dependencies)
    bases = "\n".join(f"    class {name};" for name in dict.fromkeys(base_names))
    body = "\n\n".join("    " + block for block in definitions)
    return f'''// Generated from the selected Azimuth finishes. Unchecked finishes are excluded.
class CfgPatches
{{
    class Azimuth_GunSkins
    {{
        units[] = {{"{SHOWCASE_CHEST}"}};
        weapons[] = {{{names_text}}};
        requiredVersion = 0.1;
        requiredAddons[] = {{{deps_text}}};
        author = "Ashton Kenney";
        name = "Azimuth Gun Skins";
    }};
}};
class CfgWeapons
{{
{bases}
{body}
{chr(10).join(showcase)}
}};
class CfgVehicles
{{
    class SeaChest;
    class {SHOWCASE_CHEST}: SeaChest
    {{
        scope = 2;
        displayName = "Azimuth Skin Showcase";
        descriptionShort = "Temporary showcase rifle case. Managed from Skins/Camos.";
        class Cargo
        {{
            itemsCargoSize[] = {{10,20}};
        }};
    }};
}};
'''


def build_pbo(source: Path, pack: Path, catalog: dict, selected_ids: list[str] | None = None) -> Path:
    """Pack config and already converted textures; source art stays outside the PBO."""
    payload = _pbo_bytes(source_files(source, catalog, selected_ids), PREFIX)
    pack.mkdir(parents=True, exist_ok=True)
    _ordinary(pack, True)
    addons = pack / "Addons"
    addons.mkdir(exist_ok=True)
    _ordinary(addons, True)
    target = addons / f"{PREFIX}.pbo"
    if target.exists() or target.is_symlink():
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


def _spawn_templates(mission: Path) -> dict[str, ET.Element]:
    warnings, contained_warnings = [], []
    references = _references(mission, {ECONOMY_FOLDER}, warnings, contained_warnings)
    if warnings or contained_warnings:
        raise ValueError("Cannot inherit weapon attachments: " + "; ".join(warnings + contained_warnings))
    layers = {}
    for path in references["spawnabletypes"]:
        root = _xml(mission, path, "spawnabletypes")
        seen = set()
        for node in root.findall("type"):
            name = node.get("name", "").casefold()
            if not name or name in seen:
                raise ValueError(f"Missing or duplicate weapon attachment class in {path.name}.")
            seen.add(name)
            layers.setdefault(name, []).append(TypeLayer(path, node))
    return {name: _effective(entries) for name, entries in layers.items()}


def economy_files(mission: Path, catalog: dict) -> tuple[dict[str, tuple[str, bytes]], tuple[str, ...]]:
    """Preserve the map's base-gun locations, restrictions, and attachment rules."""
    mission = mission.resolve()
    economy = read_economy(mission, exclude_folders=(ECONOMY_FOLDER,))
    if economy.warnings:
        raise ValueError("Cannot safely inherit gun loot rules: " + "; ".join(economy.warnings))
    spawn_templates = _spawn_templates(mission)
    types, spawnables = ET.Element("types"), ET.Element("spawnabletypes")
    notices = []
    for weapon in catalog["weapons"]:
        base = weapon["base_class"]
        template = economy.types.get(base.casefold())
        if template is None or template.nominal is None:
            raise ValueError(f"Avalon's economy does not define valid loot rules for {base}.")
        spawn_template = spawn_templates.get(base.casefold())
        if spawn_template is None:
            raise ValueError(f"Avalon's attachment rules for {base} are missing; gun skins were not installed.")
        flags = template.element.find("flags")
        crafted = flags is not None and flags.get("crafted", "0").strip().casefold() in {"1", "true"}
        enabled = template.nominal > 0 and not crafted
        for variant in weapon["variants"]:
            node = deepcopy(template.element)
            node.set("name", variant["class_name"])
            # Fixed classes remain registered for existing guns and explicit
            # showcase/admin creation. Normal base-weapon spawns receive their
            # random finish at runtime; no extra guns are added to the economy.
            for tag, value in (("nominal", 0), ("min", 0)):
                child = node.find(tag)
                if child is None:
                    child = ET.SubElement(node, tag)
                child.text = str(value)
            types.append(node)
            attached = deepcopy(spawn_template)
            attached.set("name", variant["class_name"])
            spawnables.append(attached)
        if not enabled:
            notices.append(f"{base} skins keep Avalon's disabled/crafted loot rules and have no automatic loot target.")
    # The showcase script creates these explicitly. Zero targets and no map
    # placement tags give them persistence without adding automatic loot.
    temporary_classes = [SHOWCASE_CHEST]
    for weapon in catalog["weapons"]:
        if weapon["base_class"] == "M4A1":
            temporary_classes.extend(showcase_class(variant["class_name"]) for variant in weapon["variants"])
    for name in temporary_classes:
        node = ET.SubElement(types, "type", name=name)
        for tag, value in (("nominal", 0), ("min", 0), ("lifetime", 604800), ("restock", 0),
                           ("quantmin", -1), ("quantmax", -1), ("cost", 0)):
            ET.SubElement(node, tag).text = str(value)
        ET.SubElement(node, "flags", count_in_map="0", count_in_cargo="0", count_in_hoarder="0",
                      count_in_player="0", crafted="1", deloot="0")
    chest_spawn = ET.SubElement(spawnables, "type", name=SHOWCASE_CHEST)
    ET.SubElement(chest_spawn, "hoarder")
    core = _read_xml(mission / "cfgeconomycore.xml", "economycore", preserve_comments=True)
    target = mission / ECONOMY_FOLDER / "types.xml"
    _check_type_collisions(mission, core, target, {node.get("name").casefold(): node.get("name") for node in types})
    payloads = {}
    for kind, root in (("types", types), ("spawnabletypes", spawnables)):
        ET.indent(root)
        payloads[kind] = (f"{kind}.xml", ET.tostring(root, encoding="utf-8", xml_declaration=True) + b"\n")
    return payloads, tuple(notices)


def remove_economy(mission: Path) -> None:
    """Remove only this add-on's references after its source and mod were removed."""
    path = mission / "cfgeconomycore.xml"
    if not path.is_file():
        return
    core = _read_xml(path, "economycore", preserve_comments=True)
    changed = False
    for extension in core.findall("ce"):
        if extension.get("folder", "").replace("\\", "/").rstrip("/").casefold() != ECONOMY_FOLDER:
            continue
        core.remove(extension)
        changed = True
    if changed:
        _write_core(path, core)


def _pack_snapshot(pack: Path) -> dict[str, tuple[int, str]] | None:
    """Fingerprint only owned pack files without retaining large texture PBOs."""
    if not os.path.lexists(pack):
        return None
    _ordinary(pack, True)
    snapshot, total = {}, 0
    for directory, folders, names in os.walk(pack, followlinks=False):
        parent = Path(directory)
        for name in folders:
            path = parent / name
            if path.relative_to(pack).as_posix() not in {"Addons", "Keys"}:
                raise ValueError("The generated skin mod contains an unexpected folder; preserve it before updating.")
            _ordinary(path, True)
        for name in names:
            path = parent / name
            relative = path.relative_to(pack).as_posix()
            if not PACK_FILE.fullmatch(relative):
                raise ValueError("The generated skin mod contains an unexpected file; preserve it before updating.")
            _ordinary(path)
            digest, size = sha256(), 0
            with path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    size += len(chunk)
                    total += len(chunk)
                    if total > MAX_PACK_BYTES:
                        raise ValueError("The generated skin mod exceeds its expected size.")
                    digest.update(chunk)
            snapshot[relative] = (size, digest.hexdigest())
    return snapshot


def _require_signed(snapshot: dict[str, tuple[int, str]]) -> None:
    signatures = [name.removeprefix("Addons/azimuthskins.pbo.").removesuffix(".bisign")
                  for name, info in snapshot.items()
                  if name.startswith("Addons/azimuthskins.pbo.") and name.endswith(".bisign") and info[0]]
    if (not snapshot.get("Addons/azimuthskins.pbo", (0, ""))[0]
            or not any(snapshot.get(f"Keys/{key}.bikey", (0, ""))[0] for key in signatures)):
        raise RuntimeError("DayZ Tools did not produce a signed skin PBO and its matching public key.")


def _publish_signed(source: Path, pack: Path, catalog: dict, selected_ids: list[str],
                    steam_libraries: list[Path], package_root: Path) -> None:
    """A failed rebuild/signing attempt leaves the installed skin pack intact."""
    pack.parent.mkdir(parents=True, exist_ok=True)
    _ordinary(pack.parent, True)
    before = _pack_snapshot(pack)
    temporary = Path(mkdtemp(prefix=".azimuth-skins-", dir=pack.parent.parent))
    staged, previous = temporary / MOD_NAME, temporary / "previous"
    keep_recovery = False
    try:
        if before is not None:
            shutil.copytree(pack, staged)
        build_pbo(source, staged, catalog, selected_ids)
        try:
            ensure_signed(staged, steam_libraries, package_root, state_name="AzimuthSkinsSigning")
        except SigningToolsMissing as error:
            raise SigningToolsMissing(
                "Azimuth Gun Skins needs signing before the first launch. "
                "In Steam, install the free DayZ Tools (Library > Tools > DayZ Tools), "
                "then click Play Avalon again."
            ) from error
        atomic_text(staged / "mod.cpp", 'name = "Azimuth Gun Skins";\nauthor = "Ashton Kenney";\noverview = "Selected static weapon finishes for Avalon.";\n')
        published = _pack_snapshot(staged)
        _require_signed(published)
        if _pack_snapshot(pack) != before:
            raise RuntimeError("The skin mod changed during preparation; its files were not replaced.")
        if published == before:
            return
        if before is not None:
            pack.rename(previous)
        try:
            staged.rename(pack)
        except OSError as error:
            if before is not None:
                try:
                    if os.path.lexists(pack):
                        raise OSError("Another folder appeared at the skin mod destination.")
                    previous.rename(pack)
                except OSError as recovery_error:
                    keep_recovery = True
                    raise RuntimeError(f"The skin mod could not be published. Previous files are preserved at {previous}. "
                                       f"{recovery_error}") from error
            raise
    finally:
        if not keep_recovery:
            shutil.rmtree(temporary, ignore_errors=True)


def disable_pack(pack: Path) -> None:
    """Remove only this generated add-on, never a mixed or user-owned folder."""
    if not pack.exists() and not pack.is_symlink():
        return
    _ordinary(pack, True)
    for directory, folders, names in os.walk(pack, followlinks=False):
        parent = Path(directory)
        for name in folders:
            relative = (parent / name).relative_to(pack).as_posix()
            if relative not in {"Addons", "Keys"}:
                raise ValueError("The generated skin mod contains an unexpected folder; preserve it before disabling the pack.")
            _ordinary(parent / name, True)
        for name in names:
            path = parent / name
            _ordinary(path)
            if not PACK_FILE.fullmatch(path.relative_to(pack).as_posix()):
                raise ValueError("The generated skin mod contains an unexpected file; preserve it before disabling the pack.")
    shutil.rmtree(pack)


def installed_classes(pack: Path, catalog: dict) -> tuple[list[str], bool]:
    """Recover class history from our validated generated PBO during upgrades.

    Older installations may predate applied.json. Read its actual config,
    never assume the entire catalog was previously loaded or scan texture bytes
    for class-like strings. An unreadable existing bank must fail closed.
    """
    if _pack_snapshot(pack) is None:
        return [], False
    path = pack / "Addons" / f"{PREFIX}.pbo"
    if not path.exists():
        return [], False
    data = path.read_bytes()
    try:
        if len(data) < 43 or data[-21] != 0 or sha1(data[:-21], usedforsecurity=False).digest() != data[-20:]:
            raise ValueError("checksum")
        cursor, limit = 0, len(data) - 21
        entries, prefix = [], ""

        def text() -> str:
            nonlocal cursor
            end = data.index(b"\0", cursor, limit)
            value = data[cursor:end].decode("ascii")
            cursor = end + 1
            return value

        while True:
            name = text().replace("\\", "/")
            if cursor + 20 > limit:
                raise ValueError("header")
            method, original, reserved, timestamp, size = struct.unpack_from("<5I", data, cursor)
            cursor += 20
            if not name and method == 0x56657273:
                if entries or prefix:
                    raise ValueError("properties")
                while True:
                    key = text()
                    if not key:
                        break
                    value = text()
                    if key == "prefix":
                        prefix = value
                continue
            if not name:
                if any((method, original, reserved, timestamp, size)):
                    raise ValueError("terminator")
                break
            if method != 0 or original != size or len(entries) >= MAX_FILES:
                raise ValueError("unsupported entry")
            entries.append((name, size))
        if prefix != PREFIX:
            raise ValueError("prefix")
        config = None
        for name, size in entries:
            if cursor + size > limit:
                raise ValueError("data bounds")
            if name == "config.cpp":
                if config is not None:
                    raise ValueError("duplicate config")
                config = data[cursor:cursor + size].decode("utf-8-sig")
            cursor += size
        if cursor != limit or config is None:
            raise ValueError("missing config")
    except (ValueError, UnicodeError, struct.error) as error:
        raise ValueError("The installed skin PBO cannot be checked for saved classes. Restore its valid file before rebuilding.") from error
    identities = set()
    known_classes = {SHOWCASE_CHEST}
    for weapon in catalog["weapons"]:
        for variant in weapon["variants"]:
            known_classes.add(variant["class_name"])
            if weapon["base_class"] == "M4A1":
                known_classes.add(showcase_class(variant["class_name"]))
            if re.search(r"\bclass\s+" + re.escape(variant["class_name"]) + r"\s*:", config):
                identities.add(variant["skin_id"])
    installed_names = set(re.findall(r"\bclass\s+(Azimuth_[A-Za-z0-9_]+)\s*:", config))
    if installed_names - known_classes:
        raise ValueError("The installed skin PBO contains classes absent from this catalog. Restore its matching catalog before rebuilding.")
    chest = bool(re.search(r"\bclass\s+" + SHOWCASE_CHEST + r"\s*:", config))
    return [skin["id"] for skin in catalog["skins"] if skin["id"] in identities], chest


def prepare(package_root: Path, mods_root: Path, mission: Path, steam_libraries: list[Path]) -> tuple[int, tuple[str, ...]]:
    bundle = package_root / SOURCE_FOLDER
    pack = mods_root / MOD_NAME
    if not bundle.exists() and not bundle.is_symlink():
        if pack.exists() or pack.is_symlink():
            raise ValueError("Azimuth gun-skin source is missing. Restore Custom/GunSkins, or remove both that folder and Worlds/avalon/Mods/@AzimuthSkins to uninstall.")
        remove_economy(mission)
        return 0, ()
    _ordinary(bundle, True)
    _ordinary(package_root / "Custom", True)
    catalog = load_catalog(bundle / "catalog.json")
    choices = skin_settings.ensure_persisted(package_root, catalog)
    selected = skin_settings.selected_catalog(catalog, choices["selected_skin_ids"])
    retained_ids, keep_chest = skin_settings.retained_classes(package_root, catalog)
    installed_ids, installed_chest = installed_classes(pack, catalog)
    supported = set(choices["selected_skin_ids"]) | set(retained_ids) | set(installed_ids)
    supported_ids = [skin["id"] for skin in catalog["skins"] if skin["id"] in supported]
    keep_chest = keep_chest or installed_chest or bool(supported_ids)
    if not supported_ids and not keep_chest:
        disable_pack(pack)
        remove_economy(mission)
        skin_settings.record_applied(package_root, [])
        return 0, ()
    supported_catalog = skin_settings.selected_catalog(catalog, supported_ids)
    # Validate all mission inputs before publishing any new mod files.
    # Historical classes have zero economy targets, just like selected fixed
    # variants. Random choice pools are generated separately from choices only.
    files, notices = economy_files(mission, supported_catalog)
    _publish_signed(bundle / PREFIX, pack, catalog, supported_ids, steam_libraries, package_root)
    if files:
        register_bytes(mission, ECONOMY_SLUG, files)
    else:
        remove_economy(mission)
    skin_settings.record_applied(package_root, choices["selected_skin_ids"],
                                retained_skin_ids=supported_ids, showcase_chest_supported=keep_chest)
    if set(supported_ids) - set(choices["selected_skin_ids"]) or (keep_chest and not supported_ids):
        notices += ("Saved fixed-skin guns and showcase chests remain supported. New random choices use only checked finishes.",)
    return sum(len(weapon["variants"]) for weapon in selected["weapons"]), notices
