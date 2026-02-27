"""
Registry.Reactive
-----------------
Low-level reactivity machinery shared by all stores in the Registry package.
Not intended to be imported directly — use `from Registry import registry`.

How the tracking pipeline works
--------------------------------
1. @registry.reactive wraps a method in a ReactiveDescriptor.
2. The first time that method is called on an instance, ReactiveDescriptor
   opens a tracking context (_push_tracking) before invoking the method.
3. Every store.get() call invokes _record(store, key), appending a (store, key)
   pair to the active context. Because each store passes itself, reads from
   registry.settings and registry.theme are collected in the same list without
   colliding.
4. After the method returns, ReactiveDescriptor closes the tracking context
   (_pop_tracking), deduplicates the collected pairs, and wires one Qt signal
   connection per unique (store, key) pair. Each connection re-runs the method
   on the same instance whenever that key changes in that store.
5. Subsequent calls to the method skip tracking entirely and run it directly.
   Connections remain active until the instance is garbage collected, at which
   point a weakref finalizer calls disconnect_conns to clean them up.

Tracking uses a per-thread stack (not a single slot) so that nested reactive
calls each collect their own dependencies without interfering with each other.

Shared internals exported to store modules
------------------------------------------
    _KeySignalEmitter      — minimal QObject hosting one per-key signal;
                             shared by SettingsModel and ThemeStore to avoid
                             duplicating the same class in both files
    _record(store, key)    — append (store, key) to the active tracking context;
                             no-op outside a tracking context
    _values_equal(a, b)    — equality that never raises and handles objects
                             whose == returns non-bool (e.g. numpy arrays)
    ReactiveDescriptor     — non-data descriptor returned by @registry.reactive
    disconnect_conns       — module-level finalizer called by weakref.finalize
"""

import threading
import weakref
from typing import Any
from functools import wraps, partial

from PySide6.QtCore import QObject, Signal


# ---------------------------------------------------------------------------
# Shared signal emitter
# ---------------------------------------------------------------------------

class _KeySignalEmitter(QObject):
    """Minimal QObject that hosts a single per-key signal.

    Qt signals must be class-level attributes on a QObject subclass and cannot
    be attached to a store dynamically, so one emitter is created per key on
    demand and parented to the store. Parenting ensures it is destroyed
    automatically when the store is destroyed.

    Defined here (not in each store module) so both SettingsModel and
    ThemeStore share one implementation with no duplication."""

    sig = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)


# ---------------------------------------------------------------------------
# Per-thread tracking stack
# ---------------------------------------------------------------------------
# A list-of-lists, one inner list per active reactive call on this thread.
# Using a stack (rather than a single slot) means nested reactive calls each
# accumulate their own (store, key) pairs without overwriting each other.

_TL = threading.local()


def _push_tracking() -> None:
    """Open a new tracking context by pushing an empty dep-list onto the stack."""
    try:
        stack = _TL.stack
    except AttributeError:
        _TL.stack = stack = []
    stack.append([])


def _pop_tracking() -> list:
    """Close the innermost tracking context and return its collected deps."""
    return _TL.stack.pop()


def _record(store, key: str) -> None:
    """Append (store, key) to the innermost active tracking context.

    Called by every store's .get() method. Silently does nothing when no
    tracking context is active (i.e. outside a @registry.reactive first call).
    Passing `store` as the first argument lets ReactiveDescriptor later wire
    each connection to the correct store."""
    stack = _TL.__dict__.get("stack")
    if stack:
        stack[-1].append((store, key))


# ---------------------------------------------------------------------------
# Safe equality
# ---------------------------------------------------------------------------

def _values_equal(a: Any, b: Any) -> bool:
    """Return True if a and b should be considered equal, without ever raising.

    Identity check first (fastest path — same object is always equal).
    Falls back to ==, guarding against two known failure modes:
      - == raising an exception (e.g. comparing a string to a QColor)
      - == returning a non-bool (e.g. numpy arrays return an element-wise array)
    In either case, conservatively returns False so the update goes through
    and downstream listeners are notified."""
    if a is b:
        return True
    try:
        result = a == b
        if isinstance(result, bool):
            return result
        return bool(result)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# ReactiveDescriptor
# ---------------------------------------------------------------------------

