"""Verify that the DayZ client received its expected mod arguments."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
import ntpath
import os
from pathlib import Path
import re
import stat
from typing import Literal


MAX_HEADER_BYTES = 64 * 1024
ReportSnapshot = dict[Path, tuple[int, int]]
_REPORT_NAME = re.compile(r"DayZ_x64_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.rpt", re.I)
_EXECUTABLE_LINE = re.compile(
    r'^==[ \t]+(?:"(?P<quoted>[^"]*?DayZ_x64\.exe)"|'
    r'(?P<plain>[^"]*?DayZ_x64\.exe))(?:[ \t]+(?P<args>.*))?$', re.I,
)
_COMMAND_SEGMENTS = re.compile(r'(\\*)"|([ \t]+)|([^"\\ \t]+|\\+)')


@dataclass(frozen=True)
class ClientLaunchReport:
    state: Literal["pending", "mods_received", "mods_missing"]
    received_count: int
    expected_count: int
    missing_mods: tuple[str, ...] = ()


def snapshot_client_reports() -> ReportSnapshot:
    """Record ordinary DayZ client logs, excluding the dedicated server logs."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        return {}
    folder = Path(local_app_data) / "DayZ"
    reports: ReportSnapshot = {}
    try:
        for path in folder.iterdir():
            if not _REPORT_NAME.fullmatch(path.name):
                continue
            try:
                info = path.lstat()
            except OSError:
                continue
            if stat.S_ISREG(info.st_mode) and not getattr(info, "st_file_attributes", 0) & 0x400:
                reports[path] = (info.st_mtime_ns, info.st_size)
    except OSError:
        pass
    return reports


def _arguments(command: str) -> Iterator[str]:
    """Tokenize Windows arguments without interpreting path separators."""
    quoted = False
    parts: list[str] = []
    for match in _COMMAND_SEGMENTS.finditer(command):
        backslashes, space, text = match.groups()
        if backslashes is not None:
            count = len(backslashes)
            parts.append("\\" * (count // 2))
            if count % 2:
                parts.append('"')
            else:
                quoted = not quoted
        elif space and not quoted:
            if parts:
                yield "".join(parts)
                parts.clear()
        else:
            parts.append(space or text)
    if quoted:
        raise ValueError("Incomplete DayZ command line.")
    if parts:
        yield "".join(parts)


def _command_mods(path: Path) -> tuple[str, set[str]] | None:
    """Read the bounded command header and return its game folder and mod paths."""
    executable = ""
    command_seen = False
    mods: set[str] = set()
    remaining = MAX_HEADER_BYTES
    try:
        with path.open("rb") as stream:
            for _ in range(8):
                line = stream.readline(remaining)
                remaining -= len(line)
                if not line.endswith(b"\n"):
                    break
                if command_seen and line.rstrip() and set(line.rstrip()) == {ord("=")}:
                    return ntpath.dirname(executable), mods
                try:
                    decoded = line.decode("utf-8-sig")
                except UnicodeDecodeError:
                    decoded = line.decode("mbcs" if os.name == "nt" else "utf-8", errors="replace")
                match = _EXECUTABLE_LINE.fullmatch(decoded.rstrip("\r\n"))
                if match is None:
                    continue
                executable = match.group("quoted") or match.group("plain")
                command = match.group("args") or ""
                if command or command_seen:
                    mods.clear()
                    for argument in _arguments(command):
                        key, separator, value = argument.partition("=")
                        if separator and key.casefold() == "-mod":
                            mods.update(_normalize(item) for item in value.split(";") if item.strip())
                command_seen = True
    except (OSError, ValueError):
        return None
    return None


def _normalize(path: str) -> str:
    return ntpath.normpath(path.strip()).casefold()


def _matches_expected(expected: str, reported: set[str], game_folder: str) -> bool:
    if expected in reported:
        return True
    for item in reported:
        if ntpath.isabs(item) or ntpath.splitdrive(item)[0]:
            continue
        if _normalize(ntpath.join(game_folder, item)) == expected:
            return True
        if not ntpath.isabs(game_folder) and expected.endswith("\\" + item):
            return True
    return False


def check_client_report(before: ReportSnapshot, expected_mods: list[Path]) -> ClientLaunchReport:
    """Check the latest fresh report without waiting or exposing command text."""
    expected_count = len(expected_mods)
    reports = snapshot_client_reports()
    fresh = []
    for path, (modified, size) in reports.items():
        previous = before.get(path)
        if previous is None or (modified > previous[0] and size < previous[1]):
            fresh.append(path)
    latest = max(fresh, key=lambda path: path.name.casefold(), default=None)
    evidence = _command_mods(latest) if latest is not None else None
    if evidence is None:
        return ClientLaunchReport("pending", 0, expected_count)

    game_folder, reported_mods = evidence
    missing = tuple(ntpath.basename(str(path)) for path in expected_mods
                    if not _matches_expected(_normalize(str(path)), reported_mods, game_folder))
    state = "mods_missing" if missing else "mods_received"
    return ClientLaunchReport(state, expected_count - len(missing), expected_count, missing)
