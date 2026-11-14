"""Repair missing *public* BI signing keys for installed Avalon vehicle mods.

DayZ can start a server while silently ignoring a client-required Workshop PBO
whose signing key is absent.  Merely passing its directory in -mod= is not proof
that the engine accepted it.  Extract the embedded public key from the publisher's
original .bisign, verify it against the original PBOs using DayZ Tools, and install
only that public key.  Never edit, repack, or re-sign a publisher's PBO.
"""
from __future__ import annotations

from hashlib import sha256
import os
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory

# On Windows os.rename is an atomic, no-overwrite move. On POSIX it can
# replace an existing file, so use a no-overwrite hard link there instead.
_WINDOWS_NO_REPLACE_RENAME = os.name == "nt"

from azimuth_app.core.worlds import children
from azimuth_app.integrations.hunter_signing import run_tool, tools_directory
from azimuth_app.integrations.solitude_signatures import _public_key, _small_file


def _mod_signers(folder: Path) -> dict[bytes, bytes]:
    """Read BI public-key identities from signatures of every publisher PBO."""
    addons = children(folder).get('addons')
    if addons is None or not addons.is_dir():
        raise RuntimeError(f'{folder.name} has no Addons directory. Repair its Steam Workshop download.')
    files = children(addons)
    pbos = {name for name, path in files.items() if name.endswith('.pbo') and path.is_file()}
    if not pbos:
        raise RuntimeError(f'{folder.name} contains no publisher PBOs. Repair its Steam Workshop download.')
    covered: set[str] = set()
    signers: dict[bytes, bytes] = {}
    for name, path in files.items():
        if not name.endswith('.bisign') or '.pbo.' not in name or not path.is_file():
            continue
        pbo = name.split('.pbo.', 1)[0] + '.pbo'
        if pbo not in pbos:
            continue
        try:
            authority, public = _public_key(_small_file(path), signature=True)
        except ValueError as error:
            raise RuntimeError(f'{folder.name} contains an invalid publisher signature ({path.name}). '
                               'Repair the original mod in Steam.') from error
        # A publisher may keep signatures from more than one signing-key
        # generation. Distinguish keys by their complete public bytes, not by
        # the (sometimes reused) signer name.
        signers[sha256(public).digest()] = public
        covered.add(pbo)
    if missing := sorted(pbos - covered):
        raise RuntimeError(f'{folder.name} is missing a publisher .bisign for {missing[0]}. '
                           'Repair the original mod in Steam before joining Avalon.')
    return signers


def _installed_keys(server_keys: Path) -> set[bytes]:
    """Locate installed public BI keys regardless of filename or letter case."""
    contents: set[bytes] = set()
    for name, path in children(server_keys).items():
        if not name.endswith('.bikey') or not path.is_file():
            continue
        try:
            _identity, public = _public_key(_small_file(path), signature=False)
        except ValueError:
            continue  # Do not remove or rewrite unrelated server keys.
        contents.add(public)
    return contents


def _publish_key(server_keys: Path, public: bytes) -> bool:
    """Publish a verified public key without replacing or locking a live key.

    In particular, NEVER unlink a NamedTemporaryFile while its own handle is
    open: Windows denies this with WinError 32 even when no DayZ process runs.
    Staging has a .tmp suffix so DayZ never tries to load an incomplete .bikey.
    """
    name = f'Azimuth-Workshop-{sha256(public).hexdigest()[:20]}.bikey'
    destination = server_keys / name
    if os.path.lexists(destination):
        if destination.is_file() and destination.read_bytes() == public:
            return False
        raise RuntimeError(f'DayZ Server has a conflicting Workshop key file ({name}). '
                           'The existing file was left untouched.')
    staging = None
    try:
        with NamedTemporaryFile(prefix='.azimuth-public-', suffix='.tmp', dir=server_keys,
                                delete=False) as file:
            staging = Path(file.name)
            file.write(public)
            file.flush()
        # The temporary handle is CLOSED before publishing or cleaning up.
        # Rename on Windows moves the file atomically and refuses to overwrite.
        # On other systems hard-linking prevents overwrite in a concurrent launch.
        try:
            if _WINDOWS_NO_REPLACE_RENAME:
                os.rename(staging, destination)
            else:
                os.link(staging, destination)
        except (FileExistsError, PermissionError) as error:
            if os.path.lexists(destination):
                if destination.is_file() and destination.read_bytes() == public:
                    return False
                raise RuntimeError(f'DayZ Server has a conflicting Workshop key file ({name}). '
                                   'The existing file was left untouched.') from error
            raise RuntimeError(f'Windows could not install Workshop key {name}. '
                               'Close the DayZ Server process and try Play Avalon again.') from error
        return True
    finally:
        if staging is not None:
            try:
                staging.unlink(missing_ok=True)
            except PermissionError:
                # An antivirus may briefly hold a harmless .tmp file. Never
                # fail the launch after the verified .bikey is already present.
                pass


