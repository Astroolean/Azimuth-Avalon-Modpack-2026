"""Adapt the installed DayZ Horse publisher setup to Avalon's grazing areas.

Workshop 3295021220 requires CF and Survivor Animations. Horse classes and AI
behavior are read from that installed release's server XML/manual, never guessed
from a display name. Publisher assets are not redistributed by this adapter.
"""
from __future__ import annotations

from copy import deepcopy
import math
import os
from pathlib import Path
import re
import stat
from tempfile import NamedTemporaryFile
from xml.etree import ElementTree as ET

from azimuth_app.economy.avalon_economy import MAX_XML_BYTES, register_bytes
from azimuth_app.economy.avalon_loot_sites import load_sites
from azimuth_app.core.backups import source_type
from azimuth_app.core.worlds import atomic_text

HORSE = "3295021220"
ITEMS = ((HORSE, "DayZ Horse"),)
EXTRA_ITEMS = ()
SLUG = "dayz_horse"
TERRITORY_PREFIX = "azimuth_horse_"
HORSE_HERD_TARGET = 6
_HORSE_WORDS = ("horse", "saddle", "bridle", "stable")
_BROKEN_CHERNARUS_BANNER = (
    '<!-- ############### HORSE MOD ####Chernarus made by Fenwichs456##### -->',
    '<!-- modification to add horses to the map, they will appear as any other animal.',
    'part extracted from vanilla territories, add the parts inside the comments in the ',
    'same order inside your wild_horse_territories.xml file.',
    '<!-- ############### HORSE MOD ###################################### -->',
)


class HorseSetupUnavailable(ValueError):
    """Missing author setup evidence; other transportation may still be used."""


def _bytes(root: ET.Element) -> bytes:
    ET.indent(root)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True) + b"\n"


def _restore_bytes(path: Path, data: bytes) -> None:
    with NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(data)
            stream.close()
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def _read(path: Path) -> bytes:
    if not stat.S_ISREG(source_type(path)):
        raise ValueError(f"Expected a regular Horse setup file: {path.name}.")
    with path.open("rb") as stream:
        data = stream.read(MAX_XML_BYTES + 1)
    if len(data) > MAX_XML_BYTES or any(mark in data.upper() for mark in (b"<!DOCTYPE", b"<!ENTITY", b"\0")):
        raise ValueError(f"Unsupported Horse setup XML: {path.name}.")
    return data


def _xml(path: Path, expected: str) -> ET.Element:
    try:
        parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
        root = ET.fromstring(_read(path), parser=parser)
    except ET.ParseError as error:
        raise ValueError(f"Invalid XML in {path.name}: {error}.") from error
    if root.tag != expected:
        raise ValueError(f"Expected <{expected}> in {path.name}.")
    return root


def _publisher_prologue(text: str) -> str:
    """Allow one declaration after a harmless banner, without editing the file.

    Only whitespace, a leading BOM and complete XML comments may precede it.
    Preserve line positions and every actual element. A declaration inside a
    root, comment or CDATA is never searched for or silently removed.
    """
    cursor = 1 if text.startswith('\ufeff') else 0
    while cursor < len(text):
        while cursor < len(text) and text[cursor] in ' \t\r\n':
            cursor += 1
        if text.startswith('<!--', cursor):
            end = text.find('-->', cursor + 4)
            if end < 0:
                return text
            cursor = end + 3
            continue
        if (text.startswith('<?xml', cursor) and cursor + 5 < len(text)
                and text[cursor + 5] in ' \t\r\n'):
            end = text.find('?>', cursor + 5)
            if end < 0:
                return text
            declaration = text[cursor:end + 2]
            # Validate the declaration itself, including encoding/version
            # syntax, before removing it from this in-memory parsing copy.
            ET.fromstring(declaration + '<azimuth_declaration_check/>')
            blank = ''.join(c if c in '\r\n' else ' ' for c in declaration)
            prefix = text[:cursor]
            if prefix.startswith('\ufeff'):
                prefix = ' ' + prefix[1:]
            return prefix + blank + text[end + 2:]
        return text
    return text


