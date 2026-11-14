"""Azimuth manages a manually started FreeDayZ server and its backups."""
from contextlib import contextmanager
import ctypes
import hashlib
import os
from pathlib import Path
from threading import Event, RLock, Thread
import time

from azimuth_app.world import avalon_workshop
from azimuth_app.transport.transport_version import require_dayz130, require_matching_dayz_versions
from azimuth_app.client.client_graphics import BALANCED, CUSTOM, MODE_LABELS, PERFORMANCE, VISUAL, read_settings, write_settings
from azimuth_app.client.client_launch import find_client, launch_client
from azimuth_app.client.client_launch_report import check_client_report, snapshot_client_reports
from azimuth_app.client.client_performance import apply_preset, apply_quality_preset, ensure_client_closed, find_profiles
from azimuth_app.client.client_profiles import choose_profile
from azimuth_app.integrations.hunter_signing import SigningToolsMissing
from azimuth_app.core.server import AVALON_TERRAIN_ID, CF_WORKSHOP_ID
from azimuth_app.core.worlds import WorkshopMissing

TICK_SECONDS = 2
HEALTHY_SECONDS = 120
BACKOFF_SECONDS = (15, 30, 60, 120, 300)
MAX_CONSECUTIVE_FAILURES = 3
SWITCH_STOP_TIMEOUT = 60
SWITCH_START_TIMEOUT = 180
PLAY_INSTALL_RETRY = 8
CLIENT_REPORT_TIMEOUT = 35
SURVEY_WAIT_SECONDS = 120


@contextmanager
def claim_dashboard(root: Path, request_show: bool = True):
    config = root / "Config"
    config.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        yield True
        return
    import msvcrt

    with (config / "azimuth.lock").open("a+b") as handle:
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            if request_show:
                (config / "show-dashboard").touch()
            yield False
            return
        (config / "show-dashboard").unlink(missing_ok=True)
        yield True


def remove_sign_in_shortcut(root: Path) -> None:
    """Remove this installation's shortcut from releases with sign-in startup."""
    if os.name != "nt":
        return
    folder = ctypes.create_unicode_buffer(32768)
    shell = ctypes.WinDLL("shell32", winmode=0x800)
    get_folder = shell.SHGetFolderPathW
    get_folder.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_wchar_p]
    get_folder.restype = ctypes.c_long
    if get_folder(None, 7, None, 0, folder) != 0:
        raise RuntimeError("Could not locate the current user's Windows Startup folder.")

    package_id = hashlib.sha256(os.path.normcase(str(root)).encode("utf-8")).hexdigest()[:10]
    link = Path(folder.value) / f"Azimuth DayZ Server {package_id}.lnk"
    link.unlink(missing_ok=True)


