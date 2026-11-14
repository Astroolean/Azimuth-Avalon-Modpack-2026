"""Build Azimuth's native DayZ script PBOs."""
from azimuth_app.paths import APP_ROOT

from argparse import ArgumentParser
from hashlib import sha1
import os
from pathlib import Path
import re
import stat
from struct import Struct
from tempfile import NamedTemporaryFile


PREFIX = "azimuthlooks"
SOURCE = APP_ROOT / "Visuals" / "NativeColor" / PREFIX
PBO_NAME = f"{PREFIX}.pbo"
MAX_SOURCE_FILES = 32
MAX_SOURCE_BYTES = 256 * 1024
PBO_ENTRY = Struct("<5I")
PBO_PROPERTIES = b"\x00sreV" + bytes(16)
PBO_TERMINATOR = bytes(1 + PBO_ENTRY.size)
SCRIPT_PATH = re.compile(r"scripts/(?:3_game|4_world|5_mission)/(?:[a-z0-9_-]+/)*[a-z0-9_-]+\.c")


def _ordinary(path: Path, expected_type) -> None:
    info = path.lstat()
    if not expected_type(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ValueError(f"Expected a regular preview file or directory at {path}.")


def _read_file(path: Path, limit: int) -> bytes:
    _ordinary(path, stat.S_ISREG)
    with path.open("rb") as stream:
        return stream.read(limit + 1)


def _source_files(source: Path) -> dict[str, bytes]:
    _ordinary(source, stat.S_ISDIR)
    files = {}
    total = 0

    def report_walk_error(error: OSError) -> None:
        raise error

    for root, directories, names in os.walk(source, onerror=report_walk_error, followlinks=False):
        folder = Path(root)
        for directory in directories:
            _ordinary(folder / directory, stat.S_ISDIR)
        for name in names:
            path = folder / name
            relative = path.relative_to(source).as_posix()
            if relative != "config.cpp" and not SCRIPT_PATH.fullmatch(relative):
                raise ValueError(f"Unexpected file in the preview source: {relative}.")
            if len(files) == MAX_SOURCE_FILES:
                raise ValueError("The preview contains too many source files.")
            content = _read_file(path, MAX_SOURCE_BYTES - total)
            total += len(content)
            if total > MAX_SOURCE_BYTES:
                raise ValueError("The preview exceeds its source size limit.")
            if not content or b"\x00" in content:
                raise ValueError(f"Expected a nonempty text file at {relative}.")
            content.decode("utf-8-sig")
            files[relative] = content
    if "config.cpp" not in files or len(files) < 2:
        raise ValueError("The preview source requires config.cpp and at least one script.")
    return files


def _pbo_bytes(files: dict[str, bytes], prefix: str) -> bytes:
    # DayZ add-on bank. First the virtual prefix and all file headers, then
    # uncompressed data in header order, then the archive checksum.
    headers = bytearray(PBO_PROPERTIES)
    headers.extend(f"prefix\0{prefix}\0\0".encode("ascii"))
    entries = sorted(files.items())
    for name, content in entries:
        headers.extend(name.replace("/", "\\").encode("ascii") + b"\x00")
        headers.extend(PBO_ENTRY.pack(0, len(content), 0, 0, len(content)))
    headers.extend(PBO_TERMINATOR)
    archive = bytes(headers) + b"".join(content for _, content in entries)
    return archive + b"\x00" + sha1(archive, usedforsecurity=False).digest()


def build_native_color(source: Path, output: Path) -> Path:
    """Pack source into a caller-owned preview mod directory and return its PBO."""
    return build_script_pbo(source, output, PREFIX)


def build_script_pbo(source: Path, output: Path, prefix: str) -> Path:
    """Build an uncompressed DayZ add-on from Azimuth's bundled script source."""
    if not re.fullmatch(r"[a-z0-9_]+", prefix):
        raise ValueError("Invalid DayZ add-on prefix.")
    payload = _pbo_bytes(_source_files(source), prefix)
    output.mkdir(parents=True, exist_ok=True)
    _ordinary(output, stat.S_ISDIR)
    addons = output / "Addons"
    addons.mkdir(exist_ok=True)
    _ordinary(addons, stat.S_ISDIR)
    target = addons / f"{prefix}.pbo"
    try:
        if _read_file(target, len(payload)) == payload:
            return target
    except FileNotFoundError:
        pass
    temporary = None
    try:
        with NamedTemporaryFile(dir=addons, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return target


def main() -> None:
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="Destination preview mod directory")
    parser.add_argument("--source", type=Path, default=SOURCE, help="Script source directory")
    arguments = parser.parse_args()
    try:
        path = build_native_color(arguments.source, arguments.output)
    except (OSError, ValueError) as error:
        parser.exit(1, f"Could not build the native color preview: {error}\n")
    print(path)


if __name__ == "__main__":
    main()
