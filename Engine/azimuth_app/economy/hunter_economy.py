"""Register Hunter Mods' loot types as an Avalon mission economy extension."""
from pathlib import Path
from xml.etree import ElementTree

from azimuth_app.core.worlds import atomic_text

ECONOMY_FOLDER = "azimuth_hunter_mods"
TYPES_FILENAME = "hunter_types.xml"
LOOT_BALANCE_FOLDER = "azimuth_loot_balance"
LOOT_BALANCE_FILENAME = "types.xml"
ECONOMY_REPAIR_FOLDER = "azimuth_economy_repairs"
TRANSPORT_OVERRIDE_FOLDERS = frozenset({"azimuth_transport_land"})


def is_loot_balance_reference(folder: str, name: str) -> bool:
    return (folder.replace("\\", "/").rstrip("/").casefold() == LOOT_BALANCE_FOLDER
            and name.casefold() == LOOT_BALANCE_FILENAME)


def is_economy_repair_reference(folder: str, name: str) -> bool:
    return (folder.replace("\\", "/").rstrip("/").casefold() == ECONOMY_REPAIR_FOLDER
            and name.casefold() == "types.xml")


def is_transport_reference(folder: str, name: str) -> bool:
    return (folder.replace("\\", "/").rstrip("/").casefold() in TRANSPORT_OVERRIDE_FOLDERS
            and name.casefold() == "types.xml")


def _read_xml(path: Path, expected_root: str, *, preserve_comments: bool = False):
    parser = None
    if preserve_comments:
        parser = ElementTree.XMLParser(
            target=ElementTree.TreeBuilder(insert_comments=True, insert_pis=True)
        )
    try:
        root = ElementTree.parse(path, parser=parser).getroot()
    except (OSError, ElementTree.ParseError) as error:
        raise ValueError(f"Could not read mission economy XML {path.name}: {error}") from error
    if root.tag != expected_root:
        raise ValueError(f"Expected <{expected_root}> in {path.name}.")
    return root


def _type_names(root: ElementTree.Element, *, check_unique: bool = False) -> dict[str, str]:
    names = {}
    for element in root.findall("type"):
        name = element.get("name", "").strip()
        if not name:
            raise ValueError("A loot type is missing its class name.")
        key = name.casefold()
        if check_unique and key in names:
            raise ValueError(f"Hunter Mods lists the loot type {name} twice.")
        names[key] = name
    return names


def _mission_file(mission: Path, folder: str, name: str) -> Path:
    folder_path = Path(folder.replace("\\", "/"))
    name_path = Path(name.replace("\\", "/"))
    if (folder_path.is_absolute() or name_path.is_absolute() or
            ".." in folder_path.parts or ".." in name_path.parts):
        raise ValueError("A mission economy extension points outside its mission folder.")
    result = mission / folder_path / name_path
    if not result.resolve().is_relative_to(mission):
        raise ValueError("A mission economy extension points outside its mission folder.")
    return result


def _write_core(path: Path, root: ElementTree.Element) -> None:
    content = ElementTree.tostring(root, encoding="unicode", xml_declaration=True)
    atomic_text(path, content + "\n")


def remove_economy(mission: Path) -> bool:
    """Unregister only the Azimuth-managed CE file, leaving the XML on disk."""
    mission = mission.resolve()
    core = mission / "cfgeconomycore.xml"
    if not core.is_file() or not (mission / ECONOMY_FOLDER / TYPES_FILENAME).is_file():
        return False
    economy = _read_xml(core, "economycore", preserve_comments=True)
    changed = False
    for extension in economy.findall("ce"):
        if extension.get("folder") != ECONOMY_FOLDER:
            continue
        for file in extension.findall("file"):
            if file.get("name") == TYPES_FILENAME and file.get("type", "").casefold() == "types":
                extension.remove(file)
                changed = True
        if not len(extension):
            economy.remove(extension)
    if changed:
        _write_core(core, economy)
    return changed


def apply_economy(mission: Path, pack_dir: Path) -> int:
    """Add a single dedicated CE reference and leave the main loot table intact."""
    mission = mission.resolve()
    source = pack_dir / "Economy" / TYPES_FILENAME
    source_types = _type_names(_read_xml(source, "types"), check_unique=True)
    if not source_types:
        raise ValueError("The Hunter Mods economy file has no loot types.")

    core = mission / "cfgeconomycore.xml"
    economy = _read_xml(core, "economycore", preserve_comments=True)
    core_changes = False
    target = _mission_file(mission, ECONOMY_FOLDER, TYPES_FILENAME)

    existing_types = [mission / "db" / "types.xml"]
    managed_entries = []
    for extension in economy.findall("ce"):
        folder = extension.get("folder", "")
        for file in extension.findall("file"):
            name = file.get("name", "")
            if file.get("type", "").casefold() != "types" or not name:
                continue
            if (is_loot_balance_reference(folder, name) or is_economy_repair_reference(folder, name)
                    or is_transport_reference(folder, name)):
                continue
            path = _mission_file(mission, folder, name)
            if str(path).casefold() == str(target).casefold():
                managed_entries.append(file)
            elif path.is_file():
                existing_types.append(path)
    if len(managed_entries) > 1:
        raise ValueError("The Avalon mission references the Hunter Mods economy file more than once.")
    for path in existing_types:
        overlap = source_types.keys() & _type_names(_read_xml(path, "types")).keys()
        if overlap:
            names = ", ".join(sorted(source_types[key] for key in overlap))
            raise ValueError(f"Hunter Mods loot types already exist in {path.name}: {names}.")

    if not managed_entries:
        extension = next(
            (item for item in economy.findall("ce") if item.get("folder") == ECONOMY_FOLDER),
            None,
        )
        if extension is None:
            extension = ElementTree.SubElement(economy, "ce", folder=ECONOMY_FOLDER)
            extension.tail = "\n"
        ElementTree.SubElement(extension, "file", name=TYPES_FILENAME, type="types")
        core_changes = True

    new_content = source.read_text(encoding="utf-8-sig")
    previous = target.read_text(encoding="utf-8-sig") if target.is_file() else None
    if previous != new_content:
        target.parent.mkdir(exist_ok=True)
        atomic_text(target, new_content)
    if core_changes:
        try:
            _write_core(core, economy)
        except OSError:
            if previous is None:
                target.unlink(missing_ok=True)
            elif previous != new_content:
                atomic_text(target, previous)
            raise
    return len(source_types)
