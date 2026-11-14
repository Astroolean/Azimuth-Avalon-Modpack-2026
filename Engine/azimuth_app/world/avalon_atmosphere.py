"""Prepare the opt-in Avalon atmosphere settings using publisher schemas."""
from hashlib import sha256
import json
from os import replace
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from xml.etree import ElementTree

from azimuth_app.economy.avalon_economy import MAX_XML_BYTES, register_bytes
from azimuth_app.core.worlds import atomic_text


SOUND_ID = "3623510671"
FISH_ID = "2757509117"
ITEMS = (
    (SOUND_ID, "Psyerns Sound System"),
    (FISH_ID, "gebsfish"),
)

FISH_ECONOMY_FOLDER = "azimuth_gebsfish"
FISH_GENERATED = {
    "types": "gebsfish-types.xml",
    "spawnabletypes": "gebsfish-spawnabletypes.xml",
}
_STATE_DIR = "AzimuthAtmosphere"
_WEATHER = {
    "overcast": {
        "current": {"actual": "0.26", "time": "180", "duration": "720"},
        "limits": {"min": "0.10", "max": "0.88"},
        "timelimits": {"min": "720", "max": "1800"},
        "changelimits": {"min": "0.02", "max": "0.26"},
    },
    "fog": {
        "current": {"actual": "0.018", "time": "150", "duration": "540"},
        "limits": {"min": "0.0", "max": "0.045"},
        "timelimits": {"min": "540", "max": "1200"},
        "changelimits": {"min": "0.0", "max": "0.025"},
    },
    "rain": {
        "current": {"actual": "0.0", "time": "120", "duration": "360"},
        "limits": {"min": "0.0", "max": "0.42"},
        "timelimits": {"min": "180", "max": "720"},
        "changelimits": {"min": "0.0", "max": "0.24"},
        "thresholds": {"min": "0.63", "max": "1.0", "end": "150"},
    },
    "windMagnitude": {
        "current": {"actual": "3.0", "time": "180", "duration": "420"},
        "limits": {"min": "0.5", "max": "8.0"},
        "timelimits": {"min": "240", "max": "840"},
        "changelimits": {"min": "0.0", "max": "3.0"},
    },
}
_SCRIPT_WEATHER = re.compile(r"\bMissionWeather\s*\(\s*(?:true|1)\s*\)", re.IGNORECASE)


def _weather_path(mission: Path) -> Path:
    return next((path for path in mission.iterdir() if path.name.casefold() == "cfgweather.xml"),
                mission / "cfgweather.xml")


def _install_weather(mission: Path, profiles: Path) -> None:
    path = _weather_path(mission)
    state = profiles / _STATE_DIR / "weather.json"
    init = next((item for item in mission.iterdir() if item.name.casefold() == "init.c"), None)
    if init and init.is_file():
        code = re.sub(r"/\*.*?\*/|//[^\n]*", "", init.read_text(encoding="utf-8-sig"), flags=re.DOTALL)
        if _SCRIPT_WEATHER.search(code):
            return

    # Keep a creator-enabled configuration in charge, and save any disabled
    # original so removing this opt-in can restore it byte for byte.
    original = path.read_bytes() if path.is_file() else None
    original_hex = original.hex() if original is not None else None
    if state.is_file():
        receipt = json.loads(state.read_text(encoding="utf-8"))
        if receipt.get("mission") != mission.name or receipt.get("original") != original_hex:
            return
    if original is None:
        root = ElementTree.Element("weather")
    else:
        parser = ElementTree.XMLParser(target=ElementTree.TreeBuilder(insert_comments=True))
        try:
            root = ElementTree.fromstring(original, parser=parser)
        except ElementTree.ParseError:
            return
        if root.tag != "weather" or root.get("enable", "").casefold() not in {"0", "false", "no"}:
            return

    root.set("enable", "1")
    root.set("reset", "0")
    for name, values in _WEATHER.items():
        section = root.find(name)
        if section is None:
            section = ElementTree.SubElement(root, name)
        for child_name, attributes in values.items():
            child = section.find(child_name)
            if child is None:
                child = ElementTree.SubElement(section, child_name)
            child.attrib.update(attributes)
    storm = root.find("storm")
    if storm is None:
        storm = ElementTree.SubElement(root, "storm")
    storm.attrib.update(density="0.20", threshold="0.84", timeout="90")
    ElementTree.indent(root, space="    ")
    updated = ElementTree.tostring(root, encoding="unicode", xml_declaration=True) + "\n"

    state.parent.mkdir(parents=True, exist_ok=True)
    receipt = {"mission": mission.name, "installed": sha256(updated.encode("utf-8")).hexdigest(),
               "original": original_hex}
    atomic_text(state, json.dumps(receipt, indent=2) + "\n")
    try:
        atomic_text(path, updated)
    except OSError:
        state.unlink(missing_ok=True)
        raise


