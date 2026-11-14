"""Read the layered Central Economy configuration for an Avalon mission."""

from collections.abc import Iterable
from copy import deepcopy
from dataclasses import dataclass, field
import math
from pathlib import Path, PurePosixPath
import stat
from xml.etree import ElementTree as ET

from azimuth_app.economy.avalon_economy import MAX_XML_BYTES
from azimuth_app.core.backups import source_type
from azimuth_app.economy.hunter_economy import _mission_file
from azimuth_app.economy.mission_xml import parse_mission_xml


@dataclass(frozen=True)
class TypeLayer:
    path: Path
    element: ET.Element


@dataclass(frozen=True)
class LootType:
    name: str
    element: ET.Element
    layers: tuple[TypeLayer, ...]
    nominal: int | None
    events: tuple[str, ...]

    @property
    def source(self) -> Path:
        return self.layers[-1].path

    @property
    def nominal_source(self) -> Path | None:
        return next((layer.path for layer in reversed(self.layers)
                     if layer.element.find("nominal") is not None), None)

    @property
    def natural(self) -> bool:
        return self.spawn_kind == "natural"

    @property
    def spawn_kind(self) -> str:
        flags = self.element.find("flags")
        flags = flags.attrib if flags is not None else {}
        if flags.get("crafted", "0").strip().casefold() in {"1", "true"}:
            return "crafted"
        if flags.get("deloot", "0").strip().casefold() in {"1", "true"}:
            return "event"
        if self.nominal is not None and self.nominal > 0:
            return "natural"
        if self.events:
            return "event"
        return "zero" if self.nominal == 0 else "unknown"


@dataclass(frozen=True)
class ContainedSource:
    """A positive configured cargo or attachment relationship."""

    parent: str
    path: Path
    kind: str
    preset: str | None = None
    preset_path: Path | None = None


@dataclass(frozen=True)
class LootEconomy:
    types: dict[str, LootType]
    type_files: tuple[Path, ...]
    warnings: tuple[str, ...]
    events: dict[str, tuple[str, ...]]
    contained: dict[str, tuple[ContainedSource, ...]] = field(default_factory=dict)
    contained_warnings: tuple[str, ...] = ()

    def gaps(self, expected: Iterable[str]) -> dict[str, str]:
        """Describe classnames without positive ordinary loot targets."""
        gaps = {}
        for name in expected:
            entry = self.types.get(name.casefold())
            if entry is None:
                gaps[name] = "event" if name.casefold() in self.events else "missing"
            elif not entry.natural:
                gaps[name] = entry.spawn_kind
        return gaps


def _relative_folder(folder: str) -> str:
    return PurePosixPath(folder.replace("\\", "/")).as_posix().casefold()


def _xml(mission: Path, path: Path, tag: str) -> ET.Element:
    # Check each path prefix below the mission, including nested CE folders.
    relative = path.relative_to(mission)
    folder = mission
    for part in relative.parts[:-1]:
        folder /= part
        if not stat.S_ISDIR(source_type(folder)):
            raise ValueError(f"Expected an economy directory at {folder.name}.")
    if not stat.S_ISREG(source_type(path)):
        raise ValueError(f"Expected an economy XML file at {path.name}.")
    with path.open("rb") as stream:
        data = stream.read(MAX_XML_BYTES + 1)
    if (len(data) > MAX_XML_BYTES or b"\0" in data
            or any(mark in data.upper() for mark in (b"<!DOCTYPE", b"<!ENTITY"))):
        raise ValueError("Unsupported economy XML size, encoding, or declaration.")
    root = parse_mission_xml(data, relative.as_posix())
    if root.tag != tag:
        raise ValueError(f"Expected <{tag}> as the root element.")
    return root