def _cleanup_legacy_staging(server_keys: Path) -> None:
    """Remove only redundant .bikey temp links leaked by the old Windows bug.

    A temp file is eligible only if its bytes match the canonical installed
    key with the same hash. Never touch publisher keys or unrecognized files.
    Failures caused by a running DayZ Server/antivirus are safely ignored.
    """
    if not server_keys.is_dir():
        return
    for candidate in server_keys.glob('.azimuth-public-*.bikey'):
        try:
            if not candidate.is_file() or candidate.is_symlink():
                continue
            public = _small_file(candidate)
            canonical = server_keys / f'Azimuth-Workshop-{sha256(public).hexdigest()[:20]}.bikey'
            if canonical.is_file() and canonical.read_bytes() == public:
                candidate.unlink()
        except OSError:
            continue


def repair_vehicle_keys(workshop: list[Path], server_keys: Path,
                        libraries: list[Path], vehicle_ids: frozenset[str]) -> tuple[str, ...]:
    """Install verified missing public keys for optional vehicle packs only.

    The server key folder is checked *after* normal publisher Keys/ copy. Native
    signatures are verified on the user's PC with DSCheckSignatures before any
    missing public key is installed. Mods with intact keys incur no tool invocation.
    """
    vehicles = [folder for folder in workshop if folder.name in vehicle_ids]
    if not vehicles:
        return ()
    _cleanup_legacy_staging(server_keys)
    available = _installed_keys(server_keys)
    pending: list[tuple[Path, dict[bytes, bytes]]] = []
    for folder in vehicles:
        signers = _mod_signers(folder)
        if not all(key in available for key in signers.values()):
            pending.append((folder, signers))
    if not pending:
        return ()
    # Validation first. No key is published unless its original publisher PBOs
    # pass DayZ Tools against the public keys embedded in those PBO signatures.
    tools = tools_directory(libraries)
    checker = tools / 'DSCheckSignatures.exe'
    if not checker.is_file():
        raise RuntimeError('DayZ Tools is missing DSCheckSignatures.exe. Verify DayZ Tools in Steam and retry.')
    for folder, signers in pending:
        with TemporaryDirectory(prefix='azimuth-workshop-keys-') as temp:
            target = Path(temp)
            for index, public in enumerate(signers.values(), 1):
                (target / f'publisher-{index}.bikey').write_bytes(public)
            try:
                run_tool(checker, [str(children(folder)['addons']), str(target)], tools)
            except RuntimeError as error:
                raise RuntimeError(f'{folder.name} failed publisher PBO signature verification; '
                                   f'no Workshop keys were replaced. {error}') from error
    server_keys.mkdir(parents=True, exist_ok=True)
    repaired = []
    for folder, signers in pending:
        count = 0
        for public in signers.values():
            if public not in available:
                if _publish_key(server_keys, public):
                    count += 1
                available.add(public)
        if count:
            repaired.append(f'{folder.name}: {count} public key(s)')
    return tuple(repaired)
