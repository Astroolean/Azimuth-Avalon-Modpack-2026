"""Check installed DayZ version resources before loading 1.30 transportation."""
import re
from pathlib import Path

from azimuth_app.network.server_discovery import read_file_version


def require_dayz130(executable: Path, label: str) -> str:
    observed = read_file_version(executable)
    if observed.get('status') == 'OBSERVED':
        # The fixed file version identifies this executable. The independent
        # product version may describe an older enclosing product; it must not
        # veto a supported binary or rescue an unsupported one. Use it only
        # when no readable file version is available.
        for field in ('file_version', 'product_version'):
            value = observed.get(field)
            if not isinstance(value, str) or len(value) > 64:
                continue
            match = re.fullmatch(r'([0-9]+)\.([0-9]+)(?:\.[0-9]+){0,2}', value)
            if not match:
                continue
            version = (int(match[1]), int(match[2]))
            if version < (1, 30):
                raise RuntimeError(f'{label} is version {value}. The transport update '
                                   f'needs DayZ 1.30 or newer. Update {label} in Steam before playing.')
            return value
    raise RuntimeError(f'Azimuth could not verify the installed {label} version. '
                       'The transport update needs DayZ 1.30 or newer. '
                       f'Update and verify {label} in Steam, then try again.')


def require_matching_dayz_versions(client_executable: Path,
                                  server_executable: Path, *,
                                  loaded_server_version: str | None = None) -> tuple[str, str]:
    """Require matching supported fixed executable versions before joining.

    A2S and product version strings can use different formats or identify a
    different product. Read both executable file resources directly instead.
    A captured server-start version also guards against Steam replacing the
    executable while an older server process remains loaded. This is a local
    compatibility preflight, not proof of an in-game join.
    """
    versions = []
    for executable, label in ((client_executable, "DayZ"),
                              (server_executable, "DayZ Server")):
        observed = read_file_version(executable)
        value = observed.get("file_version")
        if (observed.get("status") != "OBSERVED" or not isinstance(value, str)
                or len(value) > 64 or re.fullmatch(r"[0-9]+(?:\.[0-9]+){3}", value) is None):
            raise RuntimeError(f"Azimuth could not compare the installed {label} executable version. "
                               "Update and verify both DayZ and DayZ Server in Steam, then try again.")
        version = tuple(int(part) for part in value.split("."))
        if any(part > 65535 for part in version):
            raise RuntimeError(f"Azimuth could not compare the installed {label} executable version. "
                               "Update and verify both DayZ and DayZ Server in Steam, then try again.")
        if version[:2] < (1, 30):
            raise RuntimeError(f"{label} is version {value}. The transport update "
                               f"needs DayZ 1.30 or newer. Update {label} in Steam before playing.")
        versions.append((value, version))
    if versions[0][1] != versions[1][1]:
        raise RuntimeError(f"DayZ is version {versions[0][0]}, but DayZ Server is version {versions[1][0]}. "
                           "The server requires matching builds. Update and verify both in Steam, "
                           "then stop and restart Avalon before playing.")
    if loaded_server_version is not None:
        if (not isinstance(loaded_server_version, str) or len(loaded_server_version) > 64
                or re.fullmatch(r"[0-9]+(?:\.[0-9]+){3}", loaded_server_version) is None):
            raise RuntimeError("Azimuth could not verify the running DayZ Server's recorded executable version. "
                               "Stop and restart Avalon before playing.")
        loaded = tuple(int(part) for part in loaded_server_version.split("."))
        if any(part > 65535 for part in loaded):
            raise RuntimeError("Azimuth could not verify the running DayZ Server's recorded executable version. "
                               "Stop and restart Avalon before playing.")
        if versions[0][1] != loaded:
            raise RuntimeError(f"The running DayZ Server is version {loaded_server_version}, "
                               f"but the installed client and server are version {versions[0][0]}. "
                               "Stop and restart Avalon to load the updated server before playing.")
    return versions[0][0], versions[1][0]
