"""Azimuth's Windows dashboard for DayZ servers."""

from __future__ import annotations
from azimuth_app.paths import APP_ROOT

import argparse
from collections import deque
import os
import re
from pathlib import Path
from queue import Empty, Queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox

from azimuth_app.core.azimuth import Azimuth, claim_dashboard, remove_sign_in_shortcut
from azimuth_app.core.layout_migration import migrate_layout
from azimuth_app.economy.avalon_loot_balance import AUDIT_PATH
from azimuth_app.client.client_graphics import BALANCED, CUSTOM, MODE_LABELS, PERFORMANCE, VISUAL, read_settings, write_settings
from azimuth_app.client.client_performance import (
    apply_preset, apply_quality_preset, backup_file, ensure_client_closed, find_profiles,
    load_profile, restore_quality_preset, visibility_fields, visual_backup_file,
)
from azimuth_app.client.client_profiles import choose_profile
from azimuth_app.core.server import Server, get_lan_ip
from azimuth_app.ui.dashboard_monitor import ProcessMonitor
from azimuth_app.ui.dashboard_operations import backup_now, list_backups, verify_backup
from azimuth_app.ui.dashboard_overview import build_overview, render_overview
from azimuth_app.ui.dashboard_skins import SkinsPage
from azimuth_app.ui.dashboard_theme import (
    ACCENT, ACCENT_HOVER, BG, BUTTON, DANGER, DIM, EDGE, INSET,
    MUTED, PANEL, RAIL, TEXT, WARNING, configure as configure_theme, mark,
)
from azimuth_app.ui.dashboard_utility import build_activity, build_backups, build_settings
from azimuth_app.ui.dashboard_workshop import WorkshopPage
from azimuth_app.transport import vehicle_workshop
from azimuth_app.ui.dashboard_worlds import build_client, build_worlds, refresh_world_tiles
from azimuth_app.core.worlds import CATALOG