class Azimuth:
    def __init__(self, server):
        self.server = server
        self.config = server.root / "Config"
        self.show_path = self.config / "show-dashboard"
        self.lock = RLock()
        self.wake = Event()
        self.closed = Event()
        self.thread = None
        self.enabled = False
        self.message = "Ready to start"
        self.retry_at = 0.0
        self.failures = 0
        self.healthy_since = None
        self.starting_since = None
        self.play_pending = False
        self.play_error: str | None = None
        self._play_retry = 0.0
        self._play_online_deadline = None
        self._play_setup_restarted = False
        self._play_survey_deadline = None
        self._play_enable_color_preview = False
        self._play_prompts: set[str] = set()
        self._client_launch_check: tuple[float, dict[Path, tuple[int, int]], list[Path]] | None = None

    def enable(self) -> None:
        with self.lock:
            self.enabled = True
            self.play_error = None
            self.retry_at = 0.0
            self.failures = 0
            self.starting_since = self.healthy_since = None
            self.message = "Starting"
            self.wake.set()

    def disable(self) -> bool:
        with self.lock:
            if self.server.backing_up:
                raise RuntimeError("Wait for the backup to finish before stopping the server.")
            self.enabled = False
            self.play_pending = False
            self.play_error = None
            self._client_launch_check = None
            self.message = "Stopping and backing up"
            try:
                stopped = self.server.request_stop_backup()
                if not stopped:
                    self.message = "Paused"
                return stopped
            finally:
                self.wake.set()

    def play_avalon(self, *, all_enhancements: bool = False) -> str:
        if os.name != "nt":
            raise RuntimeError("Play Avalon starts the Steam DayZ client on Windows.")
        with self.lock:
            if self.play_pending:
                return "Avalon is already preparing."
            if all_enhancements:
                saved = read_settings(self.config)
                write_settings(self.config, VISUAL, saved.profile)
            self._play_enable_color_preview = all_enhancements
            self.play_pending = True
            self.play_error = None
            self._client_launch_check = None
            self._play_retry = 0.0
            self._play_online_deadline = None
            self._play_setup_restarted = False
            self._play_survey_deadline = None
            self._play_prompts.clear()
            self.message = "Checking Steam, mods, and local server"
            self.wake.set()
        return "Preparing Avalon. Subscribe or approve installs if Steam opens. Azimuth resumes automatically."

    def avalon_workshop_items(self) -> tuple[tuple[str, str], ...]:
        base = (("CF", CF_WORKSHOP_ID), ("Avalon", AVALON_TERRAIN_ID))
        return base + tuple((label.replace("_", " "), item_id) for item_id, label in avalon_workshop.ITEMS)

    def avalon_workshop_status(self) -> tuple[bool, set[str]]:
        return self.server.avalon_pack_status()

    def enable_avalon_workshop(self) -> str:
        with self.lock:
            if self.play_pending or self.server.backing_up:
                raise RuntimeError("Wait for the current Azimuth action to finish before enabling the pack.")
            self._ensure_avalon_pack()
            return "Avalon Workshop pack is enabled. Click Play Avalon."

    def _ensure_avalon_pack(self) -> None:
        enabled, installed = self.avalon_workshop_status()
        for name, item_id in self.avalon_workshop_items():
            if item_id not in installed:
                raise WorkshopMissing(self.server.worlds.get("avalon"), name, item_id)
        if enabled:
            if self.server.avalon_pack_restart_needed():
                self._restart_updated_avalon()
            return

        process = self.server.process
        restart = (self.server.active_world_id == "avalon" and process is not None
                   and process.poll() is None)
        if restart:
            status = self.server.status()
            if status["state"] != "online" or status["players"] != 0:
                raise RuntimeError("Wait for Avalon to be online and empty, then click Play Avalon again.")

        try:
            if restart:
                self._stop_for_switch(require_clean=True)
            backup = self.server.enable_avalon_pack()
        except (OSError, RuntimeError, ValueError) as error:
            if restart:
                try:
                    self.server.start()
                except (OSError, RuntimeError, ValueError) as recovery:
                    raise RuntimeError(f"Pack setup failed: {error}. Avalon could not restart: {recovery}") from recovery
            raise

        self.retry_at = 0.0
        self.starting_since = self.healthy_since = None
        self.failures = 0
        self.wake.set()
        if backup is not None:
            self.server.events.put(f"Avalon backup before enabling Workshop pack: {backup.name}")
        if restart:
            self.server.start()

    def _restart_updated_avalon(self, reason: str = "Steam mod updates") -> None:
        status = self.server.status()
        if status["state"] != "online" or status["players"] != 0:
            raise RuntimeError(f"Wait for Avalon to be online and empty so Azimuth can apply {reason}.")
        self._stop_for_switch(require_clean=True)
        try:
            backup = self.server.backup_avalon_before_change()
        except (OSError, RuntimeError, ValueError):
            self.enabled = False
            raise
        if backup is not None:
            self.server.events.put(f"Avalon backup before {reason}: {backup.name}")
        self.server.start()

    def _play_install_prompt(self, address: str, label: str, now: float) -> None:
        self.message = f"Waiting for {label} in Steam"
        self._play_retry = now + PLAY_INSTALL_RETRY
        if address not in self._play_prompts:
            self._play_prompts.add(address)
            try:
                os.startfile(address)
            except OSError:
                self.server.events.put(f"Open Steam and install or subscribe to {label}, then click Play Avalon again.")
            else:
                self.server.events.put(f"Steam opened for {label}. Approve or subscribe there to continue.")

    def _play_tick(self) -> None:
        now = time.monotonic()
        if now < self._play_retry:
            return
        if self.server.backing_up:
            status = self.server.status()
            self.server.backup_tick(status)
            self.message = "Waiting for backup to finish"
            return

        try:
            ensure_client_closed()
            installation = self.server.find_installation()
            if installation is None:
                self._play_install_prompt("steam://install/223350", "DayZ Server", now)
                return
            libraries = self.server.steam_libraries(installation, refresh=self._play_online_deadline is None)
            client = find_client(libraries)
            if client is None:
                self._play_install_prompt("steam://install/221100", "DayZ", now)
                return
            require_dayz130(client / "DayZ_x64.exe", "DayZ")
            require_matching_dayz_versions(client / "DayZ_x64.exe", installation / "DayZServer_x64.exe",
                                           loaded_server_version=self.server._loaded_server_version)

            if self._play_online_deadline is None:
                if self._play_enable_color_preview and not self.server.avalon_color_preview_enabled():
                    self.message = "Preparing Avalon color preview"
                    self.server.set_avalon_color_preview(True, prepare_for_play=True)
                if self.server.avalon_color_preview_restart_needed():
                    status = self.server.status()
                    if status["state"] != "online" or status["players"] != 0:
                        self.message = "Waiting for Avalon to be online and empty before changing the color preview"
                        return
                self.message = "Preparing the Avalon Workshop pack"
                self._ensure_avalon_pack()
                if self.server.avalon_color_preview_restart_needed():
                    self.message = "Restarting Avalon to load the color preview"
                    self._restart_updated_avalon("the color preview")
                if self.server.active_world_id != "avalon":
                    if not self.enabled:
                        self.server.prepare_world("avalon")
                        self.server.select_world("avalon")
                    else:
                        self.switch_world("avalon")
                self.server.start()
                self.enabled = True
                self.starting_since = self.healthy_since = None
                self.retry_at = 0.0
                self.failures = 0
                self._play_online_deadline = time.monotonic() + SWITCH_START_TIMEOUT

            server_status = self.server.status()
            status = server_status["state"]
            if status in {"stopped", "error"}:
                # A failed Play attempt must not fall through to the ordinary
                # watchdog on the next tick and launch the same crashing mod.
                self.enabled = False
                self.retry_at = 0.0
                self.starting_since = self.healthy_since = None
                raise RuntimeError("Avalon exited during startup. Automatic restarting is paused. "
                                   "Open Logs for the DayZ error, then click Play Avalon to retry.")
            if status != "online":
                if time.monotonic() >= self._play_online_deadline:
                    if status == "starting":
                        self._pause_unresponsive_server()
                    raise RuntimeError("Avalon did not respond in time. Open Logs for the DayZ error.")
                self.message = "Waiting for Avalon to come online"
                return

            if self.server.avalon_survey_running():
                if self._play_survey_deadline is None:
                    self._play_survey_deadline = time.monotonic() + SURVEY_WAIT_SECONDS
                if time.monotonic() < self._play_survey_deadline:
                    self.message = "Checking Avalon vehicle placement sites"
                    return
                if self._play_survey_deadline:
                    self.server.events.put("Vehicle site discovery is still running. Existing transport stays available; validated new sites will be applied at a later server start.")
                    self._play_survey_deadline = 0.0

            if not self._play_setup_restarted and self.server.avalon_first_run_pending():
                self.message = "Finishing Avalon world setup"
                self._restart_updated_avalon("Avalon world setup")
                self._play_setup_restarted = True
                self._play_online_deadline = time.monotonic() + SWITCH_START_TIMEOUT
                return

            self.message = "Preparing DayZ graphics"
            self._prepare_client_graphics()
            if self.server.avalon_pack_restart_needed():
                if server_status.get("players") != 0:
                    self.message = "Waiting for Avalon to be empty before applying Workshop updates"
                    return
                self.message = "Synchronizing Avalon with updated Workshop files"
                self._restart_updated_avalon()
                self._play_online_deadline = time.monotonic() + SWITCH_START_TIMEOUT
                return
            mods = list(self.server.loaded_client_mods)
            if not mods:
                raise RuntimeError("Azimuth did not receive Avalon's active mod list. Stop Avalon and click Play Avalon again.")
            before = snapshot_client_reports()
            self.message = "Checking Steam and launching DayZ"
            require_matching_dayz_versions(client / "DayZ_x64.exe", installation / "DayZServer_x64.exe",
                                           loaded_server_version=self.server._loaded_server_version)
            aliases = launch_client(libraries, mods, self.server.settings["game_port"], self.server.settings["password"])
            if aliases:
                self._client_launch_check = (time.monotonic() + CLIENT_REPORT_TIMEOUT, before, aliases)
        except WorkshopMissing as missing:
            self._play_install_prompt(f"steam://url/CommunityFilePage/{missing.item_id}", missing.alias, now)
            return
        except SigningToolsMissing:
            self._play_install_prompt("steam://install/830640", "DayZ Tools", now)
            return
        except (OSError, RuntimeError, ValueError) as error:
            self.play_pending = False
            self.play_error = str(error)
            self.message = "Play Avalon needs attention"
            self.server.events.put(f"Play Avalon: {error}")
            return

        self.play_pending = False
        self.message = "DayZ launch requested"
        self.server.events.put(f"Sent {len(mods)} Avalon mods and the join settings to DayZ through BattlEye.")

    def _poll_client_launch(self) -> None:
        if self._client_launch_check is None:
            return
        deadline, before, mods = self._client_launch_check
        report = check_client_report(before, mods)
        if report.state == "pending":
            if time.monotonic() >= deadline:
                self._client_launch_check = None
                self.play_error = ("DayZ did not write a launch report. If DayZ failed to open, "
                                   "check whether Steam Library > DayZ > Play can open the official launcher.")
                self.server.events.put(self.play_error)
            return
        self._client_launch_check = None
        if report.state == "mods_received":
            self.server.events.put(f"DayZ received all {report.expected_count} mod paths. DayZ verifies their PBOs when joining.")
        else:
            self.play_error = (f"DayZ received {report.received_count} of {report.expected_count} expected mod paths. "
                               "Close DayZ and its launcher, then use Play Avalon to retry.")
            self.server.events.put(self.play_error)

    def _prepare_client_graphics(self) -> None:
        try:
            settings = read_settings(self.config)
            if settings.mode == CUSTOM:
                return
            profiles = find_profiles()
            profile = choose_profile(profiles, settings.profile)
            if profile is None:
                if profiles:
                    self.server.events.put("Azimuth could not identify the DayZ client profile. "
                                           "The graphics preset will apply when the profile can be identified.")
                else:
                    self.server.events.put("DayZ will create a client profile on its first run. Graphics tuning starts on the next Play Avalon.")
                return
            quality = None
            if settings.mode == PERFORMANCE:
                apply_preset(profile)
            elif settings.mode == BALANCED:
                quality = apply_quality_preset(profile, cap_visibility=True)
            else:
                quality = apply_quality_preset(profile)
            write_settings(self.config, settings.mode, profile)
            self.server.events.put(f"{MODE_LABELS[settings.mode]} ready in {profile.name}.")
            if quality and quality["missing_fields"]:
                self.server.events.put("DayZ has not saved these graphics fields, so Azimuth left them alone: "
                                       + ", ".join(quality["missing_fields"]) + ". Save Video settings in DayZ once, then relaunch.")
        except (OSError, RuntimeError, ValueError) as error:
            self.server.events.put(f"DayZ graphics setup skipped: {error}")

    def _stop_for_switch(self, require_clean: bool) -> None:
        process = self.server.process
        if process is None or process.poll() is not None:
            return
        self.server.stop()
        deadline = time.monotonic() + SWITCH_STOP_TIMEOUT
        while process.poll() is None:
            if time.monotonic() >= deadline:
                raise RuntimeError("DayZ did not finish stopping. Check the server console.")
            time.sleep(0.5)
        self.server.status()
        if require_clean and not self.server._backup_clean_exit():
            raise RuntimeError("DayZ did not report a clean shutdown in its log.")

    def _wait_for_online(self) -> None:
        deadline = time.monotonic() + SWITCH_START_TIMEOUT
        while time.monotonic() < deadline:
            status = self.server.status()["state"]
            if status == "online":
                return
            if status in {"stopped", "error"}:
                raise RuntimeError("DayZ exited before the new world was online. See its logs.")
            time.sleep(0.5)
        raise RuntimeError("The selected world did not respond before the startup timeout.")

    def switch_world(self, world_id: str) -> None:
        with self.lock:
            if self.server.backing_up:
                raise RuntimeError("Wait for the current backup to finish before switching worlds.")
            previous = self.server.active_world_id
            if world_id == previous:
                return
            if not self.enabled:
                self.server.select_world(world_id)
                return

            process = self.server.process
            previous_running = process is not None and process.poll() is None
            self.server.prepare_world(world_id)
            self.message = "Switching worlds"
            try:
                self._stop_for_switch(require_clean=True)
                self.server.select_world(world_id)
                self.server.start()
                self._wait_for_online()
            except Exception as error:
                if self.server.active_world_id != previous:
                    try:
                        self._stop_for_switch(require_clean=False)
                        self.server.select_world(previous)
                    except Exception as recovery_error:
                        self.enabled = False
                        raise RuntimeError(
                            f"World switch failed: {error} Azimuth paused because it could not switch back: {recovery_error}"
                        ) from recovery_error
                if previous_running:
                    try:
                        self.server.start()
                    except Exception as recovery_error:
                        raise RuntimeError(
                            f"World switch failed: {error} The previous world is selected and Azimuth will retry: {recovery_error}"
                        ) from recovery_error
                raise RuntimeError(f"World switch failed: {error} The previous world remains selected.") from error
            finally:
                self.starting_since = self.healthy_since = None
                self.retry_at = 0.0
                self.failures = 0
                self.wake.set()
            self.server.events.put(f"Switched to {self.server.active_world.label}.")
            self.message = "Monitoring"

    def show_requested(self) -> bool:
        try:
            self.show_path.unlink()
            return True
        except FileNotFoundError:
            return False

    def _retry(self, reason: str, now: float) -> None:
        self.failures += 1
        if self.failures >= MAX_CONSECUTIVE_FAILURES:
            self.enabled = False
            self.retry_at = 0.0
            self.starting_since = self.healthy_since = None
            self.play_error = (f"{reason} Automatic restarting is paused after "
                               f"{self.failures} consecutive failures. Open Logs, then click Start server "
                               "or Play Avalon to retry.")
            self.message = "Server needs attention"
            self.server.events.put(f"Azimuth: {self.play_error}")
            return
        delay = BACKOFF_SECONDS[min(self.failures - 1, len(BACKOFF_SECONDS) - 1)]
        self.retry_at = now + delay
        self.message = f"Retrying in {delay}s"
        self.server.events.put(f"Azimuth: {reason} Retrying in {delay} seconds.")

    def _pause_unresponsive_server(self) -> None:
        """Bound a never-online startup without force-killing a persistent world."""
        self.enabled = False
        self.retry_at = 0.0
        self.starting_since = self.healthy_since = None
        self.play_error = "DayZ did not become online before the startup timeout. Automatic restarting is paused."
        self.message = "Server needs attention"
        try:
            self.server.stop()
        except (OSError, RuntimeError) as error:
            self.play_error += f" Shutdown could not be requested: {error}"
        else:
            self.play_error += " A graceful shutdown was requested; check the server console if it remains open."
        self.server.events.put(f"Azimuth: {self.play_error}")

    def tick(self) -> None:
        with self.lock:
            if self.play_pending:
                self._play_tick()
                return
            self._poll_client_launch()
            if not self.enabled:
                if self.server.backing_up:
                    self.server.backup_tick(self.server.status())
                    self.message = "Finishing stop backup" if self.server.backing_up else "Paused"
                return

            status = self.server.status()
            self.server.backup_tick(status)
            if self.server.backing_up:
                self.message = "Backup maintenance"
                return
            now = time.monotonic()
            state = status["state"]
            process = self.server.process
            if process is not None and process.poll() is None:
                if state == "online":
                    self.starting_since = None
                    if self.healthy_since is None:
                        self.healthy_since = now
                    elif now - self.healthy_since >= HEALTHY_SECONDS:
                        self.failures = 0
                    self.message = "Monitoring"
                else:
                    self.healthy_since = None
                    if self.starting_since is None:
                        self.starting_since = now
                    if state == "starting" and now - self.starting_since >= SWITCH_START_TIMEOUT:
                        self._pause_unresponsive_server()
                        return
                    self.message = "Waiting for DayZ" if now - self.starting_since < HEALTHY_SECONDS else "Waiting for DayZ response"
                return

            if self.starting_since is not None or self.healthy_since is not None:
                self.starting_since = self.healthy_since = None
                self._retry("DayZ exited unexpectedly.", now)
            if not self.enabled or now < self.retry_at:
                return

            try:
                self.server.start()
            except (OSError, RuntimeError, ValueError) as error:
                self._retry(str(error), now)
                return

            self.starting_since = now
            self.message = "Waiting for DayZ"

    def _run(self) -> None:
        try:
            remove_sign_in_shortcut(self.server.root)
        except (OSError, RuntimeError) as error:
            self.server.events.put(f"Azimuth could not remove its old Windows Startup shortcut: {error}")
        while not self.closed.is_set():
            self.wake.clear()
            try:
                self.tick()
            except Exception as error:
                self.message = "Maintenance retry pending"
                self.server.events.put(f"Azimuth: {error}")
            self.wake.wait(TICK_SECONDS)

    def start_worker(self) -> None:
        if self.thread is None:
            self.thread = Thread(target=self._run, name="Azimuth", daemon=True)
            self.thread.start()

    def shutdown(self) -> None:
        self.closed.set()
        self.wake.set()
