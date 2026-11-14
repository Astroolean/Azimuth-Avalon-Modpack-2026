"""Visual finish selection and temporary showcase controls for Avalon."""
from __future__ import annotations

import math
import os
from pathlib import Path
import time
import tkinter as tk
from tkinter import ttk

from azimuth_app.ui.dashboard_theme import ACCENT, BG, BUTTON, DANGER, DIM, EDGE, INSET, MUTED, PANEL, TEXT, WARNING


class SkinsPage(tk.Frame):
    """Keep editable selections separate from saved settings and background status."""

    def __init__(self, parent: tk.Misc, dashboard) -> None:
        super().__init__(parent, bg=BG)
        self.dashboard = dashboard
        self.loaded = False
        self.locked = False
        self.running = False
        self.skins: list[dict] = []
        self.skin_vars: dict[str, tk.BooleanVar] = {}
        self.cards: dict[str, tk.Frame] = {}
        self.checks: list[tk.Checkbutton] = []
        self.photos: dict[tuple[str, int], tk.PhotoImage] = {}
        self.saved_ids: tuple[str, ...] = ()
        self.saved_showcase = False
        self.saved_random_enabled = True
        self.saved_random_chance = 20
        self.saved_vehicle_enabled = True
        self.saved_vehicle_chance = 100
        self.saved_vehicle_test_enabled = True
        self.vehicle_test_status = "Queued once. Stand still outdoors; placement keeps retrying until safe ground is available."
        self.kit_status = "The showcase kit will be prepared when you start Avalon."
        self.cleanup_pending = False
        self.cleanup_deferred = False
        self.kit_var = tk.BooleanVar(self, False)
        self.random_enabled_var = tk.BooleanVar(self, True)
        self.random_chance_var = tk.IntVar(self, 20)
        self.vehicle_enabled_var = tk.BooleanVar(self, True)
        self.vehicle_chance_var = tk.IntVar(self, 100)
        self.vehicle_test_var = tk.BooleanVar(self, True)
        self.kit_expanded = False
        self.last_observed = 0.0
        self.columns = 0
        self._layout_job = None
        self._build()
        self._render_controls()

    def _build(self) -> None:
        ui = self.dashboard
        heading = tk.Frame(self, bg=BG)
        heading.pack(fill="x", pady=(0, 9))
        ui.label(heading, "Skins / Camos", size=23, bold=True, bg=BG).pack(anchor="w")
        ui.label(heading, "Choose the finishes you want in Avalon.", color=MUTED, bg=BG).pack(
            anchor="w", pady=(2, 0))
        inspect_help = ui.label(
            heading,
            "I: inspect in first person. P: screenshot freecam. "
            "Rebind in Controls under Azimuth / Azimuth photo mode.",
            size=9, color=MUTED, bg=BG, wraplength=780, justify="left")
        inspect_help.pack(fill="x", pady=(4, 0))
        inspect_help.bind("<Configure>", lambda event: inspect_help.configure(
            wraplength=max(200, event.width - 4)))

        toolbar = tk.Frame(self, bg=BG)
        toolbar.pack(fill="x", pady=(0, 7))
        self.selection_label = ui.label(toolbar, "Loading finishes…", bold=True, bg=BG)
        self.selection_label.pack(side="left", fill="x", expand=True)
        self.select_all_button = ui.button(toolbar, "Select all", lambda: self._choose_all(True), compact=True)
        self.select_all_button.pack(side="left", padx=(5, 0))
        self.clear_button = ui.button(toolbar, "Clear", lambda: self._choose_all(False), compact=True)
        self.clear_button.pack(side="left", padx=5)
        self.save_button = ui.button(toolbar, "Save changes", self._save, primary=True, compact=True)
        self.save_button.pack(side="left")

        random_panel = ui.panel(self)
        random_panel.pack(fill="x", pady=(0, 7))
        random_line = tk.Frame(random_panel, bg=PANEL)
        random_line.pack(fill="x", padx=10, pady=(6, 3))
        self.random_check = tk.Checkbutton(
            random_line, text="Random skins on weapons and gear", variable=self.random_enabled_var,
            command=self._changed, bg=PANEL, fg=TEXT, activebackground=PANEL,
            activeforeground=ACCENT, selectcolor=BUTTON, disabledforeground=DIM,
            relief="flat", borderwidth=0, highlightthickness=0, cursor="hand2",
            font=(ui.ui_font, 9, "bold"), anchor="w")
        self.random_check.pack(side="left", fill="x", expand=True)
        ui.label(random_line, "Chance", size=9, color=MUTED).pack(side="left", padx=(6, 5))
        self.random_chance = ttk.Combobox(
            random_line, textvariable=self.random_chance_var, state="readonly", width=4,
            values=(0, 5, 10, 20, 35, 50, 75, 100), font=(ui.ui_font, 9),
            style="Azimuth.TCombobox")
        self.random_chance.pack(side="left")
        self.random_chance.bind("<<ComboboxSelected>>", lambda _event: self._changed())
        ui.label(random_line, "%", size=9, color=MUTED).pack(side="left", padx=(4, 0))
        self.random_help = ui.label(
            random_panel, "Random skin chance applies to each compatible weapon, part or clothing item.",
            size=8, color=MUTED, wraplength=780, justify="left")
        self.random_help.pack(fill="x", padx=13, pady=(0, 7))
        self.random_help.bind("<Configure>", lambda event: self.random_help.configure(
            wraplength=max(200, event.width - 4)))

        vehicle_panel = ui.panel(self)
        vehicle_panel.pack(fill="x", pady=(0, 7))
        vehicle_line = tk.Frame(vehicle_panel, bg=PANEL)
        vehicle_line.pack(fill="x", padx=10, pady=(6, 3))
        self.vehicle_check = tk.Checkbutton(
            vehicle_line, text="Random wraps on vehicles", variable=self.vehicle_enabled_var,
            command=self._changed, bg=PANEL, fg=TEXT, activebackground=PANEL,
            activeforeground=ACCENT, selectcolor=BUTTON, disabledforeground=DIM,
            relief="flat", borderwidth=0, highlightthickness=0, cursor="hand2",
            font=(ui.ui_font, 9, "bold"), anchor="w")
        self.vehicle_check.pack(side="left", fill="x", expand=True)
        ui.label(vehicle_line, "Chance", size=9, color=MUTED).pack(side="left", padx=(6, 5))
        self.vehicle_chance = ttk.Combobox(
            vehicle_line, textvariable=self.vehicle_chance_var, state="readonly", width=4,
            values=(0, 5, 10, 20, 35, 50, 75, 100), font=(ui.ui_font, 9),
            style="Azimuth.TCombobox")
        self.vehicle_chance.pack(side="left")
        self.vehicle_chance.bind("<<ComboboxSelected>>", lambda _event: self._changed())
        ui.label(vehicle_line, "%", size=9, color=MUTED).pack(side="left", padx=(4, 0))
        self.vehicle_help = ui.label(
            vehicle_panel, "Random wraps use the same selected finishes below.",
            size=8, color=MUTED, wraplength=780, justify="left")
        self.vehicle_help.pack(fill="x", padx=13, pady=(0, 7))
        self.vehicle_help.bind("<Configure>", lambda event: self.vehicle_help.configure(
            wraplength=max(200, event.width - 4)))
        vehicle_tools = tk.Frame(vehicle_panel, bg=PANEL)
        vehicle_tools.pack(fill="x", padx=13, pady=(0, 7))
        self.vehicle_coverage_button = ui.button(
            vehicle_tools, "Vehicle coverage", self._open_vehicle_coverage, compact=True)
        self.vehicle_coverage_button.pack(side="left")
        self.vehicle_export_button = ui.button(
            vehicle_tools, "Export vehicle data", self._export_vehicle_data, compact=True)
        self.vehicle_export_button.pack(side="left", padx=(7, 0))
        vehicle_test_line = tk.Frame(vehicle_panel, bg=PANEL)
        vehicle_test_line.pack(fill="x", padx=10, pady=(0, 6))
        self.vehicle_test_check = tk.Checkbutton(
            vehicle_test_line, text="Spawn one test go-kart beside me", variable=self.vehicle_test_var,
            command=self._changed, bg=PANEL, fg=TEXT, activebackground=PANEL,
            activeforeground=ACCENT, selectcolor=BUTTON, disabledforeground=DIM,
            relief="flat", borderwidth=0, highlightthickness=0, cursor="hand2",
            font=(ui.ui_font, 9, "bold"), anchor="w")
        self.vehicle_test_check.pack(side="left", anchor="n")
        self.vehicle_test_label = ui.label(vehicle_test_line, "Reading go-kart status…", size=8,
                                          color=MUTED, wraplength=400, justify="left")
        self.vehicle_test_label.pack(side="left", fill="x", expand=True, padx=(10, 0))
        self.vehicle_test_label.bind("<Configure>", lambda event: self.vehicle_test_label.configure(
            wraplength=max(150, event.width - 4)))

        self.status_label = ui.label(self, "Reading the saved skin selection…", color=MUTED, bg=BG,
                                     size=9, wraplength=780, justify="left")
        self.status_label.pack(fill="x", pady=(0, 7))
        self.status_label.bind("<Configure>", lambda event: self.status_label.configure(
            wraplength=max(200, event.width - 4)))

        # Keep kit controls visible while the collection can grow and scroll.
        showcase = ui.panel(self)
        showcase.pack(side="bottom", fill="x", pady=(10, 0))
        showcase_body = tk.Frame(showcase, bg=PANEL)
        showcase_body.pack(fill="x", padx=13, pady=10)
        line = tk.Frame(showcase_body, bg=PANEL)
        line.pack(fill="x")
        ui.label(line, "Temporary showcase kit", size=12, bold=True).pack(side="left")
        self.kit_details_button = ui.button(line, "Show controls", self._toggle_kit_details, compact=True)
        self.kit_details_button.pack(side="right", padx=(8, 0))
        self.kit_check = tk.Checkbutton(
            line, text="Give on next join", variable=self.kit_var, command=self._changed,
            bg=PANEL, fg=ACCENT, activebackground=PANEL, activeforeground=ACCENT,
            selectcolor=BUTTON, disabledforeground=DIM, relief="flat", borderwidth=0,
            highlightthickness=0, cursor="hand2", font=(ui.ui_font, 9, "bold"))
        self.kit_check.pack(side="right")
        self.kit_label = ui.label(showcase_body, "Loading kit status…", size=9, color=MUTED,
                                  wraplength=780, justify="left")
        self.kit_label.pack(fill="x", pady=(4, 0))
        self.kit_label.bind("<Configure>", lambda event: self.kit_label.configure(
            wraplength=max(200, event.width - 4)))
        self.kit_details = tk.Frame(showcase_body, bg=PANEL)
        kit_description = ui.label(
            self.kit_details,
            "Up to 3 selected M4A1 finishes per kit, delivered in a test chest beside you. "
            "Open inventory and look in Vicinity. Your existing gear stays.",
            size=9, color=MUTED, wraplength=780, justify="left")
        kit_description.pack(fill="x", pady=(4, 0))
        kit_description.bind("<Configure>", lambda event: kit_description.configure(
            wraplength=max(200, event.width - 4)))
        kit_actions = tk.Frame(self.kit_details, bg=PANEL)
        kit_actions.pack(fill="x", pady=(6, 0))
        self.reissue_button = ui.button(kit_actions, "Queue another kit", self._reissue, compact=True)
        self.reissue_button.pack(side="left")
        self.remove_button = ui.button(kit_actions, "Remove test kit", self._remove, compact=True)
        self.remove_button.pack(side="left", padx=(7, 0))
        ui.label(kit_actions, "Save, then start Avalon and join.", size=9, color=MUTED).pack(
            side="right", padx=(8, 0))
        cleanup_help = ui.label(
            self.kit_details,
            "Before removal: unload test rifles, remove added gear, and leave them in the chest or on the ground.",
            size=8, color=MUTED, wraplength=780, justify="left")
        cleanup_help.pack(fill="x", pady=(6, 0))
        cleanup_help.bind("<Configure>", lambda event: cleanup_help.configure(
            wraplength=max(200, event.width - 4)))

        gallery = tk.Frame(self, bg=BG)
        gallery.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(gallery, bg=BG, borderwidth=0, highlightthickness=0,
                                yscrollincrement=30)
        scrollbar = ttk.Scrollbar(gallery, orient="vertical", style="Azimuth.Vertical.TScrollbar",
                                  command=self.canvas.yview)
        scrollbar.pack(side="right", fill="y", padx=(6, 0))
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.gallery = tk.Frame(self.canvas, bg=BG)
        self.gallery_window = self.canvas.create_window(0, 0, window=self.gallery, anchor="nw")
        self.gallery.bind("<Configure>", self._gallery_resized)
        self.canvas.bind("<Configure>", self._canvas_resized)
        self._bind_scroll(self.canvas)

    def _toggle_kit_details(self) -> None:
        self.kit_expanded = not self.kit_expanded
        if self.kit_expanded:
            self.kit_details.pack(fill="x", pady=(4, 0))
        else:
            self.kit_details.pack_forget()
        self.kit_details_button.configure(text="Hide controls" if self.kit_expanded else "Show controls")

    def _bind_scroll(self, widget: tk.Misc) -> None:
        widget.bind("<MouseWheel>", self._wheel)
        widget.bind("<Button-4>", self._wheel)
        widget.bind("<Button-5>", self._wheel)
        for child in widget.winfo_children():
            self._bind_scroll(child)

    def _wheel(self, event: tk.Event) -> str:
        if self.canvas.bbox("all") and self.canvas.bbox("all")[3] > self.canvas.winfo_height():
            if event.num in (4, 5):
                units = -1 if event.num == 4 else 1
            else:
                units = -(event.delta // 120) if abs(event.delta) >= 120 else (-1 if event.delta > 0 else 1)
            self.canvas.yview_scroll(units, "units")
        return "break"

    def _gallery_resized(self, _event=None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _canvas_resized(self, event) -> None:
        self.canvas.itemconfigure(self.gallery_window, width=event.width)
        columns = 3 if event.width >= 760 else 2 if event.width >= 430 else 1
        if columns != self.columns:
            self.columns = columns
            self._layout_cards()

    def _layout_cards(self) -> None:
        columns = max(1, self.columns)
        for column in range(3):
            self.gallery.columnconfigure(column, weight=1 if column < columns else 0,
                                         uniform="skins" if column < columns else "", minsize=0)
        for index, skin in enumerate(self.skins):
            card = self.cards.get(skin["id"])
            if card is not None:
                card.grid(row=index // columns, column=index % columns, sticky="nsew",
                          padx=(0 if index % columns == 0 else 8, 0), pady=(0, 9))

    def _photo(self, skin: dict, size: int) -> tk.PhotoImage | None:
        preview = skin.get("preview", "")
        if not isinstance(preview, str) or not preview:
            return None
        root = Path(self.dashboard.server.root).resolve()
        path = (root / preview).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            return None
        key = (str(path), size)
        if key not in self.photos:
            try:
                original = tk.PhotoImage(master=self, file=str(path))
                factor = max(1, math.ceil(max(original.width(), original.height()) / size))
                self.photos[key] = original.subsample(factor, factor)
            except (OSError, tk.TclError):
                return None
        return self.photos[key]

    def _build_cards(self, skins: list[dict]) -> None:
        for child in self.gallery.winfo_children():
            child.destroy()
        self.cards.clear()
        self.checks.clear()
        self.skin_vars.clear()
        self.skins = skins
        ui = self.dashboard
        for skin in skins:
            identity = skin["id"]
            variable = tk.BooleanVar(self, identity in self.saved_ids)
            self.skin_vars[identity] = variable
            card = tk.Frame(self.gallery, bg=PANEL, highlightthickness=1, highlightbackground=EDGE)
            self.cards[identity] = card
            preview = tk.Frame(card, bg=INSET, height=112)
            preview.pack(fill="x", padx=8, pady=(8, 0))
            preview.pack_propagate(False)
            photo = self._photo(skin, 114)
            picture = tk.Label(preview, image=photo, text="Preview unavailable" if photo is None else "",
                               bg=INSET, fg=MUTED, cursor="hand2", font=(ui.ui_font, 9))
            picture.pack(fill="both", expand=True)
            picture.bind("<Button-1>", lambda _event, item=skin: self._preview(item))
            details = tk.Frame(card, bg=PANEL)
            details.pack(fill="x", padx=10, pady=(7, 8))
            title = tk.Frame(details, bg=PANEL)
            title.pack(fill="x")
            ui.label(title, skin["label"], size=11, bold=True).pack(side="left", fill="x", expand=True)
            preview_button = tk.Button(title, text="View", command=lambda item=skin: self._preview(item),
                                       bg=PANEL, fg=MUTED, activebackground=BUTTON, activeforeground=TEXT,
                                       relief="flat", borderwidth=0, cursor="hand2", padx=3,
                                       font=(ui.ui_font, 8))
            preview_button.pack(side="right")
            description = ui.label(details, skin.get("description", "Custom weapon finish"), size=8,
                                   color=MUTED, wraplength=225, justify="left", height=2)
            description.pack(fill="x", pady=(2, 5))
            description.bind("<Configure>", lambda event, label=description: label.configure(
                wraplength=max(140, event.width - 4)))
            check = tk.Checkbutton(
                details, text="Include finish", variable=variable, command=self._changed,
                bg=PANEL, fg=TEXT, activebackground=PANEL, activeforeground=ACCENT,
                selectcolor=BUTTON, disabledforeground=DIM, relief="flat", borderwidth=0,
                highlightthickness=0, cursor="hand2", anchor="w", font=(ui.ui_font, 9, "bold"))
            check.pack(fill="x")
            self.checks.append(check)
        self._layout_cards()
        self._bind_scroll(self.gallery)

    def _preview(self, skin: dict) -> None:
        ui = self.dashboard
        dialog = tk.Toplevel(self)
        dialog.title(skin["label"] + " · Azimuth")
        dialog.configure(bg=BG)
        dialog.transient(ui.window)
        size = max(220, min(550, dialog.winfo_screenwidth() - 100, dialog.winfo_screenheight() - 220))
        photo = self._photo(skin, size)
        ui.label(dialog, skin["label"], size=20, bold=True, bg=BG).pack(anchor="w", padx=20, pady=(17, 10))
        tk.Label(dialog, image=photo, text="Preview unavailable" if photo is None else "",
                 bg=INSET, fg=MUTED).pack(padx=20)
        ui.label(dialog, skin.get("description", ""), color=MUTED, bg=BG, wraplength=size,
                 justify="left").pack(anchor="w", padx=20, pady=(10, 3))
        ui.label(dialog, "Texture preview · placement follows each weapon, part or compatible vehicle model.", size=9,
                 color=MUTED, bg=BG, wraplength=size, justify="left").pack(anchor="w", padx=20)
        ui.button(dialog, "Close preview", dialog.destroy, compact=True).pack(anchor="e", padx=20, pady=15)
        dialog.bind("<Escape>", lambda _event: dialog.destroy())

    def selected_ids(self) -> tuple[str, ...]:
        return tuple(skin["id"] for skin in self.skins if self.skin_vars[skin["id"]].get())

    def dirty(self) -> bool:
        return self.loaded and (set(self.selected_ids()) != set(self.saved_ids)
                                or bool(self.kit_var.get()) != self.saved_showcase
                                or bool(self.random_enabled_var.get()) != self.saved_random_enabled
                                or self.random_chance_var.get() != self.saved_random_chance
                                or bool(self.vehicle_enabled_var.get()) != self.saved_vehicle_enabled
                                or self.vehicle_chance_var.get() != self.saved_vehicle_chance
                                or bool(self.vehicle_test_var.get()) != self.saved_vehicle_test_enabled)

    def render_status(self, status: dict, *, observed_at: float | None = None, force: bool = False) -> None:
        if not isinstance(status, dict):
            self.show_error("Could not read skin settings.")
            return
        observed_at = time.monotonic() if observed_at is None else observed_at
        if observed_at < self.last_observed:
            return
        self.last_observed = observed_at
        skins = status.get("skins")
        if not isinstance(skins, list) or not skins or any(
            not isinstance(skin, dict) or not isinstance(skin.get("id"), str)
            or not isinstance(skin.get("label"), str) for skin in skins
        ):
            self.show_error("The skin collection is unavailable. Restore Custom/GunSkins and try again.")
            return
        pending = self.dirty()
        draft_ids, draft_kit = self.selected_ids(), bool(self.kit_var.get())
        draft_random = bool(self.random_enabled_var.get()), self.random_chance_var.get()
        draft_vehicle = bool(self.vehicle_enabled_var.get()), self.vehicle_chance_var.get()
        draft_vehicle_test = bool(self.vehicle_test_var.get())
        saved = status.get("selected_ids", status.get("selected_skin_ids", []))
        if not isinstance(saved, (list, tuple)):
            self.show_error("The saved skin selection is invalid.")
            return
        known = {skin["id"] for skin in skins}
        if any(identity not in known for identity in saved):
            self.show_error("The saved selection refers to a missing finish.")
            return
        self.saved_ids = tuple(saved)
        self.saved_showcase = bool(status.get("showcase_enabled", status.get("test_kit_enabled", False)))
        self.saved_random_enabled = bool(status.get("random_skins_enabled", True))
        self.saved_random_chance = status.get("random_skin_chance_percent", 20)
        if type(self.saved_random_chance) is not int or not 0 <= self.saved_random_chance <= 100:
            self.show_error("The random skin chance must be from 0 to 100 percent.")
            return
        self.saved_vehicle_enabled = status.get("vehicle_wraps_enabled", True)
        self.saved_vehicle_chance = status.get("vehicle_wrap_chance_percent", 100)
        if (type(self.saved_vehicle_enabled) is not bool
                or type(self.saved_vehicle_chance) is not int or not 0 <= self.saved_vehicle_chance <= 100):
            self.show_error("Vehicle wraps must be on or off with a whole chance from 0 to 100 percent.")
            return
        self.saved_vehicle_test_enabled = status.get("vehicle_test_enabled", True)
        if type(self.saved_vehicle_test_enabled) is not bool:
            self.show_error("The one-time go-kart choice must be on or off.")
            return
        self.vehicle_test_status = str(status.get("vehicle_test_status") or
                                       "Queued once for your next join, when safe ground is available.")
        self.kit_status = str(status.get("kit_status") or "The kit is delivered once; reconnecting does not create extra copies.")
        self.cleanup_pending = bool(status.get("cleanup_pending", False))
        self.cleanup_deferred = bool(status.get("cleanup_deferred", False))
        if skins != self.skins:
            self._build_cards(skins)
        keep_draft = pending and not force
        selected = set(draft_ids if keep_draft else self.saved_ids)
        for identity, variable in self.skin_vars.items():
            variable.set(identity in selected)
        self.kit_var.set(draft_kit if keep_draft else self.saved_showcase)
        self.random_enabled_var.set(draft_random[0] if keep_draft else self.saved_random_enabled)
        self.random_chance_var.set(draft_random[1] if keep_draft else self.saved_random_chance)
        self.vehicle_enabled_var.set(draft_vehicle[0] if keep_draft else self.saved_vehicle_enabled)
        self.vehicle_chance_var.set(draft_vehicle[1] if keep_draft else self.saved_vehicle_chance)
        self.vehicle_test_var.set(draft_vehicle_test if keep_draft else self.saved_vehicle_test_enabled)
        self.loaded = True
        self._render_controls()

    def show_error(self, message: str) -> None:
        self.status_label.configure(text=message, fg=DANGER)
        if not self.loaded:
            self.selection_label.configure(text="Finishes unavailable")
        self.dashboard.show_notice(message, error=True)

    def reload_status(self) -> None:
        """Discard drafts after an explicit restore and reject older scan results."""
        self.loaded = False
        self.last_observed = time.monotonic()
        self._render_controls()
        self.selection_label.configure(text="Loading finishes…")
        self.status_label.configure(text="Reading restored skin settings…", fg=MUTED)

    def set_busy(self, locked: bool, *, running: bool = False) -> None:
        if self.locked == bool(locked) and self.running == bool(running):
            return
        self.locked, self.running = bool(locked), bool(running)
        self._render_controls()

    def _changed(self) -> None:
        if not self.selected_ids():
            self.kit_var.set(False)
        self._render_controls()

    def _choose_all(self, selected: bool) -> None:
        if self.locked or self.running or not self.loaded:
            return
        for variable in self.skin_vars.values():
            variable.set(selected)
        self._changed()

    def _render_controls(self) -> None:
        selected = self.selected_ids()
        count = len(selected)
        disabled = self.locked or self.running or not self.loaded
        state = "disabled" if disabled else "normal"
        for control in [self.select_all_button, self.clear_button, self.random_check, self.vehicle_check,
                        self.vehicle_test_check, *self.checks]:
            control.configure(state=state)
        self.random_chance.configure(
            state="disabled" if disabled or not count or not self.random_enabled_var.get() else "readonly")
        self.vehicle_chance.configure(
            state="disabled" if disabled or not count or not self.vehicle_enabled_var.get() else "readonly")
        self.vehicle_export_button.configure(state="disabled" if self.locked else "normal")
        self.kit_check.configure(state="disabled" if disabled or not count or self.cleanup_pending else "normal")
        self.save_button.configure(state="disabled" if disabled or not self.dirty() else "normal")
        self.reissue_button.configure(state="disabled" if disabled or self.dirty() or not count else "normal")
        self.remove_button.configure(state="disabled" if disabled or self.dirty() or self.cleanup_pending else "normal")
        for identity, card in self.cards.items():
            card.configure(highlightbackground=ACCENT if identity in selected else EDGE)
        if not self.loaded:
            return
        self.selection_label.configure(text=f"{count} of {len(self.skins)} finishes selected")
        if self.running:
            text, color = "Stop the server to change finishes, random skins, vehicle wraps or the test kit. Previews remain available.", WARNING
        elif self.locked:
            text, color = "Please wait for the current operation to finish.", MUTED
        elif self.dirty():
            text, color = "Unsaved changes. Save changes, then start Avalon.", WARNING
        else:
            text, color = ("Saved. Checked finishes are used for new random choices. Existing fixed-skin guns and test chests stay supported.", MUTED)
        self.status_label.configure(text=text, fg=color)
        if not count:
            random_text = "Select at least one finish to enable random skins."
        elif not self.random_enabled_var.get() or self.random_chance_var.get() == 0:
            random_text = "Random skins are off (0% active chance). Your finish selection stays saved."
        else:
            random_text = (f"{self.random_chance_var.get()}% chance per item. Each compatible weapon, part or clothing item is chosen separately "
                           "using only your selected finishes.")
        random_text += ("\nApplies to compatible firearms, melee weapons/tools, magazines, stocks, "
                        "handguards, suppressors, bayonets, helmets, clothing and backpacks. Only compatible paint surfaces are used.")
        self.random_help.configure(text=random_text)
        if not count:
            vehicle_text = "Select at least one finish to enable vehicle wraps."
        elif not self.vehicle_enabled_var.get() or self.vehicle_chance_var.get() == 0:
            vehicle_text = "Vehicle wraps are off (0% active chance). Your finish selection stays saved."
        else:
            vehicle_text = (f"{self.vehicle_chance_var.get()}% chance per eligible vehicle. "
                            "Uses only your selected finishes below, independently of weapon chance.")
        vehicle_text += ("\nIncludes supported cars, trucks, karts, golf carts, motorcycles, bicycles, boats and aircraft. "
                         "Matching doors and body panels follow their vehicle. "
                         "Open Vehicle coverage for exact models and exceptions. Glass, lights and tyres stay original.")
        self.vehicle_help.configure(text=vehicle_text)
        if not self.vehicle_test_var.get():
            vehicle_test_text = ("Save to cancel the pending go-kart; existing vehicles stay."
                                 if self.saved_vehicle_test_enabled else
                                 "Off. The pending go-kart is cancelled; existing vehicles stay.")
        elif bool(self.vehicle_test_var.get()) != self.saved_vehicle_test_enabled:
            vehicle_test_text = "Save to allow the one-time go-kart request. Delivered vehicles are not repeated."
        else:
            vehicle_test_text = self.vehicle_test_status
        self.vehicle_test_label.configure(text=vehicle_test_text)
        if self.cleanup_pending:
            kit_text = self.kit_status + " Queue another kit cancels this cleanup request."
        elif self.cleanup_deferred and bool(self.kit_var.get()) == self.saved_showcase:
            kit_text = self.kit_status
        elif not count:
            kit_text = "Choose at least one finish to enable the showcase kit."
        elif not self.kit_var.get():
            kit_text = "Automatic test kit is off. Existing gear is kept."
        elif set(selected) != set(self.saved_ids) or bool(self.kit_var.get()) != self.saved_showcase:
            kit_count = min(3, count)
            kit_text = f"Save to queue {kit_count} M4A1{'s' if kit_count != 1 else ''} using the first selected finishes."
        else:
            kit_text = self.kit_status
        self.kit_label.configure(text=kit_text)

    def _open_vehicle_coverage(self) -> None:
        guide = self.dashboard.server.root / "Docs/Guides/VEHICLE_WRAP_COVERAGE.txt"
        try:
            os.startfile(str(guide))
        except (OSError, AttributeError) as error:
            self.dashboard.show_notice(f"Could not open the vehicle coverage guide: {error}", error=True)

    def _export_vehicle_data(self) -> None:
        if self.locked:
            return
        server = self.dashboard.server

        def export():
            from azimuth_app.checks.vehicle_wrap_evidence import export_vehicle_wrap_evidence
            libraries = server.steam_libraries(server.installation, refresh=True)
            return export_vehicle_wrap_evidence(server.root, libraries, server.installation)

        def finished(path: Path) -> None:
            self.dashboard.show_notice(f"Vehicle data saved: {path.name}. Use this ZIP when requesting compatibility review.")
            try:
                os.startfile(str(path.parent))
            except (OSError, AttributeError):
                self.dashboard.show_notice(f"Vehicle data saved in {path.parent}: {path.name}")

        self.dashboard.action("Collecting installed vehicle data…", export, finished)

    def _saved(self, result, message: str) -> None:
        if isinstance(result, dict) and "skins" in result:
            self.render_status(result, force=True)
        else:
            self.saved_ids = self.selected_ids()
            self.saved_showcase = bool(self.kit_var.get())
            self.saved_random_enabled = bool(self.random_enabled_var.get())
            self.saved_random_chance = self.random_chance_var.get()
            self.saved_vehicle_enabled = bool(self.vehicle_enabled_var.get())
            self.saved_vehicle_chance = self.vehicle_chance_var.get()
            self.saved_vehicle_test_enabled = bool(self.vehicle_test_var.get())
            self._render_controls()
        self.dashboard.next_skins_poll = 0.0
        self.dashboard.show_notice(message)

    def _save(self) -> None:
        if self.locked or self.running or not self.loaded:
            return
        selected, showcase = list(self.selected_ids()), bool(self.kit_var.get())
        random_enabled, random_chance = bool(self.random_enabled_var.get()), self.random_chance_var.get()
        vehicle_enabled, vehicle_chance = bool(self.vehicle_enabled_var.get()), self.vehicle_chance_var.get()
        vehicle_test = bool(self.vehicle_test_var.get())
        self.dashboard.action("Saving skin selections…",
                              lambda: self.dashboard.server.save_skin_settings(
                                  selected, showcase, random_skins_enabled=random_enabled,
                                  random_skin_chance_percent=random_chance,
                                  vehicle_wraps_enabled=vehicle_enabled,
                                  vehicle_wrap_chance_percent=vehicle_chance,
                                  vehicle_test_enabled=vehicle_test),
                              lambda result: self._saved(result, "Skin selections saved. Start Avalon to apply them."))

    def _reissue(self) -> None:
        if self.locked or self.running or self.dirty() or not self.loaded or not self.saved_ids:
            return
        self.dashboard.action("Queuing another showcase kit…", self.dashboard.server.reissue_skin_kit,
                              lambda result: self._saved(result, "Showcase kit queued. Start Avalon and join to receive it."))

    def _remove(self) -> None:
        if self.locked or self.running or self.dirty() or not self.loaded or self.cleanup_pending:
            return
        self.dashboard.action("Queuing test-kit removal…", self.dashboard.server.remove_skin_test_kit,
                              lambda result: self._saved(
                                  result, "Cleanup queued. Unload test rifles, remove added gear, leave them in the chest "
                                  "or on the ground, then join Avalon."))
