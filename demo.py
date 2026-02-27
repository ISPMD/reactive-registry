"""
Registry Demo
=============
Shows @registry.reactive (per-instance) and @registry.reactive_class
(class-level) side by side in a small PySide6 app.

Left  — controls: volume slider, brightness slider, dark/light toggle.
Right — two rows of reactive widgets:
          Row 1: PlayerCard  — uses @registry.reactive  (per-instance)
          Row 2: StatusBadge — uses @registry.reactive_class (class-level)
Top   — Settings button opens a dialog to pick a theme and flip mode.

Run:
    python demo.py
"""

import sys
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget,
    QVBoxLayout, QHBoxLayout, QLabel, QSlider,
    QPushButton, QDialog, QFrame, QButtonGroup, QRadioButton,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

from Registry import settings, theme, registry


# ---------------------------------------------------------------------------
# Theme definitions
# ---------------------------------------------------------------------------

theme.register("Slate", {
    "dark": {
        "color.bg":      "#0f172a",
        "color.surface": "#1e293b",
        "color.border":  "#475569",
        "color.text":    "#f8fafc",
        "color.subtext": "#e2e8f0",  # strong readable secondary
        "color.accent":  "#38bdf8",
    },
    "light": {
        "color.bg":      "#f8fafc",
        "color.surface": "#e2e8f0",
        "color.border":  "#94a3b8",
        "color.text":    "#0f172a",
        "color.subtext": "#1e293b",  # dark enough for body text
        "color.accent":  "#0369a1",
    },
})

theme.register("Rose", {
    "dark": {
        "color.bg":      "#1c0f12",
        "color.surface": "#2a161a",
        "color.border":  "#7f1d1d",
        "color.text":    "#fff1f2",
        "color.subtext": "#fff1f2",
        "color.accent":  "#d46a78",
    },
    "light": {
        "color.bg":      "#fff1f2",
        "color.surface": "#ffe4e6",
        "color.border":  "#fda4af",
        "color.text":    "#1c0f12",
        "color.subtext": "#14070a",   # near-black wine
        "color.accent":  "#a83a4a",
    },
})


theme.register("Moss", {
    "dark": {
        "color.bg":      "#0b1a12",
        "color.surface": "#13261a",
        "color.border":  "#2f5d3a",
        "color.text":    "#f0fdf4",
        "color.subtext": "#f0fdf4",
        "color.accent":  "#3cb56b",
    },
    "light": {
        "color.bg":      "#f0fdf4",
        "color.surface": "#dcfce7",
        "color.border":  "#86efac",
        "color.text":    "#0b1a12",
        "color.subtext": "#06140d",   # near-black green
        "color.accent":  "#1f7a46",
    },
})

# Seed settings with defaults
settings.set("volume",     72)
settings.set("brightness", 80)

theme.set_theme("Slate")
theme.set_mode("dark")


# ---------------------------------------------------------------------------
# SettingsDialog
# ---------------------------------------------------------------------------