def _publisher_xml(text: str) -> ET.Element:
    """Read a complete publisher document or complete sibling XML snippets."""
    body = _publisher_prologue(text)
    try:
        return ET.fromstring(body)
    except ET.ParseError:
        # Preserve the old support for literal sibling snippets. The entire
        # body still has to parse, so malformed or duplicate declarations fail.
        root = ET.fromstring('<fragments>' + body + '</fragments>')
        if (root.text and root.text.strip()) or any(child.tail and child.tail.strip() for child in root):
            raise ET.ParseError('Non-XML text between publisher XML fragments')
        return root


def _publisher_header(path: Path, text: str) -> str:
    """Repair the exact observed publisher comment, never arbitrary markup.

    The uploaded horses_chernarus.xml omits the closing comment marker on
    line 4. Match all five banner lines before adding it to the parsing copy.
    No coordinates, definitions or Workshop file bytes are changed.
    """
    if path.name.casefold() != 'horses_chernarus.xml':
        return text
    lines = text.splitlines(keepends=True)
    if len(lines) < 6 or tuple(line.rstrip('\r\n') for line in lines[:5]) != _BROKEN_CHERNARUS_BANNER:
        return text
    content = lines[3].rstrip('\r\n')
    lines[3] = content + ' -->' + lines[3][len(content):]
    return ''.join(lines)


def _alternate_map_example(path: Path) -> bool:
    """Other maps' coordinate examples are not Avalon setup inputs.

    The author's Chernarus environment file provides the behavior reference.
    Its Graze/Rest/Water example matches Avalon's grazing layout. Other maps
    use different zone schemes; the shipped Takistan example is truncated.
    None of those coordinates are imported or guessed for Avalon.
    """
    return (path.parent.name.casefold() == 'horse_territories'
            and path.suffix.casefold() == '.xml'
            and path.stem.casefold().startswith('horses_')
            and path.name.casefold() != 'horses_chernarus.xml')


def _documents(folder: Path) -> list[tuple[Path, ET.Element]]:
    """Read XML and literal XML snippets in the publisher's installation manual."""
    if not stat.S_ISDIR(source_type(folder)):
        raise ValueError("The installed DayZ Horse Workshop folder is missing.")
    result = []
    for current, subfolders, files in os.walk(folder, followlinks=False):
        parent = Path(current)
        depth = len(parent.relative_to(folder).parts)
        subfolders[:] = [name for name in sorted(subfolders, key=str.casefold)
                         if depth < 6 and name.casefold() not in {"addons", "keys", "storage_1"}
                         and not (parent / name).is_symlink()
                         and not getattr((parent / name).lstat(), "st_file_attributes", 0) & 0x400]
        for name in sorted(files, key=str.casefold):
            path = parent / name
            if path.suffix.casefold() not in {".xml", ".txt", ".md"}:
                continue
            if _alternate_map_example(path):
                continue
            data = _read(path)
            text = data.decode("utf-8-sig", errors="strict" if path.suffix.casefold() == ".xml" else "replace")
            if path.suffix.casefold() == '.xml':
                text = _publisher_header(path, text)
            try:
                result.append((path, ET.fromstring(text)))
            except ET.ParseError as error:
                if path.suffix.casefold() == ".xml":
                    try:
                        result.append((path, _publisher_xml(text)))
                    except ET.ParseError as remaining:
                        raise ValueError(f"Invalid publisher Horse XML in {path.name}: {remaining}.") from remaining
                    continue
                # Publisher manuals can contain several unrelated XML snippets.
                # Parse complete literal elements only; surrounding prose is data.
                pattern = r"<(type|event|territory)(?=\s|>)[^>]*>.*?</\1\s*>"
                for match in re.finditer(pattern, text, re.DOTALL | re.IGNORECASE):
                    try:
                        result.append((path, ET.fromstring(match.group(0))))
                    except ET.ParseError:
                        continue
    return result