def restore_weather(mission: Path, profiles: Path) -> bool:
    """Restore the prior weather if the installed file has not been edited."""
    state = profiles / _STATE_DIR / "weather.json"
    if not state.is_file():
        return False
    receipt = json.loads(state.read_text(encoding="utf-8"))
    path = _weather_path(mission)
    if receipt["mission"] != mission.name:
        return False
    current = path.read_bytes() if path.is_file() else None
    original = receipt["original"]
    if (current.hex() if current is not None else None) == original:
        state.unlink()
        return False
    if current is None or receipt["installed"] != sha256(current).hexdigest():
        return False
    if original is None:
        path.unlink()
    else:
        # The weather XML is small. Write the exact original bytes to a
        # sibling before replacing the current file.
        temporary = None
        try:
            with NamedTemporaryFile(dir=path.parent, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(bytes.fromhex(original))
            replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    state.unlink()
    return True


def _prepare_fish(mission: Path, profiles: Path) -> bool:
    gebs = profiles / "Gebs"
    generated = {kind: gebs / "mpmissions" / filename for kind, filename in FISH_GENERATED.items()}
    # The mod writes these files on its first start. The boat event is
    # intentionally left to the map's existing vehicle economy.
    economy_ready = all(path.is_file() for path in generated.values())
    if economy_ready:
        payloads = {}
        for kind, source in generated.items():
            with source.open("rb") as stream:
                content = stream.read(MAX_XML_BYTES + 1)
            if len(content) > MAX_XML_BYTES or b"<!DOCTYPE" in content.upper():
                raise ValueError(f"Unsupported gebsfish XML in {source.name}.")
            try:
                root = ElementTree.fromstring(content, parser=ElementTree.XMLParser(
                    target=ElementTree.TreeBuilder(insert_comments=True)
                ))
            except ElementTree.ParseError as error:
                raise ValueError(f"Invalid gebsfish XML in {source.name}: {error}") from error
            # The generator includes short overrides for vanilla canned food.
            # Keep the map's original loot counts and register only mod classes.
            for item in root.findall("type"):
                if not item.get("name", "").casefold().startswith("geb_"):
                    root.remove(item)
            payloads[kind] = (source.name, ElementTree.tostring(
                root, encoding="utf-8", xml_declaration=True
            ) + b"\n")
        register_bytes(mission, FISH_ECONOMY_FOLDER, payloads)

    path = gebs / "general.json"
    state = profiles / _STATE_DIR / "gebsfish-predators"
    if not state.is_file():
        state.parent.mkdir(parents=True, exist_ok=True)
        atomic_text(state, "existing\n" if path.is_file() else "pending\n")
    if not path.is_file() or state.read_text(encoding="utf-8").strip() != "pending":
        return economy_ready
    try:
        settings = json.loads(path.read_text(encoding="utf-8-sig"))
    except (UnicodeError, ValueError):
        return economy_ready
    predators = settings.get("PredatorSettings") if isinstance(settings, dict) else None
    value = predators.get("PredatorSpawnEnabled") if isinstance(predators, dict) else None
    if type(value) not in (bool, int) or value not in (0, 1):
        return economy_ready
    if value:
        predators["PredatorSpawnEnabled"] = False if isinstance(value, bool) else 0
        atomic_text(path, json.dumps(settings, indent=2, ensure_ascii=False) + "\n")
    atomic_text(state, "applied\n")
    return economy_ready


def fishing_setup_pending(mission: Path, profiles: Path) -> bool:
    generated = profiles / "Gebs" / "mpmissions"
    registered = mission / "azimuth_workshop" / FISH_ECONOMY_FOLDER / "types.xml"
    return not registered.is_file() and all((generated / name).is_file() for name in FISH_GENERATED.values())


def prepare(mission: Path, profiles: Path, mods: dict[str, Path]) -> tuple[str, ...]:
    """Prepare Avalon before launch. The next start adopts generated fish files."""
    if not mission.name.casefold().endswith(".avalon"):
        return ()
    _install_weather(mission, profiles)
    notices = []
    if FISH_ID in mods and not _prepare_fish(mission, profiles):
        notices.append("gebsfish generates its loot files on first launch. "
                       "Azimuth registers the fishing gear at the next server start.")
    # Psyerns writes its own configs under DeadmansEcho on first boot.
    # Keep vendor defaults until the zone coordinates and sound IDs are verified.
    if SOUND_ID in mods and not (profiles / "DeadmansEcho" / "dme_creep_timeofday.json").is_file():
        notices.append("Psyerns Sound System generates its settings on first launch.")
    return tuple(notices)