def _references(mission: Path, excluded: set[str], warnings: list[str],
                contained_warnings: list[str]) -> dict[str, list[Path]]:
    sources = {"types": [mission / "db" / "types.xml"], "events": [], "spawnabletypes": []}
    for kind, relative in (("events", "db/events.xml"), ("spawnabletypes", "cfgspawnabletypes.xml")):
        path = mission / relative
        if path.exists() or path.is_symlink():
            sources[kind].append(path)
    try:
        core = _xml(mission, mission / "cfgeconomycore.xml", "economycore")
    except (OSError, ET.ParseError, ValueError) as error:
        warnings.append(f"Cannot read cfgeconomycore.xml: {error}")
        return sources
    for block in core.findall("ce"):
        folder = block.get("folder", "")
        if _relative_folder(folder) in excluded:
            continue
        for reference in block.findall("file"):
            kind = reference.get("type", "").casefold()
            if kind not in sources:
                continue
            problems = contained_warnings if kind == "spawnabletypes" else warnings
            name = reference.get("name", "")
            try:
                if not name or ":" in folder or ":" in name:
                    raise ValueError("Invalid folder or file name.")
                path = _mission_file(mission, folder, name)
            except (OSError, RuntimeError, ValueError) as error:
                problems.append(f"Cannot resolve {kind} reference {folder}/{name}: {error}")
                continue
            if path in sources[kind]:
                problems.append(f"Duplicate {kind} reference {path.relative_to(mission).as_posix()}.")
            else:
                sources[kind].append(path)
    return sources


def _load_layers(mission: Path, sources: Iterable[Path], tag: str,
                 warnings: list[str]) -> dict[str, list[TypeLayer]]:
    layers: dict[str, list[TypeLayer]] = {}
    for path in sources:
        label = path.relative_to(mission).as_posix()
        try:
            root = _xml(mission, path, f"{tag}s")
        except (OSError, ET.ParseError, ValueError) as error:
            warnings.append(f"Cannot read {label}: {error}")
            continue
        seen = set()
        for entry in root.findall(tag):
            name = entry.get("name", "").strip()
            key = name.casefold()
            if not name or key in seen:
                warnings.append(f"Missing or duplicate {tag} name {name!r} in {label}.")
                continue
            seen.add(key)
            layers.setdefault(key, []).append(TypeLayer(path, entry))
    return layers


def _effective(layers: list[TypeLayer], *, event: bool = False) -> ET.Element:
    merged = deepcopy(layers[0].element)
    for layer in layers[1:]:
        entry = layer.element
        changed = {child.tag for child in entry}
        children = merged.find("children") if event and "children" in changed else None
        if children is not None:
            changed.remove("children")
            existing = {child.get("type", "").casefold(): child for child in children.findall("child")}
            for child in entry.findall("./children/child"):
                previous = existing.get(child.get("type", "").casefold())
                if previous is None:
                    children.append(deepcopy(child))
                else:
                    previous.attrib.update(child.attrib)
        # Partial overrides retain absent fields. Repeated usage, tag, and
        # value entries replace the old set for that field as a group.
        for child in list(merged):
            if child.tag in changed:
                merged.remove(child)
        merged.extend(deepcopy(child) for child in entry if child.tag in changed)
    return merged


def _active_event_children(layers: dict[str, list[TypeLayer]]) -> dict[str, tuple[str, ...]]:
    names: dict[str, list[str]] = {}
    for group in layers.values():
        event = _effective(group, event=True)
        if (event.findtext("active") or "").strip().casefold() not in {"1", "true"}:
            continue
        try:
            if int(event.findtext("nominal", "0")) <= 0:
                continue
        except ValueError:
            continue  # The verifier reports invalid event quantities separately.
        custom = event.findtext("limit", "").strip().casefold() == "custom"
        for child in event.findall("./children/child"):
            name = child.get("type", "").strip().casefold()
            try:
                # Infected custom-limit min values are distribution weights;
                # they can legitimately have max=0. Other events use max.
                enabled = int(child.get("min" if custom else "max", "0")) > 0
            except ValueError:
                enabled = False
            if name and enabled:
                names.setdefault(name, []).append(event.get("name", ""))
    return {name: tuple(dict.fromkeys(events)) for name, events in names.items()}


_CONTAINED_GROUPS = {"cargo": "cargo", "attachments": "attachment"}


def _group_signature(groups: list[ET.Element]) -> tuple:
    return tuple((tuple(sorted(group.attrib.items())),
                  tuple(tuple(sorted(item.attrib.items())) for item in group.findall("item")))
                 for group in groups)


def _positive_chance(element: ET.Element) -> bool:
    try:
        chance = float(element.get("chance", "1"))
    except ValueError:
        return False
    return math.isfinite(chance) and 0 < chance <= 1