def _matching(root: ET.Element, tag: str):
    if root.tag == tag:
        yield root
    yield from root.findall(".//" + tag)


def _canonical(node: ET.Element) -> str:
    return ET.canonicalize(ET.tostring(node, encoding="unicode"), strip_text=True)


def _collect(documents, tag: str, predicate) -> dict[str, ET.Element]:
    found = {}
    for path, root in documents:
        for node in _matching(root, tag):
            if not predicate(node):
                continue
            name = node.get("name", "").strip()
            if not name:
                raise ValueError(f"Unnamed Horse {tag} in {path.name}.")
            key = name.casefold()
            if key in found and _canonical(found[key]) != _canonical(node):
                raise ValueError(f"The installed Horse files contain conflicting {tag} examples for {name}.")
            found[key] = deepcopy(node)
    return found


def _author_setup(folder: Path):
    docs = _documents(folder)
    types = _collect(docs, "type", lambda node: node.find("lifetime") is not None
                     and any(word in node.get("name", "").casefold() for word in _HORSE_WORDS))
    events = _collect(docs, "event", lambda node: node.get("name", "").casefold().startswith("animal")
                      and "horse" in node.get("name", "").casefold() and node.find("children") is not None)
    territories = _collect(docs, "territory", lambda node: node.get("type") == "Herd"
                           and "horse" in node.get("name", "").casefold() and node.get("behavior"))
    if not types or not events or not territories:
        missing = [label for label, value in (("types XML", types), ("horse event XML", events),
                                             ("cfgenvironment Horse behavior snippet", territories)) if not value]
        raise HorseSetupUnavailable("DayZ Horse needs its installed publisher setup files: " + ", ".join(missing) +
                         ". Keep the mod's original Extras/ServerFiles/manual alongside Addons and Keys.")
    for name, event in events.items():
        if name.removeprefix("animal") not in territories:
            raise HorseSetupUnavailable(f"No publisher environment territory matches Horse event {event.get('name')}.")
        children = event.findall("children/child")
        if not children or any(not child.get("type") or "horse" not in child.get("type", "").casefold()
                               or child.get("type", "").casefold().endswith("_base") for child in children):
            raise HorseSetupUnavailable(f"Horse event {event.get('name')} lacks explicit rideable horse variants.")
        # Live animals are spawned by this publisher event. Its actual types
        # file intentionally contains gear/animal products, not live horses;
        # do not require or fabricate ordinary-loot rows for event children.
    animal_classes = {child.get('type').casefold() for event in events.values()
                      for child in event.findall('children/child')}
    spawnables = _collect(docs, "type", lambda node: node.get("name", "").casefold() in (types.keys() | animal_classes)
                          and node.find("lifetime") is None
                          and any(node.find(tag) is not None for tag in ("attachments", "cargo", "damage", "hoarder")))
    return docs, types, events, territories, spawnables


def _set(node: ET.Element, key: str, value) -> None:
    item = node.find(key)
    if item is None:
        item = ET.SubElement(node, key)
    item.text = str(value)


