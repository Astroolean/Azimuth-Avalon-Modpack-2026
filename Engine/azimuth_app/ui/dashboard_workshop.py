"""Live Workshop downloads and Avalon pack controls for the dashboard."""

from __future__ import annotations

import os
from pathlib import Path
import tkinter as tk
from tkinter import ttk

from azimuth_app.ui.dashboard_theme import ACCENT, BG, EDGE, INSET, MUTED, PANEL
from azimuth_app.transport import vehicle_workshop, additional_vehicles


ITEM_DETAILS = {
    "1559212036": ("Community Framework", "Core dependency"),
    "3225634444": ("Avalon", "Island terrain and mission"),
    "3556131153": ("RaG Core", "Building dependency"),
    "3117613872": ("RaG Hunting Cabin", "Placeable cabin kit"),
    "3157695626": ("RaG BaseBuilding", "Modular construction"),
    "2878980498": ("RaG BaseItems", "Workbenches and greenhouses"),
    "2879040969": ("RaG BeeHive", "Beekeeping equipment"),
    "3776514672": ("Solitude Storage", "Furniture and containers"),
    "2670506982": ("Building Fortifications", "Door and window barricades"),
    "2918418331": ("Survivor Animations", "Emotes and vehicle animations"),
    "2971190303": ("DayZ Bicycle", "Fuel-free travel"),
    "2471347750": ("DayZ Dog", "Tameable companion"),
    "3821819263": ("DayZ Guitar", "Playable acoustic guitar"),
    "3623510671": ("Psyerns Sound System", "Configurable ambient audio"),
    "2757509117": ("gebsfish", "Fishing gear and catches"),
    "1870524790": ("Airdrop-Upgraded", "Flare-called supply drops"),
}


def _repair_collection_notice(path: Path, summary: dict) -> tuple[str, bool]:
    """Describe the actual export instead of implying every requested file exists."""
    if summary.get('summary_error'):
        return (f"Vehicle repair ZIP saved: {path.name}. Its summary could not be read. "
                f"Keep this ZIP; nothing was uploaded. Folder: {path.parent}", True)
    requested = summary['requested_assets']
    copied = summary['copied_assets']
    missing = summary['missing_assets']
    companions = summary.get('companion_files', [])
    count = 1 + len(companions)
    message = f"Collected {copied} of {requested} requested vehicle assets."
    if missing:
        message += f" {missing} were not collected; the report lists why."
    if summary.get('partial_scan'):
        message += " Some archive files could not be read; details are in the report."
    if summary.get('limited_files'):
        noun = 'file' if summary['limited_files'] == 1 else 'files'
        message += f" {summary['limited_files']} {noun} exceeded a collection limit."
    lm_status = summary.get('lm_status', 'unknown')
    if lm_status not in ('copied', 'copied_in_companion', 'copied_to_companion'):
        if lm_status in ('not_found', 'not_installed', 'source_not_located'):
            message += " LM's Planes archive was not located."
        elif lm_status in ('not_scanned', 'scan_incomplete'):
            message += " LM's Planes could not be fully checked."
        elif lm_status == 'diagnostic_only':
            message += " LM's Planes was too large; diagnostic data was saved."
        elif lm_status == 'over_size_limit':
            message += " LM's Planes was too large for full collection."
        else:
            message += " LM's Planes archive was not included in full."
    if companions:
        message += f" Saved {count} ZIPs in this folder; send all {count} together."
    else:
        message += f" ZIP saved: {path.name}."
    message += " Nothing was uploaded."
    return message, bool(summary.get('partial_scan') or summary.get('limited_files')
                         or lm_status not in ('copied', 'copied_in_companion', 'copied_to_companion'))