class Dashboard:
    def __init__(self, window: tk.Tk, server: Server, azimuth: Azimuth) -> None:
        self.window = window
        self.server = server
        self.azimuth = azimuth
        self.results: Queue = Queue()
        self.busy = False
        self.last_play_error = None
        self.polling = False
        self.monitor = ProcessMonitor()
        self.process_sample = None
        self.sample_pid = None
        self.cpu_history = deque(maxlen=60)
        self.memory_history = deque(maxlen=60)
        self.next_workshop_poll = 0.0
        self.next_backups_poll = 0.0
        self.next_skins_poll = 0.0
        self.backup_records = ()
        self.workshop_status = None
        self.workshop_error = None
        self.active_page = "overview"
        self.stop_deadline = 0.0
        self.events: list[str] = []
        self.server_logs: list[str] = []
        self.last_rendered_logs: tuple[tuple[str, str], ...] = ()
        self.status_data: dict = {}
        self.backup_data: dict = {}
        self.fields: dict[str, tk.StringVar] = {}
        self.entries: list[tk.Entry] = []
        self.lan_address = ""
        self.local_join = ""
        self.lan_join = ""
        self.password_visible = False
        self.world_names = dict(server.world_options())
        self.world_ids = {name: world_id for world_id, name in self.world_names.items()}
        self.active_world_id = server.active_world_id
        self.backup_world_id = server.active_world_id
        self.world_choice = tk.StringVar(window, self.world_names[self.active_world_id])
        self.ui_font, self.mono_font = configure_theme(window)
        window.title("Azimuth · DayZ Server Manager")
        window.configure(bg=BG)
        self.layout_scale = max(1.0, float(window.tk.call("tk", "scaling")) / (96 / 72))
        usable_width, usable_height = window.winfo_screenwidth() - 32, window.winfo_screenheight() - 80
        window.geometry(f"{min(round(1170 * self.layout_scale), usable_width)}x"
                        f"{min(round(760 * self.layout_scale), usable_height)}")
        window.minsize(min(round(910 * self.layout_scale), usable_width),
                       min(round(650 * self.layout_scale), usable_height))
        window.protocol("WM_DELETE_WINDOW", self.close)
        self.build()
        self.refresh()
        window.after(150, self.drain_queues)
        window.after(350, self.poll_status)

    def label(self, parent: tk.Widget, text: str, *, size: int = 10,
              color: str = TEXT, bold: bool = False, bg: str = PANEL, **kwargs) -> tk.Label:
        return tk.Label(parent, text=text, bg=bg, fg=color, anchor="w",
                        font=(self.ui_font, size, "bold" if bold else "normal"), **kwargs)

    def button(self, parent: tk.Widget, text: str, command, *, primary: bool = False,
               danger: bool = False, compact: bool = False) -> tk.Button:
        return tk.Button(
            parent, text=text, command=command, relief="flat", borderwidth=0, cursor="hand2",
            bg=ACCENT if primary else BUTTON, fg=BG if primary else DANGER if danger else TEXT,
            activebackground=ACCENT_HOVER if primary else "#30464a", activeforeground=BG if primary else TEXT,
            disabledforeground="#758584", font=(self.ui_font, 9 if compact else 10, "bold"),
            padx=10 if compact else 15, pady=4 if compact else 8,
            highlightthickness=1, highlightbackground=EDGE, highlightcolor=ACCENT,
        )

    def panel(self, parent: tk.Widget) -> tk.Frame:
        return tk.Frame(parent, bg=PANEL, highlightthickness=1, highlightbackground=EDGE)

    def build(self) -> None:
        shell = tk.Frame(self.window, bg=BG)
        shell.pack(fill="both", expand=True)
        rail = tk.Frame(shell, bg=RAIL, width=round(180 * self.layout_scale), highlightthickness=0)
        rail.pack(side="left", fill="y")
        rail.pack_propagate(False)
        body = tk.Frame(shell, bg=BG)
        body.pack(side="left", fill="both", expand=True, padx=23)

        brand = tk.Frame(rail, bg=RAIL)
        brand.pack(fill="x", padx=15, pady=(19, 26))
        mark(brand, RAIL).pack(side="left", padx=(0, 7))
        brand_text = tk.Frame(brand, bg=RAIL)
        brand_text.pack(side="left")
        self.label(brand_text, "Azimuth", size=16, bold=True, bg=RAIL).pack(anchor="w")
        self.label(brand_text, "DayZ Server", size=9, color=MUTED, bg=RAIL).pack(anchor="w")

        navigation = (
            ("overview", "Overview", "◈"), ("worlds", "Worlds", "◇"),
            ("workshop", "Workshop", "⬡"), ("skins", "Skins/Camos", "◩"),
            ("backups", "Backups", "▤"),
            ("activity", "Activity", "≋"), ("client", "Client", "◫"),
            ("settings", "Settings", "⚙"),
        )
        self.nav_buttons = {}
        for page, label, icon in navigation:
            button = tk.Button(
                rail, text=f" {icon}    {label}", command=lambda key=page: self.show_page(key),
                font=(self.ui_font, 11, "bold"), bg=RAIL, fg=MUTED, anchor="w", padx=10, pady=9,
                relief="flat", borderwidth=0, cursor="hand2", activebackground=BUTTON, activeforeground=TEXT,
                highlightthickness=1, highlightbackground=RAIL, highlightcolor=ACCENT,
            )
            button.pack(fill="x", padx=12, pady=2)
            self.nav_buttons[page] = button
        rail_footer = tk.Frame(rail, bg=RAIL)
        rail_footer.pack(side="bottom", fill="x", padx=15, pady=(8, 16))
        tk.Frame(rail_footer, bg=EDGE, height=1).pack(fill="x", pady=(0, 10))
        self.exit_button = self.button(rail_footer, "Exit Azimuth", self.exit_azimuth, compact=True)
        self.exit_button.pack(anchor="w")

        header = tk.Frame(body, bg=BG)
        header.pack(fill="x", pady=(15, 14))
        state_group = tk.Frame(header, bg=BG)
        state_group.pack(side="left", fill="x", expand=True, padx=(0, 9))
        self.badge = self.label(state_group, "●  Offline", size=10, color=MUTED, bold=True, bg=BG)
        self.badge.pack(anchor="w")
        self.azimuth_label = self.label(state_group, "Checking server", color=MUTED, bg=BG,
                                        size=9, wraplength=355, justify="left")
        self.azimuth_label.pack(fill="x", pady=(2, 0))
        controls = tk.Frame(header, bg=BG)
        controls.pack(side="right", anchor="n")
        self.stop_button = self.button(controls, "Stop", self.stop_server, danger=True, compact=True)
        self.stop_button.pack(side="left")
        self.start_button = self.button(controls, "Start server", self.start_server, compact=True)
        self.start_button.pack(side="left", padx=7)
        self.play_button = self.button(controls, "▶  Play Avalon", self.play_avalon, primary=True)
        self.play_button.pack(side="left")

        self.page_stack = tk.Frame(body, bg=BG)
        self.page_stack.pack(fill="both", expand=True)
        self.page_stack.rowconfigure(0, weight=1)
        self.page_stack.columnconfigure(0, weight=1)
        self.pages = {}
        builders = {
            "overview": build_overview, "worlds": build_worlds,
            "backups": build_backups, "activity": build_activity,
            "client": build_client, "settings": build_settings,
        }
        for page, _, _ in navigation:
            frame = tk.Frame(self.page_stack, bg=BG)
            frame.grid(row=0, column=0, sticky="nsew")
            self.pages[page] = frame
            if page == "workshop":
                self.workshop_page = WorkshopPage(frame, self)
                self.workshop_page.pack(fill="both", expand=True)
            elif page == "skins":
                self.skins_page = SkinsPage(frame, self)
                self.skins_page.pack(fill="both", expand=True)
            else:
                builders[page](frame, self)

        footer = tk.Frame(body, bg=BG, height=33)
        footer.pack(fill="x", side="bottom", pady=(5, 8))
        footer.pack_propagate(False)
        self.notice = self.label(footer, "", bg=BG, color=MUTED, size=9, wraplength=790, justify="left")
        self.notice.pack(fill="both", expand=True)
        footer.bind("<Configure>", lambda event: self.notice.configure(wraplength=max(180, event.width - 6)))
        self.page_stack.pack_configure(after=footer)
        self.show_page("overview")
        self.show_notice("Click Play Avalon to start or finish setup.")

    def show_page(self, key: str) -> None:
        self.active_page = key
        self.pages[key].tkraise()
        for page, button in self.nav_buttons.items():
            active = page == key
            color = BUTTON if active else RAIL
            button.configure(bg=color, fg=ACCENT if active else MUTED, highlightbackground=color)
        if key == "activity":
            self.render_logs()
        elif key == "workshop":
            self.next_workshop_poll = 0.0
        elif key == "backups":
            self.next_backups_poll = 0.0
        elif key == "skins":
            self.next_skins_poll = 0.0
        elif key == "client":
            self.refresh_graphics_choice()

    def refresh_graphics_choice(self) -> None:
        try:
            saved = read_settings(self.server.root / "Config")
        except (OSError, ValueError) as error:
            self.graphics_choice_label.configure(text=str(error))
            return
        profile = f" · {saved.profile.name}" if saved.profile else " · Automatic profile selection"
        self.graphics_choice_label.configure(text=MODE_LABELS[saved.mode] + profile)

    def page_heading(self, parent, title: str, description: str = "") -> tk.Frame:
        header = tk.Frame(parent, bg=BG)
        header.pack(fill="x", pady=(0, 12))
        self.label(header, title, size=23, bold=True, bg=BG).pack(anchor="w")
        if description:
            self.label(header, description, color=MUTED, size=10, bg=BG).pack(anchor="w", pady=(3, 0))
        return header

    def entry(self, parent: tk.Widget, title: str, key: str, *, secret: bool = False, width: int = 25) -> None:
        self.label(parent, title, size=9, color=MUTED, bold=True).pack(fill="x", pady=(4, 2))
        variable = tk.StringVar(self.window, str(self.server.settings.get(key, "")))
        self.fields[key] = variable
        entry = tk.Entry(parent, textvariable=variable, bg=INSET, fg=TEXT, insertbackground=TEXT,
                         selectbackground=EDGE, relief="flat", width=width, font=(self.ui_font, 10),
                         highlightthickness=1, highlightbackground=EDGE, highlightcolor=ACCENT,
                         disabledbackground=INSET, disabledforeground=MUTED,
                         show="*" if secret else "")
        entry.pack(fill="x", ipady=4)
        self.entries.append(entry)
        if secret:
            self.password_entry = entry

    def metric(self, parent: tk.Widget, column: int, title: str, initial: str) -> tk.Label:
        card = self.panel(parent)
        card.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else 10, 0))
        self.label(card, title, size=9, color=MUTED, bold=True).pack(anchor="w", padx=13, pady=(8, 0))
        value = self.label(card, initial, size=19, bold=True)
        value.pack(anchor="w", padx=13, pady=(1, 8))
        return value

    def endpoint(self, parent: tk.Widget, column: int, title: str, command, *, row: int = 0) -> tk.Label:
        block = tk.Frame(parent, bg=INSET)
        block.grid(row=row, column=column, sticky="ew", pady=(0, 5))
        content = tk.Frame(block, bg=INSET)
        content.pack(side="left", fill="x", expand=True, padx=8, pady=4)
        self.label(content, title, size=8, color=MUTED, bg=INSET).pack(anchor="w")
        value = self.label(content, "—", size=10, bold=True, bg=INSET)
        value.pack(anchor="w")
        self.button(block, "Copy", command, compact=True).pack(side="right", padx=5, pady=5)
        return value

    def show_notice(self, text: str, *, error: bool = False) -> None:
        self.notice.configure(text=self.redact(text), fg=DANGER if error else MUTED)

    def toggle_password(self) -> None:
        self.password_visible = not self.password_visible
        self.password_entry.configure(show="" if self.password_visible else "*")
        self.show_button.configure(text="Hide" if self.password_visible else "Show")

    def copy(self, value: str, label: str) -> None:
        if not value:
            self.show_notice(f"{label} is unavailable.")
            return
        self.window.clipboard_clear()
        self.window.clipboard_append(value)
        self.show_notice(f"{label} copied.")

    def browse_installation(self) -> None:
        folder = filedialog.askdirectory(parent=self.window, title="Choose the folder containing DayZServer_x64.exe",
                                          mustexist=True)
        if folder:
            self.action("Checking the selected folder…", lambda: self.server.set_installation(Path(folder)),
                        self.found_installation)

    def open_fps_settings(self) -> None:
        self.select_graphics_profile(PERFORMANCE)

    def apply_avalon_visuals(self) -> None:
        self.select_graphics_profile(VISUAL)

    def play_avalon_enhanced(self) -> None:
        def queued(message: str) -> None:
            self.refresh_graphics_choice()
            self.show_notice(message)

        self.action("Preparing Avalon enhancements…", lambda: self.azimuth.play_avalon(all_enhancements=True), queued)

    def restore_client_graphics(self) -> None:
        self.select_graphics_profile(CUSTOM)

    def toggle_avalon_color_preview(self) -> None:
        enabled = not self.server.avalon_color_preview_enabled()

        def finished(_result) -> None:
            self.refresh_graphics_choice()
            state = "enabled" if enabled else "disabled"
            self.show_notice(f"Avalon color preview {state}. Click Play Avalon.")

        self.action("Preparing the Avalon color preview…", lambda: self.server.set_avalon_color_preview(enabled),
                    finished)

    def open_avalon_visuals_guide(self) -> None:
        guide = self.server.root / "Visuals" / "START_HERE.txt"
        try:
            os.startfile(str(guide))
        except (OSError, AttributeError) as exc:
            self.show_notice(f"Could not open the graphics guide. {exc}", error=True)

    def select_graphics_profile(self, mode: str | None = None, *, choose: bool = False) -> None:
        config = self.server.root / "Config"

        def discover():
            saved = read_settings(config)
            profiles = find_profiles()
            return profiles, saved, choose_profile(profiles, saved.profile)

        def profile_found(result) -> None:
            profiles, saved, profile = result
            selected_mode = saved.mode if mode is None else mode
            if choose:
                initial = {}
                if profile is not None:
                    initial = {"initialdir": str(profile.parent), "initialfile": profile.name}
                elif profiles:
                    initial = {"initialdir": str(profiles[0].parent)}
                selected = filedialog.askopenfilename(
                    parent=self.window, title="Choose your DayZ settings profile",
                    filetypes=[("DayZ settings profiles", "*_settings.DayZProfile")], **initial,
                )
                if not selected:
                    self.show_notice("No DayZ profile was selected.")
                    return
                profile = Path(selected)
            elif profile is None:
                if selected_mode == CUSTOM:
                    self.show_notice("Choose a DayZ profile in Client to restore its graphics backup.", error=True)
                    return
                label = MODE_LABELS[selected_mode]

                def preset_saved(_result) -> None:
                    self.refresh_graphics_choice()
                    self.show_notice(f"{label} saved. Azimuth will apply it when it identifies your DayZ profile.")

                self.action("Saving your graphics preset…",
                            lambda: write_settings(config, selected_mode, saved.profile), preset_saved)
                return
            operation, label = {
                VISUAL: (apply_quality_preset, "Avalon Visuals ready"),
                PERFORMANCE: (apply_preset, "FPS preset ready"),
                BALANCED: (lambda path: apply_quality_preset(path, cap_visibility=True), "Balanced graphics ready"),
                CUSTOM: (restore_quality_preset, "Original graphics restored"),
            }[selected_mode]

            def apply() -> dict | None:
                result = None
                if choose:
                    visibility_fields(load_profile(profile))
                if selected_mode != CUSTOM:
                    result = operation(profile)
                elif mode is not None:
                    ensure_client_closed()
                    result = operation(profile)
                write_settings(config, selected_mode, profile)
                return result if isinstance(result, dict) else None

            def finished(_result) -> None:
                status = "Using your DayZ settings" if selected_mode == CUSTOM else label
                self.refresh_graphics_choice()
                missing = _result.get("missing_fields", []) if isinstance(_result, dict) else []
                if missing:
                    self.show_notice(f"{status}. Some fields are not saved yet: {', '.join(missing)}. "
                                     "Save Video settings in DayZ once, then relaunch.")
                else:
                    self.show_notice(f"{status} for {profile.name}.")

            self.action("Preparing DayZ graphics…", apply, finished)

        self.action("Finding your DayZ profile…", discover, profile_found)

    def open_steam(self) -> None:
        try:
            os.startfile("steam://install/223350")
        except (OSError, AttributeError):
            self.show_notice("Open Steam → Library → Tools, then install DayZ Server.", error=True)
            return
        self.show_notice("Finish installing DayZ Server in Steam. Azimuth will find it while enabled.")

    def open_dayz_tools(self) -> None:
        try:
            os.startfile("steam://install/830640")
        except (OSError, AttributeError):
            self.show_notice("Open Steam → Library → Tools, then install DayZ Tools.", error=True)
            return
        self.show_notice("Install DayZ Tools in Steam, then start or restart Avalon to prepare its Hunter Mods.")

    def open_avalon_client_mod(self) -> None:
        folder = self.server.worlds.paths("avalon").mods / "@HunterMods"
        if not folder.is_dir():
            self.show_notice("The Avalon Hunter Mods folder is missing. Extract the full Azimuth ZIP.", error=True)
            return
        try:
            os.startfile(str(folder))
        except (OSError, AttributeError) as exc:
            self.show_notice(f"Could not open the client mod folder: {exc}", error=True)
            return
        self.show_notice("In DayZ Launcher → Mods → Local Mod, select @HunterMods. Enable it with Avalon and CF.")

    def open_mods(self) -> None:
        try:
            self.server.mods_dir.mkdir(parents=True, exist_ok=True)
            os.startfile(str(self.server.mods_dir))
        except (OSError, AttributeError) as exc:
            self.show_notice(f"Could not open the Mods folder: {exc}", error=True)

    def selected_world_id(self) -> str:
        return self.world_ids[self.world_choice.get()]

    def open_world_setup(self) -> None:
        try:
            os.startfile(str(self.server.world_setup_dir / self.selected_world_id()))
        except (OSError, AttributeError) as exc:
            self.show_notice(f"Could not open World Setup: {exc}", error=True)

    def open_world_workshop(self) -> None:
        world_id = self.selected_world_id()
        if world_id == "avalon":
            self.show_page("workshop")
            return
        try:
            for item in self.server.world_workshop_ids(world_id):
                try:
                    os.startfile(f"steam://url/CommunityFilePage/{item}")
                except OSError:
                    os.startfile(f"https://steamcommunity.com/sharedfiles/filedetails/?id={item}")
        except (OSError, AttributeError) as exc:
            self.show_notice(f"Could not open Workshop: {exc}", error=True)
            return
    def open_avalon_workshop_pack(self) -> None:
        self.show_page("workshop")

    def open_avalon_loot_audit(self) -> None:
        if self.selected_world_id() != "avalon":
            return
        report = self.server.worlds.paths("avalon").profiles / AUDIT_PATH
        try:
            if not report.is_file():
                self.show_notice("The loot audit will be ready after Azimuth prepares Avalon. Click Play Avalon to continue.")
                return
            os.startfile(str(report))
        except (OSError, AttributeError) as exc:
            self.show_notice(f"Could not open the Avalon loot audit. {exc}", error=True)

    def open_avalon_pack_item(self, item_id: str, dialog: tk.Toplevel) -> bool:
        try:
            try:
                os.startfile(f"steam://url/CommunityFilePage/{item_id}")
            except OSError:
                os.startfile(f"https://steamcommunity.com/sharedfiles/filedetails/?id={item_id}")
        except (OSError, AttributeError) as exc:
            messagebox.showerror("Avalon Workshop pack", f"Could not open the Workshop item: {exc}", parent=dialog)
            return False
        return True

    def open_world_source(self) -> None:
        source = self.server.world_source_url(self.selected_world_id())
        if source:
            try:
                os.startfile(source)
            except (OSError, AttributeError) as exc:
                self.show_notice(f"Could not open the mission source: {exc}", error=True)

    def switch_world(self) -> None:
        world_id = self.selected_world_id()
        world_name = self.world_names[world_id]
        if world_id == self.server.active_world_id:
            return

        def switched(_: object) -> None:
            self.status_data = {}
            self.backup_data = {}
            self.server_logs = []
            message = f"{world_name} is selected."
            if not self.azimuth.enabled:
                message += " Click Start server when ready."
            self.show_notice(message)

        self.action(f"Switching to {world_name}…", lambda: self.azimuth.switch_world(world_id), switched)

    def refresh_world(self) -> None:
        active_world_id = self.server.active_world_id
        if active_world_id != self.active_world_id:
            if self.selected_world_id() == self.active_world_id:
                self.world_choice.set(self.world_names[active_world_id])
            self.active_world_id = active_world_id
            self.server_logs = []
            self.cpu_history.clear()
            self.memory_history.clear()
        if active_world_id != self.backup_world_id:
            self.backup_data = {}
            self.backup_records = ()
            self.next_backups_poll = 0.0
            self.backup_world_id = active_world_id
        world_id = self.selected_world_id()
        world_name = self.world_names[world_id]
        locked = self.busy or bool(self.stop_deadline) or self.server.backing_up or self.azimuth.play_pending
        self.world_menu.configure(state="disabled" if locked else "readonly")
        refresh_world_tiles(self, world_id, active_world_id, locked)
        self.world_switch_button.configure(state="normal" if world_id != active_world_id and not locked else "disabled")
        self.world_workshop_button.configure(state="normal" if CATALOG[world_id].workshop_ids else "disabled")
        self.world_source_button.configure(state="normal" if self.server.world_source_url(world_id) else "disabled")
        if world_id == "avalon" and not self.avalon_links.winfo_manager():
            self.avalon_links.pack(fill="x", pady=(5, 0))
        elif world_id != "avalon" and self.avalon_links.winfo_manager():
            self.avalon_links.pack_forget()
        if world_id == active_world_id:
            summary = self.server.world_summary()
        else:
            summary = f"{world_name} selected. Click Switch world to load its own save."
        self.world_summary_label.configure(text=summary)

    def open_backups(self) -> None:
        try:
            os.startfile(str(self.server.backups_dir))
        except (OSError, AttributeError) as exc:
            self.show_notice(f"Could not open the Backups folder: {exc}", error=True)

    def backup_now(self) -> None:
        def finished(path: Path) -> None:
            self.next_backups_poll = 0.0
            self.show_notice(f"Backup saved and verified · {path.name}")

        self.action("Backing up the selected world…", lambda: backup_now(self.azimuth), finished)

    def verify_selected_backup(self, path: Path) -> None:
        root = self.server.world_paths.root
        world_id = self.server.active_world_id
        self.action("Verifying archive…", lambda: verify_backup(root, path, world_id=world_id),
                    lambda record: self.show_notice(f"Archive verified · {record.path.name}"))

    def can_restore_backup(self) -> bool:
        return not (self.busy or self.stop_deadline or self.azimuth.enabled or self.azimuth.play_pending
                    or self.server_running() or self.server.backing_up)

    def restore_selected_backup(self, path: Path) -> None:
        if not self.can_restore_backup():
            self.show_notice("Stop the server and wait for Azimuth to pause before restoring a backup.", error=True)
            return
        world = self.world_names[self.server.active_world_id]
        if not messagebox.askyesno(
            "Restore world backup",
            f"Restore {path.name} to {world}?\n\n"
            "This replaces this world's saved characters, world state, archived world settings and saved skin choices. "
            "Azimuth retains a safety copy of the replaced files and creates a safety ZIP when current save files exist. "
            "Your connection settings are kept. Close DayZ before continuing.",
            parent=self.window, icon="warning",
        ):
            return

        def restore():
            from azimuth_app.ui.dashboard_operations import restore_backup
            return restore_backup(self.azimuth, path)

        def finished(safety_backup: Path | None) -> None:
            self.status_data = {}
            self.backup_data = {}
            self.backup_records = ()
            self.server_logs = []
            self.skins_page.reload_status()
            self.workshop_page.reset_status()
            self.next_backups_poll = self.next_skins_poll = self.next_workshop_poll = 0.0
            message = f"{world} restored. Click Start server when ready."
            if safety_backup is not None:
                message += f" Safety copy: {safety_backup.name}."
            self.show_notice(message)

        self.action(f"Restoring {world} from its backup…", restore, finished)

    def open_logs(self) -> None:
        try:
            os.startfile(str(self.server.profiles))
        except (OSError, AttributeError) as exc:
            self.show_notice(f"Could not open the logs folder: {exc}", error=True)

    def found_installation(self, path: Path | None) -> None:
        if path:
            self.show_notice("DayZ Server found." if self.azimuth.enabled else "DayZ Server found. Click Start server when ready.")
        else:
            self.show_notice("Install DayZ Server in Steam, or browse to an existing installation.")

    def settings_values(self) -> dict:
        values = {name: field.get() for name, field in self.fields.items()}
        for name, title in (("max_players", "Player limit"), ("game_port", "Game port"), ("query_port", "Query port")):
            try:
                values[name] = int(values[name])
            except ValueError:
                raise ValueError(f"{title} must be a whole number.") from None
        return values

    def save_settings(self) -> None:
        try:
            values = self.settings_values()
        except ValueError as exc:
            self.show_notice(str(exc), error=True)
            return
        self.action("Saving settings…", lambda: self.server.update_settings(values),
                    lambda _: self.show_notice("Server settings saved."))

    def play_avalon(self) -> None:
        self.action(
            "Preparing Avalon…",
            self.azimuth.play_avalon,
            lambda message: self.show_notice(message or "DayZ is launching and connecting to Avalon."),
        )

    def start_server(self) -> None:
        try:
            values = self.settings_values()
        except ValueError as exc:
            self.show_notice(str(exc), error=True)
            return

        def start() -> None:
            if any(self.server.settings.get(key) != value for key, value in values.items()):
                self.server.update_settings(values)
            self.azimuth.enable()

        self.status_data = {}
        self.action("Requesting server start…", start,
                    lambda _: self.show_notice("Server start requested. Join when it is online."))

    def stop_server(self) -> None:
        self.action("Pausing Azimuth…", self.azimuth.disable, self.wait_for_stop)

    def wait_for_stop(self, _: object) -> None:
        if self.server_running():
            self.stop_deadline = time.monotonic() + 45
            self.show_notice("Azimuth paused. Waiting for DayZ to save and shut down…")
        else:
            self.show_notice("Azimuth paused.")

    def close(self) -> None:
        self.show_notice("Azimuth continues running while minimized.")
        self.window.iconify()

    def exit_azimuth(self) -> None:
        if (self.azimuth.enabled or self.azimuth.play_pending or self.server_running()
                or self.server.backing_up or self.busy):
            self.show_notice("Stop the server and wait for Azimuth to pause before exiting.", error=True)
            return
        self.window.destroy()

    def server_running(self) -> bool:
        process = self.server.process
        return process is not None and process.poll() is None

    def action(self, label: str, command, done) -> None:
        if self.busy:
            return
        self.busy = True
        self.show_notice(label)
        try:
            self.refresh()
        except Exception as error:
            Dashboard._report_ui_error(self, error)

        def work() -> None:
            try:
                self.results.put(("action", done, command(), None))
            except Exception as exc:
                self.results.put(("action", done, None, exc))

        threading.Thread(target=work, daemon=True).start()

    def poll_status(self) -> None:
        if not self.polling and not self.busy:
            self.polling = True
            clock = time.monotonic()
            workshop_due = clock >= self.next_workshop_poll
            backups_due = clock >= self.next_backups_poll
            skins_due = clock >= self.next_skins_poll
            if workshop_due:
                self.next_workshop_poll = clock + 8
            if backups_due:
                self.next_backups_poll = clock + 12
            if skins_due:
                self.next_skins_poll = clock + 8

            def work() -> None:
                try:
                    world_id = self.server.active_world_id
                    status = self.server.status()
                    process = self.server.process
                    pid = process.pid if process is not None and process.poll() is None else None
                    sample = self.monitor.sample(pid)
                    payload = (world_id, status, self.server.recent_logs(), get_lan_ip(),
                               self.server.backup_status(), pid, sample)
                    self.results.put(("status", None, payload, None))
                except Exception as exc:
                    self.results.put(("status", None, None, exc))
                if workshop_due:
                    self._poll_optional("workshop", self.azimuth.avalon_workshop_status)
                    self._poll_optional("vehicle_workshop", lambda: vehicle_workshop.downloaded_ids(self.server))
                if skins_due:
                    self._poll_optional("skins", lambda: (time.monotonic(), self.server.skin_status()))
                if backups_due:
                    try:
                        world_id = self.server.active_world_id
                        root = self.server.worlds.paths(world_id).root
                        self.results.put(("backups", None, (world_id, list_backups(root)), None))
                    except Exception as exc:
                        self.results.put(("backups", None, None, exc))
                self.results.put(("poll_done", None, None, None))

            threading.Thread(target=work, daemon=True).start()
        self.window.after(2000, self.poll_status)

    def _poll_optional(self, kind, command) -> None:
        try:
            self.results.put((kind, None, command(), None))
        except Exception as exc:
            self.results.put((kind, None, None, exc))

    def _report_ui_error(self, error: Exception) -> None:
        """Record a display failure without losing the next background result."""
        message = f"Display update failed: {error}"
        try:
            message = self.redact(message)
            if not self.events or self.events[-1] != message:
                self.events.append(message)
            self.show_notice(message, error=True)
        except Exception:
            # The window may already be closing, or the failing widget may be
            # the notice itself. Neither should escape the polling boundary.
            pass

    def _dispatch_result(self, kind, done, payload, error) -> None:
        if kind == "action":
            self.busy = False
            if error:
                self.show_notice(str(error), error=True)
                self.events.append(self.redact(str(error)))
            else:
                done(payload)
        elif kind == "status":
            if not error and payload[0] == self.server.active_world_id and not self.busy:
                _, self.status_data, self.server_logs, self.lan_address, self.backup_data, pid, sample = payload
                if self.sample_pid != pid:
                    self.cpu_history.clear()
                    self.memory_history.clear()
                    self.sample_pid = pid
                self.process_sample = sample
                if pid is not None:
                    self.cpu_history.append(sample.cpu_percent)
                    self.memory_history.append(sample.memory_bytes)
        elif kind == "workshop":
            if error:
                message = str(error)
                changed = getattr(self, "workshop_error", None) != message
                self.workshop_status = None
                self.workshop_error = message
                self.workshop_page.show_pack_error(message)
                if changed:
                    notice = f"Could not check the Avalon Workshop pack: {message}"
                    self.show_notice(notice, error=True)
                    self.events.append(self.redact(notice))
            else:
                self.workshop_status = payload
                self.workshop_error = None
                self.workshop_page.render_status(payload)
        elif kind == "vehicle_workshop":
            if error:
                self.workshop_page.show_vehicle_error(str(error))
            else:
                self.workshop_page.render_vehicle_status(payload)
        elif kind == "skins":
            if error:
                self.skins_page.show_error(str(error))
            else:
                self.skins_page.render_status(payload[1], observed_at=payload[0])
        elif kind == "backups" and not error and payload[0] == self.server.active_world_id:
            self.backup_records = payload[1]
            self.backup_world_id = payload[0]
        elif kind == "poll_done":
            self.polling = False

    def drain_queues(self) -> None:
        try:
            while True:
                try:
                    result = self.results.get_nowait()
                except Empty:
                    break
                try:
                    Dashboard._dispatch_result(self, *result)
                except Exception as error:
                    Dashboard._report_ui_error(self, error)
            while True:
                try:
                    event = self.server.events.get_nowait()
                except Empty:
                    break
                self.events.append(self.redact(str(event)))
            self.events = self.events[-30:]
            if self.stop_deadline:
                if not self.server_running():
                    self.stop_deadline = 0.0
                    self.show_notice("DayZ Server stopped.")
                elif time.monotonic() >= self.stop_deadline:
                    self.stop_deadline = 0.0
                    self.show_notice("DayZ is still running. Check its server console.", error=True)
            play_error = self.azimuth.play_error
            if play_error and play_error != self.last_play_error:
                self.show_notice(play_error, error=True)
            self.last_play_error = play_error
            if self.azimuth.show_requested():
                self.window.deiconify()
                self.window.lift()
                self.window.focus_force()
            # A broken page must not prevent the activity view from showing the
            # error, nor stop queued action completions from being processed.
            for update in (self.refresh, self.render_logs):
                try:
                    update()
                except Exception as error:
                    Dashboard._report_ui_error(self, error)
        except Exception as error:
            Dashboard._report_ui_error(self, error)
        finally:
            try:
                self.window.after(250, self.drain_queues)
            except (tk.TclError, RuntimeError):
                pass  # The native window was destroyed while this tick ran.

    def refresh(self) -> None:
        running = self.server_running()
        buttons_locked = self.busy or bool(self.stop_deadline) or self.server.backing_up
        play_pending = self.azimuth.play_pending
        graphics_state = "disabled" if self.busy or self.azimuth.play_pending else "normal"
        for button in (self.fps_button, self.looks_button, self.choose_graphics_button, self.restore_graphics_button):
            button.configure(state=graphics_state)
        if self.active_page == "client":
            preview = self.server.avalon_color_preview_enabled()
            self.color_preview_button.configure(text="Disable color preview" if preview else "Enable color preview")
        self.color_preview_button.configure(state="disabled" if buttons_locked or play_pending else "normal")
        self.enhanced_play_button.configure(state="disabled" if buttons_locked or play_pending else "normal")
        self.workshop_page.set_busy(buttons_locked or play_pending)
        self.skins_page.set_busy(buttons_locked or play_pending, running=running)
        self.avalon_pack_button.configure(state="disabled" if self.busy else "normal")
        self.backup_page.set_busy(buttons_locked or play_pending)
        self.play_button.configure(state="disabled" if buttons_locked else "normal",
                                   text="↻  Preparing Avalon" if play_pending else "▶  Play Avalon")
        self.start_button.configure(state="disabled" if buttons_locked or running else "normal")
        can_stop = running or self.azimuth.enabled or play_pending
        self.stop_button.configure(state="normal" if can_stop and not buttons_locked else "disabled")
        can_exit = not buttons_locked and not can_stop
        self.exit_button.configure(state="normal" if can_exit else "disabled")
        for button in (self.browse_button, self.install_button, self.save_button):
            button.configure(state="disabled" if buttons_locked or running else "normal")
        for entry in self.entries:
            entry.configure(state="disabled" if buttons_locked or running else "normal")
        self.installation_text.configure(text=str(self.server.installation or "DayZ Server was not found"))
        activity = "Play Avalon" if play_pending else "Server"
        self.azimuth_label.configure(text=f"{activity} · {self.azimuth.message}",
                                     fg=ACCENT if self.azimuth.enabled or play_pending else MUTED)
        self.refresh_world()
        result = str(self.status_data.get("state") or "").casefold()
        if self.stop_deadline or (running and result == "stopping"):
            state, color = "Stopping", MUTED
        elif running:
            if result in {"online", "ready"}:
                state, color = "Online", ACCENT
            elif result == "running":
                state, color = "Waiting for status", MUTED
            else:
                state, color = "Starting", MUTED
        elif result == "error":
            state, color = "Server exited", DANGER
        else:
            state, color = ("Preparing", WARNING) if play_pending else ("Offline", MUTED)
        self.badge.configure(text=f"●  {state}", fg=color)
        port = self.server.settings.get("game_port", 2302)
        self.local_join = f"127.0.0.1:{port}"
        self.lan_join = f"{self.lan_address}:{port}" if self.lan_address and self.lan_address != "127.0.0.1" else ""
        self.local_label.configure(text=self.local_join)
        self.lan_label.configure(text=self.lan_join or "—")
        backup_state = self.backup_data.get("state") or "Backups every 6 hours"
        if self.server.backing_up:
            backup_state = "Backup in progress"
        backup_parts = [backup_state]
        if self.backup_data.get("last"):
            backup_parts.append(f"Last {self.backup_data['last']}")
        if self.backup_data.get("next"):
            backup_parts.append(f"Next {self.backup_data['next']}")
        self.backup_label.configure(text="  ·  ".join(backup_parts))
        self.backup_page.render_status(self.backup_data, self.backup_records)
        can_backup = not buttons_locked and not play_pending and (
            not running or result == "online" and self.status_data.get("players") == 0
        )
        self.backup_now_button.configure(state="normal" if can_backup else "disabled")
        render_overview(self, state, running)

    def render_logs(self) -> None:
        if self.active_page != "activity":
            return
        source = self.log_filter.get()
        entries = []
        if source in ("All", "Azimuth"):
            entries.extend((line, "azimuth") for line in self.events)
        if source in ("All", "Server"):
            entries.extend((self.redact(str(line).rstrip()), "") for line in self.server_logs[-85:])
        latest = tuple((line, "error" if any(word in line.casefold() for word in
                       ("error", "failed", "exception")) else tag) for line, tag in entries)
        if not latest:
            latest = (("No recent messages for this view.", "muted"),)
        if latest == self.last_rendered_logs:
            return
        self.last_rendered_logs = latest
        bottom = self.log_view.yview()[1] >= 0.97
        position = self.log_view.yview()[0]
        self.log_view.configure(state="normal")
        self.log_view.delete("1.0", "end")
        for line, tag in latest:
            self.log_view.insert("end", line + "\n", tag)
        self.log_view.configure(state="disabled")
        if bottom:
            self.log_view.see("end")
        else:
            self.log_view.yview_moveto(position)

    def redact(self, line: str) -> str:
        line = re.sub(r'(?i)(-password(?:=|\s+))(?:"[^"]*"|\S+)', r"\1[hidden]", line)
        for secret in (self.server.settings.get("password"), getattr(self.server, "_admin_password", None)):
            if secret:
                line = line.replace(secret, "[hidden]")
        return line


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--background", action="store_true", help=argparse.SUPPRESS)
    options = parser.parse_args()
    root = APP_ROOT
    if options.background:
        try:
            remove_sign_in_shortcut(root)
        except (OSError, RuntimeError):
            pass
        return
    with claim_dashboard(root) as claimed:
        if not claimed:
            return
        window = tk.Tk()
        azimuth = None
        try:
            archive = migrate_layout(root)
            server = Server(root)
            if archive is not None:
                server.events.put(f"Old layout archived: {archive.relative_to(root)}")
            azimuth = Azimuth(server)
            Dashboard(window, server, azimuth)
            azimuth.start_worker()
            window.mainloop()
        except (OSError, ValueError, RuntimeError) as exc:
            window.withdraw()
            messagebox.showerror("Azimuth", str(exc), parent=window)
            window.destroy()
        finally:
            if azimuth is not None:
                azimuth.shutdown()


if __name__ == "__main__":
    main()
