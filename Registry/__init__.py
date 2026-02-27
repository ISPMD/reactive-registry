"""
Registry
--------
Single entry point for the reactive store system. Import the stores and the
registry singleton directly — do not import Settings, Theme, or Reactive
individually from application code.

    from Registry import settings, theme, registry

Stores
------
    settings   — SettingsModel: arbitrary key/value application state
    theme      — ThemeStore: design tokens organised into dark/light themed variants

Reading values
--------------
Each store exposes a .get() method tuned to its own data model:

    settings.get("volume")             # current setting value, or None
    theme.get("color.background")      # active theme token value, or None

Reactive methods
----------------
@registry.reactive is a single decorator that works across both stores. Apply
it to any instance method that reads from settings or theme. On the first call
to the method on a given instance, every .get() access is recorded as a
dependency. After the method returns, one Qt signal connection is wired per
unique (store, key) pair so the method re-runs automatically whenever any of
those values change — whether from a settings.set() or a full theme/mode switch.
Connections are cleaned up automatically when the instance is garbage collected.

    from Registry import settings, theme, registry

    class MyWidget(QWidget):

        @registry.reactive
        def refresh(self):
            # Read from either store freely — all reads are tracked together.
            # IMPORTANT: every key you want to track must be read unconditionally.
            # A key only reached inside a branch that did not execute on the
            # first call will not be tracked and will not trigger re-runs.
            vol = settings.get("volume")
            bg  = theme.get("color.background")
            fg  = theme.get("color.text")
            self.setStyleSheet(f"background:{bg}; color:{fg};")
            self.slider.setValue(vol)

        def __init__(self):
            super().__init__()
            self.refresh()  # first call — tracks keys and wires all connections

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
            vol = settings.get("volume")
            bg  = theme.get("color.background")
            self.setText(str(vol))
            self.setStyleSheet(f"background: {bg};")

        def __init__(self):
            super().__init__()
            self.refresh()

Exports
-------
    settings  — SettingsModel singleton instance
    theme     — ThemeStore singleton instance
    registry  — Registry singleton (needed for @registry.reactive and
                 @registry.reactive_class)
"""

from .Registry import Registry

__all__ = ["settings", "theme", "registry"]

# Pre-built singleton. Import and use directly — do not instantiate Registry
# yourself unless you intentionally want an isolated, independent store pair.
registry = Registry()

# Convenience aliases so stores can be imported without going through registry:
#   from Registry import settings, theme
settings = registry.settings
theme = registry.theme
