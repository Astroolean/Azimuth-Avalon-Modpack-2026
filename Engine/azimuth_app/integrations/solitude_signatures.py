"""Verify the installed Solitude Workshop release against its public keys."""

from contextlib import suppress
from hashlib import sha256
import os
from pathlib import Path
import struct
from tempfile import NamedTemporaryFile, TemporaryDirectory

from azimuth_app.integrations.hunter_signing import run_tool, tools_directory
from azimuth_app.core.worlds import children


WORKSHOP_ID = "3776514672"
MAIN_PBO = "solitudeguncase.pbo"
_SIGNATURE_LIMIT = 32 * 1024
_PUBLIC_HEADER = struct.Struct("<I12sII")
_RSA_SIGN = b"\x06\x02\x00\x00\x00\x24\x00\x00RSA1"


def _small_file(path: Path) -> bytes:
    with path.open("rb") as stream:
        return stream.read(_SIGNATURE_LIMIT + 1)


def _public_key(data: bytes, *, signature: bool) -> tuple[bytes, bytes]:
    """Read the public-key prefix documented by Bohemia for BI keys."""
    name, separator, blob = data.partition(b"\0")
    if not separator or not 1 <= len(name) <= 255 or len(blob) < _PUBLIC_HEADER.size:
        raise ValueError("Invalid BI signing key header.")
    blob_length, header, bits, exponent = _PUBLIC_HEADER.unpack_from(blob)
    size = bits // 8
    end = len(name) + 1 + 4 + blob_length
    if (len(data) > _SIGNATURE_LIMIT or header != _RSA_SIGN or bits not in (512, 1024, 2048, 3072, 4096)
            or blob_length != 20 + size or exponent < 3 or exponent % 2 == 0
            or (signature and len(data) < end + 4 + size) or (not signature and len(data) != end)):
        raise ValueError("Invalid BI public-key length or format.")
    return name, data[:end]


def _signature_keys(addons: Path) -> dict[bytes, bytes]:
    files = children(addons)
    pbos = {name for name, path in files.items() if name.endswith(".pbo") and path.is_file()}
    if MAIN_PBO not in pbos:
        raise ValueError("Solitude Storage is missing SolitudeGunCase.pbo. Let Steam finish updating the mod.")
    keys = {}
    signed_pbos = set()
    for name, path in files.items():
        if not name.endswith(".bisign") or ".pbo." not in name or not path.is_file():
            continue
        pbo_name = name.split(".pbo.", 1)[0] + ".pbo"
        if pbo_name not in pbos:
            continue
        try:
            authority, key = _public_key(_small_file(path), signature=True)
        except ValueError as error:
            raise ValueError(f"Solitude Storage has an invalid signature file ({path.name}). Repair its Steam download.") from error
        previous = keys.setdefault(authority.lower(), key)
        if previous != key:
            raise ValueError("Solitude Storage ships conflicting public keys for the same signer.")
        signed_pbos.add(pbo_name)
    if missing := sorted(pbos - signed_pbos):
        raise ValueError(f"Solitude Storage is missing a Workshop signature for {missing[0]}. "
                         "Let Steam finish updating the mod or repair its download.")
    return keys


def prepare(workshop: Path, server_keys: Path, libraries: list[Path]) -> int:
    """Install only public keys that verify the original local Workshop PBOs."""
    if workshop.name != WORKSHOP_ID:
        raise ValueError("Solitude Storage must come from its Steam Workshop installation.")
    addons = children(workshop).get("addons")
    if addons is None:
        raise ValueError("Solitude Storage is missing its Workshop Addons folder.")
    keys = _signature_keys(addons)
    checker = tools_directory(libraries) / "DSCheckSignatures.exe"
    if not checker.is_file():
        raise RuntimeError("DayZ Tools is missing DSCheckSignatures.exe. Verify DayZ Tools in Steam and retry.")

    installed = {}
    for name, path in children(server_keys).items():
        if not name.endswith(".bikey") or not path.is_file():
            continue
        with suppress(ValueError):
            authority, key = _public_key(_small_file(path), signature=False)
            if authority.lower() in keys and keys[authority.lower()] != key:
                raise RuntimeError(f"DayZ Server has a conflicting Solitude public key ({path.name}). "
                                   "Move that key out of the server keys folder, then retry.")
            installed[authority.lower()] = key

    with TemporaryDirectory(prefix="azimuth-solitude-") as temporary:
        check_keys = Path(temporary)
        for index, key in enumerate(keys.values(), 1):
            (check_keys / f"Solitude-{index}.bikey").write_bytes(key)
        try:
            run_tool(checker, [str(addons), str(check_keys)], checker.parent)
        except RuntimeError as error:
            raise RuntimeError(f"Solitude Storage did not pass DayZ Tools signature verification. {error}") from error

    server_keys.mkdir(parents=True, exist_ok=True)
    added = 0
    for authority, key in keys.items():
        if installed.get(authority) == key:
            continue
        destination = server_keys / f"Azimuth-Solitude-{sha256(authority).hexdigest()[:16]}.bikey"
        if os.path.lexists(destination):
            raise RuntimeError(f"DayZ Server already has a different file named {destination.name}. "
                               "Move it out of the server keys folder, then retry.")
        with NamedTemporaryFile(prefix=".azimuth-key-", dir=server_keys, delete=False) as stream:
            temporary_key = Path(stream.name)
            try:
                stream.write(key)
                stream.close()
                os.link(temporary_key, destination)
            finally:
                temporary_key.unlink(missing_ok=True)
        added += 1
    return added
