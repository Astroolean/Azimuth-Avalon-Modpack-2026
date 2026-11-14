"""The live overview for Azimuth."""
from azimuth_app.paths import ASSETS_ROOT

from pathlib import Path
import tkinter as tk

from azimuth_app.ui.dashboard_monitor import format_memory, format_uptime
from azimuth_app.ui.dashboard_theme import ACCENT, BG, DIM, EDGE, INSET, MUTED, PANEL, TEAL, TEXT, Trend


class Harbor(tk.Canvas):
    def __init__(self, parent, dashboard) -> None:
        super().__init__(parent, height=146, bg="#142426", highlightthickness=1, highlightbackground=EDGE)
        self.dashboard = dashboard
        self.asset = None
        try:
            self.asset = tk.PhotoImage(master=self, file=ASSETS_ROOT / "Harbor.png")
        except tk.TclError:
            pass
        if self.asset is not None:
            self.art = self.create_image(0, 0, image=self.asset, anchor="w")
        self.world = self.create_text(28, 31, text="ACTIVE WORLD", fill=ACCENT, anchor="w",
                                      font=(dashboard.ui_font, 9, "bold"))
        self.name = self.create_text(26, 68, text="", fill=TEXT, anchor="w",
                                     font=(dashboard.ui_font, 33, "bold"))
        self.detail = self.create_text(29, 109, text="", fill=MUTED, anchor="w",
                                       font=(dashboard.ui_font, 10))
        self.bind("<Configure>", self._place_art)

    def _place_art(self, event) -> None:
        if self.asset is not None:
            self.coords(self.art, min(0, event.width - self.asset.width()), event.height / 2)
        self.itemconfigure(self.detail, width=max(260, event.width - 56))

    def render(self, name: str, detail: str) -> None:
        self.itemconfigure(self.name, text=name)
        self.itemconfigure(self.detail, text=detail)


def build_overview(parent, dashboard) -> None:
    harbor = Harbor(parent, dashboard)
    harbor.pack(fill="x", pady=(0, 12))
    dashboard.harbor = harbor

    metrics = tk.Frame(parent, bg=BG)
    metrics.pack(fill="x", pady=(0, 12))
    metrics.columnconfigure((0, 1, 2, 3), weight=1, uniform="overview-metrics")
    dashboard.players_label = dashboard.metric(metrics, 0, "Players", "—")
    dashboard.cpu_label = dashboard.metric(metrics, 1, "Server CPU", "—")
    dashboard.memory_label = dashboard.metric(metrics, 2, "Server memory", "—")
    dashboard.uptime_label = dashboard.metric(metrics, 3, "Uptime", "—")

    detail = tk.Frame(parent, bg=BG)
    detail.pack(fill="both", expand=True)
    detail.columnconfigure(0, weight=3, uniform="overview-detail")
    detail.columnconfigure(1, weight=2, uniform="overview-detail")
    detail.rowconfigure(0, weight=1)
    activity = dashboard.panel(detail)
    activity.grid(row=0, column=0, sticky="nsew", padx=(0, 11))
    activity_inner = tk.Frame(activity, bg=PANEL)
    activity_inner.pack(fill="both", expand=True, padx=15, pady=12)
    row = tk.Frame(activity_inner, bg=PANEL)
    row.pack(fill="x", pady=(0, 5))
    dashboard.label(row, "Server telemetry", size=13, bold=True).pack(side="left")
    dashboard.label(row, "Last 60 samples", color=DIM, size=9).pack(side="right")
    dashboard.label(activity_inner, "CPU  ·  % of host capacity", color=ACCENT, size=9).pack(anchor="w")
    dashboard.cpu_trend = Trend(activity_inner, height=47)
    dashboard.cpu_trend.pack(fill="both", expand=True, pady=(2, 6))
    dashboard.label(activity_inner, "Memory  ·  resident", color=TEAL, size=9).pack(anchor="w")
    dashboard.memory_trend = Trend(activity_inner, TEAL, height=47)
    dashboard.memory_trend.pack(fill="both", expand=True, pady=(2, 0))
    dashboard.telemetry_note = dashboard.label(activity_inner, "Waiting for a DayZ Server process", size=8, color=MUTED)
    dashboard.telemetry_note.pack(anchor="w", pady=(5, 0))

    info = dashboard.panel(detail)
    info.grid(row=0, column=1, sticky="nsew")
    info_inner = tk.Frame(info, bg=PANEL)
    info_inner.pack(fill="both", expand=True, padx=15, pady=12)
    dashboard.label(info_inner, "Connection", size=13, bold=True).pack(anchor="w")
    endpoints = tk.Frame(info_inner, bg=PANEL)
    endpoints.pack(fill="x", pady=(5, 7))
    endpoints.columnconfigure(0, weight=1)
    dashboard.local_label = dashboard.endpoint(endpoints, 0, "On this PC",
                                               lambda: dashboard.copy(dashboard.local_join, "Join address"), row=0)
    dashboard.lan_label = dashboard.endpoint(endpoints, 0, "Local network",
                                             lambda: dashboard.copy(dashboard.lan_join, "Join address"), row=1)
    tk.Frame(info_inner, bg=EDGE, height=1).pack(fill="x", pady=(0, 6))
    dashboard.connection_detail = dashboard.label(info_inner, "", size=9, color=MUTED, wraplength=238, justify="left")
    dashboard.connection_detail.pack(anchor="w")
    dashboard.backup_label = dashboard.label(info_inner, "", size=9, color=MUTED, wraplength=238, justify="left")
    dashboard.backup_label.pack(anchor="w", pady=(4, 0))
    dashboard.workshop_summary = dashboard.label(info_inner, "Checking Steam Workshop", size=9, color=MUTED,
                                                 wraplength=238, justify="left")
    dashboard.workshop_summary.pack(anchor="w", pady=(4, 0))
    def fit_connection(event) -> None:
        for label in (dashboard.connection_detail, dashboard.backup_label, dashboard.workshop_summary):
            label.configure(wraplength=max(120, event.width - 2))

    info_inner.bind("<Configure>", fit_connection)

    shortcuts = tk.Frame(parent, bg=BG)
    shortcuts.pack(fill="x", pady=(11, 0))
    for text, action in (
        ("View activity", lambda: dashboard.show_page("activity")),
        ("Steam Workshop", lambda: dashboard.show_page("workshop")),
    ):
        dashboard.button(shortcuts, text, action, compact=True).pack(side="right", padx=(8, 0))
    dashboard.overview_note = dashboard.label(shortcuts, "", color=MUTED, bg=BG, size=9, wraplength=420, justify="left")
    dashboard.overview_note.pack(side="left", fill="x", expand=True)


