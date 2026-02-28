"""
Registry.Registry
-----------------
Defines the Registry class, which owns a SettingsModel, a ThemeStore, and a
TranslationStore and exposes @registry.reactive and @registry.reactive_class
as decorators that work across all three stores.

Not imported directly — use `from Registry import settings, theme, translations, registry`.
"""

from .Settings import SettingsModel
from .Theme import ThemeStore
from .Translation import TranslationStore
from .Reactive import ReactiveDescriptor, reactive_class_decorator


class Registry:
    """Owns a SettingsModel, a ThemeStore, and a TranslationStore and exposes
    the reactive decorators.

    Instantiated once at module level in __init__.py as the `registry`
    singleton. There is rarely a reason to instantiate Registry more than once;
    doing so creates a fully isolated, independent set of stores."""

    def __init__(self):
        self.settings = SettingsModel()
        self.theme = ThemeStore()
        self.translations = TranslationStore()

    def reactive(self, fn):
        """Decorator that auto-wires a method to every store key it reads.

        Apply to any instance method that calls settings.get(), theme.get(),
        or translations.get(). On the first call to the method on a given
        instance, all .get() accesses are recorded across all three stores.

        For settings and theme, one Qt signal connection is created per unique
        (store, key) pair. For translations, a single connection is wired per
        method regardless of how many translation keys it reads — the entire
        language pack is treated as one dependency.

        Connections are cleaned up automatically via a weakref finalizer when
        the instance is garbage collected — no manual disconnection is needed.

        Constraint: every key that should trigger re-runs must be read
        unconditionally on the first call. A key only reached inside a branch
        that does not execute on the first call will not be tracked and will
        never cause a re-run.

        See ReactiveDescriptor in Reactive.py for the full implementation.
        """
        return ReactiveDescriptor(fn, stores=[self.settings, self.theme, self.translations])

    @staticmethod
    def reactive_class(cls):
        """Class decorator that opts a class into class-level reactive tracking.

        Apply above any class that has @registry.reactive methods. All such
        methods switch from per-instance tracking to class-level tracking:

        - The first instance to call a reactive method triggers dependency
          tracking and wires one Qt signal connection per (store, key) pair
          for the entire class.
        - Every subsequent instance joins the shared WeakSet and runs the
          method directly — no new connections are created.
        - When a tracked key changes, one Qt dispatch calls the method on
          every currently living instance.

        This is more efficient than per-instance tracking when many identical
        widget instances exist (e.g. list rows, status badges), because the
        number of Qt connections stays constant regardless of instance count.

        Constraint: all instances must read the same keys unconditionally.
        Keys only read by a later instance will never be tracked.

        Usage::

            @registry.reactive_class
            class StatusLabel(QLabel):

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
        """
        return reactive_class_decorator(cls)