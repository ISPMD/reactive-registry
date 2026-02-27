"""
Registry
--------
Single entry point for the reactive store system. Import stores and the
registry singleton directly — do not import Settings, Theme, or Reactive
individually.

    from Registry import settings, theme, registry

Stores
------
    settings   — SettingsModel: arbitrary key/value pairs
    theme      — ThemeStore: design tokens with dark/light variants

Reading values
--------------
Each store exposes its own .get() tuned to its internals:

    settings.get("volume")             # current setting value
    theme.get("color.background")      # active theme token

Reactive methods
----------------
@registry.reactive is a single decorator that works across both stores. Apply
it to any instance method that reads from settings or theme. On the first call,
every .get() access is recorded as a dependency. After the method returns, one
Qt signal connection is wired per unique (store, key) pair so the method
re-runs automatically whenever any of those values change — whether from a
settings.set() or a full theme/mode switch.

    from Registry import settings, theme, registry

    class MyWidget(QWidget):

        @registry.reactive
        def refresh(self):
            # Read from either store freely — all reads are tracked together.
            # IMPORTANT: access every key you want to track unconditionally.
            # A key inside a branch that does not execute on the first call
            # will not be tracked and will not trigger re-runs.
            vol = settings.get("volume")
            bg  = theme.get("color.background")
            fg  = theme.get("color.text")
            self.setStyleSheet(f"background:{bg}; color:{fg};")
            self.slider.setValue(vol)

        def __init__(self):
            super().__init__()
            self.refresh()  # first call — tracks keys, wires all connections
                            # connections disconnect automatically on GC,
                            # no manual cleanup needed

Exports
-------
    settings  — SettingsModel singleton instance
    theme     — ThemeStore singleton instance
    registry  — Registry singleton (needed for @registry.reactive)
"""

from .Registry import Registry

__all__ = ["settings", "theme", "registry"]

# Pre-made singleton. Import and use directly — do not instantiate Registry
# yourself unless you intentionally want an isolated second store pair.
registry = Registry()

# Convenience aliases so stores can be imported without going through registry:
#   from Registry import settings, theme
settings = registry.settings
theme = registry.theme