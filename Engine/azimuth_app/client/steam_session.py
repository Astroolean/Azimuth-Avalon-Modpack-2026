"""Check Steam in the Windows desktop session that will launch DayZ."""

from __future__ import annotations

import csv
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
import time


STEAM_START_TIMEOUT = 20
_ACTIVE_STEAM = r"Software\Valve\Steam\ActiveProcess"
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_TOKEN_QUERY = 0x0008
_TOKEN_USER = 1
_TOKEN_ELEVATION = 20


@dataclass(frozen=True)
class SteamSession:
    steam_elevated: bool
    azimuth_elevated: bool


def _session_id(pid: int | None = None) -> int:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True, winmode=0x800)
    lookup = kernel.ProcessIdToSessionId
    lookup.argtypes = (wintypes.DWORD, ctypes.POINTER(wintypes.DWORD))
    lookup.restype = wintypes.BOOL
    session = wintypes.DWORD()
    if not lookup(os.getpid() if pid is None else pid, ctypes.byref(session)):
        raise ctypes.WinError(ctypes.get_last_error())
    return session.value


def session_processes() -> dict[int, str]:
    tasklist = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "tasklist.exe"
    try:
        result = subprocess.run(
            [str(tasklist), "/FI", f"SESSION eq {_session_id()}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, errors="replace", timeout=8, check=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError("Azimuth could not check Steam and DayZ in this Windows session.") from error
    return {int(row[1]): row[0].casefold() for row in csv.reader(result.stdout.splitlines())
            if len(row) >= 2 and row[1].isdecimal()}


def _active_steam_pid() -> int | None:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _ACTIVE_STEAM) as handle:
            pid, _ = winreg.QueryValueEx(handle, "pid")
            user, _ = winreg.QueryValueEx(handle, "ActiveUser")
    except OSError:
        return None
    return pid if isinstance(pid, int) and pid > 0 and isinstance(user, int) and user != 0 else None


@contextmanager
def _token_information(process, info_class: int, output):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True, winmode=0x800)
    security = ctypes.WinDLL("advapi32", use_last_error=True, winmode=0x800)
    close = kernel.CloseHandle
    close.argtypes = (wintypes.HANDLE,)
    close.restype = wintypes.BOOL
    open_token = security.OpenProcessToken
    open_token.argtypes = (wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE))
    open_token.restype = wintypes.BOOL
    get_token_info = security.GetTokenInformation
    get_token_info.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
                               ctypes.POINTER(wintypes.DWORD))
    get_token_info.restype = wintypes.BOOL

    token = wintypes.HANDLE()
    if not open_token(process, _TOKEN_QUERY, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        size = wintypes.DWORD()
        if output is None:
            get_token_info(token, info_class, None, 0, ctypes.byref(size))
            if not size.value:
                raise ctypes.WinError(ctypes.get_last_error())
            output = ctypes.create_string_buffer(size.value)
        if not get_token_info(token, info_class, ctypes.byref(output), ctypes.sizeof(output), ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        yield output
    finally:
        close(token)


def _process_is_elevated(process) -> bool:
    with _token_information(process, _TOKEN_ELEVATION, wintypes.DWORD()) as elevated:
        return bool(elevated.value)


def _process_user(process) -> bytes:
    security = ctypes.WinDLL("advapi32", use_last_error=True, winmode=0x800)
    sid_length = security.GetLengthSid
    sid_length.argtypes = (wintypes.LPVOID,)
    sid_length.restype = wintypes.DWORD
    with _token_information(process, _TOKEN_USER, None) as info:
        sid = ctypes.cast(info, ctypes.POINTER(wintypes.LPVOID)).contents
        return ctypes.string_at(sid, sid_length(sid))


def _current_process():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True, winmode=0x800)
    current = kernel.GetCurrentProcess
    current.argtypes = ()
    current.restype = wintypes.HANDLE
    return current()


def _process_context(pid: int) -> tuple[bool, bytes]:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True, winmode=0x800)
    open_process = kernel.OpenProcess
    open_process.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    open_process.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = (wintypes.HANDLE,)
    close.restype = wintypes.BOOL
    process = open_process(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not process:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return _process_is_elevated(process), _process_user(process)
    finally:
        close(process)


def ensure_steam_running(processes: dict[int, str]) -> SteamSession:
    """Open Steam if needed and wait for the active user in this desktop session."""
    if "steam.exe" not in processes.values():
        try:
            os.startfile("steam://open/main")
        except OSError as error:
            raise RuntimeError("Azimuth could not open Steam. Open Steam and click Play Avalon again.") from error
    deadline = time.monotonic() + STEAM_START_TIMEOUT
    while True:
        steam_pid = _active_steam_pid()
        if processes.get(steam_pid) == "steam.exe":
            try:
                steam_elevated, steam_user = _process_context(steam_pid)
                current = _current_process()
                azimuth_elevated, azimuth_user = _process_is_elevated(current), _process_user(current)
            except OSError as error:
                raise RuntimeError("Azimuth could not read Steam's Windows access level. "
                                   "Open Steam and Azimuth normally from the same Windows account.") from error
            if steam_user != azimuth_user:
                raise RuntimeError("Open Azimuth from the Windows account that runs Steam to launch DayZ.")
            return SteamSession(steam_elevated, azimuth_elevated)
        if time.monotonic() >= deadline:
            raise RuntimeError("No signed-in Steam session was found for this Windows account. "
                               "Open Steam Library, sign in if prompted, then click Play Avalon.")
        time.sleep(1)
        processes = session_processes()