class WorkshopPage(tk.Frame):
    """Render Steam readiness without doing disk or network work on the UI thread."""

    def __init__(self, parent: tk.Misc, dashboard) -> None:
        super().__init__(parent, bg=BG)
        self.dashboard = dashboard
        self.items = tuple((name, str(item_id)) for name, item_id in dashboard.azimuth.avalon_workshop_items())
        self.status: tuple[bool, frozenset[str]] | None = None
        self.opened: set[str] = set()
        self.busy = False
        self.rows: dict[str, tuple[tk.Label, tk.Button]] = {}
        self.vehicle_items = vehicle_workshop.ITEMS
        self.vehicle_status: frozenset[str] | None = None
        self.vehicle_opened: set[str] = set()
        self.vehicle_rows: dict[str, tuple[tk.Label, tk.Button]] = {}
        self._build()
        self._render_controls()

    def _build(self) -> None:
        ui = self.dashboard
        heading = tk.Frame(self, bg=BG)
        heading.pack(fill="x", pady=(0, 15))
        titles = tk.Frame(heading, bg=BG)
        titles.pack(side="left", fill="x", expand=True)
        ui.label(titles, "Workshop", size=23, bold=True, bg=BG).pack(anchor="w")
        ui.label(titles, "Steam downloads and mod setup for Avalon", color=MUTED, bg=BG).pack(
            anchor="w", pady=(2, 0)
        )
        ui.button(heading, "Steam downloads", self._open_steam_downloads).pack(side="right")
        self.refresh_button = ui.button(heading, "Refresh", self._refresh, compact=True)
        self.refresh_button.pack(side="right", padx=(0, 8))

        overview = ui.panel(self)
        overview.pack(fill="x", pady=(0, 13))
        body = tk.Frame(overview, bg=PANEL)
        body.pack(fill="x", padx=17, pady=14)
        summary = tk.Frame(body, bg=PANEL)
        summary.pack(side="left", fill="both", expand=True)
        tools = tk.Frame(body, bg=PANEL)
        tools.pack(side="right", anchor="n", padx=(16, 0))

        title = tk.Frame(summary, bg=PANEL)
        title.pack(fill="x", pady=(0, 8))
        ui.label(title, "Avalon Workshop pack", size=13, bold=True).pack(side="left")
        self.pack_state = ui.label(title, "Checking Steam", size=9, color=MUTED)
        self.pack_state.pack(side="right", padx=(8, 0))
        self.progress = tk.Canvas(summary, bg=INSET, height=7, bd=0, highlightthickness=0)
        self.progress.pack(fill="x")
        self.progress.bind("<Configure>", self._draw_progress)
        self.summary = ui.label(summary, "Reading installed Workshop items…", size=9, color=MUTED)
        self.summary.pack(anchor="w", pady=(8, 0))
        self.next_button = ui.button(tools, "Open next missing", self._open_next, primary=True)
        self.next_button.pack(fill="x")
        self.enable_button = ui.button(tools, "Enable pack", self._enable, primary=True)

        table = ui.panel(self)
        table.pack(fill="both", expand=True)
        label_row = tk.Frame(table, bg=PANEL)
        label_row.pack(fill="x", padx=17, pady=(12, 9))
        ui.label(label_row, "Additional vehicles", size=13, bold=True).pack(side="left")
        self.vehicle_summary = ui.label(label_row, "Checking vehicle downloads…", size=9, color=MUTED)
        self.vehicle_summary.pack(side="right")
        instructions = ui.label(
            table, "Open a vehicle in Steam and click Subscribe. Supported downloaded packs are added to both DayZ and the server the next time Avalon starts.",
            size=9, color=MUTED, wraplength=780, justify="left")
        instructions.pack(fill="x", padx=17, pady=(0, 4))
        instructions.bind("<Configure>", lambda event: instructions.configure(wraplength=max(180, event.width - 4)))
        existing_help = ui.label(
            table, "Vehicle coverage lists the exact wrap and spawn support for the integrated packs. "
            "Jana 50 mopeds and Bitrak motorcycles are included with DayZ.",
            size=9, color=MUTED, wraplength=780, justify="left")
        existing_help.pack(fill="x", padx=17, pady=(0, 8))
        existing_help.bind("<Configure>", lambda event: existing_help.configure(wraplength=max(180, event.width - 4)))
        vehicle_tools = tk.Frame(table, bg=PANEL)
        vehicle_tools.pack(fill="x", padx=17, pady=(0, 10))
        self.vehicle_next_button = ui.button(
            vehicle_tools, "Open next vehicle", self._open_next_vehicle, primary=True, compact=True)
        self.vehicle_next_button.pack(side="left")
        self.vehicle_export_button = ui.button(
            vehicle_tools, "Export vehicle data", self._export_vehicle_data, compact=True)
        self.vehicle_export_button.pack(side="left", padx=(8, 0))
        self.vehicle_repair_button = ui.button(
            vehicle_tools, "Collect vehicle repair files",
            lambda: WorkshopPage._collect_vehicle_repair_files(self), compact=True)
        self.vehicle_repair_button.pack(side="left", padx=(8, 0))
        repair_help = ui.label(
            table, "Collect relevant vehicle models/mod files into a new folder. Large collections split into ZIPs automatically. "
            "No saves, player profiles or upload. Normal integrated packs do not need this.",
            size=9, color=MUTED, wraplength=780, justify="left")
        repair_help.pack(fill="x", padx=17, pady=(0, 10))
        repair_help.bind("<Configure>", lambda event: repair_help.configure(wraplength=max(180, event.width - 4)))
        tk.Frame(table, height=1, bg=EDGE).pack(fill="x")
        list_frame = tk.Frame(table, bg=PANEL)
        list_frame.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(list_frame, bg=PANEL, bd=0, highlightthickness=0, yscrollincrement=27)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", style="Azimuth.Vertical.TScrollbar",
                                  command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y", pady=5, padx=(0, 2))
        self.canvas.pack(side="left", fill="both", expand=True)
        rows = tk.Frame(self.canvas, bg=PANEL)
        row_window = self.canvas.create_window(0, 0, anchor="nw", window=rows)
        rows.bind("<Configure>", lambda _event: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda event: self.canvas.itemconfigure(row_window, width=event.width))
        for item_id, name, detail in self.vehicle_items:
            row = tk.Frame(rows, bg=PANEL)
            row.pack(fill="x", padx=(17, 7))
            text = tk.Frame(row, bg=PANEL)
            text.pack(side="left", fill="x", expand=True, pady=7)
            ui.label(text, name, bold=True).pack(anchor="w")
            detail_label = ui.label(text, detail, size=8, color=MUTED, wraplength=540, justify="left")
            detail_label.pack(fill="x", anchor="w")
            detail_label.bind("<Configure>", lambda event, label=detail_label: label.configure(
                wraplength=max(150, event.width - 4)))
            action = ui.button(row, "Open Steam", lambda item_id=item_id: self._open_vehicle_item(item_id), compact=True)
            action.pack(side="right", pady=5)
            state = ui.label(row, "Checking", size=9, color=MUTED, bg=INSET, width=14, padx=8, pady=4)
            state.pack(side="right", padx=(0, 10))
            self.vehicle_rows[item_id] = (state, action)
            tk.Frame(rows, height=1, bg=EDGE).pack(fill="x", padx=(17, 7))
        current_heading = tk.Frame(rows, bg=PANEL)
        current_heading.pack(fill="x", padx=17, pady=(18, 8))
        ui.label(current_heading, "Current Avalon pack", size=12, bold=True).pack(side="left")
        ui.label(current_heading, "Existing required mods", size=9, color=MUTED).pack(side="right")
        for name, item_id in self.items:
            row = tk.Frame(rows, bg=PANEL)
            row.pack(fill="x", padx=(17, 7))
            name, detail = ITEM_DETAILS.get(item_id, (name, ""))
            text = tk.Frame(row, bg=PANEL)
            text.pack(side="left", fill="x", expand=True, pady=6)
            ui.label(text, name, bold=True).pack(anchor="w")
            if detail:
                ui.label(text, detail, size=8, color=MUTED).pack(anchor="w")
            action = ui.button(row, "Open Steam", lambda item_id=item_id: self._open_item(item_id), compact=True)
            action.pack(side="right", pady=5)
            state = ui.label(row, "Checking", size=9, color=MUTED, bg=INSET, width=11, padx=8, pady=4)
            state.pack(side="right", padx=(0, 10))
            self.rows[item_id] = (state, action)
            tk.Frame(rows, height=1, bg=EDGE).pack(fill="x", padx=(17, 7))
        self._bind_scroll(self.canvas)

    def _bind_scroll(self, widget: tk.Misc) -> None:
        widget.bind("<MouseWheel>", self._wheel)
        widget.bind("<Button-4>", self._wheel)
        widget.bind("<Button-5>", self._wheel)
        for child in widget.winfo_children():
            self._bind_scroll(child)

    def _wheel(self, event: tk.Event) -> str:
        if event.num in (4, 5):
            units = -1 if event.num == 4 else 1
        else:
            units = -(event.delta // 120) if abs(event.delta) >= 120 else (-1 if event.delta > 0 else 1)
        self.canvas.yview_scroll(units, "units")
        return "break"

    def _draw_progress(self, _event=None) -> None:
        self.progress.delete("fill")
        if self.status is None or not self.items:
            return
        width = self.progress.winfo_width()
        ratio = len(self.status[1]) / len(self.items)
        if ratio:
            self.progress.create_rectangle(0, 0, width * ratio, 7, fill=ACCENT, outline="", tags="fill")

    def render_status(self, status: tuple[bool, set[str]] | None) -> None:
        if status is None:
            return
        enabled, installed = status
        ready = frozenset(str(item_id) for item_id in installed if str(item_id) in self.rows)
        updated = bool(enabled), ready
        self.dashboard.workshop_status = updated
        self.dashboard.workshop_error = None
        if updated == self.status:
            return
        self.status = updated
        self.opened.difference_update(ready)
        for item_id, (state, _action) in self.rows.items():
            installed = item_id in ready
            state.configure(text="Installed" if installed else "Needed", fg=ACCENT if installed else MUTED)
        self._draw_progress()
        self._render_controls()

    def show_pack_error(self, message: str) -> None:
        self.status = None
        self.dashboard.workshop_status = None
        self.dashboard.workshop_error = message
        self.pack_state.configure(text="Check failed", fg=MUTED)
        self.summary.configure(text="Could not check the Avalon pack. Click Refresh.")
        for state, _action in self.rows.values():
            state.configure(text="Check failed", fg=MUTED)
        self._draw_progress()
        self._render_controls()

    def reset_status(self) -> None:
        self.status = None
        self.dashboard.workshop_status = None
        self.dashboard.workshop_error = None
        self.pack_state.configure(text="Checking Steam", fg=MUTED)
        self.summary.configure(text="Reading installed Workshop items…")
        for state, _action in self.rows.values():
            state.configure(text="Checking", fg=MUTED)
        self._draw_progress()
        self._render_controls()

    def set_busy(self, busy: bool) -> None:
        if busy != self.busy:
            self.busy = busy
            self._render_controls()

    def _render_controls(self) -> None:
        self.refresh_button.configure(state="disabled" if self.busy else "normal")
        self.vehicle_export_button.configure(state="disabled" if self.busy else "normal")
        if hasattr(self, "vehicle_repair_button"):
            self.vehicle_repair_button.configure(state="disabled" if self.busy else "normal")
        vehicle_missing = self.vehicle_status is not None and any(
            item_id not in self.vehicle_status for item_id, _name, _detail in self.vehicle_items)
        self.vehicle_next_button.configure(state="normal" if vehicle_missing and not self.busy else "disabled")
        for _state, action in self.vehicle_rows.values():
            action.configure(state="disabled" if self.busy else "normal")
        if self.status is None:
            self.next_button.configure(state="disabled")
            self.enable_button.configure(state="disabled")
            self.enable_button.pack_forget()
            return
        enabled, ready = self.status
        total = len(self.items)
        missing = total - len(ready)
        if missing:
            self.pack_state.configure(text="Waiting for Steam", fg=MUTED)
            self.summary.configure(text=f"{len(ready)} of {total} items ready")
            self.enable_button.pack_forget()
            if not self.next_button.winfo_manager():
                self.next_button.pack(fill="x")
            self.next_button.configure(text="Open next missing", state="normal")
        else:
            self.pack_state.configure(text="Enabled" if enabled else "Ready to enable", fg=ACCENT)
            self.summary.configure(text=f"All {total} items ready")
            self.next_button.pack_forget()
            if enabled:
                self.enable_button.pack_forget()
            elif total:
                self.enable_button.pack(fill="x")
                self.enable_button.configure(state="disabled" if self.busy else "normal")

    def _open_item(self, item_id: str) -> bool:
        if self.dashboard.open_avalon_pack_item(item_id, self.winfo_toplevel()):
            self.opened.add(item_id)
            return True
        return False

    def _open_next(self) -> None:
        if self.status is None:
            return
        _enabled, ready = self.status
        missing = [item_id for _, item_id in self.items if item_id not in ready]
        if not missing:
            return
        item_id = next((item_id for item_id in missing if item_id not in self.opened), None)
        if item_id is None:
            item_id = missing[0]
            self.opened.difference_update(missing)
        if self._open_item(item_id):
            self.dashboard.show_notice("Steam opened the Workshop item. Click Subscribe there to download it.")

    def render_vehicle_status(self, downloaded: set[str] | None) -> None:
        if downloaded is None:
            return
        ready = frozenset(str(item_id) for item_id in downloaded if str(item_id) in self.vehicle_rows)
        self.vehicle_status = ready
        self.vehicle_opened.difference_update(ready)
        for item_id, (state, _action) in self.vehicle_rows.items():
            found = item_id in ready
            text = "Not downloaded"
            if found:
                text = "Auto-load" if item_id in additional_vehicles.READY_IDS else "Data unreadable"
            state.configure(text=text, fg=ACCENT if found and item_id in additional_vehicles.READY_IDS else MUTED)
        supported = len(ready & additional_vehicles.READY_IDS)
        self.vehicle_summary.configure(text=f"{len(ready)} of {len(self.vehicle_items)} downloads found · {supported} packs configured for Avalon")
        self._render_controls()

    def show_vehicle_error(self, _message: str) -> None:
        self.vehicle_status = None
        self.vehicle_summary.configure(text="Could not check vehicle downloads. Click Refresh.")
        for state, _action in self.vehicle_rows.values():
            state.configure(text="Check failed", fg=MUTED)
        self._render_controls()

    def _open_vehicle_item(self, item_id: str) -> bool:
        if self.busy or item_id not in self.vehicle_rows:
            return False
        if self.dashboard.open_avalon_pack_item(item_id, self.winfo_toplevel()):
            self.vehicle_opened.add(item_id)
            if item_id in additional_vehicles.READY_IDS:
                self.dashboard.show_notice("Click Subscribe in Steam. When downloads finish, restart Avalon to load this vehicle pack.")
            else:
                self.dashboard.show_notice("This pack is not integrated. Collect vehicle repair files gathers its installed source evidence and opens the folder to send.")
            return True
        return False

    def _open_next_vehicle(self) -> None:
        if self.busy or self.vehicle_status is None:
            return
        missing = [item_id for item_id, _name, _detail in self.vehicle_items if item_id not in self.vehicle_status]
        if not missing:
            return
        item_id = next((item_id for item_id in missing if item_id not in self.vehicle_opened), None)
        if item_id is None:
            self.vehicle_opened.difference_update(missing)
            item_id = missing[0]
        self._open_vehicle_item(item_id)

    def _export_vehicle_data(self) -> None:
        if not self.busy:
            # One export implementation and one file format across both pages.
            self.dashboard.skins_page._export_vehicle_data()

    def _collect_vehicle_repair_files(self) -> None:
        if self.busy:
            return
        server = self.dashboard.server

        def collect():
            from azimuth_app.checks.vehicle_repair_assets import collect_vehicle_repair_assets, read_collection_summary
            libraries = server.steam_libraries(server.installation, refresh=True)
            path = collect_vehicle_repair_assets(server.root, libraries, server.installation)
            try:
                summary = read_collection_summary(path)
            except (OSError, ValueError):
                summary = {'summary_error': True}
            return path, summary

        def finished(result) -> None:
            path, summary = result
            notice, incomplete = _repair_collection_notice(path, summary)
            self.dashboard.show_notice(notice, error=incomplete)
            try:
                os.startfile(str(path.parent))
            except (OSError, AttributeError):
                self.dashboard.show_notice(f"{notice} Folder: {path.parent}", error=incomplete)

        self.dashboard.action("Collecting vehicle repair files…", collect, finished)

    def _open_steam_downloads(self) -> None:
        try:
            os.startfile("steam://open/downloads")
        except (OSError, AttributeError) as error:
            self.dashboard.show_notice(f"Could not open Steam downloads: {error}", error=True)

    def _refresh(self) -> None:
        if self.busy:
            return

        def read_status():
            results = []
            for command in (self.dashboard.azimuth.avalon_workshop_status,
                            lambda: vehicle_workshop.downloaded_ids(self.dashboard.server)):
                try:
                    results.append((command(), None))
                except Exception as error:
                    # Keep the two catalogs independent if one scan fails.
                    results.append((None, str(error)))
            return results

        self.dashboard.action("Checking Steam downloads…", read_status, self._refreshed)

    def _refreshed(self, results) -> None:
        (pack_status, pack_error), (vehicle_status, vehicle_error) = results
        if pack_error is None:
            self.render_status(pack_status)
        else:
            WorkshopPage.show_pack_error(self, pack_error)
        if vehicle_error is None:
            self.render_vehicle_status(vehicle_status)
        else:
            self.show_vehicle_error(vehicle_error)
        if pack_error or vehicle_error:
            detail = "; ".join(error for error in (pack_error, vehicle_error) if error)
            self.dashboard.show_notice(f"Some Workshop downloads could not be checked: {detail}", error=True)
        else:
            self.dashboard.show_notice("Workshop status refreshed.")

    def _enable(self) -> None:
        self.dashboard.action("Preparing the Avalon Workshop pack…", self.dashboard.azimuth.enable_avalon_workshop,
                              self._enabled)

    def _enabled(self, notice: str) -> None:
        if self.status is not None:
            self.render_status((True, self.status[1]))
        self.dashboard.show_notice(notice)