class SettingsDialog(QDialog):
    """Theme picker and dark / light mode toggle."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setFixedWidth(280)

        root = QVBoxLayout(self)
        root.setSpacing(16)
        root.setContentsMargins(24, 24, 24, 24)

        root.addWidget(self._heading("Theme"))

        self._theme_group = QButtonGroup(self)
        for i, name in enumerate(("Slate", "Rose", "Moss")):
            rb = QRadioButton(name)
            rb.setFont(QFont("Courier New", 10))
            rb.setChecked(theme.active_theme == name)
            rb.toggled.connect(lambda on, n=name: theme.set_theme(n) if on else None)
            self._theme_group.addButton(rb, i)
            root.addWidget(rb)

        root.addWidget(self._separator())
        root.addWidget(self._heading("Mode"))

        self._mode_group = QButtonGroup(self)
        for i, mode_name in enumerate(("dark", "light")):
            rb = QRadioButton(mode_name.capitalize())
            rb.setFont(QFont("Courier New", 10))
            rb.setChecked(theme.active_mode == mode_name)
            rb.toggled.connect(lambda on, m=mode_name: theme.set_mode(m) if on else None)
            self._mode_group.addButton(rb, i)
            root.addWidget(rb)

        root.addStretch()

        btn = QPushButton("Done")
        btn.setFont(QFont("Courier New", 10))
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(self.accept)
        root.addWidget(btn)

        self.apply_style()   # first call — tracks + wires

    def _heading(self, text):
        lbl = QLabel(text)
        lbl.setFont(QFont("Courier New", 10, QFont.Bold))
        return lbl

    def _separator(self):
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        return line

    @registry.reactive
    def apply_style(self):
        bg      = theme.get("color.bg")
        surface = theme.get("color.surface")
        border  = theme.get("color.border")
        text    = theme.get("color.text")
        subtext = theme.get("color.subtext")
        accent  = theme.get("color.accent")
        self.setStyleSheet(f"""
            QDialog   {{ background:{bg}; }}
            QLabel    {{ color:{text}; background:transparent; }}
            QFrame    {{ color:{border}; }}
            QRadioButton {{
                color:{subtext}; spacing:8px;
            }}
            QRadioButton::indicator {{
                width:13px; height:13px; border-radius:7px;
                border:2px solid {border};
            }}
            QRadioButton::indicator:checked {{
                background:{accent}; border-color:{accent};
            }}
            QPushButton {{
                background:{accent}; color:{bg};
                border:none; border-radius:6px; padding:8px 0;
                font-weight:600;
            }}
            QPushButton:hover {{ background:{surface}; color:{accent}; border:1px solid {accent}; }}
        """)


# ---------------------------------------------------------------------------
# ControlPanel — left side
# ---------------------------------------------------------------------------

class ControlPanel(QWidget):
    """Volume slider, brightness slider, mode toggle button."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(220)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 28, 20, 28)
        root.setSpacing(20)

        heading = QLabel("Controls")
        heading.setFont(QFont("Courier New", 13, QFont.Bold))
        root.addWidget(heading)

        root.addWidget(self._separator())

        # Volume
        root.addWidget(self._label("volume"))
        self._vol_readout = QLabel(str(settings.get("volume")))
        self._vol_readout.setFont(QFont("Courier New", 10))
        root.addWidget(self._vol_readout)

        self._vol_slider = QSlider(Qt.Horizontal)
        self._vol_slider.setRange(0, 100)
        self._vol_slider.setValue(settings.get("volume"))
        self._vol_slider.valueChanged.connect(self._on_volume)
        root.addWidget(self._vol_slider)

        # Brightness
        root.addWidget(self._label("brightness"))
        self._bri_readout = QLabel(str(settings.get("brightness")))
        self._bri_readout.setFont(QFont("Courier New", 10))
        root.addWidget(self._bri_readout)

        self._bri_slider = QSlider(Qt.Horizontal)
        self._bri_slider.setRange(0, 100)
        self._bri_slider.setValue(settings.get("brightness"))
        self._bri_slider.valueChanged.connect(self._on_brightness)
        root.addWidget(self._bri_slider)

        root.addWidget(self._separator())

        self._mode_btn = QPushButton()
        self._mode_btn.setFont(QFont("Courier New", 10))
        self._mode_btn.setCursor(Qt.PointingHandCursor)
        self._mode_btn.clicked.connect(theme.toggle_mode)
        root.addWidget(self._mode_btn)

        root.addStretch()

        self.apply_style()  # first call — tracks + wires

    def _label(self, text):
        lbl = QLabel(text)
        lbl.setFont(QFont("Courier New", 9))
        return lbl

    def _separator(self):
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        return line

    def _on_volume(self, val):
        settings.set("volume", val)
        self._vol_readout.setText(str(val))

    def _on_brightness(self, val):
        settings.set("brightness", val)
        self._bri_readout.setText(str(val))

    @registry.reactive
    def apply_style(self):
        bg      = theme.get("color.bg")
        surface = theme.get("color.surface")
        border  = theme.get("color.border")
        text    = theme.get("color.text")
        subtext = theme.get("color.subtext")
        accent  = theme.get("color.accent")

        mode = theme.active_mode
        self._mode_btn.setText(f"→ {('light' if mode == 'dark' else 'dark')} mode")

        self.setStyleSheet(f"""
            QWidget  {{ background:{bg}; color:{text}; }}
            QLabel   {{ color:{subtext}; background:transparent; border:none; }}
            QFrame   {{ color:{border}; }}
            QSlider::groove:horizontal {{
                height:3px; background:{border}; border-radius:2px;
            }}
            QSlider::sub-page:horizontal {{
                background:{accent}; border-radius:2px;
            }}
            QSlider::handle:horizontal {{
                width:14px; height:14px; margin:-6px 0;
                background:{accent}; border-radius:7px;
            }}
            QPushButton {{
                background:transparent; color:{subtext};
                border:1px solid {border}; border-radius:6px; padding:7px;
            }}
            QPushButton:hover {{ color:{accent}; border-color:{accent}; }}
        """)


# ---------------------------------------------------------------------------
# PlayerCard  — @registry.reactive  (per-instance tracking)
#
# Each card wires its own connections on its first refresh() call.
# Instance A and Instance B each hold separate signal connections.
# ---------------------------------------------------------------------------

