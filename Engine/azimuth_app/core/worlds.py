"""World catalog, save locations, and import of creator-supplied missions."""
from dataclasses import dataclass
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
from zipfile import ZipFile, is_zipfile

from azimuth_app.network.server_discovery import MAX_CATALOG_BYTES, _read_vdf

DEFAULT_WORLD = "avalon"
MAX_MISSION_SIZE = 128 * 1024 * 1024
MAX_MISSION_FILES = 5000


@dataclass(frozen=True)
class World:
    id: str
    label: str
    template: str
    workshop_ids: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    source_url: str = ""


OFFICIAL_SOURCE = "https://github.com/BohemiaInteractive/DayZ-Central-Economy"
CATALOG = {
    world.id: world for world in (
        World("avalon", "Avalon", "dayzOffline.avalon", ("3225634444",), ("Avalon",),
              "https://steamcommunity.com/sharedfiles/filedetails/?id=3225634444"),
        World("chernarus", "Chernarus+", "dayzOffline.chernarusplus", source_url=OFFICIAL_SOURCE),
        World("livonia", "Livonia", "dayzOffline.enoch", source_url=OFFICIAL_SOURCE),
        World("deer_isle", "Deer Isle Stable", "empty.deerisle", ("1559212036", "1602372402"),
              ("CF", "DeerIsle"), "https://github.com/johnmclane666/Deerisle-Stable"),
        World("namalsk", "Namalsk Regular", "regular.namalsk", ("2289456201", "2289461232"),
              ("Namalsk Island", "Namalsk Survival"), "https://github.com/SumrakDZN/Namalsk-Server"),
    )
}


def atomic_text(path: Path, text: str) -> None:
    """Publish a complete small settings file on the same volume."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(text)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@dataclass(frozen=True)
class WorldPaths:
    root: Path
    world: Path
    profiles: Path
    mods: Path
    backups: Path
    config: Path
    mission_source: Path


class Worlds:
    def __init__(self, root: Path):
        self.root = root
        self.setup = root / "WorldSetup"
        self.selection = root / "Config" / "world-selection.json"
        self.active_id = DEFAULT_WORLD
        if self.selection.is_file():
            try:
                saved = json.loads(self.selection.read_text(encoding="utf-8-sig"))
                if not isinstance(saved, dict):
                    raise ValueError("Invalid world selection.")
                self.active_id = self.get(saved.get("world")).id
            except (OSError, ValueError) as error:
                raise RuntimeError(f"Could not load Config/world-selection.json: {error}") from error
        for world in CATALOG.values():
            (self.setup / world.id).mkdir(parents=True, exist_ok=True)

    def get(self, world_id: str) -> World:
        if not isinstance(world_id, str) or world_id not in CATALOG:
            raise ValueError("Choose a world listed in Azimuth.")
        return CATALOG[world_id]

    def paths(self, world_id: str) -> WorldPaths:
        world_id = self.get(world_id).id
        base = self.root if world_id == "chernarus" else self.root / "Worlds" / world_id
        return WorldPaths(base, base / "World", base / "Profiles", base / "Mods",
                          base / "Backups", base / "Config", self.setup / world_id)

    def select(self, world_id: str) -> None:
        world = self.get(world_id)
        self.selection.parent.mkdir(parents=True, exist_ok=True)
        atomic_text(self.selection, json.dumps({"world": world.id}, indent=2) + "\n")
        self.active_id = world.id


def children(path: Path) -> dict[str, Path]:
    if not path.is_dir():
        return {}
    return {item.name.casefold(): item for item in sorted(path.iterdir(), key=lambda item: item.name.casefold())}


def has_mission(path: Path) -> bool:
    top = children(path)
    db = top.get("db")
    return bool(top.get("init.c") and top["init.c"].is_file() and db and
                (types := children(db).get("types.xml")) and types.is_file())


def has_mod(path: Path) -> bool:
    addons = children(path).get("addons")
    return addons is not None and any(p.is_file() and p.suffix.casefold() == ".pbo" for p in children(addons).values())


class WorkshopMissing(RuntimeError):
    def __init__(self, world: World, alias: str, item_id: str):
        self.item_id = item_id
        self.alias = alias
        super().__init__(
            f"{world.label}: Steam Workshop item {alias} ({item_id}) is missing. "
            "Open Workshop, subscribe, wait for Steam to finish, then click Start server."
        )


def workshop_paths(world: World, setup: Path, mods_dir: Path, libraries: list[Path],
                   steam_only: frozenset[str] = frozenset()):
    """Yield each configured item's location without requiring all downloads."""
    local = {item.name.casefold().lstrip("@"): item
             for folder in (setup, mods_dir) for item in children(folder).values() if item.is_dir()}
    catalogs = {}
    if steam_only:
        for library in libraries:
            manifest = library / "steamapps/workshop/appworkshop_221100.acf"
            _, data = _read_vdf(manifest, MAX_CATALOG_BYTES)
            catalogs[library] = (data or {}).get("appworkshop")
    for item_id, alias in zip(world.workshop_ids, world.aliases):
        candidates = [] if item_id in steam_only else [local.get(item_id), local.get(alias.casefold())]
        candidates.extend(library / "steamapps" / "workshop" / "content" / "221100" / item_id
                          for library in libraries if item_id not in steam_only
                          or not _workshop_pending(catalogs[library], item_id))
        selected = next((path for path in candidates if path is not None and has_mod(path)), None)
        if selected is not None and any(character in str(selected) for character in ';"'):
            raise ValueError(f"Remove semicolons or quotes from the mod path for {alias}.")
        yield item_id, alias, selected


