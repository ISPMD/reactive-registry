"""
Registry.Registry
-----------------
Defines the Registry class, which owns a SettingsModel and a ThemeStore and
exposes @registry.reactive as a single decorator that works across both.

Not imported directly — use `from Registry import settings, theme, registry`.
"""

from .Settings import SettingsModel
from .Theme import ThemeStore
from .Reactive import ReactiveDescriptor


class Registry:
    """Owns a SettingsModel and a ThemeStore and exposes @registry.reactive.

    Instantiated once at module level in __init__.py as the `registry`
    singleton. There is rarely a reason to instantiate Registry more than once."""

    def __init__(self):
        self.settings = SettingsModel()
        self.theme = ThemeStore()

    def reactive(self, fn):
        """Decorator that auto-wires a method to every store key it reads.

        Wrap any instance method that calls settings.get() or theme.get(). On
        the first call to the method on a given instance, all .get() accesses
        are recorded across both stores. A Qt signal connection is created per
        unique (store, key) pair so the method re-runs automatically on the
        same instance whenever any of those values change — whether triggered
        by a settings.set() call or a full theme/mode switch.

        Connections are cleaned up automatically via weakref finalizer when the
        instance is garbage collected — no manual disconnection is needed.

        Constraint: every key that should trigger re-runs must be read
        unconditionally on the first call. A key only reached inside a branch
        that does not execute on the first call will not be tracked."""
        return ReactiveDescriptor(fn, stores=[self.settings, self.theme])
