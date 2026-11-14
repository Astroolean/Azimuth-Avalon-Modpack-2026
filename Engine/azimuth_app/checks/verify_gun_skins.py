"""Check bundled skins and showcase source without launching or changing DayZ."""
from azimuth_app.paths import APP_ROOT
from hashlib import sha256
from pathlib import Path
import struct
import sys
import tempfile
import zlib

from azimuth_app.cosmetics import azimuth_skins
from azimuth_app.cosmetics.native_color_build import build_script_pbo
from azimuth_app.cosmetics import skin_settings
from azimuth_app.cosmetics import skin_showcase
from azimuth_app.cosmetics import random_skin_mode
from azimuth_app.cosmetics import vehicle_showcase

MAX_PREVIEW_BYTES = 16 * 1024 * 1024


def validate_preview(root: Path, relative: str) -> tuple[int, int]:
    """Check ordinary PNG assets, dimensions, chunk integrity and termination.

    This uses the standard library shipped with the panel. It does not claim to
    render the PNG or test its texture mapping on a game model.
    """
    path = root / relative
    if Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise ValueError("A skin preview must stay inside the bundle.")
    for folder in path.parents:
        if folder == root:
            break
        azimuth_skins._ordinary(folder, True)
    azimuth_skins._ordinary(path)
    with path.open("rb") as stream:
        data = stream.read(MAX_PREVIEW_BYTES + 1)
    if len(data) > MAX_PREVIEW_BYTES or not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError(f"Invalid or oversized preview PNG: {path.name}.")
    offset, dimensions, image_data = 8, None, False
    while offset + 12 <= len(data):
        length = struct.unpack_from(">I", data, offset)[0]
        kind = data[offset + 4:offset + 8]
        end = offset + 12 + length
        if end > len(data):
            break
        payload = data[offset + 8:offset + 8 + length]
        checksum = struct.unpack_from(">I", data, offset + 8 + length)[0]
        if zlib.crc32(kind + payload) & 0xFFFFFFFF != checksum:
            raise ValueError(f"Preview PNG checksum failed: {path.name}.")
        if dimensions is None:
            if kind != b"IHDR" or length != 13:
                raise ValueError(f"Preview PNG has no image header: {path.name}.")
            dimensions = struct.unpack_from(">II", payload)
            if any(dimension < 1 or dimension > 8192 for dimension in dimensions):
                raise ValueError(f"Preview dimensions are unsupported: {path.name}.")
        elif kind == b"IHDR":
            raise ValueError(f"Preview PNG has duplicate image headers: {path.name}.")
        if kind == b"IDAT":
            image_data = True
        if kind == b"IEND":
            if length or end != len(data) or not image_data:
                raise ValueError(f"Preview PNG terminator is invalid: {path.name}.")
            return dimensions
        offset = end
    raise ValueError(f"Preview PNG is truncated: {path.name}.")


