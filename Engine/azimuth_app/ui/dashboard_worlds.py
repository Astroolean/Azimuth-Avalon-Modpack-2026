"""World selection and client settings pages for the Azimuth dashboard."""

import tkinter as tk
from tkinter import ttk

from azimuth_app.ui.dashboard_theme import ACCENT, BG, EDGE, FONT, INSET, MUTED, PANEL, TEXT


WORLD_DETAILS = {
    "avalon": ("Coastal island", "Shoreline towns, ridges, and inland trails.", "#9ed1c6"),
    "chernarus": ("Official map", "Large towns, forests, and open farmland.", "#c9d99c"),
    "livonia": ("Official map", "Rivers, dense woods, and rural settlements.", "#b3c5a0"),
    "deer_isle": ("Workshop map", "Island roads, coves, and remote outposts.", "#92bbcb"),
    "namalsk": ("Workshop map", "Cold terrain and a more demanding survival run.", "#c3cce3"),
}

def _page_title(parent, dashboard, title, description):
    dashboard.label(parent, title, size=25, bold=True, bg=BG).pack(anchor="w")
    dashboard.label(parent, description, size=10, color=MUTED, bg=BG,
                    wraplength=650, justify="left").pack(anchor="w", pady=(4, 16))


def _terrain_icon(parent, world_id, color):
    """Draw a terrain symbol without suggesting a geographic map."""
    icon = tk.Canvas(parent, width=32, height=32, highlightthickness=0, bg=INSET)
    if world_id in {"avalon", "deer_isle"}:
        for height in (18, 24):
            icon.create_line(5, height, 9, height - 3, 14, height, 19, height - 3,
                             24, height, 28, height - 3, fill=color, smooth=True, width=1.4)
        icon.create_oval(15, 7, 20, 12, outline=color, width=1.2)
    elif world_id == "namalsk":
        icon.create_line(5, 25, 15, 9, 27, 25, fill=color, width=1.4)
        icon.create_line(11, 16, 15, 19, 20, 16, fill=color, width=1.4)
    else:
        icon.create_line(4, 24, 11, 13, 16, 22, 22, 10, 29, 24, fill=color, width=1.4)
        icon.create_line(11, 13, 11, 26, fill=color, width=1.0)
        icon.create_line(22, 10, 22, 26, fill=color, width=1.0)
    return icon


def _tile(parent, dashboard, world_id, world_name, position):
    category, description, color = WORLD_DETAILS.get(world_id, ("World", "", ACCENT))
    row, column = divmod(position, 2)
    card = tk.Frame(parent, bg=PANEL, highlightthickness=1, highlightbackground=EDGE,
                    highlightcolor=ACCENT, takefocus=True, cursor="hand2", padx=12, pady=9)
    card.grid(row=row, column=column, sticky="nsew", padx=(0, 9) if column == 0 else (0, 0), pady=(0, 8))
    card.columnconfigure(1, weight=1)
    icon = _terrain_icon(card, world_id, color)
    icon.grid(row=0, column=0, rowspan=2, sticky="nw", padx=(0, 10), pady=(1, 0))
    title = dashboard.label(card, world_name, size=12, bold=True)
    title.grid(row=0, column=1, columnspan=2, sticky="w")
    status = dashboard.label(card, category, size=9, color=MUTED, bold=True)
    status.grid(row=1, column=2, sticky="e", padx=(7, 0), pady=(3, 0))
    details = dashboard.label(card, description, size=9, color=MUTED, justify="left", wraplength=190)
    details.grid(row=1, column=1, sticky="ew", pady=(3, 0))
    card.bind("<Configure>", lambda event: details.configure(
        wraplength=max(80, event.width - 84 - status.winfo_reqwidth())))

    def select(_event):
        if str(dashboard.world_menu.cget("state")) != "disabled":
            card.focus_set()
            dashboard.world_choice.set(world_name)
            dashboard.refresh_world()
        return "break"

    for widget in (card, icon, title, status, details):
        widget.bind("<Button-1>", select)
    card.bind("<Return>", select)
    card.bind("<space>", select)
    return card, status, category


