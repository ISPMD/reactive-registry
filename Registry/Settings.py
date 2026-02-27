"""
Registry.Settings
-----------------
A key/value store whose changes are observable via Qt signals.
Part of the Registry package — use `from Registry import registry` rather
than importing this module directly.

    registry.settings.get("volume")
    registry.settings.set("volume", 80)

To auto-wire a method so it re-runs whenever a setting it reads changes,
use @registry.reactive (defined in Registry.__init__).

Public API
----------
Construction (done once inside Registry.__init__):
    settings = SettingsModel(defaults={"volume": 50, "muted": False})
    # None is not a valid value — ValueError raised if any default is None.

Reading:
    settings.get("volume")            # current value, or None if not set
    settings.get("volume", default=0) # current value, or 0 if not set

Writing:
    settings.set("volume", 80)        # emits signals if value changed
                                      # raises TypeError if value is None

Bulk operations:
    settings.as_dict()                # shallow snapshot of all values
    settings.load_dict({"volume": 80, "muted": True})
                                      # calls set() per key; signals fire
                                      # per changed key; None still rejected

Subscribing to changes:
    settings.on("volume").connect(cb) # cb(new_value) — fires for this key only
    settings.changed.connect(cb)      # cb(key, value) — fires for every change

Signals:
    changed(key: str, value: Any)
        Emitted after every successful set(). Always fires after the per-key
        .on(key) signal so per-key listeners run before global ones.

None policy:
    None is intentionally not a valid value. It cannot be distinguished from
    "key not present" when returned by get(). Use a sentinel (e.g. "") or
    simply omit the key to represent absence.
"""

from typing import Any
from PySide6.QtCore import QObject, Signal

from .Reactive import _KeySignalEmitter, _record, _values_equal


class SettingsModel(QObject):
    """A key/value store whose changes are observable via Qt signals.

    None is not a valid value — set() raises TypeError, __init__() raises
    ValueError if any default is None. Use a sentinel or omit the key instead."""

    # Emitted after every successful set(): (key, new_value).
    # Per-key .on(key) signal fires first; .changed fires second.
    changed = Signal(str, object)

    def __init__(self, defaults: dict[str, Any] | None = None, parent=None):
        super().__init__(parent)

        self._store: dict[str, Any] = {}
        # One _KeySignalEmitter per key, created on demand in on().
        # Stored here so Reactive.disconnect_conns can reach them via
        # store._key_signals.
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
        @registry.reactive first call, the key is registered as a dependency
        of that method on this store."""
        _record(self, key)
        return self._store.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Set key to value.

        Emits .on(key) then .changed only when the value actually changes.
        No-ops silently if the new value equals the stored one.
        Raises TypeError if value is None.

        Thread safety: call only from the Qt main thread. Background-thread
        calls risk dict corruption (the GIL is not sufficient for concurrent
        reads and writes) and unreliable signal delivery (Qt cross-thread
        signal queuing only works correctly inside a running event loop on a
        thread that does not own this QObject). To update a setting from a
        worker thread, post to the main thread via QMetaObject.invokeMethod
        or a queued signal connection instead."""
        if value is None:
            raise TypeError(
                f"None is not a valid settings value (key: {key!r}). "
                "Use a sentinel value or omit the key instead."
            )
        if _values_equal(self._store.get(key), value):
            return
        self._store[key] = value
        emitter = self._key_signals.get(key)
        if emitter:
            emitter.sig.emit(value)   # per-key listeners first
        self.changed.emit(key, value) # global listeners second

    def on(self, key: str) -> Signal:
        """Return the per-key signal for key, creating its emitter on demand.

        Emits (new_value,) whenever that key is updated to a different value.
        Prefer this over .changed when you only care about one specific key.
        Also used internally by ReactiveDescriptor to wire connections."""
        if key not in self._key_signals:
            self._key_signals[key] = _KeySignalEmitter(self)
        return self._key_signals[key].sig

    def as_dict(self) -> dict[str, Any]:
        """Return a shallow snapshot of all current key/value pairs."""
        return dict(self._store)

    def load_dict(self, data: dict[str, Any]) -> None:
        """Bulk-load from a dict. Delegates to set() for each key so signals
        fire normally for changed keys and None values are still rejected."""
        for key, value in data.items():
            self.set(key, value)
