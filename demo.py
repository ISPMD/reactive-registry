"""
Registry Demo
-------------
Showcases the Registry library:
  - Multiple named themes (Catppuccin Mocha, Nord, Solarized)
  - Dark / light mode toggle
  - Live settings controls (volume slider, username input, notifications toggle)
  - Multiple independent widgets all reacting to the same stores
  - No manual signal wiring — everything driven by @registry.reactive

Run:
    python demo.py
"""

import sys
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QSlider, QLineEdit, QCheckBox, QComboBox, QPushButton,
    QFrame, QSizePolicy,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

from Registry import settings, theme, registry


# ---------------------------------------------------------------------------
# Theme definitions
# ---------------------------------------------------------------------------

THEMES = {
    "Catppuccin Mocha": {
        "dark": {
            "bg":         "#1e1e2e",
            "bg.surface": "#313244",
            "bg.overlay": "#45475a",
            "fg":         "#cdd6f4",
            "fg.subtle":  "#a6adc8",
            "accent":     "#cba6f7",
            "accent.alt": "#89b4fa",
            "danger":     "#f38ba8",
            "border":     "#45475a",
        },
        "light": {
            "bg":         "#eff1f5",
            "bg.surface": "#e6e9ef",
            "bg.overlay": "#dce0e8",
            "fg":         "#4c4f69",
            "fg.subtle":  "#6c6f85",
            "accent":     "#8839ef",
            "accent.alt": "#1e66f5",
            "danger":     "#d20f39",
            "border":     "#dce0e8",
        },
    },
    "Nord": {
        "dark": {
            "bg":         "#2e3440",
            "bg.surface": "#3b4252",
            "bg.overlay": "#434c5e",
            "fg":         "#eceff4",
            "fg.subtle":  "#d8dee9",
            "accent":     "#88c0d0",
            "accent.alt": "#81a1c1",
            "danger":     "#bf616a",
            "border":     "#434c5e",
        },
        "light": {
            "bg":         "#eceff4",
            "bg.surface": "#e5e9f0",
            "bg.overlay": "#d8dee9",
            "fg":         "#2e3440",
            "fg.subtle":  "#4c566a",
            "accent":     "#5e81ac",
            "accent.alt": "#81a1c1",
            "danger":     "#bf616a",
            "border":     "#d8dee9",
        },
    },
    "Solarized": {
        "dark": {
            "bg":         "#002b36",
            "bg.surface": "#073642",
            "bg.overlay": "#094e5a",
            "fg":         "#839496",
            "fg.subtle":  "#657b83",
            "accent":     "#268bd2",
            "accent.alt": "#2aa198",
            "danger":     "#dc322f",
            "border":     "#094e5a",
        },
        "light": {
            "bg":         "#fdf6e3",
            "bg.surface": "#eee8d5",
            "bg.overlay": "#ddd6c1",
            "fg":         "#657b83",
            "fg.subtle":  "#839496",
            "accent":     "#268bd2",
            "accent.alt": "#2aa198",
            "danger":     "#dc322f",
            "border":     "#ddd6c1",
        },
    },
}

# ---------------------------------------------------------------------------
# Settings defaults
# ---------------------------------------------------------------------------

settings.load_dict({
    "volume":        75,
    "username":      "user",
    "notifications": True,
})

for name, definition in THEMES.items():
    theme.register(name, definition)

theme.set_theme("Catppuccin Mocha")
theme.set_mode("dark")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _card(parent=None) -> QFrame:
    """Return a styled card frame."""
    f = QFrame(parent)
    f.setFrameShape(QFrame.StyledPanel)
    return f


def _label(text: str, parent=None, bold=False, small=False) -> QLabel:
    lbl = QLabel(text, parent)
    font = lbl.font()
    if bold:
        font.setWeight(QFont.Bold)
    if small:
        font.setPointSize(font.pointSize() - 1)
    lbl.setFont(font)
    return lbl


def _divider(parent=None) -> QFrame:
    line = QFrame(parent)
    line.setFrameShape(QFrame.HLine)
    line.setFixedHeight(1)
    return line


# ---------------------------------------------------------------------------
# ControlPanel — settings controls, theme selector, mode toggle
# ---------------------------------------------------------------------------

