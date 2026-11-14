"""Read process telemetry without shell commands or background services."""

from dataclasses import dataclass
from functools import cache
import os
from pathlib import Path
from time import monotonic, time


@dataclass(frozen=True)
class ProcessSample:
    cpu_percent: float | None = None
    memory_bytes: int | None = None
    uptime_seconds: float | None = None


@dataclass(frozen=True)
class _Counters:
    identity: int
    cpu_seconds: float
    memory_bytes: int | None
    uptime_seconds: float


class ProcessMonitor:
    """Sample one process. Call from the dashboard's polling worker."""

    def __init__(self) -> None:
        self._previous: tuple[int, _Counters, float] | None = None
        self._cpus = os.cpu_count() or 1

    def sample(self, pid: int | None) -> ProcessSample:
        if not pid:
            self._previous = None
            return ProcessSample()
        try:
            counters = _read_counters(pid)
        except (OSError, ValueError, IndexError, AttributeError):
            self._previous = None
            return ProcessSample()

        now = monotonic()
        previous = self._previous
        self._previous = (pid, counters, now)
        percent = None
        if previous is not None:
            last_pid, last_counters, last_time = previous
            elapsed = now - last_time
            cpu_used = counters.cpu_seconds - last_counters.cpu_seconds
            if pid == last_pid and counters.identity == last_counters.identity and elapsed > 0 and cpu_used >= 0:
                percent = min(100.0, 100 * cpu_used / elapsed / self._cpus)

        return ProcessSample(percent, counters.memory_bytes, max(0.0, counters.uptime_seconds))


def format_uptime(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    elapsed = max(0, int(seconds))
    minutes = elapsed // 60
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes:02}m"
    return f"{minutes}m" if minutes else f"{elapsed}s"


def format_memory(memory: int | None) -> str:
    if memory is None:
        return "—"
    if memory >= 1024 ** 3:
        return f"{memory / 1024 ** 3:.1f} GiB"
    return f"{memory / 1024 ** 2:.0f} MiB"


def _read_counters(pid: int) -> _Counters:
    if os.name == "nt":
        return _windows_counters(pid)
    return _proc_counters(pid)


def _proc_counters(pid: int) -> _Counters:
    # /proc surrounds comm with parentheses. Find the last one so an embedded
    # space or parenthesis in the process name cannot shift the field positions.
    status = (Path("/proc") / str(pid) / "stat").read_text(encoding="utf-8", errors="replace")
    fields = status.rsplit(")", 1)[1].split()
    if fields[0] == "Z":
        raise ProcessLookupError(pid)
    ticks = os.sysconf("SC_CLK_TCK")
    start = int(fields[19])
    uptime = float(Path("/proc/uptime").read_text(encoding="ascii").split()[0])
    return _Counters(start, (int(fields[11]) + int(fields[12])) / ticks,
                     max(0, int(fields[21])) * os.sysconf("SC_PAGE_SIZE"), uptime - start / ticks)


@cache
def _windows_api():
    import ctypes
    from ctypes import wintypes

    size = ctypes.c_size_t

    class MemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", size), ("WorkingSetSize", size),
            ("QuotaPeakPagedPoolUsage", size), ("QuotaPagedPoolUsage", size),
            ("QuotaPeakNonPagedPoolUsage", size), ("QuotaNonPagedPoolUsage", size),
            ("PagefileUsage", size), ("PeakPagefileUsage", size),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    filetime_pointer = ctypes.POINTER(wintypes.FILETIME)
    kernel.GetProcessTimes.argtypes = (wintypes.HANDLE, filetime_pointer, filetime_pointer,
                                      filetime_pointer, filetime_pointer)
    kernel.GetProcessTimes.restype = wintypes.BOOL
    kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel.GetExitCodeProcess.restype = wintypes.BOOL
    kernel.K32GetProcessMemoryInfo.argtypes = (wintypes.HANDLE, ctypes.POINTER(MemoryCounters), wintypes.DWORD)
    kernel.K32GetProcessMemoryInfo.restype = wintypes.BOOL
    return ctypes, wintypes, kernel, MemoryCounters


def _windows_counters(pid: int) -> _Counters:
    ctypes, wintypes, kernel, memory_counters = _windows_api()

    def ticks(value: wintypes.FILETIME) -> int:
        return (value.dwHighDateTime << 32) | value.dwLowDateTime

    handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        status = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(status)):
            raise ctypes.WinError(ctypes.get_last_error())
        if status.value != 259:  # STILL_ACTIVE
            raise ProcessLookupError(pid)

        created, exited, system, user = (wintypes.FILETIME() for _ in range(4))
        if not kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                      ctypes.byref(system), ctypes.byref(user)):
            raise ctypes.WinError(ctypes.get_last_error())

        memory = memory_counters()
        memory.cb = ctypes.sizeof(memory)
        available = kernel.K32GetProcessMemoryInfo(handle, ctypes.byref(memory), memory.cb)
        start = ticks(created)
        epoch_offset = 11_644_473_600  # seconds between FILETIME and Unix epochs
        return _Counters(start, (ticks(system) + ticks(user)) / 10_000_000,
                         memory.WorkingSetSize if available else None,
                         time() + epoch_offset - start / 10_000_000)
    finally:
        kernel.CloseHandle(handle)