def _supplies(mission: Path, types: dict[str, ET.Element]) -> tuple[str, ...]:
    sites = load_sites(mission)
    usages = tuple(name for name in ("Farm", "Village") if sites is not None
                   and sites.supports(category="tools", usages=(name,)))
    if not usages:
        raise HorseSetupUnavailable("Horse tack needs verified Farm or Village tool-loot placements in Avalon.")
    chosen = []
    for term in ("saddle", "bridle"):
        candidates = [(name, node) for name, node in types.items() if term in name
                      and not any(skip in name for skip in ("armor", "armour", "bag", "proxy", "_base", "kit"))]
        if not candidates:
            raise HorseSetupUnavailable(f"The publisher Horse types XML contains no usable {term} row.")
        for name, node in candidates:
            flags = node.find("flags")
            if flags is not None and flags.get("crafted", "0") not in {"0", "false"}:
                continue
            nominal = max(int(node.findtext("nominal", "0")), 8)
            minimum = min(nominal, max(int(node.findtext("min", "0")), 5))
            restock = min(300, max(0, int(node.findtext("restock", "300"))))
            for key, value in (("nominal", nominal), ("min", minimum), ("restock", restock),
                               ("quantmin", -1), ("quantmax", -1), ("cost", 100)):
                _set(node, key, value)
            if flags is None:
                flags = ET.SubElement(node, "flags")
            flags.attrib.update(count_in_cargo="0", count_in_hoarder="0", count_in_player="0",
                                count_in_map="1", crafted="0", deloot="0")
            for tag in ("category", "usage", "value", "tag"):
                for old in list(node.findall(tag)):
                    node.remove(old)
            ET.SubElement(node, "category", name="tools")
            for usage in usages:
                ET.SubElement(node, "usage", name=usage)
            chosen.append(node.get("name"))
        if not any(term in name.casefold() for name in chosen):
            raise HorseSetupUnavailable(f"No non-crafted publisher Horse {term} is available for ordinary loot.")
    return tuple(chosen)


def _environment(mission: Path, territories: dict[str, ET.Element], documents):
    """Reuse the map author's grazing geometry, preserving horse AI definitions."""
    if not stat.S_ISDIR(source_type(mission / "env")):
        raise ValueError("Avalon environment data must be an ordinary directory.")
    environment_path = mission / "cfgenvironment.xml"
    environment = _xml(environment_path, "env")
    node = environment.find("territories")
    if node is None:
        raise HorseSetupUnavailable("Avalon has no environment territory section for Horse setup.")
    cattle = _xml(mission / "env/cattle_territories.xml", "territory-type")
    grazing = []
    for territory in cattle.findall("territory"):
        zones = territory.findall("zone")
        names = {zone.get("name") for zone in zones}
        if not {"Graze", "Rest", "Water"} <= names:
            continue
        for zone in zones:
            for key in ("x", "z", "r"):
                value = float(zone.get(key, "nan"))
                if not math.isfinite(value) or value <= 0:
                    raise ValueError("Avalon cattle territories contain an invalid grazing coordinate.")
        grazing.append(deepcopy(territory))
    if not grazing:
        raise HorseSetupUnavailable("Avalon has no complete cattle grazing/rest/water territories for horses.")
    # The publisher must use the same AI zone vocabulary. Coordinate reuse is
    # insufficient if a future release changes the horse behavior territory format.
    publisher_zones = {zone.get("name") for path, root in documents
                       if root.tag == "territory-type" and "horse" in str(path).casefold()
                       for zone in root.findall("territory/zone")}
    if not publisher_zones or not publisher_zones <= {"Graze", "Rest", "Water"} or "Graze" not in publisher_zones:
        raise HorseSetupUnavailable("The installed Horse territory files must document Graze/Rest/Water zones before Avalon coordinates can be adapted.")
    generated = {}
    for key, original in sorted(territories.items()):
        if not re.fullmatch(r"[a-z0-9_]+", key):
            raise ValueError("Unsupported publisher Horse territory name.")
        definition = deepcopy(original)
        files = definition.findall("file")
        if len(files) != 1 or not files[0].get("usable"):
            raise HorseSetupUnavailable(f"Horse territory {original.get('name')} needs one publisher territory file reference.")
        stem = TERRITORY_PREFIX + key
        files[0].set("usable", stem)
        existing = [item for item in node.findall("territory") if item.get("name", "").casefold() == key]
        if existing and (len(existing) != 1 or not any(f.get("usable") == stem for f in existing[0].findall("file"))):
            raise HorseSetupUnavailable(f"Avalon already has a custom {original.get('name')} territory; it was not overwritten.")
        for old in existing:
            node.remove(old)
        node.append(definition)
        path = "env/" + stem + ".xml"
        if not any(item.get("path") == path for item in node.findall("file")):
            node.insert(0, ET.Element("file", path=path))
        output = ET.Element("territory-type")
        output.extend(deepcopy(grazing))
        generated[mission / path] = _bytes(output)
    generated[environment_path] = _bytes(environment)
    return generated, len(grazing)