def build_worlds(parent, dashboard):
    """Build world controls using the main dashboard's existing action handlers."""
    _page_title(parent, dashboard, "Worlds", "Choose the active map. Each world keeps its own save and backups.")

    tiles = tk.Frame(parent, bg=BG)
    tiles.pack(fill="x", pady=(0, 5))
    tiles.columnconfigure((0, 1), weight=1, uniform="world-tiles")
    dashboard.world_tiles = {
        world_id: _tile(tiles, dashboard, world_id, name, position)
        for position, (world_id, name) in enumerate(dashboard.world_names.items())
    }

    controls = dashboard.panel(parent)
    controls.pack(fill="x")
    body = tk.Frame(controls, bg=PANEL, padx=14, pady=12)
    body.pack(fill="x")
    dashboard.label(body, "World setup", size=13, bold=True).pack(anchor="w", pady=(0, 7))
    selector = tk.Frame(body, bg=PANEL)
    selector.pack(fill="x")
    dashboard.world_menu = ttk.Combobox(
        selector, textvariable=dashboard.world_choice, width=18, values=list(dashboard.world_ids),
        state="readonly", style="Azimuth.TCombobox", font=(FONT, 10),
    )
    dashboard.world_menu.pack(side="left", fill="x", expand=True, ipady=3)
    dashboard.world_menu.bind("<<ComboboxSelected>>", lambda _event: dashboard.refresh_world())
    dashboard.world_switch_button = dashboard.button(selector, "Switch world", dashboard.switch_world,
                                                     compact=True, primary=True)
    dashboard.world_switch_button.pack(side="left", padx=(8, 0))
    dashboard.world_summary_label = dashboard.label(body, "", size=9, color=MUTED,
                                                     justify="left", wraplength=565)
    dashboard.world_summary_label.pack(fill="x", pady=(6, 7))
    body.bind("<Configure>", lambda event: dashboard.world_summary_label.configure(
        wraplength=max(180, event.width - 30)))

    tools = tk.Frame(body, bg=PANEL)
    tools.pack(fill="x")
    dashboard.button(tools, "Setup folder", dashboard.open_world_setup, compact=True).pack(side="left")
    dashboard.button(tools, "Active Mods folder", dashboard.open_mods, compact=True).pack(side="left", padx=(6, 0))
    dashboard.world_workshop_button = dashboard.button(tools, "Steam Workshop", dashboard.open_world_workshop,
                                                       compact=True)
    dashboard.world_workshop_button.pack(side="left", padx=(6, 0))
    dashboard.world_source_button = dashboard.button(tools, "Mission source", dashboard.open_world_source,
                                                     compact=True)
    dashboard.world_source_button.pack(side="left", padx=(6, 0))

    dashboard.avalon_links = tk.Frame(body, bg=PANEL)
    dashboard.button(dashboard.avalon_links, "DayZ Tools", dashboard.open_dayz_tools, compact=True).pack(side="left")
    dashboard.button(dashboard.avalon_links, "Hunter Mods folder", dashboard.open_avalon_client_mod,
                     compact=True).pack(side="left", padx=(6, 0))
    dashboard.avalon_pack_button = dashboard.button(dashboard.avalon_links, "Workshop pack",
                                                    dashboard.open_avalon_workshop_pack, compact=True)
    dashboard.avalon_pack_button.pack(side="left", padx=(6, 0))
    dashboard.button(dashboard.avalon_links, "Loot audit", dashboard.open_avalon_loot_audit,
                     compact=True).pack(side="left", padx=(6, 0))


def refresh_world_tiles(dashboard, selected_id, active_id, locked):
    for world_id, (card, status, category) in dashboard.world_tiles.items():
        selected, active = world_id == selected_id, world_id == active_id
        card.configure(highlightbackground=ACCENT if selected else EDGE,
                       cursor="arrow" if locked else "hand2", takefocus=not locked)
        status.configure(text="Active" if active else "Selected" if selected else category,
                         fg=ACCENT if active or selected else MUTED)


def _client_card(parent, dashboard, column, title, description):
    panel = dashboard.panel(parent)
    panel.grid(row=0, column=column, sticky="nsew", padx=(0, 10) if column == 0 else (0, 0))
    body = tk.Frame(panel, bg=PANEL, padx=15, pady=10)
    body.pack(fill="both", expand=True)
    dashboard.label(body, title, size=15, bold=True).pack(anchor="w")
    detail = dashboard.label(body, description, size=10, color=MUTED, justify="left", wraplength=255)
    detail.pack(fill="x", pady=(4, 7))
    body.bind("<Configure>", lambda event: detail.configure(wraplength=max(150, event.width - 33)))
    return body


