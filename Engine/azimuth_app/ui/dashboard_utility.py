"""Settings, archive management, and log views for the Azimuth desktop."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from azimuth_app.ui.dashboard_theme import ACCENT, BG, BUTTON, DANGER, DIM, EDGE, INSET, MUTED, PANEL, TEXT


def _panel_content(parent, dashboard, *, padding=20):
    panel = dashboard.panel(parent)
    content = tk.Frame(panel, bg=PANEL)
    content.pack(fill="both", expand=True, padx=padding, pady=padding)
    return panel, content


def build_settings(parent, d) -> None:
    d.page_heading(parent, "Server settings", "Manage your server identity, connection ports, and Steam installation.")
    grid = tk.Frame(parent, bg=BG)
    grid.pack(fill="x")
    grid.columnconfigure(0, weight=3)
    grid.columnconfigure(1, weight=2)

    card, settings = _panel_content(grid, d)
    card.grid(row=0, column=0, sticky="new", padx=(0, 12))
    d.label(settings, "Server identity", size=15, bold=True).pack(anchor="w", pady=(0, 10))
    d.entry(settings, "Server name", "hostname", width=18)
    d.entry(settings, "Join password", "password", secret=True, width=18)
    password_actions = tk.Frame(settings, bg=PANEL)
    password_actions.pack(fill="x", pady=(7, 9))
    d.show_button = d.button(password_actions, "Show", d.toggle_password, compact=True)
    d.show_button.pack(side="left")
    d.button(password_actions, "Copy password", lambda: d.copy(d.fields["password"].get(), "Password"),
             compact=True).pack(side="left", padx=(8, 0))

    d.entry(settings, "Player limit", "max_players", width=6)
    ports = tk.Frame(settings, bg=PANEL)
    ports.pack(fill="x", pady=(8, 0))
    ports.columnconfigure((0, 1), weight=1, uniform="server_ports")
    game_port = tk.Frame(ports, bg=PANEL)
    game_port.grid(row=0, column=0, sticky="ew", padx=(0, 6))
    query_port = tk.Frame(ports, bg=PANEL)
    query_port.grid(row=0, column=1, sticky="ew", padx=(6, 0))
    d.entry(game_port, "Game port", "game_port", width=6)
    d.entry(query_port, "Query port", "query_port", width=6)

    d.save_button = d.button(settings, "Save settings", d.save_settings, primary=True)
    d.save_button.pack(anchor="w", pady=(17, 5))
    d.label(settings, "Stop the server before changing its settings.", size=9, color=MUTED,
            wraplength=300, justify="left").pack(fill="x", pady=(3, 0))

    install_card, install = _panel_content(grid, d)
    install_card.grid(row=0, column=1, sticky="new")
    d.label(install, "DayZ Server files", size=15, bold=True).pack(anchor="w")
    d.label(install, "Detected installation", size=9, color=MUTED).pack(anchor="w", pady=(13, 5))
    d.installation_text = d.label(
        install, "Looking for Steam installation…", color=ACCENT, size=10, bg=INSET, padx=11, pady=10,
        wraplength=230, justify="left",
    )
    d.installation_text.pack(fill="x", pady=(0, 12))
    d.browse_button = d.button(install, "Browse for server", d.browse_installation, compact=True)
    d.browse_button.pack(anchor="w", pady=(0, 7))
    d.install_button = d.button(install, "Install via Steam", d.open_steam, compact=True)
    d.install_button.pack(anchor="w")


def _display_size(byte_count: int) -> str:
    value = float(byte_count)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024


class BackupsPage(tk.Frame):
    """Recent verified-save archives for the selected world."""

    def __init__(self, parent, d) -> None:
        super().__init__(parent, bg=BG)
        self.d = d
        self.records = {}
        self._archive_snapshot = None
        self._busy = False
        d.page_heading(self, "Backups", "Review the active world’s saved archives and create a verified snapshot.")

        self.world = d.label(self, "Active world", size=10, color=ACCENT, bg=BG)
        self.world.pack(anchor="w", pady=(0, 8))
        metrics = tk.Frame(self, bg=BG)
        metrics.pack(fill="x", pady=(0, 12))
        metrics.columnconfigure((0, 1, 2), weight=1, uniform="backup_metrics")
        self.metrics = []
        for column, name in enumerate(("Schedule", "Last completed", "Next scheduled")):
            card, content = _panel_content(metrics, d, padding=14)
            card.grid(row=0, column=column, sticky="nsew", padx=(0 if not column else 8, 0))
            d.label(content, name, size=9, color=MUTED, bold=True).pack(anchor="w")
            value = d.label(content, "—", size=12, bold=True, wraplength=150, justify="left")
            value.pack(fill="x", pady=(5, 0))
            self.metrics.append(value)

        history, content = _panel_content(self, d, padding=16)
        history.pack(fill="both", expand=True)
        toolbar = tk.Frame(content, bg=PANEL)
        toolbar.pack(fill="x", pady=(0, 12))
        d.label(toolbar, "Recent archives", size=14, bold=True).pack(side="left")
        d.backup_now_button = d.button(toolbar, "Back up now", d.backup_now, primary=True, compact=True)
        d.backup_now_button.pack(side="right")
        d.button(toolbar, "Open folder", d.open_backups, compact=True).pack(side="right", padx=(0, 8))

        footer = tk.Frame(content, bg=PANEL)
        footer.pack(side="bottom", fill="x", pady=(12, 0))
        self.verify = d.button(footer, "Verify selected", self._verify_selected, compact=True)
        self.verify.pack(side="right")
        self.restore = d.button(footer, "Restore selected", self._restore_selected, compact=True)
        self.restore.pack(side="right", padx=(0, 8))
        hint = d.label(footer, "An online server must be empty for a manual backup.", size=9, color=MUTED,
                       wraplength=330, justify="left")
        hint.pack(side="left", fill="x", expand=True)
        hint.bind("<Configure>", lambda event: hint.configure(wraplength=max(100, event.width - 4)))

        table = tk.Frame(content, bg=INSET, highlightthickness=1, highlightbackground=EDGE)
        table.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(table, columns=("date", "size", "archive"), show="headings",
                                 selectmode="browse", style="Azimuth.Treeview", height=5)
        for key, title, width, stretch in (
            ("date", "Created", 155, False),
            ("size", "Size", 85, False),
            ("archive", "Archive", 180, True),
        ):
            self.tree.heading(key, text=title, anchor="w")
            self.tree.column(key, width=width, minwidth=width, stretch=stretch, anchor="w")
        bar = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview,
                            style="Azimuth.Vertical.TScrollbar")
        self.tree.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.empty = d.label(table, "No archives for this world yet.", bg=INSET, color=MUTED,
                             padx=12, pady=10)
        self.empty.place(relx=.5, rely=.52, anchor="center")
        self.tree.bind("<<TreeviewSelect>>", lambda _event: self._refresh_verify())

        self._refresh_verify()

    def render_status(self, backup_data, records) -> None:
        world_id = self.d.server.active_world_id
        world_name = self.d.world_names.get(world_id, world_id)
        self.world.configure(text=world_name)
        schedule = backup_data.get("state")
        if not schedule or schedule == "Backups every 6 hours":
            schedule = "Every 6 hours"
        values = (
            schedule,
            backup_data.get("last") or "—",
            backup_data.get("next") or "—",
        )
        for label, value in zip(self.metrics, values):
            if label.cget("text") != value:
                label.configure(text=value)
        records = tuple(records or ())
        snapshot = world_id, records
        if snapshot == self._archive_snapshot:
            return
        self._archive_snapshot = snapshot
        selected = self.tree.selection()
        children = self.tree.get_children()
        if children:
            self.tree.delete(*children)
        self.records = {str(record.path): record for record in records}
        for key, record in self.records.items():
            self.tree.insert("", "end", iid=key, values=(
                record.created_at.strftime("%b %d, %Y · %H:%M"),
                _display_size(record.size_bytes),
                record.path.name,
            ))
        if selected and selected[0] in self.records:
            self.tree.selection_set(selected[0])
        if records:
            self.empty.place_forget()
        else:
            self.empty.place(relx=.5, rely=.52, anchor="center")
        self._refresh_verify()

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._refresh_verify()

    def _refresh_verify(self) -> None:
        self.verify.configure(state="normal" if self.tree.selection() and not self._busy else "disabled")
        self.restore.configure(state="normal" if self.tree.selection() and not self._busy
                               and self.d.can_restore_backup() else "disabled")

    def _verify_selected(self) -> None:
        selected = self.tree.selection()
        if selected and selected[0] in self.records:
            self.d.verify_selected_backup(self.records[selected[0]].path)

    def _restore_selected(self) -> None:
        selected = self.tree.selection()
        if selected and selected[0] in self.records and not self._busy and self.d.can_restore_backup():
            self.d.restore_selected_backup(self.records[selected[0]].path)


def build_backups(parent, d) -> None:
    d.backup_page = BackupsPage(parent, d)
    d.backup_page.pack(fill="both", expand=True)


def build_activity(parent, d) -> None:
    d.page_heading(parent, "Activity", "Read recent Azimuth events and DayZ server logs as they arrive.")
    card, content = _panel_content(parent, d, padding=16)
    card.pack(fill="both", expand=True)
    toolbar = tk.Frame(content, bg=PANEL)
    toolbar.pack(fill="x", pady=(0, 12))
    d.log_filter = tk.StringVar(d.window, "All")
    for label in ("All", "Azimuth", "Server"):
        tab = tk.Radiobutton(toolbar, text=label, variable=d.log_filter, value=label, indicatoron=False,
                             bg=INSET, fg=MUTED, activebackground=BUTTON, activeforeground=TEXT,
                             selectcolor=BUTTON, highlightcolor=ACCENT, highlightbackground=EDGE,
                             highlightthickness=1, borderwidth=0, padx=11, pady=6,
                             font=(d.ui_font, 9, "bold"), relief="flat", offrelief="flat",
                             overrelief="flat", cursor="hand2")
        tab.pack(side="left", padx=(0, 5))
    d.button(toolbar, "Open logs folder", d.open_logs, compact=True).pack(side="right")
    d.button(toolbar, "Copy log", lambda: d.copy(d.log_view.get("1.0", "end-1c"), "Activity log"),
             compact=True).pack(side="right", padx=(0, 8))

    log_area = tk.Frame(content, bg=INSET, highlightthickness=1, highlightbackground=EDGE)
    log_area.pack(fill="both", expand=True)
    d.log_view = tk.Text(log_area, bg=INSET, fg=MUTED, font=(d.mono_font, 9), height=9, width=30,
                         relief="flat", padx=12, pady=12, wrap="word", state="disabled",
                         selectbackground=BUTTON, selectforeground=TEXT, highlightthickness=0)
    scrollbar = ttk.Scrollbar(log_area, orient="vertical", command=d.log_view.yview,
                              style="Azimuth.Vertical.TScrollbar")
    d.log_view.configure(yscrollcommand=scrollbar.set)
    scrollbar.pack(side="right", fill="y")
    d.log_view.pack(side="left", fill="both", expand=True)
    d.log_view.tag_configure("azimuth", foreground=ACCENT)
    d.log_view.tag_configure("error", foreground=DANGER)
    d.log_view.tag_configure("muted", foreground=DIM)
    d.log_filter.trace_add("write", lambda *_: d.render_logs())
