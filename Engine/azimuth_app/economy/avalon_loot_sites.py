"""Find mission-local evidence for DayZ container and dispatch loot placement."""

from dataclasses import dataclass, field
from math import isfinite
from pathlib import Path
import stat
from xml.etree import ElementTree

from azimuth_app.core.backups import source_type
from azimuth_app.core.server_workspace import MAX_FILE_BYTES


@dataclass(frozen=True)
class _Container:
    categories: frozenset[str]
    tags: frozenset[str]


@dataclass(frozen=True)
class _Group:
    name: str
    usages: frozenset[str]
    values: frozenset[str]
    containers: tuple[_Container, ...]


@dataclass(frozen=True)
class LootSites:
    """Placed prototype groups with enabled containers and valid loot points."""

    _groups: tuple[_Group, ...]
    _proxy_groups: dict[str, tuple[str, ...]] = field(default_factory=dict)
    _aliases: dict[tuple[str, str], tuple[str, ...]] = field(default_factory=dict)
    ambiguous_groups: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def matching_groups(self, *, category: str | None = None, usages: tuple[str, ...] = (),
                        tags: tuple[str, ...] = (), values: tuple[str, ...] = ()) -> tuple[str, ...]:
        """Return building classes with evidence for all requested filters.

        Alternatives within a filter match any requested name. Values must be
        explicitly present on the prototype; map-region loot tiers are outside
        this check. Item size, actual entity classes, and live CE occupancy
        require a DayZ runtime.
        """
        return tuple(group.name for group in self._groups
                     if (not usages or not group.usages.isdisjoint(usages))
                     and (not values or not group.values.isdisjoint(values))
                     and any((category is None or category in container.categories)
                             and (not tags or not container.tags.isdisjoint(tags))
                             for container in group.containers))

    def supports(self, *, category: str | None = None, usages: tuple[str, ...] = (),
                 tags: tuple[str, ...] = (), values: tuple[str, ...] = ()) -> bool:
        return bool(self.matching_groups(category=category, usages=usages, tags=tags, values=values))

    def proxy_groups(self, name: str) -> tuple[str, ...]:
        """Return placed group classes with a dispatch proxy of the given type."""
        return self._proxy_groups.get(name, ())

    def flags(self, node: ElementTree.Element, kind: str) -> tuple[str, ...]:
        return loot_flags(node, kind, self._aliases)


def _xml(mission: Path, filename: str, root_tag: str) -> ElementTree.Element:
    candidates = [item for item in mission.iterdir() if item.name.casefold() == filename]
    if len(candidates) != 1 or not stat.S_ISREG(source_type(candidates[0])):
        raise ValueError(f"Expected one mission file named {filename}.")
    with candidates[0].open("rb") as source:
        data = source.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise ValueError(f"Mission file {filename} is too large.")
    payload = data.decode("utf-8-sig")
    if "<!DOCTYPE" in payload.upper() or "<!ENTITY" in payload.upper():
        raise ValueError(f"Unsupported XML declarations in {filename}.")
    root = ElementTree.fromstring(payload)
    if root.tag != root_tag:
        raise ValueError(f"Expected <{root_tag}> in {filename}.")
    return root


def loot_flags(node: ElementTree.Element, kind: str,
               aliases: dict[tuple[str, str], tuple[str, ...]] | None = None) -> tuple[str, ...]:
    flags = []
    for item in node.findall(kind):
        if name := item.get("name"):
            flags.append(name)
        elif alias := item.get("user"):
            flags.extend(aliases.get((kind, alias), (alias,)) if aliases is not None else (alias,))
    return tuple(dict.fromkeys(flags))


def _user_aliases(mission: Path) -> dict[tuple[str, str], tuple[str, ...]]:
    try:
        root = _xml(mission, "cfglimitsdefinitionuser.xml", "user_lists")
    except (OSError, ValueError, ElementTree.ParseError):
        return {}
    return {(kind, name): loot_flags(alias, kind)
            for kind in ("usage", "value") for alias in root.findall(f"{kind}flags/user")
            if (name := alias.get("name"))}


def _has_position(node: ElementTree.Element) -> bool:
    coords = node.get("pos", "").split()
    if len(coords) != 3:
        return False
    try:
        return all(isfinite(float(coord)) for coord in coords)
    except ValueError:
        return False


def _enabled(node: ElementTree.Element, default: str) -> bool:
    try:
        return int(node.get("lootmax", default)) > 0
    except ValueError:
        return False


def load_sites(mission: Path) -> LootSites | None:
    """Index the current mission files. Missing or invalid evidence is unknown."""
    try:
        if not stat.S_ISDIR(source_type(mission)):
            return None
        prototype = _xml(mission, "mapgroupproto.xml", "prototype")
        placements = _xml(mission, "mapgrouppos.xml", "map")
    except (OSError, ValueError, ElementTree.ParseError):
        return None

    placed = {name for item in placements.findall("group")
              if (name := item.get("name")) and _has_position(item)}
    defaults = {item.get("name"): item.get("lootmax", "1")
                for item in prototype.findall("defaults/default")}
    aliases = _user_aliases(mission)
    groups = {}
    ambiguous = set()
    seen = set()
    proxy_groups: dict[str, set[str]] = {}
    for group in prototype.findall("group"):
        name = group.get("name", "")
        if name not in placed:
            continue
        # Duplicate prototypes have engine-dependent precedence. Exclude the
        # affected building instead of throwing away every other verified
        # placement on the map. Track disabled copies too: either copy might
        # be the one the game uses. The source mission remains untouched.
        if name in seen:
            ambiguous.add(name)
            groups.pop(name, None)
            for found in proxy_groups.values():
                found.discard(name)
            continue
        seen.add(name)
        if not _enabled(group, defaults.get("group", "1")):
            continue
        containers = tuple(_Container(frozenset(loot_flags(container, "category")),
                                      frozenset(loot_flags(container, "tag")))
                           for container in group.findall("container")
                           if _enabled(container, defaults.get("container", "1"))
                           and any(_has_position(point) for point in container.findall("point")))
        groups[name] = _Group(name, frozenset(loot_flags(group, "usage", aliases)),
                              frozenset(loot_flags(group, "value", aliases)), containers)
        for dispatch in group.findall("dispatch"):
            if not _enabled(dispatch, defaults.get("dispatch", "1")):
                continue
            for proxy in dispatch.iter("proxy"):
                item_type = proxy.get("type")
                if item_type and _has_position(proxy):
                    proxy_groups.setdefault(item_type, set()).add(name)
    return LootSites(tuple(groups[name] for name in sorted(groups) if groups[name].containers),
                     {name: tuple(sorted(found)) for name, found in proxy_groups.items() if found}, aliases,
                     tuple(sorted(ambiguous)),
                     tuple(f"Placement not certified for duplicate prototype {name}."
                           for name in sorted(ambiguous)))