def _client_option(parent, dashboard, label, description):
    row = tk.Frame(parent, bg=INSET, padx=9, pady=5)
    row.pack(fill="x", pady=(0, 4))
    body = tk.Frame(row, bg=INSET)
    body.pack(side="left", fill="both", expand=True)
    dashboard.label(body, label, size=10, bold=True, bg=INSET).pack(anchor="w")
    detail = dashboard.label(body, description, size=9, color=MUTED, bg=INSET,
                             wraplength=220, justify="left")
    detail.pack(fill="x", pady=(2, 0))
    body.bind("<Configure>", lambda event: detail.configure(wraplength=max(80, event.width)))


def build_client(parent, dashboard):
    """Offer enhanced play and individual graphics controls."""
    _page_title(parent, dashboard, "Client", "Set how DayZ looks when you play Avalon.")

    enhanced = dashboard.panel(parent)
    enhanced.pack(fill="x", pady=(0, 8))
    dashboard.enhanced_play_button = dashboard.button(
        enhanced, "▶  Play Avalon Enhanced", dashboard.play_avalon_enhanced, primary=True)
    dashboard.enhanced_play_button.pack(side="right", padx=14, pady=12)
    summary = tk.Frame(enhanced, bg=PANEL)
    summary.pack(side="left", fill="x", expand=True, padx=(14, 0), pady=8)
    dashboard.label(summary, "Distance and image quality", size=12, bold=True).pack(anchor="w")
    detail = dashboard.label(summary, "Farther views, cleaner edges and daylight color. Uses more GPU/CPU; FPS may drop.",
                             size=9, color=MUTED, wraplength=330, justify="left")
    detail.pack(fill="x", pady=(3, 0))
    summary.bind("<Configure>", lambda event: detail.configure(wraplength=max(80, event.width)))

    cards = tk.Frame(parent, bg=BG)
    cards.pack(fill="x")
    cards.columnconfigure((0, 1), weight=1, uniform="client-cards")

    visuals = _client_card(cards, dashboard, 0, "Avalon Visuals", "Map models and textures still limit detail.")
    _client_option(visuals, dashboard, "Farther views, more object detail", "5,000 m terrain and 3,000 m objects")
    _client_option(visuals, dashboard, "Textures, shadows and edges", "Hardware AA, foliage smoothing and high filtering")
    dashboard.looks_button = dashboard.button(visuals, "Apply Avalon Visuals", dashboard.apply_avalon_visuals,
                                              primary=True)
    dashboard.looks_button.pack(side="bottom", anchor="w", pady=(7, 0))

    fps = _client_card(cards, dashboard, 1, "Performance", "Shorter view distances for smoother play.")
    _client_option(fps, dashboard, "Terrain visibility", "Caps terrain view distance at 1,600 m")
    _client_option(fps, dashboard, "Object visibility", "Caps object view distance at 1,000 m")
    dashboard.fps_button = dashboard.button(fps, "Apply FPS preset", dashboard.open_fps_settings, primary=True)
    dashboard.fps_button.pack(side="bottom", anchor="w", pady=(7, 0))

    actions = tk.Frame(parent, bg=BG)
    actions.pack(fill="x", pady=(9, 0))
    dashboard.choose_graphics_button = dashboard.button(
        actions, "Choose profile", lambda: dashboard.select_graphics_profile(choose=True), compact=True)
    dashboard.choose_graphics_button.pack(side="left")
    dashboard.restore_graphics_button = dashboard.button(
        actions, "Restore graphics", dashboard.restore_client_graphics, compact=True)
    dashboard.restore_graphics_button.pack(side="left", padx=(7, 0))
    dashboard.button(actions, "Graphics guide", dashboard.open_avalon_visuals_guide, compact=True).pack(
        side="left", padx=(7, 0))
    dashboard.graphics_choice_label = dashboard.label(parent, "", size=9, color=MUTED, bg=BG, justify="left")
    dashboard.graphics_choice_label.pack(fill="x", pady=(6, 0))
    preview = tk.Frame(parent, bg=BG)
    preview.pack(fill="x", pady=(7, 0))
    dashboard.color_preview_button = dashboard.button(preview, "Enable color preview",
                                                      dashboard.toggle_avalon_color_preview, compact=True)
    dashboard.color_preview_button.pack(side="left")
    dashboard.label(preview, "Experimental outdoor grade. Stop Avalon before switching.",
                    size=9, color=MUTED, bg=BG).pack(side="left", padx=(9, 0))
