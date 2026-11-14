"""Tune the installed Avalon economy and report the effective loot rules."""

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
import stat
from xml.etree import ElementTree as ET

from azimuth_app.economy.avalon_loot_audit import LootEconomy, LootType, read_economy
from azimuth_app.economy.avalon_loot_sites import LootSites, load_sites, loot_flags
from azimuth_app.core.backups import source_type
from azimuth_app.economy.hunter_economy import (
    LOOT_BALANCE_FILENAME, LOOT_BALANCE_FOLDER, _read_xml, _write_core, is_loot_balance_reference,
)
from azimuth_app.core.worlds import atomic_text


AUDIT_PATH = Path("Azimuth", "Avalon Loot Audit.txt")
PRACTICAL_ITEMS = frozenset(name.casefold() for name in (
    "WaterBottle", "Canteen", "BandageDressing", "DisinfectantSpray", "AlcoholTincture",
    "TetracyclineAntibiotics", "CharcoalTablets", "PainkillerTablets", "VitaminBottle",
    "DisinfectantAlcohol", "PurificationTablets", "SalineBag", "StartKitIV", "BloodTestKit",
    "CanOpener", "KitchenKnife", "SteakKnife", "HuntingKnife", "Matchbox", "Roadflare",
    "NailBox", "Hammer", "Hatchet", "Handsaw", "Hacksaw", "Pliers", "Shovel", "Pickaxe",
    "Crowbar", "MetalWire", "BarbedWire", "CombinationLock", "CombinationLock4", "Rope",
    "DuctTape", "SewingKit", "LeatherSewingKit", "EpoxyPutty", "WeaponCleaningKit",
    "SparkPlug", "GlowPlug", "CarBattery", "TruckBattery", "CarRadiator", "LugWrench",
    "TireRepairKit", "CanisterGasoline", "Battery9V", "SmallProtectorCase", "SeaChest",
    "MediumTent", "CarTent", "Slingbag_Black", "FishingRod", "Hook", "AirdropFlare",
))
FALLBACK_USAGES = {
    "bandagedressing": ("Town", "Village"),
    "canopener": ("Town", "Village"),
    "kitchenknife": ("Town", "Village"),
    "matchbox": ("Town", "Village", "Farm"),
    "nailbox": ("Industrial", "Farm"),
    "hammer": ("Industrial", "Farm"),
    "sparkplug": ("Industrial", "Farm"),
    "carbattery": ("Industrial", "Farm"),
}
RESTRICTED_USAGES = {"ContaminatedArea", "Historical", "Prison", "SeasonalEvent"}
NO_BUILDING_POINTS = "no matching static building loot points"
NO_PROXY_POINTS = "no matching static wreck proxies"


@dataclass(frozen=True)
class SupplyRule:
    """Bounded, named solo supplies; never a blanket multiplier for rare loot."""

    floor: int
    percent: int = 50
    cap: int = 20
    quantity: int | None = None
    restock: int = 300
    usages: tuple[str, ...] = ()
    values: tuple[str, ...] = ()
    enable_zero: bool = False
    ordinary_magazine: bool = False