class ControlPanel(QWidget):
    """Left panel. Owns all the controls that mutate the stores."""

    def __init__(self):
        super().__init__()
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(16)
        root.setContentsMargins(0, 0, 0, 0)

        # --- Theme selector ---
        root.addWidget(_label("Theme", bold=True))
        self._theme_combo = QComboBox()
        self._theme_combo.addItems(list(THEMES.keys()))
        self._theme_combo.currentTextChanged.connect(theme.set_theme)
        root.addWidget(self._theme_combo)

        # --- Mode toggle ---
        self._mode_btn = QPushButton()
        self._mode_btn.clicked.connect(theme.toggle_mode)
        root.addWidget(self._mode_btn)

        root.addWidget(_divider())

        # --- Volume ---
        root.addWidget(_label("Volume", bold=True))
        vol_row = QHBoxLayout()
        self._vol_slider = QSlider(Qt.Horizontal)
        self._vol_slider.setRange(0, 100)
        self._vol_slider.setValue(settings.get("volume"))
        self._vol_slider.valueChanged.connect(lambda v: settings.set("volume", v))
        self._vol_label = _label(str(settings.get("volume")))
        self._vol_label.setFixedWidth(28)
        vol_row.addWidget(self._vol_slider)
        vol_row.addWidget(self._vol_label)
        root.addLayout(vol_row)

        # --- Username ---
        root.addWidget(_label("Username", bold=True))
        self._username_input = QLineEdit()
        self._username_input.setText(settings.get("username"))
        self._username_input.textChanged.connect(
            lambda t: settings.set("username", t) if t else None
        )
        root.addWidget(self._username_input)

        # --- Notifications ---
        self._notif_check = QCheckBox("Enable notifications")
        self._notif_check.setChecked(settings.get("notifications"))
        self._notif_check.toggled.connect(lambda v: settings.set("notifications", v))
        root.addWidget(self._notif_check)

        root.addStretch()

    @registry.reactive
    def refresh(self):
        bg         = theme.get("bg")
        bg_surface = theme.get("bg.surface")
        fg         = theme.get("fg")
        fg_subtle  = theme.get("fg.subtle")
        accent     = theme.get("accent")
        border     = theme.get("border")
        mode       = theme.active_mode  # not tracked — read for label only

        self.setStyleSheet(f"""
            QWidget       {{ background: {bg}; color: {fg}; }}
            QComboBox     {{ background: {bg_surface}; color: {fg};
                             border: 1px solid {border}; border-radius: 4px;
                             padding: 4px 8px; }}
            QComboBox QAbstractItemView {{ background: {bg_surface}; color: {fg};
                             selection-background-color: {accent}; }}
            QLineEdit     {{ background: {bg_surface}; color: {fg};
                             border: 1px solid {border}; border-radius: 4px;
                             padding: 4px 8px; }}
            QSlider::groove:horizontal {{ background: {bg_surface};
                             height: 4px; border-radius: 2px; }}
            QSlider::handle:horizontal {{ background: {accent};
                             width: 14px; height: 14px; margin: -5px 0;
                             border-radius: 7px; }}
            QCheckBox     {{ color: {fg}; spacing: 6px; }}
            QCheckBox::indicator {{ width: 16px; height: 16px;
                             border: 1px solid {border}; border-radius: 3px;
                             background: {bg_surface}; }}
            QCheckBox::indicator:checked {{ background: {accent};
                             border-color: {accent}; }}
            QPushButton   {{ background: {bg_surface}; color: {fg};
                             border: 1px solid {border}; border-radius: 4px;
                             padding: 6px 12px; }}
            QPushButton:hover {{ border-color: {accent}; color: {accent}; }}
            QLabel        {{ background: transparent; color: {fg}; }}
            QFrame[frameShape="4"] {{ color: {border}; }}
        """)

        self._mode_btn.setText(
            "Switch to Light Mode" if mode == "dark" else "Switch to Dark Mode"
        )
        self._vol_label.setText(str(settings.get("volume")))


# ---------------------------------------------------------------------------
# StatusCard — shows current settings values, reacts to all changes
# ---------------------------------------------------------------------------

class StatusCard(QWidget):
    """Displays current settings state. Reacts to both stores."""

    def __init__(self):
        super().__init__()
        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.setContentsMargins(16, 16, 16, 16)

        root.addWidget(_label("Current State", bold=True))
        root.addWidget(_divider())

        self._volume_lbl    = _label("")
        self._username_lbl  = _label("")
        self._notif_lbl     = _label("")
        self._theme_lbl     = _label("")
        self._mode_lbl      = _label("")

        for lbl in (self._volume_lbl, self._username_lbl, self._notif_lbl,
                    self._theme_lbl, self._mode_lbl):
            root.addWidget(lbl)

        root.addStretch()
        self.refresh()

    @registry.reactive
    def refresh(self):
        bg        = theme.get("bg.surface")
        fg        = theme.get("fg")
        fg_subtle = theme.get("fg.subtle")
        accent    = theme.get("accent")
        border    = theme.get("border")
        vol       = settings.get("volume")
        username  = settings.get("username")
        notif     = settings.get("notifications")

        self.setStyleSheet(f"""
            QWidget {{ background: {bg}; border-radius: 6px; }}
            QLabel  {{ background: transparent; color: {fg}; }}
            QFrame[frameShape="4"] {{ color: {border}; }}
        """)

        self._volume_lbl.setText(f"Volume:         {vol}")
        self._username_lbl.setText(f"Username:      {username}")
        self._notif_lbl.setText(
            f"Notifications: {'on' if notif else 'off'}"
        )
        self._theme_lbl.setText(f"Theme:          {theme.active_theme}")
        self._mode_lbl.setText(f"Mode:           {theme.active_mode}")


