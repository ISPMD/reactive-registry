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
   settings and theme are collected in the same list without colliding.
4. After the method returns, ReactiveDescriptor closes the tracking context
   (_pop_tracking), deduplicates the collected pairs, and wires one Qt signal
   connection per unique (store, key) pair. Each connection re-runs the method
   on the same instance whenever that key changes in that store.
5. Subsequent calls to the method skip tracking entirely and run it directly.
   Connections remain active until the instance is garbage collected, at which
   point a weakref finalizer calls disconnect_conns to clean them up.

Tracking uses a per-thread stack (not a single slot) so that nested reactive
calls each collect their own dependencies without interfering with each other.

Two tracking modes
------------------
Per-instance (default):
    Each instance gets its own connections wired on its first call. The handler
    holds a weakref to the instance and is cleaned up by a weakref finalizer
    when the instance is GC'd.

Class-level (@registry.reactive_class on the owning class):
    The first instance ever to call the method triggers tracking and wires one
    Qt signal connection per unique (store, key) pair for the entire class.
    The handler iterates a WeakSet of all living instances and calls the method
    on each — one Qt dispatch regardless of how many instances exist.
    Every subsequent instance simply registers itself into the class WeakSet on
    its first call — no new tracking, no new connections.

Shared internals exported to store modules
------------------------------------------
    _KeySignalEmitter          — minimal QObject hosting one per-key Signal;
                                 shared by SettingsModel and ThemeStore to avoid
                                 duplicating the same boilerplate in both files
    _record(store, key)        — append (store, key) to the active tracking
                                 context; silently no-ops outside a context
    _values_equal(a, b)        — equality that never raises and handles objects
                                 whose == returns a non-bool (e.g. numpy arrays)
    ReactiveDescriptor         — non-data descriptor returned by @registry.reactive
    reactive_class_decorator   — class decorator that opts a class into class-level
                                 tracking; apply via @registry.reactive_class
    disconnect_conns           — module-level finalizer called by weakref.finalize
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
    """Minimal QObject that hosts a single per-key Signal.

    Qt signals must be class-level attributes on a QObject subclass and cannot
    be added to a store dynamically. One emitter is created per key on demand
    and parented to the store, so it is destroyed automatically when the store
    is destroyed.

    Defined here (not in each store module) so both SettingsModel and
    ThemeStore share one implementation without duplication.
    """

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
    """Close the innermost tracking context and return its collected (store, key) deps."""
    return _TL.stack.pop()