class PlayerCard(QWidget):
    """Shows a channel name, volume bar, and brightness bar.

    @registry.reactive — per-instance. Every card independently wires
    connections to "volume", "brightness", and each theme token it reads.
    Three cards means three times as many connections as one card.
    """

    def __init__(self, name: str, parent=None):
        super().__init__(parent)
        self._name = name
        self.setFixedSize(180, 120)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(6)

        self._name_lbl = QLabel(name)
        self._name_lbl.setFont(QFont("Courier New", 9, QFont.Bold))
        root.addWidget(self._name_lbl)

        self._vol_lbl = QLabel()
        self._vol_lbl.setFont(QFont("Courier New", 8))
        root.addWidget(self._vol_lbl)

        self._vol_track = QFrame()
        self._vol_track.setFixedHeight(6)
        self._vol_fill = QFrame(self._vol_track)
        self._vol_fill.setFixedHeight(6)
        root.addWidget(self._vol_track)

        self._bri_lbl = QLabel()
        self._bri_lbl.setFont(QFont("Courier New", 8))
        root.addWidget(self._bri_lbl)

        self._bri_track = QFrame()
        self._bri_track.setFixedHeight(6)
        self._bri_fill = QFrame(self._bri_track)
        self._bri_fill.setFixedHeight(6)
        root.addWidget(self._bri_track)

        self.refresh()  # first call — tracks keys, wires this instance's connections

    @registry.reactive
    def refresh(self):
        vol = settings.get("volume")
        bri = settings.get("brightness")
        bg      = theme.get("color.bg")
        surface = theme.get("color.surface")
        border  = theme.get("color.border")
        text    = theme.get("color.text")
        subtext = theme.get("color.subtext")
        accent  = theme.get("color.accent")

        self._vol_lbl.setText(f"vol  {vol:>3}%")
        self._bri_lbl.setText(f"bri  {bri:>3}%")

        self.setStyleSheet(f"""
            QWidget {{ background:{surface}; border-radius:8px; }}
            QFrame  {{ border:none; background:transparent; }}
        """)
        self._name_lbl.setStyleSheet(f"color:{text}; background:transparent;")
        self._vol_lbl.setStyleSheet(f"color:{subtext}; background:transparent;")
        self._bri_lbl.setStyleSheet(f"color:{subtext}; background:transparent;")
        self._vol_track.setStyleSheet(f"background:{border}; border-radius:3px;")
        self._bri_track.setStyleSheet(f"background:{border}; border-radius:3px;")
        self._vol_fill.setStyleSheet(f"background:{accent}; border-radius:3px;")
        self._bri_fill.setStyleSheet(f"background:{accent}; border-radius:3px;")

        # Update fill widths once the widget has a real width
        self._vol = vol
        self._bri = bri
        self._vol_track.resizeEvent = lambda _: self._resize_bars()
        self._resize_bars()

    def _resize_bars(self):
        tw = self._vol_track.width()
        self._vol_fill.setFixedWidth(max(0, int(tw * self._vol / 100)))
        self._bri_fill.setFixedWidth(max(0, int(tw * self._bri / 100)))


# ---------------------------------------------------------------------------
# StatusBadge  — @registry.reactive_class  (class-level tracking)
#
# All StatusBadge instances share exactly one set of signal connections.
# The first instance wires them; every later one just joins the WeakSet.
# ---------------------------------------------------------------------------

@registry.reactive_class
class StatusBadge(QWidget):
    """Shows the active theme name, mode, and accent swatch.

    @registry.reactive_class — class-level. No matter how many StatusBadge
    instances exist, there are only as many signal connections as there are
    unique (store, key) pairs read in refresh(). One dispatch re-runs refresh()
    on every living instance at once.
    """

    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        self._label = label
        self.setFixedSize(180, 120)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(6)

        self._label_lbl = QLabel(label)
        self._label_lbl.setFont(QFont("Courier New", 9, QFont.Bold))
        root.addWidget(self._label_lbl)

        self._theme_lbl = QLabel()
        self._theme_lbl.setFont(QFont("Courier New", 8))
        root.addWidget(self._theme_lbl)

        self._mode_lbl = QLabel()
        self._mode_lbl.setFont(QFont("Courier New", 8))
        root.addWidget(self._mode_lbl)

        self._swatch = QFrame()
        self._swatch.setFixedHeight(16)
        root.addWidget(self._swatch)

        self.refresh()  # first instance: tracks + wires; others: join WeakSet + run

    @registry.reactive
    def refresh(self):
        bg      = theme.get("color.bg")
        surface = theme.get("color.surface")
        border  = theme.get("color.border")
        text    = theme.get("color.text")
        subtext = theme.get("color.subtext")
        accent  = theme.get("color.accent")

        self._theme_lbl.setText(f"theme  {theme.active_theme}")
        self._mode_lbl.setText(f"mode   {theme.active_mode}")

        self.setStyleSheet(f"""
            QWidget {{ background:{surface}; border-radius:8px; }}
            QFrame  {{ border:none; background:transparent; }}
        """)
        self._label_lbl.setStyleSheet(f"color:{text}; background:transparent;")
        self._theme_lbl.setStyleSheet(f"color:{subtext}; background:transparent;")
        self._mode_lbl.setStyleSheet(f"color:{subtext}; background:transparent;")
        self._swatch.setStyleSheet(f"background:{accent}; border-radius:4px;")


