"""Archive only known files from the previous flat layout after an overlay update.

Called while the dashboard owns its existing instance lock. No world, settings,
installed mod, unknown user file or current package file is a migration target.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import stat
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

from azimuth_app.paths import ENGINE_ROOT


def _ordinary(path: Path, root: Path, directory: bool = False):
    """Do not traverse links or Windows junctions inside the installation."""
    relative = path.relative_to(root)
    current = root
    for part in relative.parts:
        current = current / part
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise OSError(f"Cannot organize a linked legacy path: {relative.as_posix()}")
        expected_dir = current != path or directory
        if not (stat.S_ISDIR(info.st_mode) if expected_dir else stat.S_ISREG(info.st_mode)):
            raise OSError(f"Unexpected legacy path type: {relative.as_posix()}")
    return path.stat()


def _digest(stream) -> str:
    digest = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(block)
    return digest.hexdigest()


def _fingerprint(path: Path, root: Path) -> tuple:
    info = _ordinary(path, root)
    with path.open("rb") as stream:
        digest = _digest(stream)
    return (info.st_size, info.st_mtime_ns, info.st_ino, digest)


def _legacy_paths(root: Path) -> list[Path]:
    manifest = json.loads((ENGINE_ROOT / "azimuth_app/core/legacy_layout.json").read_text(encoding="utf-8"))
    paths = []
    for name in manifest["paths"]:
        relative = Path(name)
        allowed = (len(relative.parts) == 1 and relative.suffix in (".bat", ".txt")
                   and relative.name != "START_SERVER.bat") or (
            len(relative.parts) == 2 and relative.parts[0] in ("Engine", "Audit"))
        if not allowed or relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Invalid legacy layout manifest. Extract the complete update again.")
        path = root / relative
        try:
            _ordinary(path, root)
        except FileNotFoundError:
            continue
        paths.append(path)
    cache = root / "Engine/__pycache__"
    try:
        _ordinary(cache, root, directory=True)
    except FileNotFoundError:
        return paths
    names = "|".join(re.escape(name) for name in manifest["cache_modules"])
    pattern = re.compile(rf"(?:{names})\.cpython-\d+(?:\.opt-\d+)?\.pyc")
    for path in sorted(cache.iterdir()):
        if pattern.fullmatch(path.name):
            _ordinary(path, root)
            paths.append(path)
    return paths


def migrate_layout(root: Path) -> Path | None:
    """Verify an exact ZIP backup before removing any old known file.

    Repeated launches are a no-op. Interrupted cleanup can safely resume into a
    separate archive. Unknown files and modified old files are never discarded:
    the latter are included byte-for-byte in the verified archive.
    """
    root = root.resolve()
    paths = _legacy_paths(root)
    if not paths:
        return None
    expected = {path: _fingerprint(path, root) for path in paths}
    folder = root / "Engine/Archive"
    _ordinary(folder.parent, root, directory=True)
    folder.mkdir(exist_ok=True)
    _ordinary(folder, root, directory=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive = folder / f"LegacyLayout-{stamp}-{uuid4().hex[:8]}.zip"
    records = [{"path": path.relative_to(root).as_posix(), "bytes": values[0], "sha256": values[3]}
               for path, values in expected.items()]
    created = False
    try:
        with ZipFile(archive, "x", compression=ZIP_DEFLATED, strict_timestamps=False) as output:
            created = True
            for path in paths:
                if _fingerprint(path, root) != expected[path]:
                    raise OSError("A legacy file changed during organization. Close old Azimuth windows and retry.")
                output.write(path, path.relative_to(root).as_posix())
            output.writestr("_layout_manifest.json", json.dumps({"files": records}, indent=2) + "\n")
        with ZipFile(archive) as stored:
            if stored.testzip() is not None:
                raise OSError("The legacy archive failed its integrity check; original files were retained.")
            for record in records:
                with stored.open(record["path"]) as stream:
                    if _digest(stream) != record["sha256"]:
                        raise OSError("The legacy archive differs from its source; original files were retained.")
        # Check every source again before removing any original.
        if any(_fingerprint(path, root) != expected[path] for path in paths):
            raise OSError("A legacy file changed during organization. Original files were retained; retry after closing old Azimuth windows.")
    except Exception:
        if created:
            archive.unlink(missing_ok=True)
        raise
    # The verified archive must survive any cleanup error or interrupted launch.
    for path in paths:
        if _fingerprint(path, root) != expected[path]:
            raise OSError(f"Legacy cleanup paused. Your backup is in {archive.relative_to(root)}.")
        path.unlink()
    for old_folder in (root / "Engine/__pycache__", root / "Audit"):
        try:
            old_folder.rmdir()  # Only removes an empty directory; unknown files stay.
        except OSError:
            pass
    return archive
