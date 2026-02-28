"""
Registry Demo
=============
Shows @registry.reactive (per-instance) and @registry.reactive_class
(class-level) side by side in a small PySide6 app, now with translations.

Left  — controls: volume slider, brightness slider, dark/light toggle,
         language selector (EN / ES / FR / RO).
Right — two rows of reactive widgets:
          Row 1: PlayerCard  — uses @registry.reactive  (per-instance)
          Row 2: StatusBadge — uses @registry.reactive_class (class-level)
Top   — Settings button opens a dialog to pick a theme and flip mode.
         Save button writes registry state to demo_state.json.
         State is auto-loaded from demo_state.json on startup if it exists.

Run:
    python demo.py
"""

import sys
from pathlib import Path
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget,
    QVBoxLayout, QHBoxLayout, QLabel, QSlider,
    QPushButton, QDialog, QFrame, QButtonGroup, QRadioButton,
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont

from Registry import settings, theme, translations, registry
from Registry.Persistence import save, load


# ---------------------------------------------------------------------------
# Persistence path — sits next to demo.py
# ---------------------------------------------------------------------------

_STATE_PATH = Path(__file__).with_name("demo_state.json")


# ---------------------------------------------------------------------------
# Theme definitions
# ---------------------------------------------------------------------------

theme.register("Slate", {
    "dark": {
        "color.bg":      "#0f172a",
        "color.surface": "#1e293b",
        "color.border":  "#475569",
        "color.text":    "#f8fafc",
        "color.subtext": "#e2e8f0",
        "color.accent":  "#38bdf8",
    },
    "light": {
        "color.bg":      "#f8fafc",
        "color.surface": "#e2e8f0",
        "color.border":  "#94a3b8",
        "color.text":    "#0f172a",
        "color.subtext": "#1e293b",
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
        "color.subtext": "#14070a",
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
        "color.subtext": "#06140d",
        "color.accent":  "#1f7a46",
    },
})


# ---------------------------------------------------------------------------
# Translation definitions
# ---------------------------------------------------------------------------

translations.register("en", {
    "controls.heading":        "Controls",
    "controls.volume":         "volume",
    "controls.brightness":     "brightness",
    "controls.mode.to_light":  "→ light mode",
    "controls.mode.to_dark":   "→ dark mode",
    "controls.language":       "language",
    "card.vol":                "vol",
    "card.bri":                "bri",
    "badge.theme":             "theme",
    "badge.mode":              "mode",
    "section.per_instance":    "@registry.reactive  —  per-instance",
    "section.per_instance.note":
        "Each PlayerCard independently tracks its own deps. "
        "3 cards × 8 keys = 24 signal connections total.",
    "section.class_level":     "@registry.reactive_class  —  class-level",
    "section.class_level.note":
        "All StatusBadges share one set of connections for the whole class. "
        "3 badges or 300 — still 6 signal connections total.",
    "window.title":            "Reactive Widgets",
    "button.settings":         "settings",
    "button.save":             "save",
    "status.saved":            "saved ✓",
    "status.loaded":           "loaded ✓",
    "status.save_err":         "save failed ✗",
    "status.load_err":         "load failed ✗",
    "dialog.title":            "Settings",
    "dialog.theme":            "Theme",
    "dialog.mode":             "Mode",
    "dialog.mode.dark":        "Dark",
    "dialog.mode.light":       "Light",
    "dialog.done":             "Done",
    "dialog.language":         "Language",
})

translations.register("es", {
    "controls.heading":        "Controles",
    "controls.volume":         "volumen",
    "controls.brightness":     "brillo",
    "controls.mode.to_light":  "→ modo claro",
    "controls.mode.to_dark":   "→ modo oscuro",
    "controls.language":       "idioma",
    "card.vol":                "vol",
    "card.bri":                "bri",
    "badge.theme":             "tema",
    "badge.mode":              "modo",
    "section.per_instance":    "@registry.reactive  —  por instancia",
    "section.per_instance.note":
        "Cada PlayerCard rastrea sus propias dependencias. "
        "3 tarjetas × 8 claves = 24 conexiones en total.",
    "section.class_level":     "@registry.reactive_class  —  nivel de clase",
    "section.class_level.note":
        "Todos los StatusBadge comparten una sola conexión por clase. "
        "3 badges o 300 — solo 6 conexiones en total.",
    "window.title":            "Widgets Reactivos",
    "button.settings":         "ajustes",
    "button.save":             "guardar",
    "status.saved":            "guardado ✓",
    "status.loaded":           "cargado ✓",
    "status.save_err":         "error al guardar ✗",
    "status.load_err":         "error al cargar ✗",
    "dialog.title":            "Ajustes",
    "dialog.theme":            "Tema",
    "dialog.mode":             "Modo",
    "dialog.mode.dark":        "Oscuro",
    "dialog.mode.light":       "Claro",
    "dialog.done":             "Listo",
    "dialog.language":         "Idioma",
})