def _record(store, key: str) -> None:
    """Append (store, key) to the innermost active tracking context.

    Called by every store's .get() method. Silently does nothing when no
    tracking context is active (i.e. outside a @registry.reactive first call).
    Passing `store` as the first argument lets ReactiveDescriptor wire each
    connection to the correct store after the method returns.
    """
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
    and downstream listeners are notified rather than silently dropped.
    """
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

    Supports two tracking modes, selected by whether the owning class has been
    decorated with @registry.reactive_class.

    Per-instance mode (default)
    ---------------------------
    The first call on each instance runs the method inside a tracking context,
    collects (store, key) deps, and wires one Qt signal connection per unique
    pair. The signal handler re-runs the method on that specific instance.
    A weakref finalizer disconnects all signals when the instance is GC'd.

    Subsequent calls skip tracking and run the method directly.

    Class-level mode (@registry.reactive_class on the owning class)
    ----------------------------------------------------------------
    The first instance ever to call the method triggers tracking and wires one
    Qt signal connection per unique (store, key) pair for the entire class.
    The single handler iterates a WeakSet of all living instances and calls the
    method on each — one Qt dispatch regardless of how many instances exist.

    Every subsequent instance simply registers itself into the class WeakSet on
    its first call — no new tracking, no new connections.

    Constraint (both modes)
    -----------------------
    All keys that should trigger re-runs must be read unconditionally on the
    first call. A key accessed only inside a branch that does not execute on
    the first call will never be tracked and will never cause a re-run.

    Additional constraint (class-level mode only)
    ---------------------------------------------
    Tracking is done once using the first instance. All instances must read the
    same keys unconditionally. Keys only read by later instances will never be
    tracked.

    Wired vs unwired state
    ----------------------
    Per-instance:  _wired (WeakSet) tracks which instances have been through
                   the first call and had their connections wired.
    Class-level:   _cls_wired (set of class objects) tracks which classes are
                   already wired; _cls_instances (dict[cls, WeakSet]) holds
                   living instances per class.

    Reference cycle prevention
    --------------------------
    Per-instance handlers hold a weakref to the instance rather than a strong
    reference, breaking any cycle that would prevent GC. Class-level handlers
    iterate a WeakSet, which holds weak references to all living instances.
    """

    def __init__(self, fn, stores: list) -> None:
        self._fn = fn
        self._stores = stores

        # Per-instance tracking state.
        # _wired: instances that have completed their first call.
        # _inst_conns: maps instance → list of (store, key, handler) triples
        #              for cleanup by the weakref finalizer.
        self._wired: weakref.WeakSet = weakref.WeakSet()
        self._inst_conns: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()

        # Class-level tracking state.
        # _cls_wired: set of classes whose signal connections are already wired.
        # _cls_instances: dict[cls, WeakSet] — living instances per class.
        # _cls_conns: dict[cls, list] — connection list per class (for teardown).
        self._cls_wired: set = set()
        self._cls_instances: dict = {}
        self._cls_conns: dict = {}

        wraps(fn)(self)

    def __get__(self, obj, objtype=None):
        # Return self (the descriptor) when accessed on the class.
        # Return a bound partial when accessed on an instance.
        if obj is None:
            return self
        return partial(self._call, obj)

    def _call(self, obj):
        cls = type(obj)

        # Dispatch to the appropriate tracking mode.
        if getattr(cls, "_reactive_class", False):
            self._call_class(obj, cls)
            return

        # --- Per-instance mode ---

        if obj in self._wired:
            # Already wired — run directly, no tracking needed.
            self._fn(obj)
            return

        # First call for this instance: run inside a tracking context to
        # collect (store, key) dependencies, then wire signal connections.
        _push_tracking()
        try:
            self._fn(obj)
            raw_deps = _pop_tracking()
        except Exception:
            _pop_tracking()  # always clean up the stack on error
            raise

        # Wire one connection per unique (store, key) pair.
        # The handler holds a weakref to the instance so it does not prevent GC.
        conns: list = []
        seen: set = set()
        obj_ref = weakref.ref(obj)
        for store, key in raw_deps:
            pair = (id(store), key)
            if pair in seen:
                continue  # deduplicate — multiple reads of the same key
            seen.add(pair)
            def handler(_value, _ref=obj_ref, _fn=self._fn):
                o = _ref()
                if o is not None:
                    _fn(o)
            store.on(key).connect(handler)
            conns.append((store, key, handler))

        self._wired.add(obj)
        self._inst_conns[obj] = conns

        # Register a weakref finalizer so connections are disconnected
        # automatically when the instance is garbage collected.
        if conns:
            weakref.finalize(obj, disconnect_conns, list(conns))

    def _call_class(self, obj, cls):
        """Class-level tracking path.

        First instance to call: runs the method inside a tracking context,
        wires one connection per (store, key) pair. The handler iterates
        _cls_instances[cls] — a WeakSet — and calls the method on every
        living instance.

        Every subsequent instance: registers into _cls_instances[cls] and runs
        the method directly. No new tracking, no new connections.
        """
        # Ensure the WeakSet for this class exists before any instance is added.
        if cls not in self._cls_instances:
            self._cls_instances[cls] = weakref.WeakSet()

        instances = self._cls_instances[cls]

        if cls in self._cls_wired:
            # Class already wired — just register this instance and run.
            instances.add(obj)
            self._fn(obj)
            return

        # --- First instance for this class ---
        # Track deps by running the method inside a tracking context.
        _push_tracking()
        try:
            self._fn(obj)
            raw_deps = _pop_tracking()
        except Exception:
            _pop_tracking()
            raise

        # Register the first instance before wiring connections so it is
        # already in the WeakSet if a signal fires synchronously during
        # connect() (rare with direct connections but possible).
        instances.add(obj)

        # Wire one connection per unique (store, key) pair.
        # The handler holds a reference to `instances` (the WeakSet) and to
        # `self._fn` — no strong reference to any individual instance, so
        # instances can be GC'd freely.
        conns: list = []
        seen: set = set()
        for store, key in raw_deps:
            pair = (id(store), key)
            if pair in seen:
                continue  # deduplicate
            seen.add(pair)
            def handler(_value, _instances=instances, _fn=self._fn):
                # Snapshot the WeakSet before iterating — it may shrink mid-loop
                # if an instance is GC'd during signal dispatch.
                for inst in list(_instances):
                    _fn(inst)
            store.on(key).connect(handler)
            conns.append((store, key, handler))

        self._cls_wired.add(cls)
        self._cls_conns[cls] = conns
        # Class-level connections are intentionally permanent for the lifetime
        # of the class. Classes are never GC'd in normal use, so no finalizer
        # is registered. For explicit teardown, call disconnect_conns directly
        # with self._cls_conns[cls].


# ---------------------------------------------------------------------------
# Finalizer helper — must be module-level
# ---------------------------------------------------------------------------

def disconnect_conns(conns: list) -> None:
    """Disconnect every (store, key, handler) triple from its per-key signal.

    Must be a plain module-level function rather than a method so that
    weakref.finalize can hold a reference to it without indirectly keeping
    the watched instance alive (a bound method would hold a reference to self,
    which holds _inst_conns, which holds the instance — preventing GC).

    The RuntimeError guard handles the edge case where a _KeySignalEmitter was
    already destroyed before the finalizer ran (e.g. the store was torn down
    before the widget that observed it).
    """
    for store, key, handler in conns:
        emitter = store._key_signals.get(key)
        if emitter:
            try:
                emitter.sig.disconnect(handler)
            except RuntimeError:
                pass  # emitter already destroyed — nothing to disconnect
    conns.clear()


# ---------------------------------------------------------------------------
# Class decorator for class-level reactive tracking
# ---------------------------------------------------------------------------

def reactive_class_decorator(cls):
    """Class decorator that opts a class into class-level reactive tracking.

    Apply to any class that contains @registry.reactive methods. All such
    methods will use class-level tracking: the first instance to call the
    method triggers dependency tracking and wires one Qt signal connection per
    (store, key) pair. Every subsequent instance simply joins the shared
    WeakSet — no new connections are created.

    When a tracked key changes, one Qt signal dispatch triggers the method on
    every currently living instance of the class. This is more efficient than
    per-instance tracking when many identical widget instances exist, because
    the number of Qt connections stays constant regardless of instance count.

    Usage::

        @registry.reactive_class
        class VolumeLabel(QLabel):

            @registry.reactive
            def refresh(self):
                vol = settings.get("volume")
                bg  = theme.get("color.background")
                self.setText(f"Volume: {vol}")
                self.setStyleSheet(f"background: {bg};")

            def __init__(self):
                super().__init__()
                self.refresh()  # first instance: tracks + wires;
                                # later instances: join WeakSet + run directly

    Constraint: all instances must read the same keys unconditionally in the
    decorated method. Keys only read by later instances will never be tracked
    and will never trigger re-runs.
    """
    cls._reactive_class = True
    return cls