# These are deliberately exact class lists. New mod weapons, explosives,
# crafted objects and collectible/event variants must not become common by
# matching a prefix. Counts are mapwide targets, quantities are percentages.
COMMON_AMMO = frozenset(name.casefold() for name in (
    "Ammo_12gaPellets", "Ammo_12gaSlug", "Ammo_22", "Ammo_308Win", "Ammo_357",
    "Ammo_380", "Ammo_45ACP", "Ammo_545x39", "Ammo_556x45", "Ammo_762x39",
    "Ammo_762x54", "Ammo_9x19", "Ammo_9x39",
))
COMMON_AMMO_BOXES = frozenset(name.casefold() for name in (
    "AmmoBox_00buck_10rnd", "AmmoBox_12gaSlug_10Rnd", "AmmoBox_22_50Rnd",
    "AmmoBox_308Win_20Rnd", "AmmoBox_357_20Rnd", "AmmoBox_380_35rnd",
    "AmmoBox_45ACP_25rnd", "AmmoBox_545x39_20Rnd", "AmmoBox_556x45_20Rnd",
    "AmmoBox_762x39_20Rnd", "AmmoBox_762x54_20Rnd", "AmmoBox_9x19_25rnd",
    "AmmoBox_9x39_20Rnd",
))
STANDARD_MAGAZINES = frozenset(name.casefold() for name in (
    "Mag_1911_7Rnd", "Mag_AK101_30Rnd", "Mag_AKM_30Rnd", "Mag_Aug_30Rnd",
    "Mag_CMAG_10Rnd", "Mag_CMAG_20Rnd", "Mag_CMAG_30Rnd", "Mag_CZ527_5rnd",
    "Mag_CZ550_10Rnd", "Mag_CZ61_20Rnd", "Mag_CZ75_15Rnd", "Mag_Deagle_9rnd",
    "Mag_FAL_20Rnd", "Mag_FAMAS_25Rnd", "Mag_FNX45_15Rnd", "Mag_Glock_15Rnd", "Mag_IJ70_8Rnd",
    "Mag_M14_10Rnd", "Mag_MKII_10Rnd", "Mag_MP5_15Rnd", "Mag_MP5_30Rnd",
    "Mag_P1_8Rnd", "Mag_PP19_64Rnd", "Mag_Ruger1022_15Rnd", "Mag_SSG82_5rnd",
    "Mag_STANAG_30Rnd", "Mag_SV98_10Rnd", "Mag_SVD_10Rnd", "Mag_Saiga_5Rnd",
    "Mag_Saiga_8Rnd", "Mag_Scout_5Rnd", "Mag_UMP_25Rnd", "Mag_VAL_20Rnd",
    "Mag_VSS_10Rnd", "Mag_Vikhr_30Rnd",
))
PROGRESSION_FLOORS = {name.casefold(): 4 for name in (
    "rag_hunting_cabin_kit", "RaG_BB_FoundationKit", "RaG_BB_FloorKit",
    "RaG_BB_WallKit", "RaG_BB_CeilingKit", "RaG_BB_Single_DoorKit",
    "RaG_BB_Window_SmallKit", "rag_baseitems_repair_station_kit",
    "rag_baseitems_table_saw_static_kit", "rag_baseitems_retro_fridge_kit",
    "rag_baseitems_solarpanel_kit", "rag_baseitems_greenhouse_kit",
    "rag_baseitems_greenhouse_big_kit", "rag_beehive_kit",
)}
PROGRESSION_FLOORS.update({name.casefold(): 6 for name in (
    "RaG_BB_Book", "rag_baseitems_workbench_kit", "rag_baseitems_workbench_blueprint",
)})
SUPPLY_ROUTES = {
    "ammo_380": ("Town", "Village"), "ammobox_380_35rnd": ("Town", "Village"),
    "ammo_45acp": ("Police", "Village"), "ammobox_45acp_25rnd": ("Police", "Village"),
    "ammo_545x39": ("Police",), "ammobox_545x39_20rnd": ("Police",),
    "ammo_556x45": ("Police", "Hunting"), "ammobox_556x45_20rnd": ("Police", "Hunting"),
    "ammobox_762x39_20rnd": ("Hunting", "Village"),
    "mag_1911_7rnd": ("Town", "Village"), "mag_cmag_10rnd": ("Hunting", "Police"),
    "mag_cmag_20rnd": ("Hunting", "Police"), "mag_ssg82_5rnd": ("Police",),
    "mag_mp5_15rnd": ("Police",), "mag_mp5_30rnd": ("Police",),
    "bandagedressing": ("Town", "Village"),
}