translations.register("ro", {
    "controls.heading":        "Controale",
    "controls.volume":         "volum",
    "controls.brightness":     "luminozitate",
    "controls.mode.to_light":  "→ mod deschis",
    "controls.mode.to_dark":   "→ mod întunecat",
    "controls.language":       "limbă",
    "card.vol":                "vol",
    "card.bri":                "lum",
    "badge.theme":             "temă",
    "badge.mode":              "mod",
    "section.per_instance":    "@registry.reactive  —  per instanta",
    "section.per_instance.note":
        "Fiecare PlayerCard urmareste propriile dependinte. "
        "3 carduri × 8 chei = 24 conexiuni în total.",
    "section.class_level":     "@registry.reactive_class  —  nivel clasa",
    "section.class_level.note":
        "Toate StatusBadge-urile impart un singur set de conexiuni. "
        "3 sau 300 de badge-uri — tot 6 conexiuni în total.",
    "window.title":            "Widget-uri Reactive",
    "button.settings":         "setări",
    "button.save":             "salvează",
    "status.saved":            "salvat ✓",
    "status.loaded":           "încărcat ✓",
    "status.save_err":         "eroare la salvare ✗",
    "status.load_err":         "eroare la încărcare ✗",
    "dialog.title":            "Setări",
    "dialog.theme":            "Temă",
    "dialog.mode":             "Mod",
    "dialog.mode.dark":        "Întunecat",
    "dialog.mode.light":       "Deschis",
    "dialog.done":             "Gata",
    "dialog.language":         "Limbă",
})

translations.register("fr", {
    "controls.heading":        "Contrôles",
    "controls.volume":         "volume",
    "controls.brightness":     "luminosité",
    "controls.mode.to_light":  "→ mode clair",
    "controls.mode.to_dark":   "→ mode sombre",
    "controls.language":       "langue",
    "card.vol":                "vol",
    "card.bri":                "lum",
    "badge.theme":             "thème",
    "badge.mode":              "mode",
    "section.per_instance":    "@registry.reactive  —  par instance",
    "section.per_instance.note":
        "Chaque PlayerCard suit ses propres dépendances. "
        "3 cartes × 8 clés = 24 connexions au total.",
    "section.class_level":     "@registry.reactive_class  —  niveau classe",
    "section.class_level.note":
        "Tous les StatusBadge partagent un seul ensemble de connexions. "
        "3 badges ou 300 — seulement 6 connexions au total.",
    "window.title":            "Widgets Réactifs",
    "button.settings":         "réglages",
    "button.save":             "sauvegarder",
    "status.saved":            "sauvegardé ✓",
    "status.loaded":           "chargé ✓",
    "status.save_err":         "échec sauvegarde ✗",
    "status.load_err":         "échec chargement ✗",
    "dialog.title":            "Réglages",
    "dialog.theme":            "Thème",
    "dialog.mode":             "Mode",
    "dialog.mode.dark":        "Sombre",
    "dialog.mode.light":       "Clair",
    "dialog.done":             "Terminer",
    "dialog.language":         "Langue",
})


# ---------------------------------------------------------------------------
# Seed defaults — used only when no saved state exists
# ---------------------------------------------------------------------------

def _seed_defaults():
    settings.set("volume",     72)
    settings.set("brightness", 80)
    theme.set_theme("Slate")
    theme.set_mode("dark")
    translations.set_language("en")


# Auto-load saved state; fall back to defaults if file is absent or broken.
_load_result = load(_STATE_PATH)
if _load_result.ok:
    # Translations must be seeded even when loading, because load() only
    # restores packs/languages that were present at save time — the four
    # language packs above are registered fresh every run regardless.
    # The active language was already restored by load(); nothing extra needed.
    pass
else:
    # File missing or unreadable — seed defaults so the app starts cleanly.
    _seed_defaults()