# ---------------------------------------------------------------------------
# MainWindow
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Registry Demo")
        self.setMinimumSize(860, 500)

        # ── root split ──────────────────────────────────────────────────────
        root_w = QWidget()
        self.setCentralWidget(root_w)
        root_h = QHBoxLayout(root_w)
        root_h.setContentsMargins(0, 0, 0, 0)
        root_h.setSpacing(0)

        # Left panel
        self.controls = ControlPanel()
        root_h.addWidget(self.controls)

        # Divider
        div = QFrame()
        div.setFrameShape(QFrame.VLine)
        root_h.addWidget(div)

        # Right panel
        right_w = QWidget()
        right_v = QVBoxLayout(right_w)
        right_v.setContentsMargins(28, 24, 28, 24)
        right_v.setSpacing(20)
        root_h.addWidget(right_w, stretch=1)

        # ── top bar ─────────────────────────────────────────────────────────
        top = QHBoxLayout()
        top.setSpacing(0)

        title = QLabel("Reactive Widgets")
        title.setFont(QFont("Courier New", 13, QFont.Bold))
        top.addWidget(title)
        top.addStretch()

        self._settings_btn = QPushButton("settings")
        self._settings_btn.setFont(QFont("Courier New", 9))
        self._settings_btn.setCursor(Qt.PointingHandCursor)
        self._settings_btn.clicked.connect(self._open_settings)
        top.addWidget(self._settings_btn)

        right_v.addLayout(top)

        # ── per-instance section ─────────────────────────────────────────────
        pi_tag = QLabel("@registry.reactive  —  per-instance")
        pi_tag.setFont(QFont("Courier New", 8))
        right_v.addWidget(pi_tag)

        pi_note = QLabel(
            "Each PlayerCard independently tracks its own deps. "
            "3 cards × 8 keys = 24 signal connections total."
        )
        pi_note.setFont(QFont("Courier New", 8))
        pi_note.setWordWrap(True)
        right_v.addWidget(pi_note)

        pi_row = QHBoxLayout()
        pi_row.setSpacing(12)
        pi_row.setAlignment(Qt.AlignLeft)
        self._cards = [PlayerCard(f"ch-{i+1}") for i in range(3)]
        for card in self._cards:
            pi_row.addWidget(card)
        pi_row.addStretch()
        right_v.addLayout(pi_row)

        # ── separator ───────────────────────────────────────────────────────
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        right_v.addWidget(sep)

        # ── class-level section ──────────────────────────────────────────────
        cl_tag = QLabel("@registry.reactive_class  —  class-level")
        cl_tag.setFont(QFont("Courier New", 8))
        right_v.addWidget(cl_tag)

        cl_note = QLabel(
            "All StatusBadges share one set of connections for the whole class. "
            "3 badges or 300 — still 6 signal connections total."
        )
        cl_note.setFont(QFont("Courier New", 8))
        cl_note.setWordWrap(True)
        right_v.addWidget(cl_note)

        cl_row = QHBoxLayout()
        cl_row.setSpacing(12)
        cl_row.setAlignment(Qt.AlignLeft)
        self._badges = [StatusBadge(f"svc-{i+1}") for i in range(3)]
        for badge in self._badges:
            cl_row.addWidget(badge)
        cl_row.addStretch()
        right_v.addLayout(cl_row)

        right_v.addStretch()

        self.apply_style()  # first call — tracks + wires

    def _open_settings(self):
        SettingsDialog(self).exec()

    @registry.reactive
    def apply_style(self):
        bg     = theme.get("color.bg")
        border = theme.get("color.border")
        text   = theme.get("color.text")
        subtext = theme.get("color.subtext")
        accent  = theme.get("color.accent")
        self.setStyleSheet(f"""
            QMainWindow, QWidget {{ background:{bg}; color:{text}; }}
            QLabel  {{ color:{subtext}; background:transparent; }}
            QFrame  {{ color:{border}; }}
            QPushButton {{
                background:transparent; color:{subtext};
                border:1px solid {border}; border-radius:5px; padding:5px 12px;
            }}
            QPushButton:hover {{ color:{accent}; border-color:{accent}; }}
        """)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
