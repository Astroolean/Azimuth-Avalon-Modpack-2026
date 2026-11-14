"""Shared visual styles for Azimuth's native dashboard."""

import tkinter as tk
from tkinter import font, ttk

BG = "#0b1214"
RAIL = "#0e1719"
PANEL = "#121d20"
INSET = "#0e171a"
EDGE = "#28383b"
TEXT = "#f1f4ed"
MUTED = "#a5b6b3"
DIM = "#738984"
ACCENT = "#d5ed9e"
ACCENT_HOVER = "#e4f6bc"
DANGER = "#ffab9a"
WARNING = "#edc788"
TEAL = "#7ed2c3"
BUTTON = "#203034"
FONT = "Segoe UI"
MONO = "Consolas"


def configure(window: tk.Misc) -> tuple[str, str]:
    available = {family.casefold() for family in font.families(window)}
    default_font = next((family for family in (FONT, "DejaVu Sans") if family.casefold() in available), "Helvetica")
    mono_font = next((family for family in (MONO, "DejaVu Sans Mono") if family.casefold() in available), "Courier")
    style = ttk.Style(window)
    style.theme_use("clam")
    style.configure(
        "Azimuth.TCombobox", fieldbackground=INSET, background=BUTTON, foreground=TEXT,
        selectbackground=INSET, selectforeground=TEXT, arrowcolor=MUTED, bordercolor=EDGE,
        lightcolor=EDGE, darkcolor=EDGE, padding=(10, 6),
    )
    style.map("Azimuth.TCombobox", fieldbackground=[("disabled", INSET), ("readonly", INSET)],
              foreground=[("disabled", DIM), ("readonly", TEXT)])
    style.configure("Azimuth.Vertical.TScrollbar", background=EDGE, troughcolor=INSET,
                    bordercolor=INSET, lightcolor=EDGE, darkcolor=EDGE, arrowcolor=MUTED, arrowsize=12)
    style.map("Azimuth.Vertical.TScrollbar", background=[("active", BUTTON)])
    style.configure("Azimuth.Treeview", background=INSET, fieldbackground=INSET, foreground=TEXT,
                    borderwidth=0, rowheight=34, font=(default_font, 10))
    style.configure("Azimuth.Treeview.Heading", background=PANEL, foreground=MUTED,
                    borderwidth=0, padding=(10, 7), font=(default_font, 9, "bold"))
    style.map("Azimuth.Treeview", background=[("selected", BUTTON)], foreground=[("selected", ACCENT)])
    for name, value in (
        ("background", INSET), ("foreground", TEXT),
        ("selectBackground", BUTTON), ("selectForeground", ACCENT),
    ):
        window.option_add(f"*TCombobox*Listbox.{name}", value)
    return default_font, mono_font


def mark(parent: tk.Misc, bg: str, size: int = 36) -> tk.Canvas:
    canvas = tk.Canvas(parent, width=size, height=size, bg=bg, highlightthickness=0, borderwidth=0)
    center, radius = size / 2, size * 0.43
    canvas.create_oval(center - radius, center - radius, center + radius, center + radius,
                       outline=EDGE, width=1)
    canvas.create_line(center - 9, center + 8, center + 5, center - 11, center + 1, center + 2,
                       center + 11, center + 8, fill=ACCENT, width=2, joinstyle=tk.ROUND)
    canvas.create_oval(center - 1, center + 5, center + 1, center + 7, fill=ACCENT, outline="")
    return canvas


class Trend(tk.Canvas):
    """Render only observed samples, including gaps between valid readings."""

    def __init__(self, parent: tk.Misc, color: str = ACCENT, **kwargs) -> None:
        super().__init__(parent, bg=PANEL, highlightthickness=0, borderwidth=0, **kwargs)
        self.color = color
        self.values = ()
        self.maximum = 1
        self.bind("<Configure>", self.redraw)

    def set_data(self, values, maximum: float) -> None:
        values = tuple(values)
        if self.values != values or self.maximum != maximum:
            self.values = values
            self.maximum = max(maximum, 1)
            self.redraw()

    def redraw(self, _event=None) -> None:
        self.delete("plot")
        width, height = self.winfo_width(), self.winfo_height()
        if width < 12 or height < 12:
            return
        left, right, top, bottom = 4, width - 5, 4, height - 5
        for y in (top, (top + bottom) / 2, bottom):
            self.create_line(left, y, right, y, fill=EDGE, tags="plot")
        step = (right - left) / 59
        points = []
        for index, value in enumerate(self.values[-60:], start=max(0, 60 - len(self.values))):
            if value is None:
                self._draw_run(points, bottom)
                points = []
                continue
            x = left + index * step
            y = bottom - min(1, max(0, value / self.maximum)) * (bottom - top)
            points.append((x, y))
        self._draw_run(points, bottom, show_latest=True)

    def _draw_run(self, points, bottom, *, show_latest: bool = False) -> None:
        if len(points) > 1:
            self.create_polygon((points[0][0], bottom), *points, (points[-1][0], bottom),
                                fill="#22312a" if self.color == ACCENT else "#1c3030",
                                outline="", tags="plot")
            self.create_line(*points, fill=self.color, width=2, joinstyle=tk.ROUND, tags="plot")
        if points and (show_latest or len(points) == 1):
            x, y = points[-1]
            self.create_oval(x - 2, y - 2, x + 2, y + 2, fill=self.color, outline="", tags="plot")