def check_sources(root: Path) -> dict:
    """Build selected assets in an isolated temp folder; never persist choices."""
    bundle = root / azimuth_skins.SOURCE_FOLDER
    catalog = azimuth_skins.load_catalog(bundle / "catalog.json")
    choices = skin_settings.load(root, catalog)
    vehicle_configuration = vehicle_showcase.config_for(choices)
    selected_ids = choices["selected_skin_ids"]
    selected = skin_settings.selected_catalog(catalog, selected_ids)
    for skin in catalog["skins"]:
        validate_preview(root, skin["preview"])
    # Validate the complete editable library even when no finishes are selected.
    azimuth_skins.source_files(bundle / azimuth_skins.PREFIX, catalog)
    files = azimuth_skins.source_files(bundle / azimuth_skins.PREFIX, catalog, selected_ids) if selected_ids else {}
    skin_digest = None
    with tempfile.TemporaryDirectory(prefix="azimuth-skins-check-") as temporary:
        output = Path(temporary)
        if selected_ids:
            pack = output / azimuth_skins.MOD_NAME
            archive = azimuth_skins.build_pbo(bundle / azimuth_skins.PREFIX, pack, catalog, selected_ids)
            first = archive.read_bytes()
            repeated = azimuth_skins.build_pbo(bundle / azimuth_skins.PREFIX, pack, catalog, selected_ids)
            if repeated.read_bytes() != first:
                raise ValueError("The selected skin PBO was not deterministic.")
            skin_digest = sha256(first).hexdigest()
        script_pack = output / skin_showcase.MOD_NAME
        script_archive = build_script_pbo(root / skin_showcase.SOURCE, script_pack, skin_showcase.PREFIX)
        first_script = script_archive.read_bytes()
        repeated_script = build_script_pbo(root / skin_showcase.SOURCE, script_pack, skin_showcase.PREFIX)
        if repeated_script.read_bytes() != first_script:
            raise ValueError("The showcase script PBO was not deterministic.")
        script_digest = sha256(first_script).hexdigest()
        random_archive = random_skin_mode.build_pbo(
            root / random_skin_mode.SOURCE, output / random_skin_mode.MOD_NAME, catalog, choices)
        first_random = random_archive.read_bytes()
        repeated_random = random_skin_mode.build_pbo(
            root / random_skin_mode.SOURCE, output / random_skin_mode.MOD_NAME, catalog, choices)
        if repeated_random.read_bytes() != first_random:
            raise ValueError("The random-finish script PBO was not deterministic.")
        random_digest = sha256(first_random).hexdigest()
        vehicle_archive = build_script_pbo(root / vehicle_showcase.SOURCE,
                                           output / vehicle_showcase.MOD_NAME, vehicle_showcase.PREFIX)
        first_vehicle = vehicle_archive.read_bytes()
        repeated_vehicle = build_script_pbo(root / vehicle_showcase.SOURCE,
                                            output / vehicle_showcase.MOD_NAME, vehicle_showcase.PREFIX)
        if repeated_vehicle.read_bytes() != first_vehicle:
            raise ValueError("The test-go-kart script PBO was not deterministic.")
        vehicle_digest = sha256(first_vehicle).hexdigest()
    return {
        "catalog_finishes": len(catalog["skins"]),
        "selected_ids": selected_ids,
        "variant_count": sum(len(weapon["variants"]) for weapon in selected["weapons"]),
        "showcase_count": len(skin_showcase.config_for(catalog, choices)["weapon_classes"]),
        "packed_file_count": len(files),
        "skin_sha256": skin_digest,
        "script_sha256": script_digest,
        "random_script_sha256": random_digest,
        "vehicle_showcase_script_sha256": vehicle_digest,
        "random_skins_enabled": choices["random_skins_enabled"],
        "random_skin_chance_percent": choices["random_skin_chance_percent"],
        "vehicle_wraps_enabled": choices["vehicle_wraps_enabled"],
        "vehicle_wrap_chance_percent": choices["vehicle_wrap_chance_percent"],
        "vehicle_test_enabled": choices["vehicle_test_enabled"],
        "vehicle_test_class": vehicle_configuration["vehicle_class"],
        "vehicle_test_ready": vehicle_configuration["vehicle_test_enabled"],
    }


def main() -> int:
    root = APP_ROOT
    try:
        result = check_sources(root)
        print(f"SOURCE CHECKS: PASS ({result['catalog_finishes']} catalog finishes and previews)")
        print(f"SELECTED: {', '.join(result['selected_ids']) or '(none)'}")
        print(f"SELECTED PACK: {result['variant_count']} normal weapon variants, "
              f"{result['showcase_count']} test M4A1 classes, {result['packed_file_count']} files")
        if result["skin_sha256"]:
            print(f"DETERMINISTIC SKIN PBO SHA-256: {result['skin_sha256']}")
        else:
            print("SKIN PBO: NONE (all finishes are disabled)")
        print(f"DETERMINISTIC SHOWCASE SCRIPT PBO SHA-256: {result['script_sha256']}")
        print(f"DETERMINISTIC RANDOM-FINISH PBO SHA-256: {result['random_script_sha256']}")
        print(f"DETERMINISTIC TEST-GO-KART SCRIPT PBO SHA-256: {result['vehicle_showcase_script_sha256']}")
        print(f"RANDOM FINISHES: {'ON' if result['random_skins_enabled'] else 'OFF'}, "
              f"{result['random_skin_chance_percent']}% configured chance on compatible weapons, parts and clothing")
        print(f"VEHICLE WRAPS: {'ON' if result['vehicle_wraps_enabled'] else 'OFF'}, "
              f"{result['vehicle_wrap_chance_percent']}% configured chance per eligible vehicle; "
              "compatible body panels only, not every model")
        print(f"ONE-TIME TEST GO-KART: {'ON' if result['vehicle_test_enabled'] else 'OFF'}, "
              f"{result['vehicle_test_class']} "
              f"({'ready' if result['vehicle_test_ready'] else 'paused or off'}; "
              "requires MBM GoKart, selected wraps at 100%, its unused receipt and safe placement)")
        print("PBO checks used a temporary folder. Installed mods and settings were unchanged.")
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"SOURCE CHECKS: FAIL: {error}")
        print("DAYZ TOOL SIGNING: NOT RUN")
        print("DAYZ COMPILE / JOIN / VISUAL / PERSISTENCE: NOT RUN")
        return 1
    print("DAYZ TOOL SIGNING: NOT RUN (the manager signs the client skin pack when Avalon starts)")
    print("DAYZ COMPILE / JOIN / VISUAL / PERSISTENCE: NOT RUN (requires your PC game)")
    print("Packing source is not compilation or an in-game compatibility result.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