# ---------------------------------------------------------------------------
# PreviewCard — purely cosmetic, shows theme colours live
# ---------------------------------------------------------------------------

class PreviewCard(QWidget):
    """Shows a live colour preview of the active theme tokens."""

    def __init__(self):
        super().__init__()
        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.setContentsMargins(16, 16, 16, 16)

        root.addWidget(_label("Theme Preview", bold=True))
        root.addWidget(_divider())

        self._swatch_row = QHBoxLayout()
        self._swatches: list[QFrame] = []
        for _ in range(6):
            swatch = QFrame()
            swatch.setFixedSize(32, 32)
            swatch.setFrameShape(QFrame.StyledPanel)
            self._swatch_row.addWidget(swatch)
            self._swatches.append(swatch)
        self._swatch_row.addStretch()
        root.addLayout(self._swatch_row)

        self._preview_label = _label("")
        root.addWidget(self._preview_label)
        root.addStretch()

        self.refresh()

    @registry.reactive
    def refresh(self):
        bg        = theme.get("bg.surface")
        fg        = theme.get("fg")
        border    = theme.get("border")
        colors = [
            theme.get("bg"),
            theme.get("bg.surface"),
            theme.get("bg.overlay"),
            theme.get("accent"),
            theme.get("accent.alt"),
            theme.get("danger"),
        ]

        self.setStyleSheet(f"""
            QWidget {{ background: {bg}; border-radius: 6px; }}
            QLabel  {{ background: transparent; color: {fg}; }}
            QFrame[frameShape="4"] {{ color: {border}; }}
        """)

        labels = ["bg", "surface", "overlay", "accent", "alt", "danger"]
        for swatch, color, label in zip(self._swatches, colors, labels):
            swatch.setStyleSheet(
                f"background: {color}; border: 1px solid {border}; border-radius: 4px;"
            )
            swatch.setToolTip(f"{label}: {color}")

        self._preview_label.setText(
            "  ".join(f"{l}: {c}" for l, c in zip(labels, colors))
        )


# ---------------------------------------------------------------------------
# GreetingWidget — reacts to username and theme only
# ---------------------------------------------------------------------------

class GreetingWidget(QWidget):
    """A simple widget that reacts only to username and theme tokens."""

    def __init__(self):
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        self._label = QLabel()
        self._label.setAlignment(Qt.AlignCenter)
        font = self._label.font()
        font.setPointSize(font.pointSize() + 4)
        font.setWeight(QFont.Bold)
        self._label.setFont(font)
        root.addWidget(self._label)
        self.refresh()

    @registry.reactive
    def refresh(self):
        bg     = theme.get("bg.surface")
        fg     = theme.get("fg")
        accent = theme.get("accent")
        border = theme.get("border")
        name   = settings.get("username")
        notif  = settings.get("notifications")

        self.setStyleSheet(f"""
            QWidget {{ background: {bg}; border-radius: 6px; }}
            QLabel  {{ background: transparent; color: {accent}; }}
        """)

        notif_str = " 🔔" if notif else ""
        self._label.setText(f"Hello, {name}{notif_str}")


# ---------------------------------------------------------------------------
# MainWindow
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Registry Demo")
        self.setMinimumSize(820, 520)

        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setSpacing(16)
        outer.setContentsMargins(20, 20, 20, 20)

        # Top greeting — spans full width
        self._greeting = GreetingWidget()
        self._greeting.setFixedHeight(72)
        outer.addWidget(self._greeting)

        # Middle row — controls on the left, cards on the right
        middle = QHBoxLayout()
        middle.setSpacing(16)

        self._controls = ControlPanel()
        self._controls.setFixedWidth(240)
        middle.addWidget(self._controls)

        right = QVBoxLayout()
        right.setSpacing(16)
        self._status  = StatusCard()
        self._preview = PreviewCard()
        right.addWidget(self._status)
        right.addWidget(self._preview)
        middle.addLayout(right)

        outer.addLayout(middle)

        self._apply_window_bg()
        theme.theme_changed.connect(lambda *_: self._apply_window_bg())

    def _apply_window_bg(self):
        bg = theme.get("bg")
        self.setStyleSheet(f"QMainWindow {{ background: {bg}; }}")
        self.centralWidget().setStyleSheet(f"QWidget {{ background: {bg}; }}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())