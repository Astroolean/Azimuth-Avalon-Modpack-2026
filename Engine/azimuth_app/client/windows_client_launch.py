"""Start BattlEye at Steam's Windows access level when Azimuth differs."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
from typing import Mapping

from azimuth_app.client.steam_session import (
    _PROCESS_QUERY_LIMITED_INFORMATION, _current_process, _process_is_elevated, _process_user, _session_id,
)


_PROCESS_CREATE_PROCESS = 0x0080
_PARENT_PROCESS = 0x00020000
_EXTENDED_STARTUPINFO_PRESENT = 0x00080000
_CREATE_UNICODE_ENVIRONMENT = 0x00000400
_ERROR_CANCELLED = 1223


class _StartupInfo(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR),
                ("lpDesktop", wintypes.LPWSTR), ("lpTitle", wintypes.LPWSTR),
                ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD), ("dwXSize", wintypes.DWORD),
                ("dwYSize", wintypes.DWORD), ("dwXCountChars", wintypes.DWORD),
                ("dwYCountChars", wintypes.DWORD), ("dwFillAttribute", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("wShowWindow", wintypes.WORD),
                ("cbReserved2", wintypes.WORD), ("lpReserved2", wintypes.LPVOID),
                ("hStdInput", wintypes.HANDLE), ("hStdOutput", wintypes.HANDLE),
                ("hStdError", wintypes.HANDLE)]


class _StartupInfoEx(ctypes.Structure):
    _fields_ = [("StartupInfo", _StartupInfo), ("lpAttributeList", wintypes.LPVOID)]


class _ProcessInformation(ctypes.Structure):
    _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
                ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD)]


def _function(library, name, result, *arguments):
    function = getattr(library, name)
    function.restype = result
    function.argtypes = arguments
    return function


def _environment_block(environment: Mapping[str, str]):
    entries = (f"{name}={environment[name]}" for name in sorted(environment, key=str.upper))
    return ctypes.create_unicode_buffer("\0".join(entries) + "\0")


def launch_as_desktop_user(executable: Path, command: str, cwd: Path, environment: Mapping[str, str]) -> None:
    """Use the standard desktop shell as the parent for this one client process."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True, winmode=0x800)
    user = ctypes.WinDLL("user32", use_last_error=True, winmode=0x800)
    shell_window = _function(user, "GetShellWindow", wintypes.HWND)()
    window_pid = _function(user, "GetWindowThreadProcessId", wintypes.DWORD, wintypes.HWND,
                           ctypes.POINTER(wintypes.DWORD))
    open_process = _function(kernel, "OpenProcess", wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    close = _function(kernel, "CloseHandle", wintypes.BOOL, wintypes.HANDLE)
    shell_pid = wintypes.DWORD()
    if not shell_window or not window_pid(shell_window, ctypes.byref(shell_pid)):
        raise RuntimeError("Azimuth could not find the Windows desktop to launch DayZ at Steam's access level.")
    if _session_id(shell_pid.value) != _session_id():
        raise RuntimeError("The Windows desktop belongs to a different session.")
    access = _PROCESS_CREATE_PROCESS | _PROCESS_QUERY_LIMITED_INFORMATION
    parent = wintypes.HANDLE(open_process(access, False, shell_pid.value))
    if not parent:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if _process_is_elevated(parent):
            raise RuntimeError("The Windows desktop is running as administrator while Steam uses standard access.")
        if _process_user(parent) != _process_user(_current_process()):
            raise RuntimeError("Open Azimuth from the Windows account that runs Steam to launch DayZ.")
        _create_desktop_process(kernel, parent, executable, command, cwd, environment)
    finally:
        close(parent)


def _create_desktop_process(kernel, parent, executable: Path, command: str, cwd: Path,
                            environment: Mapping[str, str]) -> None:
    pointer = wintypes.LPVOID
    size_t = ctypes.c_size_t
    initialize = _function(kernel, "InitializeProcThreadAttributeList", wintypes.BOOL, pointer,
                           wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(size_t))
    update = _function(kernel, "UpdateProcThreadAttribute", wintypes.BOOL, pointer, wintypes.DWORD,
                       size_t, pointer, size_t, pointer, ctypes.POINTER(size_t))
    delete = _function(kernel, "DeleteProcThreadAttributeList", None, pointer)
    create = _function(kernel, "CreateProcessW", wintypes.BOOL, wintypes.LPCWSTR, wintypes.LPWSTR,
                       pointer, pointer, wintypes.BOOL, wintypes.DWORD, pointer, wintypes.LPCWSTR,
                       ctypes.POINTER(_StartupInfo), ctypes.POINTER(_ProcessInformation))
    close = _function(kernel, "CloseHandle", wintypes.BOOL, wintypes.HANDLE)
    size = size_t()
    initialize(None, 1, 0, ctypes.byref(size))
    if not size.value:
        raise ctypes.WinError(ctypes.get_last_error())
    attributes = ctypes.create_string_buffer(size.value)
    if not initialize(attributes, 1, 0, ctypes.byref(size)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not update(attributes, 0, _PARENT_PROCESS, ctypes.byref(parent), ctypes.sizeof(parent), None, None):
            raise ctypes.WinError(ctypes.get_last_error())
        startup = _StartupInfoEx()
        startup.StartupInfo.cb = ctypes.sizeof(startup)
        startup.lpAttributeList = ctypes.cast(attributes, pointer)
        process = _ProcessInformation()
        flags = _EXTENDED_STARTUPINFO_PRESENT | _CREATE_UNICODE_ENVIRONMENT
        if not create(str(executable), ctypes.create_unicode_buffer(command), None, None, False, flags,
                      _environment_block(environment), str(cwd), ctypes.byref(startup.StartupInfo),
                      ctypes.byref(process)):
            raise ctypes.WinError(ctypes.get_last_error())
        close(process.hThread)
        close(process.hProcess)
    finally:
        delete(attributes)


def launch_with_windows_consent(executable: Path, parameters: str, cwd: Path) -> None:
    """Ask Windows to launch the installed BattlEye executable as administrator."""
    try:
        os.startfile(str(executable), "runas", parameters, str(cwd))
    except OSError as error:
        if getattr(error, "winerror", None) == _ERROR_CANCELLED:
            raise RuntimeError("Windows approval was cancelled. Click Play Avalon again to retry.") from None
        raise
