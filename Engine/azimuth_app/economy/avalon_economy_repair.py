"""Repair known Avalon CE defects in a generated layer, preserving publisher XML."""

from copy import deepcopy
from pathlib import Path
import stat
from xml.etree import ElementTree as ET

from azimuth_app.economy.avalon_loot_audit import (
    _effective, _load_layers, _load_random_presets, _references, _xml, TypeLayer, read_economy,
)
from azimuth_app.economy.avalon_loot_sites import load_sites
from azimuth_app.core.backups import source_type
from azimuth_app.economy.hunter_economy import LOOT_BALANCE_FOLDER, _read_xml, _write_core
from azimuth_app.core.worlds import atomic_text


FOLDER = "azimuth_economy_repairs"
EXCLUDED = {FOLDER, LOOT_BALANCE_FOLDER}
WHEELS = ("bicycle_wheel", "ladybicycle_wheel")


def _limits(mission: Path) -> dict[str, set[str]]:
    root = _xml(mission, mission / "cfglimitsdefinition.xml", "lists")
    return {kind: {node.get("name", "") for node in root.findall(route)}
            for kind, route in (("category", "categories/category"), ("value", "valueflags/value"))}


def _positive_vehicle_child(mission: Path, classname: str) -> bool:
    warnings: list[str] = []
    contained_warnings: list[str] = []
    paths = _references(mission, EXCLUDED, warnings, contained_warnings)["events"]
    layers = _load_layers(mission, paths, "event", warnings)
    if warnings:
        return False
    for event_layers in layers.values():
        event = _effective(event_layers, event=True)
        if (not event.get("name", "").casefold().startswith("vehicle")
                or event.findtext("active", "").strip().casefold() not in {"1", "true"}):
            continue
        try:
            if int(event.findtext("nominal", "0")) <= 0:
                continue
            if any(child.get("type", "").casefold() == classname.casefold()
                   and int(child.get("max", "0")) > 0 for child in event.findall("children/child")):
                return True
        except ValueError:
            continue
    return False


def _types(mission: Path, economy, limits: dict[str, set[str]], notices: list[str]) -> ET.Element:
    output = ET.Element("types")
    sites = load_sites(mission)
    for key in WHEELS:
        item = economy.types.get(key)
        if item is None:
            continue
        category = item.element.find("category")
        if (category is not None and category.get("name") == "vehiclesparts"
                and "vehiclesparts" not in limits["category"] and "tools" in limits["category"]
                and sites is not None and sites.supports(category="tools", usages=sites.flags(item.element, "usage"),
                                                        tags=sites.flags(item.element, "tag"))):
            node = ET.SubElement(output, "type", name=item.name)
            ET.SubElement(node, "category", name="tools")
            notices.append(f"Repaired {item.name}: undefined vehiclesparts category now uses verified tools placements.")
    # The source mission equips the active desert RZR with this exact class,
    # but omits its CE persistence/spare row. Do not infer arbitrary mod names.
    desert_key = "rt_rzr_wheel_desert"
    sibling = economy.types.get("rt_rzr_wheel")
    attached = any(source.kind == "attachment" and source.parent.casefold() == "rtrzr_desert"
                   for source in economy.contained.get(desert_key, ()))
    if (desert_key not in economy.types and sibling is not None and attached
            and sites is not None and "tools" in limits["category"]
            and sites.flags(sibling.element, "category") == ("tools",)
            and sites.supports(category="tools", usages=sites.flags(sibling.element, "usage"),
                               tags=sites.flags(sibling.element, "tag"))
            and _positive_vehicle_child(mission, "RTrzr_Desert")):
        node = deepcopy(sibling.element)
        node.set("name", "RT_rzr_wheel_desert")
        output.append(node)
        notices.append("Registered missing RT_rzr_wheel_desert from the active desert vehicle's authored attachment and sibling wheel rules.")
    dog = economy.types.get("dog_gasmask")
    if dog is not None:
        values = dog.element.findall("value")
        names = {node.get("name") for node in values}
        if ({"Tier3", "Tier4"} <= names and "Tier3" in limits["value"]
                and "Tier4" not in limits["value"]):
            node = ET.SubElement(output, "type", name=dog.name)
            node.extend(deepcopy(value) for value in values if value.get("name") != "Tier4")
            notices.append("Repaired dog_gasmask: removed undefined Tier4; its existing Tier3 route remains.")
    crossbow = economy.types.get("crossbow_black")
    siblings = [economy.types.get("crossbow_" + suffix) for suffix in ("autumn", "summer", "wood")]
    if (crossbow is not None and crossbow.element.findtext("quantmin", "").strip() == "80"
            and crossbow.element.findtext("quantmax", "").strip() == "0"
            and all(item is not None and item.element.findtext("quantmin", "").strip() == "30"
                    and item.element.findtext("quantmax", "").strip() == "80" for item in siblings)):
        node = ET.SubElement(output, "type", name=crossbow.name)
        ET.SubElement(node, "quantmin").text = "30"
        ET.SubElement(node, "quantmax").text = "80"
        notices.append("Repaired Crossbow_Black: reversed quantity range 80..0 now matches sibling crossbows at 30..80.")
    # These source rows request ordinary loot in unused Lunapark/Oil groups.
    # Add only map-verified routes, retaining their counts, tiers and flags.
    # HipPack_Black supplies the same-family Village route; SVD supplies the
    # weapon's Military route. The card already has military convoy cargo.
    for key, donor_name, desired in (("hippack_party", "hippack_black", "Village"),
                                     ("svd_wooden", "svd", "Military"),
                                     ("punchedcard", "scientificbriefcase", "Military")):
        item, donor = economy.types.get(key), economy.types.get(donor_name)
        if item is None or donor is None or not item.natural or sites is None:
            continue
        categories = sites.flags(item.element, "category")
        category = categories[0] if categories else None
        usages = sites.flags(item.element, "usage")
        tags = sites.flags(item.element, "tag")
        if (desired not in sites.flags(donor.element, "usage")
                or sites.supports(category=category, usages=usages, tags=tags)
                or not sites.supports(category=category, usages=(desired,), tags=tags)):
            continue
        node = ET.SubElement(output, "type", name=item.name)
        node.extend(deepcopy(usage) for usage in item.element.findall("usage"))
        ET.SubElement(node, "usage", name=desired)
        notices.append(f"Repaired {item.name}: added verified {desired} placement while retaining its original route, tiers and population.")
    return output


