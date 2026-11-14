"""Manual maintenance commands used by the Azimuth dashboard worker."""

from dataclasses import dataclass
from contextlib import ExitStack
from datetime import datetime, timezone
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import tempfile
import time
from zipfile import BadZipFile, ZipFile

from azimuth_app.core.backups import TIME_FORMAT, backup_time, source_type, validate_archive, write_backup, MANAGER_SKIN_FILES, recover_restore
from azimuth_app.core.server_workspace import _check_existing, _open_no_follow
from azimuth_app.core.worlds import CATALOG, mission_candidate, atomic_text
from azimuth_app.client.client_performance import ensure_client_closed


@dataclass(frozen=True)
class BackupRecord:
    path: Path
    created_at: datetime
    size_bytes: int


def _backup_record(path: Path) -> BackupRecord:
    if not stat.S_ISREG(source_type(path)):
        raise ValueError("Choose a regular Azimuth backup ZIP.")
    return BackupRecord(path, datetime.fromtimestamp(backup_time(path)).astimezone(), path.stat().st_size)


def list_backups(root: Path, *, limit: int = 12) -> tuple[BackupRecord, ...]:
    """Read the newest backup records without opening their archives."""
    folder = root / "Backups"
    if not folder.exists() or not stat.S_ISDIR(source_type(folder)) or limit <= 0:
        return ()
    records = []
    for path in sorted(folder.glob("FreeDayZ_Backup_*.zip"), reverse=True):
        try:
            records.append(_backup_record(path))
        except (OSError, ValueError, OverflowError):
            continue
        if len(records) >= limit:
            break
    return tuple(records)


def verify_backup(root: Path, archive: Path, *, world_id: str | None = None) -> BackupRecord:
    """Verify a selected backup belongs to the active world and has valid save data."""
    folder = root / "Backups"
    if archive.parent != folder or not stat.S_ISDIR(source_type(folder)):
        raise ValueError("Choose a backup from the active world's Backups folder.")
    record = _backup_record(archive)
    world = CATALOG.get(world_id) if world_id is not None else None
    validate_archive(archive, mission_matches=(lambda name: mission_candidate(name, world)) if world else None)
    return record


