"""
Registry.Settings
-----------------
A key/value store whose changes are observable via Qt signals.
Part of the Registry package — use `from Registry import settings` rather
than importing this module directly.

    settings.get("volume")
    settings.set("volume", 80)

To auto-wire a method so it re-runs whenever a setting it reads changes,
use @registry.reactive (defined in Registry.py / __init__.py).

Public API
----------
Construction (done once inside Registry.__init__):
    settings = SettingsModel(defaults={"volume": 50, "muted": False})
    # None is not a valid value — ValueError is raised if any default is None.

Reading:
    settings.get("volume")             # current value, or None if not set
    settings.get("volume", default=0)  # current value, or 0 if not set

Writing:
    settings.set("volume", 80)         # emits signals only if value changed
                                       # raises TypeError if value is None

Bulk operations:
    settings.as_dict()                 # shallow snapshot of all key/value pairs
    settings.load_dict({"volume": 80, "muted": True})
                                       # delegates to set() per key; signals fire
                                       # for each changed key; None still rejected

Subscribing to changes:
    settings.on("volume").connect(cb)  # cb(new_value) — fires for this key only
    settings.changed.connect(cb)       # cb(key, value) — fires for every change

Signal ordering:
    Per-key .on(key) always fires before .changed. This guarantees that
    narrowly-scoped listeners run before broadly-scoped ones.

None policy:
    None is intentionally not a valid value. It is indistinguishable from
    "key not present" when returned by get(). Use a sentinel (e.g. "" or -1)
    or simply omit the key to represent absence.
"""

from typing import Any
from PySide6.QtCore import QObject, Signal

from .Reactive import _KeySignalEmitter, _record, _values_equal


class SettingsModel(QObject):
    """A flat key/value store whose changes are observable via Qt signals.

    None is not a valid value — set() raises TypeError, and __init__() raises
    ValueError if any default is None. Use a sentinel or omit the key instead.

    All mutation must happen on the Qt main thread. See set() for details.
    """

    # Emitted after every successful set(): (key, new_value).
    # The per-key .on(key) signal always fires before this one.
    changed = Signal(str, object)

    def __init__(self, defaults: dict[str, Any] | None = None, parent=None):
        super().__init__(parent)

        self._store: dict[str, Any] = {}

        # One _KeySignalEmitter per key, created on demand in on().
        # Keyed by setting name so Reactive.disconnect_conns can reach a specific
        # emitter via store._key_signals[key] without importing this module.
        self._key_signals: dict[str, _KeySignalEmitter] = {}

        if defaults is not None:
            none_keys = [k for k, v in defaults.items() if v is None]
            if none_keys:
                raise ValueError(
                    f"None is not a valid settings value. "
                    f"Keys with None in defaults: {none_keys}"
                )
            self._store.update(defaults)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        """Return the current value for key, or default if the key is not set.

        Also calls _record(self, key) so that when this runs inside a
        @registry.reactive first call the key is registered as a dependency,
        and a Qt signal connection is wired to re-run the method when it changes.
        Outside a tracking context _record is a no-op.
        """
        _record(self, key)
        return self._store.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Set key to value and emit signals if the value actually changed.

        No-ops silently when the new value equals the stored one, avoiding
        unnecessary signal dispatches and reactive re-runs.

        Raises TypeError if value is None (see None policy in module docstring).

        Signal order: per-key .on(key) fires first, then .changed.

        Thread safety: call only from the Qt main thread. Background threads
        risk dict corruption (the GIL does not protect concurrent reads and
        writes) and unreliable signal delivery (Qt queues cross-thread signals
        only when the receiving thread has a running event loop and owns the
        QObject). Use QMetaObject.invokeMethod or a queued signal connection
        to post updates from worker threads.
        """
        if value is None:
            raise TypeError(
                f"None is not a valid settings value (key: {key!r}). "
                "Use a sentinel value or omit the key instead."
            )
        if _values_equal(self._store.get(key), value):
            return  # value unchanged — skip signals and reactive re-runs

        self._store[key] = value

        # Fire per-key listeners before the global .changed signal so that
        # narrowly-scoped subscribers always run first.
        emitter = self._key_signals.get(key)
        if emitter:
            emitter.sig.emit(value)
        self.changed.emit(key, value)

    def on(self, key: str) -> Signal:
        """Return the per-key signal for key, creating its emitter on demand.

        The emitter is parented to this QObject so it is destroyed automatically
        when the store is destroyed. Emits (new_value,) whenever that key is
        updated to a different value.

        Also used internally by ReactiveDescriptor to wire signal connections
        during the first call to a @registry.reactive method.
        """
        if key not in self._key_signals:
            self._key_signals[key] = _KeySignalEmitter(self)
        return self._key_signals[key].sig

    def as_dict(self) -> dict[str, Any]:
        """Return a shallow snapshot of all current key/value pairs."""
        return dict(self._store)

    def load_dict(self, data: dict[str, Any]) -> None:
        """Bulk-load key/value pairs from a dict.

        Delegates to set() for each key so signals fire normally for changed
        keys and None values are still rejected with TypeError.
        """
        for key, value in data.items():
            self.set(key, value)
