"""
Registry
--------
Single entry point for the reactive store system. Import the stores and the
registry singleton directly — do not import Settings, Theme, Translation, or
Reactive individually from application code.

    from Registry import settings, theme, translations, registry

Stores
------
    settings      — SettingsModel: arbitrary key/value application state
    theme         — ThemeStore: design tokens organised into dark/light themed variants
    translations  — TranslationStore: string translations organised into language packs

Reading values
--------------
Each store exposes a .get() method tuned to its own data model:

    settings.get("volume")             # current setting value, or None
    theme.get("color.background")      # active theme token value, or None
    translations.get("greeting")       # active translation string, or key as fallback
    translations.get("welcome", name="Alice")  # with interpolation

Reactive methods
----------------
@registry.reactive is a single decorator that works across all three stores.
Apply it to any instance method that reads from settings, theme, or
translations. On the first call to the method on a given instance, every
.get() access is recorded as a dependency. After the method returns, signal
connections are wired so the method re-runs automatically whenever any tracked
value changes.

For settings and theme, one connection is wired per unique (store, key) pair.
For translations, one connection is wired per method regardless of how many
keys it reads — the entire language pack is treated as a single dependency.

Connections are cleaned up automatically when the instance is garbage collected.

    from Registry import settings, theme, translations, registry

    class MyWidget(QWidget):

        @registry.reactive
        def refresh(self):
            vol  = settings.get("volume")
            bg   = theme.get("color.background")
            fg   = theme.get("color.text")
            text = translations.get("volume.label")
            self.setStyleSheet(f"background:{bg}; color:{fg};")
            self.label.setText(text)
            self.slider.setValue(vol)

        def __init__(self):
            super().__init__()
            self.refresh()  # first call — tracks deps and wires all connections

Class-level tracking
--------------------
@registry.reactive_class opts an entire class into class-level tracking, which
is more efficient when many instances of the same widget class exist and all
read the same keys. The first instance triggers tracking and wires one Qt
connection per (store, key) pair for the whole class. Every subsequent instance
just joins a WeakSet; one Qt dispatch then calls the method on all living
instances when a tracked key changes.

    @registry.reactive_class
    class StatusBadge(QLabel):

        @registry.reactive
        def refresh(self):
            vol  = settings.get("volume")
            bg   = theme.get("color.background")
            text = translations.get("status.volume")
            self.setText(f"{text}: {vol}")
            self.setStyleSheet(f"background: {bg};")

        def __init__(self):
            super().__init__()
            self.refresh()

Exports
-------
    settings      — SettingsModel singleton instance
    theme         — ThemeStore singleton instance
    translations  — TranslationStore singleton instance
    registry      — Registry singleton (needed for @registry.reactive and
                     @registry.reactive_class)
"""

from .Registry import Registry

__all__ = ["settings", "theme", "translations", "registry"]

# Pre-built singleton. Import and use directly — do not instantiate Registry
# yourself unless you intentionally want an isolated, independent store set.
registry = Registry()

# Convenience aliases so stores can be imported without going through registry:
#   from Registry import settings, theme, translations
settings = registry.settings
theme = registry.theme
translations = registry.translations