def _events(mission: Path, notices: list[str]) -> ET.Element:
    warnings, contained = [], []
    paths = _references(mission, EXCLUDED, warnings, contained)["events"]
    layers = _load_layers(mission, paths, "event", warnings)
    if warnings:
        raise ValueError("Cannot safely repair event inputs: " + "; ".join(warnings))
    output = ET.Element("events")
    rows = layers.get("vehicleoffroad02")
    if not rows:
        return output
    event = _effective(rows, event=True)
    children = event.findall("children/child")
    # Exact known contradiction only; do not rewrite arbitrary population policy.
    if (event.findtext("active", "").strip() == "1"
            and event.findtext("limit", "").strip() == "mixed"
            and event.findtext("nominal", "").strip() == "4"
            and event.findtext("max", "").strip() == "5"
            and len(children) == 1 and children[0].get("type", "").casefold() == "offroad_02"
            and children[0].get("min") == "3" and children[0].get("max") == "2"):
        children[0].set("max", "4")
        output.append(event)
        notices.append("Repaired Humvee event: child maximum 2 raised to the authored target 4, retaining minimum 3 and all event settings.")
    return output


def _spawnable_types(mission: Path, notices: list[str]) -> ET.Element:
    warnings: list[str] = []
    contained_warnings: list[str] = []
    paths = _references(mission, EXCLUDED, warnings, contained_warnings)["spawnabletypes"]
    if warnings or contained_warnings:
        raise ValueError("Cannot safely repair spawnable references: " + "; ".join((*warnings, *contained_warnings)))
    presets = _load_random_presets(mission, contained_warnings)
    if contained_warnings:
        raise ValueError("Cannot safely repair ambiguous random presets: " + "; ".join(contained_warnings))
    layers = {}
    for path in paths:
        seen = set()
        for node in _xml(mission, path, "spawnabletypes").findall("type"):
            name = node.get("name", "").strip()
            key = name.casefold()
            if not name or key in seen:
                raise ValueError(f"Cannot safely repair duplicate or unnamed spawnable type in {path.name}.")
            seen.add(key)
            layers.setdefault(key, []).append(TypeLayer(path, node))
    output = ET.Element("spawnabletypes")
    bags_fixed = 0
    for key, source_layers in layers.items():
        node = _effective(source_layers)
        changed = False
        if (key.startswith("zmb") and ("attachments", "bagsindustrial") not in presets
                and ("attachments", "bagsvillage") in presets):
            for group in node.findall("attachments"):
                if group.get("preset", "").casefold() == "bagsindustrial":
                    group.set("preset", "bagsVillage")
                    bags_fixed += 1
                    changed = True
        if key == "zmbf_milkmaidold_green":
            missing = {name for name in ("drinks_t1", "food_t1", "toolscommon_t1")
                       if ("cargo", name) not in presets}
            available = {("cargo", "foodvillage"), ("cargo", "toolsvillage")} <= presets.keys()
            if missing and available:
                present = {group.get("preset", "").casefold() for group in node.findall("cargo")}
                for group in list(node.findall("cargo")):
                    old = group.get("preset", "").casefold()
                    if old not in missing:
                        continue
                    replacement = "toolsVillage" if old == "toolscommon_t1" else "foodVillage"
                    if replacement.casefold() in present:
                        node.remove(group)
                    else:
                        group.set("preset", replacement)
                        present.add(replacement.casefold())
                    changed = True
                if changed:
                    notices.append("Repaired green milkmaid infected cargo using the existing village food/drink and tool presets.")
        if changed:
            # CE replaces entire cargo/attachment groups, so carry the complete
            # effective type, including unaffected clothing, flags and damage.
            output.append(node)
    if bags_fixed:
        notices.append(f"Repaired missing industrial bag preset on {bags_fixed} infected types using existing village bags.")
    return output