def render_overview(dashboard, state: str, running: bool) -> None:
    status = dashboard.status_data
    process = dashboard.process_sample if running else None
    players = status.get("players") if running and state == "Online" else None
    slots = status.get("max_players") or dashboard.server.settings.get("max_players")
    dashboard.players_label.configure(text=f"{players if players is not None else '—'} / {slots or '—'}")
    cpu = process.cpu_percent if process else None
    dashboard.cpu_label.configure(text=f"{cpu:.1f}%" if cpu is not None else "—")
    dashboard.memory_label.configure(text=format_memory(process.memory_bytes if process else None))
    dashboard.uptime_label.configure(text=format_uptime(process.uptime_seconds if process else None))
    dashboard.cpu_trend.set_data(dashboard.cpu_history, 100)
    max_memory = max((value or 0 for value in dashboard.memory_history), default=0)
    dashboard.memory_trend.set_data(dashboard.memory_history, max(1024 ** 3, max_memory * 1.15))
    if not running:
        telemetry_note = "Waiting for a DayZ Server process"
    elif process is None or process.memory_bytes is None and process.uptime_seconds is None:
        telemetry_note = "Waiting for process telemetry"
    else:
        telemetry_note = ""
    dashboard.telemetry_note.configure(text=telemetry_note)

    world = dashboard.world_names[dashboard.server.active_world_id]
    version = status.get("version") if running and state == "Online" else None
    query_ms = status.get("query_ms") if running and state == "Online" else None
    if version is not None:
        parts = [f"DayZ {version}"]
    else:
        parts = ["Steam dedicated server"]
    if query_ms is not None:
        parts.append(f"Local query {query_ms:g} ms")
    dashboard.connection_detail.configure(text="  ·  ".join(parts))
    if dashboard.workshop_status is not None:
        enabled, installed = dashboard.workshop_status
        total = len(dashboard.azimuth.avalon_workshop_items())
        ready = len(installed)
        text = f"Avalon Workshop · {ready}/{total} ready"
        if enabled and ready == total:
            text = f"Avalon Workshop · {total} items ready"
        dashboard.workshop_summary.configure(text=text)
    elif dashboard.workshop_error:
        dashboard.workshop_summary.configure(text="Avalon Workshop · check failed. Open Workshop to retry.")
    else:
        dashboard.workshop_summary.configure(text="Checking Steam Workshop")
    hero_detail = dashboard.server.settings.get("hostname", "FreeDayZ Private Server")
    dashboard.harbor.render(world, hero_detail)
    latest = dashboard.events[-1] if dashboard.events else ""
    if len(latest) > 115:
        latest = latest[:112] + "…"
    dashboard.overview_note.configure(text=latest)