def _workshop_pending(catalog, item_id: str) -> bool:
    if not isinstance(catalog, dict):
        return False
    installed = catalog.get("workshopitemsinstalled")
    details = catalog.get("workshopitemdetails")
    local = installed.get(item_id) if isinstance(installed, dict) else None
    latest = details.get(item_id) if isinstance(details, dict) else None
    if not isinstance(local, dict) or not isinstance(latest, dict):
        return False
    local = local.get("manifest")
    latest = latest.get("latest_manifest")
    return bool(local and latest and latest != "-1" and local != latest)


def workshop_mods(world: World, setup: Path, mods_dir: Path, libraries: list[Path],
                  steam_only: frozenset[str] = frozenset()) -> list[Path]:
    result = []
    for item_id, alias, selected in workshop_paths(world, setup, mods_dir, libraries, steam_only):
        if selected is None:
            raise WorkshopMissing(world, alias, item_id)
        result.append(selected)
    return result


def mission_candidate(name: str, world: World) -> bool:
    normalized = name.casefold()
    if normalized == world.template.casefold():
        return True
    return world.id == "avalon" and normalized.endswith(".avalon") and re.fullmatch(
        r"[A-Za-z0-9_][A-Za-z0-9_-]*\.avalon", normalized
    ) is not None


def mission_sources(base: Path, world: World):
    """Find named mission directories and small mission ZIPs within a release."""
    pending = [(base, 0)]
    inspected = 0
    while pending:
        current, depth = pending.pop()
        if current.is_symlink() or not current.is_dir():
            continue
        info = current.lstat()
        if getattr(info, "st_file_attributes", 0) & 0x400:
            continue
        inspected += 1
        if inspected > 1000:
            break
        if mission_candidate(current.name, world) and has_mission(current):
            yield current
            continue
        if depth >= 6:
            continue
        entries = list(children(current).values())
        for item in entries:
            if (item.is_file() and not item.is_symlink() and item.suffix.casefold() == ".zip"
                    and item.stat().st_size <= MAX_MISSION_SIZE):
                yield item
        excluded = {"addons", "keys", "storage_1", "saves", "profiles", ".git", "docs"}
        pending.extend((item, depth + 1) for item in reversed(entries)
                       if item.is_dir() and item.name.casefold() not in excluded)


def windows_device_name(part: str) -> bool:
    stem = part.split(".", 1)[0].upper()
    return stem in {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} or re.fullmatch(
        r"(?:COM|LPT)[1-9¹²³]", stem
    ) is not None


def unpack_mission(archive_path: Path, destination: Path, world: World) -> Path | None:
    if not is_zipfile(archive_path):
        return None
    with ZipFile(archive_path) as archive:
        files = {}
        for item in archive.infolist():
            path = PurePosixPath(item.filename)
            if (item.is_dir() or path.is_absolute() or ".." in path.parts
                    or any(part in ("", ".") or re.search(r'[\x00-\x1f:<>|?*"\\]', part)
                           or part.endswith((" ", ".")) or windows_device_name(part)
                           for part in path.parts)):
                continue
            if any(part.casefold().startswith("storage_") for part in path.parts):
                continue
            files[path] = item
        candidates = []
        lower_files = {path.as_posix().casefold() for path in files}
        for path in files:
            parent = path.parent
            if (path.name.casefold() == "init.c" and mission_candidate(parent.name, world)
                    and (parent / "db" / "types.xml").as_posix().casefold() in lower_files):
                candidates.append(parent)
        if not candidates:
            if world.id != "avalon" or "init.c" not in lower_files or "db/types.xml" not in lower_files:
                return None
            candidates.append(PurePosixPath("."))
        parent = sorted(candidates, key=lambda path: (path.name.casefold() != world.template.casefold(), len(path.parts), path.as_posix()))[0]
        selected = [(path.relative_to(parent), info) for path, info in files.items()
                    if path.is_relative_to(parent)]
        if len(selected) > MAX_MISSION_FILES or sum(info.file_size for _, info in selected) > MAX_MISSION_SIZE:
            raise ValueError(f"Mission archive {archive_path.name} is too large.")
        result = destination / (parent.name or world.template)
        result.mkdir()
        for relative, info in selected:
            target = result.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("xb") as out:
                shutil.copyfileobj(source, out)
        return result