def _ordinary_directory(path: Path) -> None:
    path.mkdir(exist_ok=True)
    if not stat.S_ISDIR(source_type(path)):
        raise OSError(f"Expected an economy directory at {path.name}.")


def _publish(mission: Path, payloads: dict[str, str]) -> None:
    core_path = mission / "cfgeconomycore.xml"
    core = _read_xml(core_path, "economycore", preserve_comments=True)
    old_core = ET.tostring(core, encoding="unicode")
    # Retire just our own references, then place the fresh repair before the
    # final balancing layer. Repeated startup does not accumulate overrides.
    for ce in list(core.findall("ce")):
        if ce.get("folder", "").replace("\\", "/").rstrip("/").casefold() == FOLDER:
            core.remove(ce)
    if payloads:
        extension = ET.Element("ce", folder=FOLDER)
        extension.tail = "\n"
        for kind in payloads:
            ET.SubElement(extension, "file", name=kind + ".xml", type=kind)
        balance = next((node for node in core.findall("ce")
                        if node.get("folder", "").replace("\\", "/").rstrip("/").casefold()
                        == LOOT_BALANCE_FOLDER), None)
        # A newly registered transport/mod layer can follow yesterday's balance
        # reference. Put repairs after every source on the very first run too;
        # the balance publisher will then move its own reference to the end.
        if balance is None or core.findall("ce")[-1] is not balance:
            core.append(extension)
        else:
            core.insert(list(core).index(balance), extension)
    previous: dict[Path, str | None] = {}
    try:
        if payloads:
            folder = mission / FOLDER
            _ordinary_directory(folder)
            for kind, content in payloads.items():
                target = folder / (kind + ".xml")
                if target.exists() or target.is_symlink():
                    if not stat.S_ISREG(source_type(target)):
                        raise OSError(f"Expected a regular economy file at {target.name}.")
                original = target.read_text(encoding="utf-8-sig") if target.is_file() else None
                if original != content:
                    atomic_text(target, content)
                    previous[target] = original
        if ET.tostring(core, encoding="unicode") != old_core:
            _write_core(core_path, core)
    except OSError:
        for target, original in previous.items():
            if original is None:
                target.unlink(missing_ok=True)
            else:
                atomic_text(target, original)
        raise


def prepare(mission: Path) -> tuple[str, ...]:
    """Publish evidence-gated repairs after mod registration and before balancing."""
    mission = mission.resolve()
    economy = read_economy(mission, exclude_folders=EXCLUDED)
    if economy.warnings:
        raise ValueError("Cannot safely repair economy inputs: " + "; ".join(economy.warnings))
    notices: list[str] = []
    roots = {"types": _types(mission, economy, _limits(mission), notices),
             "spawnabletypes": _spawnable_types(mission, notices),
             "events": _events(mission, notices)}
    payloads = {}
    for kind, root in roots.items():
        if len(root):
            ET.indent(root)
            payloads[kind] = ET.tostring(root, encoding="unicode", xml_declaration=True) + "\n"
    _publish(mission, payloads)
    return tuple(notices)