def _supply_rule(item: LootType, category: str) -> SupplyRule | None:
    name = item.name.casefold()
    routes = SUPPLY_ROUTES.get(name, ())
    if name in COMMON_AMMO:
        return SupplyRule(24 if name == "ammo_9x39" else 40, quantity=50, usages=routes,
                          values=("Tier1",), enable_zero=name == "ammo_545x39")
    if name in COMMON_AMMO_BOXES:
        return SupplyRule(12, cap=10, usages=routes, values=("Tier1",))
    if name in STANDARD_MAGAZINES:
        return SupplyRule(10, percent=35, cap=10, quantity=35, usages=routes,
                          values=("Tier2",) if name == "mag_ak101_30rnd" else (),
                          enable_zero=name in {"mag_cz61_20rnd", "mag_p1_8rnd"},
                          ordinary_magazine=name in {"mag_akm_30rnd", "mag_sv98_10rnd", "mag_fal_20rnd"})
    if name in {"nailbox", "combinationlock", "combinationlock4"}:
        return SupplyRule({"nailbox": 24, "combinationlock": 6, "combinationlock4": 8}[name],
                          enable_zero=True)
    if name in {"bicycle_wheel", "ladybicycle_wheel"}:
        return SupplyRule(10, percent=25, cap=4, restock=600)
    if name in {"hatchbackwheel", "offroad_02_wheel", "sedan_02_wheel"}:
        # Spares for the vanilla cars actually enabled in this Avalon mission.
        # Dispatch classes still require a placed wreck proxy in _review.
        return SupplyRule(10, percent=25, cap=4, restock=600, enable_zero=True)
    if name in {"rt_rzr_wheel", "rt_rzr_wheel_black", "rt_rzr_wheel_eclipse", "rt_rzr_wheel_desert"}:
        # Exact wheels named in the enabled RZR variants' authored attachment
        # groups. Their existing tools/floor routes must pass the same static
        # placement check as every other ordinary supply item.
        return SupplyRule(6, percent=25, cap=4, restock=600, enable_zero=True)
    if name in PROGRESSION_FLOORS:
        return SupplyRule(PROGRESSION_FLOORS[name], percent=0, cap=0, restock=600)
    if name in PRACTICAL_ITEMS and name not in {
            "cartent", "mediumtent", "seachest", "smallprotectorcase", "airdropflare"}:
        return SupplyRule(16, quantity=50, usages=routes)
    if category == "food":
        return SupplyRule(8, percent=35, cap=8, quantity=50)
    return None


@dataclass(frozen=True)
class Review:
    item: LootType
    category: str
    usages: tuple[str, ...]
    values: tuple[str, ...]
    tags: tuple[str, ...]
    placement: str
    added_usages: tuple[str, ...]
    nominal: int | None
    minimum: int | None
    updated_minimum: int | None
    added_values: tuple[str, ...] = ()
    scalar_overrides: tuple[tuple[str, int], ...] = ()
    flag_overrides: tuple[tuple[str, str], ...] = ()
    supply_rule: bool = False

    @property
    def changed(self) -> bool:
        return (self.nominal != self.item.nominal or self.updated_minimum != self.minimum
                or bool(self.added_usages or self.added_values or self.scalar_overrides or self.flag_overrides))


def _number(item: LootType, field: str) -> int | None:
    try:
        return int(item.element.findtext(field, ""))
    except ValueError:
        return None


def _minimum(item: LootType) -> int | None:
    try:
        return int(item.element.findtext("min", ""))
    except ValueError:
        return None