def restore_backup(azimuth, archive: Path) -> Path | None:
    """Restore stopped world data atomically by component, with complete rollback.

    Connection settings stay current. Archived Workshop/load-order choices and
    global skin choices are restored when present. A safety ZIP, or a directory
    for an incomplete prior world, retains every replaced original.
    """
    server = azimuth.server
    with azimuth.lock, server.lock:
        if azimuth.enabled or azimuth.play_pending or server.backing_up or server._running():
            raise RuntimeError("Stop and pause the server, and wait for its backup before restoring.")
        ensure_client_closed()
        if (server.world_paths.config / "restore-transaction.json").exists():
            server._load_world_paths()
        root = server.world_paths.root
        verify_backup(root, archive, world_id=server.active_world_id)
        server._reserve_launch()
        server._backup_writing = True
        safety = None
        originals = None
        committed = False
        ports = ExitStack()
        try:
            ports.enter_context(server.reserve_stopped_ports())
            with tempfile.TemporaryDirectory(prefix=".azimuth-restore-", dir=root) as temporary:
                staging = Path(temporary)
                snapshot = staging / "archive.zip"
                # Work only from our private copy, avoiding archive changes
                # between validation, extraction, and publication.
                with _open_no_follow(archive) as original, snapshot.open("xb") as target:
                    shutil.copyfileobj(original, target)
                world = server.active_world
                names = validate_archive(snapshot, mission_matches=lambda name: mission_candidate(name, world))
                extracted = staging / "data"
                extracted.mkdir()
                with ZipFile(snapshot) as backup:
                    for name in names:
                        target = extracted.joinpath(*PurePosixPath(name).parts)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with backup.open(name) as source, target.open("xb") as output:
                            shutil.copyfileobj(source, output)
                (extracted / "Profiles").mkdir(exist_ok=True)
                changes = [(extracted / name, root / name, name) for name in ("World", "Profiles")]
                for name in ("Config/avalon-workshop.json", "Config/native-color-preview.enabled",
                             "Mods/load-order.txt", "Mods/ServerOnly/load-order.txt"):
                    if name in names:
                        changes.append((extracted / name, root / name, name))
                for name in MANAGER_SKIN_FILES:
                    archived = "ManagerSettings/" + name
                    if archived in names:
                        changes.append((extracted / archived, server.root / name, archived))
                provenance = extracted / "Config/launch-session.json"
                provenance.parent.mkdir(parents=True, exist_ok=True)
                provenance.write_text(json.dumps({"version": 1, "kind": "restore", "reports": []}) + "\n", encoding="utf-8")
                changes.append((provenance, root / "Config/launch-session.json", "Config/launch-session.json"))
                for _, target, _ in changes:
                    if target.exists() or target.is_symlink():
                        _check_existing(target, target.is_dir())
                    else:
                        existing = next(parent for parent in target.parents if parent.exists())
                        _check_existing(existing, True)
                if (server.world / "Saves").is_dir() and any(path.is_file() for path in (server.world / "Saves").rglob("*")):
                    safety = write_backup(root, time.time(), manager_root=server.root if server.active_world_id == "avalon" else None)
                originals = root / "Backups" / ("BeforeRestore-" + str(time.time_ns()))
                originals.mkdir()
                journal = root / "Config/restore-transaction.json"
                transaction = {"version": 1, "phase": "publishing", "originals": originals.name,
                               "operations": [{"path": relative, "existed": target.exists()}
                                              for _, target, relative in changes]}
                atomic_text(journal, json.dumps(transaction, indent=2) + "\n")
                try:
                    for staged, target, relative in changes:
                        old = originals / relative
                        old.parent.mkdir(parents=True, exist_ok=True)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        had_original = target.exists()
                        if had_original:
                            os.replace(target, old)
                        os.replace(staged, target)
                    server._load_world_paths(recover=False)
                    transaction["phase"] = "committed"
                    atomic_text(journal, json.dumps(transaction, indent=2) + "\n")
                    committed = True
                    journal.unlink()
                except Exception:
                    if committed:
                        # A completed state must not be undone solely because
                        # Windows temporarily prevents journal cleanup.
                        raise RuntimeError("The world was restored; its completion journal could not be removed. Reopen Azimuth to finish cleanup.")
                    recover_restore(root, server.root)
                    server._load_world_paths()
                    raise
                if safety is not None:
                    # The ordinary backup omits launch provenance and certain
                    # config files. Keep the exact displaced originals too.
                    server.events.put(f"Pre-restore ZIP saved: {safety.name}. Exact replaced files: {originals.name}.")
                server.events.put(f"Restored {archive.name}. The server remains stopped.")
                return safety or originals
        finally:
            ports.close()
            server._backup_writing = False
            server._release_launch_lock()
            if (not committed and originals is not None and originals.exists()
                    and not (root / "Config/restore-transaction.json").exists()
                    and not any(path.is_file() for path in originals.rglob("*"))):
                shutil.rmtree(originals)
            azimuth.wake.set()


def backup_now(azimuth) -> Path:
    """Take a verified snapshot of the active world and restore its run state."""
    server = azimuth.server
    with azimuth.lock, server.lock:
        if azimuth.play_pending or server.backing_up:
            raise RuntimeError("Wait for the current server operation to finish before backing up.")
        status = server.status()
        was_running = server.process is not None and server.process.poll() is None
        if was_running and (status["state"] != "online" or status["players"] != 0):
            raise RuntimeError("Manual backup needs an empty online server or a cleanly stopped world.")

        if was_running:
            server._backup_pending = True
            server._backup_state = "Stopping for backup"
            try:
                azimuth._stop_for_switch(require_clean=False)
            except (OSError, RuntimeError, ValueError):
                server._backup_pending = False
                server._backup_state = "Backup delayed"
                if not server._running():
                    server._release_launch_lock()
                azimuth.wake.set()
                raise

        stamp = time.time()
        server._archive_stopped_server(stamp, restart=was_running)
        azimuth.wake.set()
        timestamp = datetime.fromtimestamp(stamp, timezone.utc).strftime(TIME_FORMAT)
        path = server.backups_dir / f"FreeDayZ_Backup_{timestamp}.zip"
        if server._backup_last != backup_time(path) or not path.is_file():
            raise RuntimeError("Azimuth could not finish the backup. Check Server activity for details.")
        if was_running and (server.process is None or server.process.poll() is not None):
            raise RuntimeError(f"Backup saved as {path.name}. DayZ could not restart. Check Server activity.")
        return path