# ---------------------------------------------------------------------------
# SettingsDialog
# ---------------------------------------------------------------------------

class SettingsDialog(QDialog):
    """Theme picker and dark / light mode toggle."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(300)

        root = QVBoxLayout(self)
        root.setSpacing(16)
        root.setContentsMargins(24, 24, 24, 24)

        self._heading_lbl = QLabel()
        self._heading_lbl.setFont(QFont("Courier New", 10, QFont.Bold))
        root.addWidget(self._heading_lbl)

        self._theme_group = QButtonGroup(self)
        for i, name in enumerate(("Slate", "Rose", "Moss")):
            rb = QRadioButton(name)
            rb.setFont(QFont("Courier New", 10))
            rb.setChecked(theme.active_theme == name)
            rb.toggled.connect(lambda on, n=name: theme.set_theme(n) if on else None)
            self._theme_group.addButton(rb, i)
            root.addWidget(rb)

        root.addWidget(self._separator())

        self._mode_heading_lbl = QLabel()
        self._mode_heading_lbl.setFont(QFont("Courier New", 10, QFont.Bold))
        root.addWidget(self._mode_heading_lbl)

        self._mode_group = QButtonGroup(self)
        self._dark_rb  = QRadioButton()
        self._light_rb = QRadioButton()
        for i, (rb, mode_name) in enumerate([(self._dark_rb, "dark"), (self._light_rb, "light")]):
            rb.setFont(QFont("Courier New", 10))
            rb.setChecked(theme.active_mode == mode_name)
            rb.toggled.connect(lambda on, m=mode_name: theme.set_mode(m) if on else None)
            self._mode_group.addButton(rb, i)
            root.addWidget(rb)

        root.addStretch()

        root.addWidget(self._separator())

        self._lang_heading_lbl = QLabel()
        self._lang_heading_lbl.setFont(QFont("Courier New", 10, QFont.Bold))
        root.addWidget(self._lang_heading_lbl)

        self._lang_group = QButtonGroup(self)
        self._lang_rbs: dict[str, QRadioButton] = {}
        for i, (code, label) in enumerate([("en", "EN"), ("es", "ES"), ("fr", "FR"), ("ro", "RO")]):
            rb = QRadioButton(label)
            rb.setFont(QFont("Courier New", 10))
            rb.setChecked(translations.active_language == code)
            rb.toggled.connect(lambda on, c=code: translations.set_language(c) if on else None)
            self._lang_group.addButton(rb, i)
            self._lang_rbs[code] = rb
            root.addWidget(rb)

        root.addStretch()

        self._done_btn = QPushButton()
        self._done_btn.setFont(QFont("Courier New", 10))
        self._done_btn.setCursor(Qt.PointingHandCursor)
        self._done_btn.clicked.connect(self.accept)
        root.addWidget(self._done_btn)

        self.apply_style()

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

        self.setWindowTitle(translations.get("dialog.title"))
        self._heading_lbl.setText(translations.get("dialog.theme"))
        self._mode_heading_lbl.setText(translations.get("dialog.mode"))
        self._dark_rb.setText(translations.get("dialog.mode.dark"))
        self._light_rb.setText(translations.get("dialog.mode.light"))
        self._lang_heading_lbl.setText(translations.get("dialog.language"))
        self._done_btn.setText(translations.get("dialog.done"))

        active_lang = translations.active_language
        for code, rb in self._lang_rbs.items():
            rb.blockSignals(True)
            rb.setChecked(code == active_lang)
            rb.blockSignals(False)

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
    """Volume slider, brightness slider, mode toggle, language selector."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(220)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 28, 20, 28)
        root.setSpacing(20)

        self._heading_lbl = QLabel()
        self._heading_lbl.setFont(QFont("Courier New", 13, QFont.Bold))
        root.addWidget(self._heading_lbl)

        root.addWidget(self._separator())

        # Volume
        self._vol_label = QLabel()
        self._vol_label.setFont(QFont("Courier New", 9))
        root.addWidget(self._vol_label)

        self._vol_readout = QLabel(str(settings.get("volume")))
        self._vol_readout.setFont(QFont("Courier New", 10))
        root.addWidget(self._vol_readout)

        self._vol_slider = QSlider(Qt.Horizontal)
        self._vol_slider.setRange(0, 100)
        self._vol_slider.setValue(settings.get("volume"))
        self._vol_slider.valueChanged.connect(self._on_volume)
        root.addWidget(self._vol_slider)

        # Brightness
        self._bri_label = QLabel()
        self._bri_label.setFont(QFont("Courier New", 9))
        root.addWidget(self._bri_label)

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

        root.addWidget(self._separator())

        # Language selector
        self._lang_label = QLabel()
        self._lang_label.setFont(QFont("Courier New", 9))
        root.addWidget(self._lang_label)

        lang_row = QHBoxLayout()
        lang_row.setSpacing(6)
        self._lang_group = QButtonGroup(self)
        self._lang_btns: dict[str, QPushButton] = {}
        for i, (code, label) in enumerate([("en", "EN"), ("es", "ES"), ("fr", "FR"), ("ro", "RO")]):
            btn = QPushButton(label)
            btn.setFont(QFont("Courier New", 9))
            btn.setCursor(Qt.PointingHandCursor)
            btn.setCheckable(True)
            btn.setChecked(translations.active_language == code)
            btn.clicked.connect(lambda _, c=code: translations.set_language(c))
            self._lang_group.addButton(btn, i)
            self._lang_btns[code] = btn
            lang_row.addWidget(btn)
        root.addLayout(lang_row)

        root.addStretch()

        self.apply_style()

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

        self._heading_lbl.setText(translations.get("controls.heading"))
        self._vol_label.setText(translations.get("controls.volume"))
        self._bri_label.setText(translations.get("controls.brightness"))
        self._lang_label.setText(translations.get("controls.language"))

        mode = theme.active_mode
        key = "controls.mode.to_light" if mode == "dark" else "controls.mode.to_dark"
        self._mode_btn.setText(translations.get(key))

        active_lang = translations.active_language
        for code, btn in self._lang_btns.items():
            btn.setChecked(code == active_lang)

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
            QPushButton:checked {{
                background:{accent}; color:{bg};
                border-color:{accent};
            }}
        """)


# ---------------------------------------------------------------------------
# PlayerCard  — @registry.reactive  (per-instance tracking)
# ---------------------------------------------------------------------------

class PlayerCard(QWidget):
    """Shows a channel name, volume bar, and brightness bar."""

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

        self.refresh()

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

        vol_label = translations.get("card.vol")
        bri_label = translations.get("card.bri")

        self._vol_lbl.setText(f"{vol_label}  {vol:>3}%")
        self._bri_lbl.setText(f"{bri_label}  {bri:>3}%")

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
# ---------------------------------------------------------------------------

@registry.reactive_class
class StatusBadge(QWidget):
    """Shows the active theme name, mode, and accent swatch."""

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

        self.refresh()

    @registry.reactive
    def refresh(self):
        bg      = theme.get("color.bg")
        surface = theme.get("color.surface")
        border  = theme.get("color.border")
        text    = theme.get("color.text")
        subtext = theme.get("color.subtext")
        accent  = theme.get("color.accent")

        theme_label = translations.get("badge.theme")
        mode_label  = translations.get("badge.mode")

        self._theme_lbl.setText(f"{theme_label}  {theme.active_theme}")
        self._mode_lbl.setText(f"{mode_label}   {theme.active_mode}")

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
        self.setMinimumSize(860, 500)

        # Timer used to clear the status label 2 s after a save/load.
        self._status_timer = QTimer(self)
        self._status_timer.setSingleShot(True)
        self._status_timer.timeout.connect(self._clear_status)

        root_w = QWidget()
        self.setCentralWidget(root_w)
        root_h = QHBoxLayout(root_w)
        root_h.setContentsMargins(0, 0, 0, 0)
        root_h.setSpacing(0)

        self.controls = ControlPanel()
        root_h.addWidget(self.controls)

        div = QFrame()
        div.setFrameShape(QFrame.VLine)
        root_h.addWidget(div)

        right_w = QWidget()
        right_v = QVBoxLayout(right_w)
        right_v.setContentsMargins(28, 24, 28, 24)
        right_v.setSpacing(20)
        root_h.addWidget(right_w, stretch=1)

        # Top bar: title | stretch | status label | save btn | settings btn
        top = QHBoxLayout()

        self._title_lbl = QLabel()
        self._title_lbl.setFont(QFont("Courier New", 13, QFont.Bold))
        top.addWidget(self._title_lbl)

        top.addStretch()

        # Status label — shows "saved ✓" / "loaded ✓" / error, then fades.
        self._status_lbl = QLabel()
        self._status_lbl.setFont(QFont("Courier New", 8))
        self._status_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._status_lbl.hide()
        top.addWidget(self._status_lbl)

        self._save_btn = QPushButton()
        self._save_btn.setFont(QFont("Courier New", 9))
        self._save_btn.setCursor(Qt.PointingHandCursor)
        self._save_btn.clicked.connect(self._on_save)
        top.addWidget(self._save_btn)

        self._settings_btn = QPushButton()
        self._settings_btn.setFont(QFont("Courier New", 9))
        self._settings_btn.setCursor(Qt.PointingHandCursor)
        self._settings_btn.clicked.connect(self._open_settings)
        top.addWidget(self._settings_btn)

        right_v.addLayout(top)

        # Per-instance section
        self._pi_tag  = QLabel()
        self._pi_tag.setFont(QFont("Courier New", 8))
        right_v.addWidget(self._pi_tag)

        self._pi_note = QLabel()
        self._pi_note.setFont(QFont("Courier New", 8))
        self._pi_note.setWordWrap(True)
        right_v.addWidget(self._pi_note)

        pi_row = QHBoxLayout()
        pi_row.setSpacing(12)
        pi_row.setAlignment(Qt.AlignLeft)
        self._cards = [PlayerCard(f"ch-{i+1}") for i in range(3)]
        for card in self._cards:
            pi_row.addWidget(card)
        pi_row.addStretch()
        right_v.addLayout(pi_row)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        right_v.addWidget(sep)

        # Class-level section
        self._cl_tag  = QLabel()
        self._cl_tag.setFont(QFont("Courier New", 8))
        right_v.addWidget(self._cl_tag)

        self._cl_note = QLabel()
        self._cl_note.setFont(QFont("Courier New", 8))
        self._cl_note.setWordWrap(True)
        right_v.addWidget(self._cl_note)

        cl_row = QHBoxLayout()
        cl_row.setSpacing(12)
        cl_row.setAlignment(Qt.AlignLeft)
        self._badges = [StatusBadge(f"svc-{i+1}") for i in range(3)]
        for badge in self._badges:
            cl_row.addWidget(badge)
        cl_row.addStretch()
        right_v.addLayout(cl_row)

        right_v.addStretch()

        # Show "loaded ✓" if we successfully restored state on startup.
        if _load_result.ok:
            self._show_status(translations.get("status.loaded"), ok=True)

        self.apply_style()

    # ------------------------------------------------------------------
    # Save / load handlers
    # ------------------------------------------------------------------

    def _on_save(self):
        result = save(_STATE_PATH)
        if result.ok:
            self._show_status(translations.get("status.saved"), ok=True)
        else:
            self._show_status(translations.get("status.save_err"), ok=False)

    def _show_status(self, text: str, *, ok: bool):
        """Display text in the status label and hide it after 2 s."""
        accent = theme.get("color.accent")
        err_color = "#ef4444"  # fixed red — readable on any theme
        color = accent if ok else err_color
        self._status_lbl.setStyleSheet(f"color:{color}; background:transparent;")
        self._status_lbl.setText(text)
        self._status_lbl.show()
        self._status_timer.start(2000)

    def _clear_status(self):
        self._status_lbl.hide()
        self._status_lbl.setText("")

    # ------------------------------------------------------------------
    # Settings dialog
    # ------------------------------------------------------------------

    def _open_settings(self):
        SettingsDialog(self).exec()

    # ------------------------------------------------------------------
    # Reactive style
    # ------------------------------------------------------------------

    @registry.reactive
    def apply_style(self):
        bg      = theme.get("color.bg")
        border  = theme.get("color.border")
        text    = theme.get("color.text")
        subtext = theme.get("color.subtext")
        accent  = theme.get("color.accent")

        self.setWindowTitle(translations.get("window.title"))
        self._title_lbl.setText(translations.get("window.title"))
        self._settings_btn.setText(translations.get("button.settings"))
        self._save_btn.setText(translations.get("button.save"))
        self._pi_tag.setText(translations.get("section.per_instance"))
        self._pi_note.setText(translations.get("section.per_instance.note"))
        self._cl_tag.setText(translations.get("section.class_level"))
        self._cl_note.setText(translations.get("section.class_level.note"))

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