def _increase(item: LootType, category: str, usages: tuple[str, ...], values: tuple[str, ...]) -> int:
    name = item.name.casefold()
    nominal = item.nominal
    if (nominal is None or nominal < 2 or item.events or RESTRICTED_USAGES.intersection(usages)
            or "unique" in {value.casefold() for value in values}
            or name.startswith("hm_mystery_box")
            or name in {"hippack_party", "svd_wooden", "punchedcard"}):
        return 0
    if (category in {"weapons", "explosives"} or name.startswith(("ammo_", "mag_"))
            or usages == ("Military",) or values == ("Tier4",)):
        return min(nominal * 8 // 100, 3)
    if name in PRACTICAL_ITEMS or category == "food" or name.startswith("hm_"):
        return min(max(1, nominal * 25 // 100), 15)
    if category in {"tools", "containers", "lootdispatch"}:
        return min(max(1, nominal * 18 // 100), 10)
    if nominal < 5:
        return 0
    return min(max(1, nominal * 12 // 100), 8)


def _review(item: LootType, sites: LootSites | None, allow_changes: bool) -> Review:
    flags_for = sites.flags if sites is not None else loot_flags
    category, *_ = flags_for(item.element, "category") or ("",)
    usages, values, tags = (flags_for(item.element, tag) for tag in ("usage", "value", "tag"))
    nominal = item.nominal
    minimum = updated_minimum = _minimum(item)
    added_usages = ()
    added_values = ()
    scalar_overrides = ()
    flag_overrides = ()
    used_rule = False
    placement = "special source"
    flags = item.element.find("flags")
    counted = flags is None or flags.get("count_in_map", "1").strip().casefold() not in {"0", "false"}
    rule = _supply_rule(item, category.casefold())
    protected = (bool(item.events) or bool(RESTRICTED_USAGES.intersection(usages))
                 or "unique" in {value.casefold() for value in values}
                 or item.spawn_kind == "crafted")
    restore = bool(rule and not protected and (
        (item.spawn_kind == "zero" and rule.enable_zero)
        or (item.spawn_kind == "event" and rule.ordinary_magazine)))
    if (item.natural or restore) and counted:
        placement = "unverified"
        if category == "lootdispatch" and sites is not None:
            placement = "matching wreck proxies" if sites.proxy_groups(item.name) else NO_PROXY_POINTS
        elif sites is not None:
            if sites.supports(category=category or None, usages=usages, tags=tags):
                placement = "matching building loot points"
            else:
                placement = NO_BUILDING_POINTS
                if allow_changes and not item.events and not RESTRICTED_USAGES.intersection(usages):
                    added_usages = tuple(usage for usage in FALLBACK_USAGES.get(item.name.casefold(), ())
                                         if usage not in usages and sites.supports(
                                             category=category or None, usages=(usage,), tags=tags))
                    if added_usages:
                        placement = "fallback building loot points"
        # A useful source can still be needlessly confined (for example,
        # bandages only in hospitals or .45ACP only on military shelves).
        # Add only individually verified, compatible static routes. Missing
        # map evidence does not authorize inventing a new route or restoring
        # a disabled item.
        if allow_changes and rule and not protected and sites is not None:
            added_usages = tuple(dict.fromkeys((*added_usages, *(
                usage for usage in rule.usages if usage not in usages
                and sites.supports(category=category or None, usages=(usage,), tags=tags)))))
            if added_usages:
                placement = "fallback building loot points" if placement == NO_BUILDING_POINTS else placement
        if (allow_changes and placement not in {NO_BUILDING_POINTS, NO_PROXY_POINTS}
                and (sites is not None or category != "lootdispatch")):
            if minimum is not None and 0 <= minimum <= nominal:
                if rule and not protected and (not restore or sites is not None):
                    # Preserve any larger publisher target; the bounded uplift
                    # is always recomputed from source layers, never last run.
                    nominal = max(rule.floor, nominal + min((nominal * rule.percent + 99) // 100, rule.cap))
                    updated_minimum = max(minimum, min(nominal - 1, (nominal * 7 + 9) // 10))
                    changes = {}
                    restock = _number(item, "restock")
                    if restock is not None and restock > rule.restock:
                        changes["restock"] = rule.restock
                    lower, upper = _number(item, "quantmin"), _number(item, "quantmax")
                    if rule.quantity is not None and lower is not None and upper is not None and 0 <= lower <= upper <= 100:
                        if lower < rule.quantity:
                            changes["quantmin"] = rule.quantity
                        if upper < 100:
                            changes["quantmax"] = 100
                    scalar_overrides = tuple(changes.items())
                    # Unrestricted source items stay unrestricted. For common
                    # ammunition, existing limited tiers gain early access;
                    # weapon tiers and high-capacity/exotic ammunition stay.
                    if values:
                        added_values = tuple(value for value in rule.values if value not in values)
                    if item.spawn_kind == "event" and rule.ordinary_magazine:
                        flag_overrides = (("deloot", "0"),)
                    used_rule = True
                elif not restore:
                    increase = _increase(item, category.casefold(), usages, values)
                    nominal += increase
                    if minimum > 0:
                        updated_minimum += (increase + 1) // 2
    elif item.natural:
        placement = "map counting disabled"
    return Review(item, category, usages, values, tags, placement, added_usages, nominal, minimum,
                  updated_minimum, added_values, scalar_overrides, flag_overrides, used_rule)


def _override(reviews: list[Review]) -> str:
    root = ET.Element("types")
    for review in reviews:
        node = ET.SubElement(root, "type", name=review.item.name)
        if review.nominal != review.item.nominal:
            ET.SubElement(node, "nominal").text = str(review.nominal)
        if review.updated_minimum != review.minimum:
            ET.SubElement(node, "min").text = str(review.updated_minimum)
        for field, value in review.scalar_overrides:
            ET.SubElement(node, field).text = str(value)
        if review.flag_overrides:
            flags = review.item.element.find("flags")
            attributes = dict(flags.attrib) if flags is not None else {}
            attributes.update(review.flag_overrides)
            ET.SubElement(node, "flags", attributes)
        if review.added_usages:
            for usage in review.item.element.findall("usage"):
                ET.SubElement(node, "usage", usage.attrib)
            for usage in review.added_usages:
                ET.SubElement(node, "usage", name=usage)
        if review.added_values:
            for value in review.item.element.findall("value"):
                ET.SubElement(node, "value", value.attrib)
            for value in review.added_values:
                ET.SubElement(node, "value", name=value)
    ET.indent(root)
    return ET.tostring(root, encoding="unicode", xml_declaration=True) + "\n"


def _safe_folder(path: Path) -> None:
    path.mkdir(exist_ok=True)
    if not stat.S_ISDIR(source_type(path)):
        raise OSError(f"Expected a directory at {path.name}.")


def _existing_text(path: Path) -> str | None:
    if path.exists() or path.is_symlink():
        if not stat.S_ISREG(source_type(path)):
            raise OSError(f"Expected a regular file at {path.name}.")
        return path.read_text(encoding="utf-8-sig")
    return None


def _publish_override(mission: Path, reviews: list[Review]) -> None:
    core = mission / "cfgeconomycore.xml"
    economy = _read_xml(core, "economycore", preserve_comments=True)
    existing = [(ce, node) for ce in economy.findall("ce") for node in ce.findall("file")
                if node.get("type", "").casefold() == "types"
                and is_loot_balance_reference(ce.get("folder", ""), node.get("name", ""))]
    target = mission / LOOT_BALANCE_FOLDER / LOOT_BALANCE_FILENAME
    last = economy.findall("ce")[-1:]  # The generated override must follow each source layer.
    current = (len(existing) == 1 and existing[0][0] in last and len(existing[0][0]) == 1
               and existing[0][0].get("folder") == LOOT_BALANCE_FOLDER
               and existing[0][1].attrib == {"name": LOOT_BALANCE_FILENAME, "type": "types"})
    change_core = bool(existing) if not reviews else not current
    if change_core:
        for extension, node in existing:
            extension.remove(node)
            if not len(extension):
                economy.remove(extension)
        if reviews:
            extension = ET.SubElement(economy, "ce", folder=LOOT_BALANCE_FOLDER)
            extension.tail = "\n"
            ET.SubElement(extension, "file", name=LOOT_BALANCE_FILENAME, type="types")

    previous = None
    wrote = False
    if reviews:
        _safe_folder(target.parent)
        previous = _existing_text(target)
        content = _override(reviews)
        if previous != content:
            atomic_text(target, content)
            wrote = True
    if change_core:
        try:
            _write_core(core, economy)
        except OSError:
            if wrote:
                if previous is None:
                    target.unlink(missing_ok=True)
                else:
                    atomic_text(target, previous)
            raise


def _report(mission: Path, reviews: list[Review], changed: list[Review], economy: LootEconomy,
            sites: LootSites | None, notices: tuple[str, ...]) -> str:
    kinds = Counter(review.item.spawn_kind for review in reviews)
    categories = Counter(review.category or "uncategorized" for review in reviews if review.item.natural)
    tuned = Counter(review.category or "uncategorized" for review in changed)
    delta = sum((review.nominal or 0) - (review.item.nominal or 0) for review in changed)
    missing = sum(review.placement == NO_BUILDING_POINTS for review in reviews)
    missing_proxy = sum(review.placement == NO_PROXY_POINTS for review in reviews)
    lines = ["AZIMUTH | AVALON LOOT AUDIT", "", f"Mission: {mission.name}",
             f"Loot classes reviewed: {len(reviews)}", f"Ordinary loot classes: {kinds['natural']}",
             f"Classes adjusted: {len(changed)}", f"Added to mapwide loot targets: {delta}",
             f"Ordinary classes with no matching static building loot points: {missing}" if sites is not None else
             "Building evidence: placement files missing or unreadable. Routes could not be checked.",
             f"Crafted: {kinds['crafted']}  Event: {kinds['event']}  Zero: {kinds['zero']}  Unknown: {kinds['unknown']}",
             "", "Counts are configuration targets. DayZ fills eligible slots over time.",
             "Placement evidence covers static buildings and wreck proxies.",
             "Live occupancy, regional tiers, item dimensions and mod scripts require DayZ.",
             "Zero nominal can mean crafting, event cargo, attachments, scripted rewards or a disabled item."]
    lines.extend(("", "SOLO SUPPLY POLICY",
                  "Common loose ammunition: at least 50% per stack; standard magazines: at least 35% full.",
                  "Known zero-count nails, locks, 5.45 ammunition and civilian spare magazines are restored",
                  "only when their source flags and verified building routes permit it.",
                  "Standard AKM/SV98/FAL magazines gain ordinary loot routes; rare guns and event weapons stay rare.",
                  "Common supplies restock within their existing delay or a 300-second cap;"))
    lines.extend(("progression kits and vehicle wheels use a 600-second cap. These are CE eligibility delays,",
                  "not guarantees that loot appears nearby or within that many seconds.",
                  "Counts remain mapwide targets. This supply policy does not wipe saves, add events or force respawns.",
                  f"Classes receiving the targeted solo supply policy: {sum(review.supply_rule for review in reviews)}"))
    if sites is not None:
        lines.append(f"Vehicle part classes with no matching static wreck proxy: {missing_proxy}")
        if sites.warnings:
            lines.extend(("", "PLACEMENT EVIDENCE LIMITS", *sites.warnings))
    if economy.warnings:
        lines.extend(("", "BALANCING SKIPPED DUE TO ECONOMY INPUTS", *economy.warnings))
    if economy.contained_warnings:
        lines.extend(("", "CARGO AND ATTACHMENT INPUTS", *economy.contained_warnings))
    if notices:
        lines.extend(("", "WORKSHOP SETUP", *notices))
    lines.extend(("", "ORDINARY LOOT BY CATEGORY (reviewed / adjusted)"))
    lines.extend(f"{name}: {count} / {tuned[name]}" for name, count in sorted(categories.items()))
    lines.extend(("", "ACTIVE EVENT CHILDREN", *(f"{name}: {', '.join(events)}"
                                                for name, events in sorted(economy.events.items())),
                  "", "CONFIGURED CARGO AND ATTACHMENTS"))
    for name, sources in sorted(economy.contained.items()):
        lines.append(f"{name}: " + ", ".join(dict.fromkeys(f"{source.kind} on {source.parent}" for source in sources)))
    lines.extend(("", "ITEM DETAILS"))
    for review in reviews:
        item = review.item
        nominal = f"{item.nominal} -> {review.nominal}" if review.nominal != item.nominal else str(item.nominal)
        minimum = f"{review.minimum} -> {review.updated_minimum}" if review.updated_minimum != review.minimum else str(review.minimum)
        usages = ", ".join((*review.usages, *review.added_usages)) or "any"
        events = f" | events: {', '.join(item.events)}" if item.events else ""
        extra = "".join(f" | {field}: {item.element.findtext(field)} -> {value}"
                        for field, value in review.scalar_overrides)
        extra += "".join(f" | flag {field} -> {value}" for field, value in review.flag_overrides)
        lines.append(f"{item.name} | source {item.spawn_kind} | {review.category or 'any category'} | "
                     f"nominal {nominal} | min {minimum} | {review.placement} | usage: {usages} | "
                     f"tiers: {', '.join((*review.values, *review.added_values)) or 'any'} | tags: {', '.join(review.tags) or 'any'} | "
                     f"{item.source.relative_to(mission).as_posix()}{events}{extra}")
    return "\n".join(lines) + "\n"


def prepare(mission: Path, profiles: Path, notices: tuple[str, ...] = ()) -> str:
    """Recompute the Avalon layer after all source mods have registered their CE files."""
    mission = mission.resolve()
    economy = read_economy(mission, exclude_folders=(LOOT_BALANCE_FOLDER,))
    sites = load_sites(mission)
    reviews = [_review(item, sites, not economy.warnings) for _, item in sorted(economy.types.items())]
    changed = [review for review in reviews if review.changed]
    if not economy.warnings:
        _publish_override(mission, changed)
    audit = profiles / AUDIT_PATH
    _safe_folder(profiles)
    _safe_folder(audit.parent)
    content = _report(mission, reviews, changed, economy, sites, notices)
    if _existing_text(audit) != content:
        atomic_text(audit, content)
    if economy.warnings:
        return f"Avalon loot: audit found {len(economy.warnings)} economy input issue(s). See Worlds > Loot audit."
    return f"Avalon loot: reviewed {len(reviews)} classes and adjusted {len(changed)}. See Worlds > Loot audit."