def _event_positions(mission: Path, events: dict[str, ET.Element], documents) -> dict[Path, bytes]:
    """Register the author's empty animal-event entry, never foreign positions."""
    position_docs = [(path, root) for path, root in documents if root.tag == 'eventposdef']
    entries = _collect(position_docs, 'event', lambda node: node.get('name', '').casefold() in events)
    if not entries:
        return {}
    if any(len(node) or (node.text and node.text.strip()) for node in entries.values()):
        raise HorseSetupUnavailable('Publisher Horse event positions need an Avalon-specific adaptation; '
                                    'coordinates from another map were not imported.')
    path = mission / 'cfgeventspawns.xml'
    root = _xml(path, 'eventposdef')
    changed = False
    for key, entry in sorted(entries.items()):
        existing = [node for node in root.findall('event') if node.get('name', '').casefold() == key]
        if len(existing) > 1:
            raise ValueError(f'Avalon has duplicate {entry.get("name")} event-position entries.')
        if not existing:
            root.append(deepcopy(entry))
            changed = True
        # Existing entries, including user-defined positions, remain untouched.
    return {path: _bytes(root)} if changed else {}


def prepare(mission: Path, profiles: Path, mods: dict[str, Path]) -> tuple[str, ...]:
    if HORSE not in mods:
        return ()
    mission = mission.resolve()
    try:
        documents, types, events, territories, spawnables = _author_setup(mods[HORSE])
        supplies = _supplies(mission, types)
        generated, territory_count = _environment(mission, territories, documents)
        generated.update(_event_positions(mission, events, documents))
        type_root, event_root = ET.Element("types"), ET.Element("events")
        type_root.extend(types[name] for name in sorted(types))
        for name, event in sorted(events.items()):
            _set(event, "active", 1)
            _set(event, "nominal", max(1, HORSE_HERD_TARGET // len(events)))
            event_root.append(event)
        old = {}
        try:
            for path, payload in generated.items():
                old[path] = _read(path) if path.exists() or path.is_symlink() else None
                if old[path] != payload:
                    atomic_text(path, payload.decode("utf-8"))
            payloads = {
                "types": ("horse_types.xml", _bytes(type_root)),
                "events": ("horse_events.xml", _bytes(event_root)),
            }
            root = ET.Element("spawnabletypes")
            root.extend(spawnables[name] for name in sorted(spawnables))
            payloads["spawnabletypes"] = ("horse_spawnabletypes.xml", _bytes(root))
            register_bytes(mission, SLUG, payloads)
        except (OSError, ValueError):
            for path, payload in old.items():
                if payload is None:
                    path.unlink(missing_ok=True)
                else:
                    _restore_bytes(path, payload)
            raise
    except HorseSetupUnavailable as error:
        return (f"NEEDS SETUP: DayZ Horse. {error} No new horse spawn rules were applied; "
                "existing mission files were left unchanged and other transport can still load.",)
    except (OSError, ValueError, ET.ParseError, UnicodeError) as error:
        raise ValueError(f"Horse transport setup could not be completed: {error}") from error
    return (f"Horse transport prepared from installed publisher files: {len(events)} event(s), "
            f"{territory_count} Avalon grazing areas, saddle/bridle loot ({', '.join(supplies)}). "
            "Avalon uses its own grazing coordinates; other maps' coordinate examples are not imported. "
            "Use an apple to lure a horse; claim it by attaching a saddle, then add its bridle. "
            "Riding and grazing clearance still require an in-game check.",)
