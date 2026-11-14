"""Register Avalon add-ons without replacing the creator's economy files."""

import re
import stat
from pathlib import Path
from xml.etree import ElementTree

from azimuth_app.core.backups import source_type
from azimuth_app.economy.hunter_economy import (
    _mission_file, _read_xml, _type_names, _write_core,
    is_economy_repair_reference, is_loot_balance_reference, is_transport_reference,
)
from azimuth_app.core.worlds import atomic_text


ROOTS = {"types": "types", "events": "events", "spawnabletypes": "spawnabletypes", "globals": "variables"}
SAFE_SLUG = re.compile(r"[a-z0-9]+(?:[-_][a-z0-9]+)*\Z")
SAFE_NAME = re.compile(r"[A-Za-z0-9_-]+\.xml\Z", re.IGNORECASE)
MAX_XML_BYTES = 16 * 1024 * 1024


def register(mission: Path, slug: str, files: dict[str, Path]) -> None:
    payloads = {}
    for kind, path in files.items():
        if not stat.S_ISREG(source_type(path)):
            raise ValueError(f"Expected an Avalon economy file at {path.name}.")
        with path.open("rb") as stream:
            payloads[kind] = (path.name, stream.read(MAX_XML_BYTES + 1))
    register_bytes(mission, slug, payloads)


def register_bytes(mission: Path, slug: str, files: dict[str, tuple[str, bytes]]) -> None:
    if not files:
        return
    if not SAFE_SLUG.fullmatch(slug):
        raise ValueError("Invalid Avalon economy folder name.")

    mission = mission.resolve()
    core = mission / "cfgeconomycore.xml"
    source_type(core)
    economy = _read_xml(core, "economycore", preserve_comments=True)
    folder = f"azimuth_workshop/{slug}"
    matches = [node for node in economy.findall("ce") if node.get("folder", "").casefold() == folder]
    if len(matches) > 1:
        raise ValueError(f"The Avalon mission references {folder} twice.")
    extension = matches[0] if matches else ElementTree.Element("ce", folder=folder)

    inputs = {}
    for kind, (name, content) in files.items():
        if kind not in ROOTS or not SAFE_NAME.fullmatch(name) or len(content) > MAX_XML_BYTES:
            raise ValueError(f"Invalid Avalon economy file for {slug}: {name}.")
        payload = content.decode("utf-8-sig")
        if "<!DOCTYPE" in payload.upper() or "<!ENTITY" in payload.upper():
            raise ValueError(f"Unsupported XML declarations in {name}.")
        try:
            root = ElementTree.fromstring(payload)
        except ElementTree.ParseError as error:
            raise ValueError(f"Invalid Avalon economy XML {name}: {error}.") from error
        if root.tag != ROOTS[kind]:
            raise ValueError(f"Expected <{ROOTS[kind]}> in {name}.")
        # Keep one stable filename per CE type across publisher updates.
        target = _mission_file(mission, folder, f"{kind}.xml")
        inputs[kind] = (target, payload)
        if kind == "types":
            _check_type_collisions(mission, economy, target, _type_names(root, check_unique=True))

    changed = not matches
    for kind, (target, _) in inputs.items():
        matching = [node for node in extension.findall("file") if node.get("name") == target.name]
        if len(matching) > 1 or any(node.get("type") != kind for node in matching):
            raise ValueError(f"Conflicting economy references for {folder}/{target.name}.")
        if not matching:
            ElementTree.SubElement(extension, "file", name=target.name, type=kind)
            changed = True
    if not matches:
        extension.tail = "\n"
        economy.append(extension)

    previous = {}
    try:
        for target, payload in inputs.values():
            for parent in (target.parent.parent, target.parent):
                parent.mkdir(exist_ok=True)
                if not stat.S_ISDIR(source_type(parent)):
                    raise OSError(f"Expected an economy folder at {parent.name}.")
            if target.exists() or target.is_symlink():
                if not stat.S_ISREG(source_type(target)):
                    raise OSError(f"Expected an economy file at {target.name}.")
            original = target.read_text(encoding="utf-8-sig") if target.is_file() else None
            if payload != original:
                atomic_text(target, payload)
                previous[target] = original
        if changed:
            _write_core(core, economy)
    except OSError:
        for target, original in previous.items():
            if original is None:
                target.unlink(missing_ok=True)
            else:
                atomic_text(target, original)
        raise


def _check_type_collisions(mission: Path, economy, target: Path, names: dict[str, str]) -> None:
    existing = [mission / "db" / "types.xml"]
    for extension in economy.findall("ce"):
        folder = extension.get("folder", "")
        for node in extension.findall("file"):
            name = node.get("name", "")
            if (node.get("type", "").casefold() == "types" and name
                    and not is_loot_balance_reference(folder, name)
                    and not is_economy_repair_reference(folder, name)
                    and not is_transport_reference(folder, name)):
                path = _mission_file(mission, folder, name)
                if str(path).casefold() != str(target).casefold() and path.is_file():
                    existing.append(path)
    for path in existing:
        duplicate = names.keys() & _type_names(_read_xml(path, "types")).keys()
        if duplicate:
            label = ", ".join(sorted(names[name] for name in duplicate))
            raise ValueError(f"Avalon loot types already exist in {path.name}: {label}.")
