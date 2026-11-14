"""Sign the local Hunter Mods pack using Steam's DayZ Tools."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import tempfile

from azimuth_app.network.server_discovery import DiscoveryDataError, _installation_under_library, parse_keyvalues
from azimuth_app.core.worlds import atomic_text

TOOL_APP_ID = "830640"
KEY_PREFIX = "AzimuthHunter_"
KEY_NAME = re.compile(r"AzimuthHunter_[0-9a-f]{16}")
TOOLS_INSTALL_MESSAGE = (
    "The Hunter Mods pack needs signing before the first launch. "
    "In Steam, install the free DayZ Tools (Library > Tools > DayZ Tools), "
    "then click Start server again."
)


class SigningToolsMissing(RuntimeError):
    """Steam DayZ Tools are needed for the local Hunter Mods signatures."""


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def tools_directory(steam_libraries: list[Path]) -> Path:
    for library in steam_libraries:
        default = library / "steamapps" / "common" / "DayZ Tools"
        manifest = library / "steamapps" / f"appmanifest_{TOOL_APP_ID}.acf"
        installation = default
        try:
            if manifest.stat().st_size <= 1024 * 1024:
                app = parse_keyvalues(manifest.read_text(encoding="utf-8-sig")).get("appstate", {})
                if isinstance(app, dict) and app.get("appid") == TOOL_APP_ID:
                    installation = _installation_under_library(library, app.get("installdir")) or default
        except (OSError, UnicodeError, DiscoveryDataError):
            pass
        for location in dict.fromkeys((installation, default)):
            tools = location / "Bin" / "DsUtils"
            if all((tools / name).is_file() for name in ("DSCreateKey.exe", "DSSignFile.exe")):
                return tools
    raise SigningToolsMissing(TOOLS_INSTALL_MESSAGE)


def run_tool(executable: Path, arguments: list[str], directory: Path) -> None:
    try:
        result = subprocess.run(
            [str(executable), *arguments], cwd=directory, capture_output=True,
            errors="replace", timeout=120, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(f"DayZ Tools could not run {executable.name}: {error}") from error
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()[-300:]
        raise RuntimeError(f"{executable.name} failed ({result.returncode}). {detail}")


def nonempty_file(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0


def read_records(path: Path) -> dict:
    try:
        contents = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return contents if isinstance(contents, dict) else {}


def matching_key(key_dir: Path) -> str | None:
    if not key_dir.is_dir():
        return None
    pairs = [
        path.stem for path in key_dir.glob("*.biprivatekey")
        if KEY_NAME.fullmatch(path.stem) and nonempty_file(path)
        and nonempty_file(key_dir / f"{path.stem}.bikey")
    ]
    if len(pairs) != 1:
        raise RuntimeError(
            f"The local signing keys are incomplete. Move Config/{key_dir.parent.name}/Key "
            "aside, then retry to generate a fresh pair."
        )
    return pairs[0]


def valid_signature(pbo: Path, key_name: str, pbo_hash: str, record: object) -> bool:
    signature = pbo.with_name(f"{pbo.name}.{key_name}.bisign")
    return (
        isinstance(record, dict) and record.get("pbo_sha256") == pbo_hash
        and nonempty_file(signature) and record.get("signature_sha256") == file_hash(signature)
    )


def ensure_signed(pack_dir: Path, steam_libraries: list[Path], package_root: Path,
                  *, state_name: str = "HunterSigning") -> None:
    """Sign local PBOs and publish only the public key to the mod."""
    addons = pack_dir / "Addons"
    pbos = sorted(
        (path for path in addons.iterdir() if path.is_file() and path.suffix.casefold() == ".pbo"),
        key=lambda path: path.name.casefold(),
    )
    if not pbos:
        raise RuntimeError(f"{pack_dir.name} contains no PBO files in its Addons folder.")

    keys = pack_dir / "Keys"
    state = package_root / "Config" / state_name
    key_dir = state / "Key"
    records_path = state / "signatures.json"
    records = read_records(records_path)
    current_hashes = {path.name: file_hash(path) for path in pbos}
    recorded_name = records.get("key_name")
    if isinstance(recorded_name, str) and KEY_NAME.fullmatch(recorded_name):
        recorded_public = keys / f"{recorded_name}.bikey"
        recorded_files = records.get("files")
        if (nonempty_file(recorded_public)
                and records.get("public_key_sha256") == file_hash(recorded_public)
                and isinstance(recorded_files, dict)
                and all(valid_signature(pbo, recorded_name, current_hashes[pbo.name],
                                        recorded_files.get(pbo.name)) for pbo in pbos)):
            return

    key_name = matching_key(key_dir)
    if key_name is None:
        tools = tools_directory(steam_libraries)
        state.mkdir(parents=True, exist_ok=True)
        key_name = KEY_PREFIX + secrets.token_hex(8)
        with tempfile.TemporaryDirectory(prefix="key-", dir=state) as temporary:
            staging = Path(temporary) / "Key"
            staging.mkdir()
            run_tool(tools / "DSCreateKey.exe", [key_name], staging)
            if not all(nonempty_file(staging / f"{key_name}.{extension}")
                       for extension in ("biprivatekey", "bikey")):
                raise RuntimeError("DayZ Tools did not produce both Hunter Mods signing keys.")
            staging.rename(key_dir)
    else:
        tools = None

    public_name = f"{key_name}.bikey"
    public_key = key_dir / public_name
    public_hash = file_hash(public_key)
    old_records = records.get("files", {})
    if not isinstance(old_records, dict) or records.get("public_key_sha256") != public_hash:
        old_records = {}
    signed = {}
    for pbo in pbos:
        signature_name = f"{pbo.name}.{key_name}.bisign"
        signature = addons / signature_name
        pbo_hash = current_hashes[pbo.name]
        previous = old_records.get(pbo.name)
        if valid_signature(pbo, key_name, pbo_hash, previous):
            signed[pbo.name] = previous
            continue

        if tools is None:
            tools = tools_directory(steam_libraries)
        with tempfile.TemporaryDirectory(prefix="sign-", dir=addons) as temporary:
            staging = Path(temporary)
            pbo_copy = staging / pbo.name
            shutil.copyfile(pbo, pbo_copy)
            if file_hash(pbo_copy) != pbo_hash:
                raise RuntimeError(f"{pbo.name} changed while signing. Wait for the copy to finish and retry.")
            run_tool(
                tools / "DSSignFile.exe",
                [str(key_dir / f"{key_name}.biprivatekey"), str(pbo_copy)],
                staging,
            )
            generated = staging / signature_name
            if not nonempty_file(generated):
                raise RuntimeError(f"DayZ Tools did not sign {pbo.name}.")
            signature_hash = file_hash(generated)
            generated.replace(signature)
        signed[pbo.name] = {"pbo_sha256": pbo_hash, "signature_sha256": signature_hash}

    keys.mkdir(parents=True, exist_ok=True)
    published = keys / public_name
    if not nonempty_file(published) or file_hash(published) != public_hash:
        with tempfile.TemporaryDirectory(prefix="public-", dir=keys) as temporary:
            staging = Path(temporary) / public_name
            shutil.copyfile(public_key, staging)
            staging.replace(published)
    updated = {"key_name": key_name, "public_key_sha256": public_hash, "files": signed}
    if records != updated:
        atomic_text(records_path, json.dumps(updated, indent=2, sort_keys=True) + "\n")