class ReactiveDescriptor:
    """Non-data descriptor returned by @registry.reactive.

    Lifecycle per instance
    ----------------------
    First call:
        Runs the wrapped method inside a tracking context. Every store.get()
        call during that run appends a (store, key) pair. After the method
        returns, one Qt signal connection is created per unique (store, key)
        pair. The handler re-runs the method on the same instance whenever
        that key changes in that store.
        A weakref finalizer is registered to disconnect all signals when the
        instance is garbage collected — no manual cleanup is needed.

    Subsequent calls:
        Tracking is skipped. The method runs directly. Connections wired on
        the first call remain active and continue to trigger re-runs.

    Wired vs unwired state
    ----------------------
    _wired (WeakSet) tracks which instances have been through the first call.
    This is explicit and unambiguous — no empty-list sentinel needed.
    _inst_conns (WeakKeyDictionary) stores the connection list per instance
    so disconnect_conns can clean up the right handlers on GC.

    Reference cycle prevention
    --------------------------
    Signal handlers hold a weakref to the instance rather than a strong
    reference. A strong reference would create a cycle:
        obj → _inst_conns (value) → conns → handler → obj
    which would prevent GC even though _inst_conns uses weak keys (weak keys
    only weaken the key reference, not the values stored against it). With a
    weakref in the handler, the cycle is broken and the weakref finalizer can
    fire, disconnecting all signals cleanly.

    Constraint
    ----------
    All keys that should trigger re-runs must be read unconditionally on the
    first call. A key accessed only inside a branch that does not execute on
    the first call will never be tracked and will never cause a re-run.
    """

    def __init__(self, fn, stores: list) -> None:
        self._fn = fn
        # _stores is not iterated at runtime — tracking collects only stores
        # that were actually accessed during the first call. Retained for
        # introspection (e.g. to inspect which stores a descriptor covers).
        self._stores = stores
        self._wired: weakref.WeakSet = weakref.WeakSet()
        self._inst_conns: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
        wraps(fn)(self)

    def __get__(self, obj, objtype=None):
        # Descriptor protocol: return self when accessed on the class,
        # return a bound callable when accessed on an instance.
        if obj is None:
            return self
        return partial(self._call, obj)

    def _call(self, obj):
        if obj in self._wired:
            # Already wired from the first call — just run the method.
            # All signal connections remain active; no re-tracking needed.
            self._fn(obj)
            return

        # --- First call ---
        # Run the method inside a tracking context. The per-thread stack keeps
        # this safe even if a reactive method calls another reactive method.
        _push_tracking()
        try:
            self._fn(obj)
            raw_deps = _pop_tracking()
        except Exception:
            _pop_tracking()
            raise

        # Wire one connection per unique (store, key) pair. Deduplication
        # ensures a key read multiple times produces only one connection.
        conns: list = []
        seen: set = set()
        # Hold a weak reference to obj in the handler rather than a strong one.
        # A strong reference (o=obj) would create a cycle:
        #   obj → _inst_conns → conns → handler → obj
        # which prevents GC even though _inst_conns is a WeakKeyDictionary
        # (the WeakKeyDictionary only weakly references the key, not the value).
        # With a weakref, the handler becomes a no-op once the instance is gone,
        # and the weakref finalizer can then run and disconnect all signals.
        obj_ref = weakref.ref(obj)
        for store, key in raw_deps:
            pair = (id(store), key)
            if pair in seen:
                continue
            seen.add(pair)
            def handler(_value, _ref=obj_ref, _fn=self._fn):
                o = _ref()
                if o is not None:
                    _fn(o)
            store.on(key).connect(handler)
            conns.append((store, key, handler))

        self._wired.add(obj)
        self._inst_conns[obj] = conns

        # Register a weakref finalizer to disconnect signals when the instance
        # is GC'd. We pass list(conns) — a snapshot — because by the time the
        # finalizer fires, _inst_conns[obj] may already be gone.
        # Only register when at least one key was tracked; a reactive method
        # that reads no store keys has nothing to disconnect.
        if conns:
            weakref.finalize(obj, disconnect_conns, list(conns))


# ---------------------------------------------------------------------------
# Finalizer helper — must be module-level
# ---------------------------------------------------------------------------

def disconnect_conns(conns: list) -> None:
    """Disconnect every (store, key, handler) triple from its per-key signal.

    Must be a plain module-level function rather than a method so that
    weakref.finalize can hold a reference to it without indirectly keeping
    the watched instance alive.

    The RuntimeError guard handles the edge case where a _KeySignalEmitter was
    already destroyed before the finalizer ran (e.g. the store was torn down
    before the widget that observed it)."""
    for store, key, handler in conns:
        emitter = store._key_signals.get(key)
        if emitter:
            try:
                emitter.sig.disconnect(handler)
            except RuntimeError:
                pass
    conns.clear()