def _load_random_presets(mission: Path, warnings: list[str]) -> dict[tuple[str, str], ET.Element]:
    path = mission / "cfgrandompresets.xml"
    if not path.exists() and not path.is_symlink():
        return {}
    try:
        root = _xml(mission, path, "randompresets")
    except (OSError, ET.ParseError, ValueError) as error:
        warnings.append(f"Cannot read cfgrandompresets.xml: {error}")
        return {}
    presets = {}
    ambiguous = set()
    for group in root:
        name = group.get("name", "").strip()
        if group.tag not in _CONTAINED_GROUPS or not name:
            continue
        key = (group.tag, name.casefold())
        if key in presets:
            ambiguous.add(key)
        else:
            presets[key] = group
    for kind, name in sorted(ambiguous):
        del presets[kind, name]
        warnings.append(f"Ambiguous {kind} preset {name} in cfgrandompresets.xml.")
    return presets


def _contained_sources(mission: Path, paths: list[Path], warnings: list[str]
                       ) -> dict[str, tuple[ContainedSource, ...]]:
    if not paths:
        return {}
    presets = _load_random_presets(mission, warnings)
    effective = {}
    preset_path = mission / "cfgrandompresets.xml"
    for path in paths:
        label = path.relative_to(mission).as_posix()
        try:
            root = _xml(mission, path, "spawnabletypes")
        except (OSError, ET.ParseError, ValueError) as error:
            warnings.append(f"Cannot read {label}: {error}")
            continue
        file_groups = {}
        duplicate_groups = set()
        for parent in root.findall("type"):
            name = parent.get("name", "").strip()
            if not name:
                warnings.append(f"Missing parent type name in {label}.")
                continue
            for tag in _CONTAINED_GROUPS:
                groups = parent.findall(tag)
                if groups:
                    key = (name.casefold(), tag)
                    if key in file_groups:
                        if _group_signature(groups) != _group_signature(file_groups[key][2]):
                            duplicate_groups.add(key)
                        continue
                    file_groups[key] = (name, path, groups)
        for key in sorted(duplicate_groups):
            name, _, _ = file_groups[key]
            file_groups[key] = (name, path, [])
            warnings.append(f"Ambiguous {key[1]} for {name} in {label}.")
        # A later CE layer replaces the parent’s corresponding cargo or
        # attachment groups, including explicit zero-chance or empty groups.
        effective.update(file_groups)

    sources: dict[str, set[ContainedSource]] = {}
    unresolved = set()
    for (_, tag), (name, path, groups) in effective.items():
        for group in groups:
            if not _positive_chance(group):
                continue
            preset_name = group.get("preset", "").strip()
            if preset_name:
                preset = presets.get((tag, preset_name.casefold()))
                if preset is None:
                    unresolved.add((tag, preset_name))
                    continue
                if not _positive_chance(preset):
                    continue
                group = preset
            source = ContainedSource(name, path, _CONTAINED_GROUPS[tag], preset_name or None,
                                     preset_path if preset_name else None)
            for item in group.findall("item"):
                child = item.get("name", "").strip().casefold()
                if child and _positive_chance(item):
                    sources.setdefault(child, set()).add(source)
    for tag, preset in sorted(unresolved):
        warnings.append(f"Cannot resolve {tag} preset {preset} in cfgrandompresets.xml.")
    return {name: tuple(sorted(references, key=lambda ref: (ref.parent.casefold(), ref.kind,
                                                           str(ref.path), ref.preset or "")))
            for name, references in sources.items()}


def read_economy(mission: Path, *, exclude_folders: Iterable[str] = ()) -> LootEconomy:
    """Resolve CE fields in base-then-reference order without changing files.

    Natural means a positive configured nominal with ordinary spawn flags.
    CE placement, available class definitions, and actual spawn counts need
    verification on the game server.
    """
    mission = mission.resolve()
    warnings: list[str] = []
    contained_warnings: list[str] = []
    sources = _references(mission, {_relative_folder(folder) for folder in exclude_folders},
                          warnings, contained_warnings)
    type_layers = _load_layers(mission, sources["types"], "type", warnings)
    event_layers = _load_layers(mission, sources["events"], "event", warnings)
    events = _active_event_children(event_layers)
    types = {}
    for key, layers in type_layers.items():
        element = _effective(layers)
        name = element.get("name", "").strip()
        nominal_text = element.findtext("nominal")
        try:
            nominal = int(nominal_text) if nominal_text is not None else None
            if nominal is not None and nominal < 0:
                raise ValueError
        except ValueError:
            warnings.append(f"Invalid nominal for {name}.")
            nominal = None
        types[key] = LootType(name, element, tuple(layers), nominal, events.get(key, ()))
    contained = _contained_sources(mission, sources["spawnabletypes"], contained_warnings)
    return LootEconomy(types, tuple(sources["types"]), tuple(warnings), events, contained, tuple(contained_warnings))
