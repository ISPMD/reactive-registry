"""
test_public_api.py
------------------
Tests and benchmarks for the Registry package.

Usage
-----
    python test_public_api.py

Runs all unit tests first, then two benchmark suites:

  1. Registry benchmarks  — real Registry + real PySide6 signals. Measures
     memory, settings read throughput, theme read throughput, and theme-switch
     signal fan-out across 10 000 wired instances using @registry.reactive
     (per-instance tracking).

  2. Comparison benchmark — real Registry + real PySide6 signals. Directly
     compares @registry.reactive (per-instance tracking) against
     @registry.reactive_class (class-level tracking) on wiring time, dispatch
     time, GC cleanup time, and live memory. Both modes use QObject-based
     widgets and real SettingsModel stores so numbers are directly comparable
     and reflect what you would actually pay in a PySide6 application.

Covers
------
    SettingsModel  — get, set, on, changed, as_dict, load_dict, None policy
    ThemeStore     — register, unregister, set_theme, set_mode, toggle_mode,
                     get, on, changed, theme_changed, as_dict, active_theme,
                     active_mode, None policy, state-before-signals ordering
    @registry.reactive — dependency tracking, auto re-run, multi-store,
                         deduplication, GC cleanup
"""

import sys
import gc
import weakref
import time
import tracemalloc

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QObject

# QApplication must exist before any QObject or Signal is instantiated.
app = QApplication.instance() or QApplication(sys.argv)

# Import after QApplication is created.
from Registry import settings, theme, registry
from Registry.Settings import SettingsModel
from Registry.Theme import ThemeStore
from Registry.Reactive import ReactiveDescriptor, reactive_class_decorator


# ===========================================================================
# Helpers
# ===========================================================================

def make_settings(**defaults):
    """Return a fresh SettingsModel with the given defaults."""
    return SettingsModel(defaults=defaults or None)


def make_theme():
    """Return a fresh ThemeStore (no active theme)."""
    return ThemeStore()


DARK_LIGHT = {
    "dark":  {"color.bg": "#000", "color.fg": "#fff", "font.size": 14},
    "light": {"color.bg": "#fff", "color.fg": "#000", "font.size": 14},
}

SECOND_THEME = {
    "dark":  {"color.bg": "#111", "color.fg": "#eee", "font.size": 16},
    "light": {"color.bg": "#eee", "color.fg": "#111", "font.size": 16},
}

passed = []
failed = []


def run(name, fn):
    """Run a single test function and record pass/fail."""
    try:
        fn()
        passed.append(name)
        print(f"  PASS  {name}")
    except Exception as e:
        failed.append(name)
        print(f"  FAIL  {name}")
        print(f"        {type(e).__name__}: {e}")


# ===========================================================================
# SettingsModel tests
# ===========================================================================

def test_settings_get_default():
    s = make_settings()
    assert s.get("x") is None
    assert s.get("x", default=42) == 42

def test_settings_get_set():
    s = make_settings()
    s.set("volume", 80)
    assert s.get("volume") == 80

def test_settings_defaults():
    s = make_settings(volume=50, muted=False)
    assert s.get("volume") == 50
    assert s.get("muted") == False

def test_settings_set_emits_on():
    s = make_settings()
    received = []
    s.on("volume").connect(lambda v: received.append(v))
    s.set("volume", 99)
    app.processEvents()
    assert received == [99]

def test_settings_set_emits_changed():
    s = make_settings()
    received = []
    s.changed.connect(lambda k, v: received.append((k, v)))
    s.set("volume", 5)
    app.processEvents()
    assert received == [("volume", 5)]

def test_settings_set_noop_on_equal():
    s = make_settings(volume=50)
    received = []
    s.changed.connect(lambda k, v: received.append((k, v)))
    s.set("volume", 50)  # same value — must not emit
    app.processEvents()
    assert received == []

def test_settings_on_fires_before_changed():
    s = make_settings()
    order = []
    s.on("x").connect(lambda v: order.append("on"))
    s.changed.connect(lambda k, v: order.append("changed"))
    s.set("x", 1)
    app.processEvents()
    assert order == ["on", "changed"]

def test_settings_as_dict():
    s = make_settings(a=1, b=2)
    d = s.as_dict()
    assert d == {"a": 1, "b": 2}
    d["a"] = 99  # mutation must not affect the store
    assert s.get("a") == 1

def test_settings_load_dict():
    s = make_settings()
    s.load_dict({"x": 10, "y": 20})
    assert s.get("x") == 10
    assert s.get("y") == 20

def test_settings_load_dict_signals():
    s = make_settings(x=10)
    received = []
    s.changed.connect(lambda k, v: received.append((k, v)))
    s.load_dict({"x": 10, "y": 99})  # x unchanged, y is new
    app.processEvents()
    assert ("y", 99) in received
    assert ("x", 10) not in received

def test_settings_none_value_raises():
    s = make_settings()
    try:
        s.set("x", None)
        assert False, "Expected TypeError"
    except TypeError:
        pass

def test_settings_none_default_raises():
    try:
        SettingsModel(defaults={"x": None})
        assert False, "Expected ValueError"
    except ValueError:
        pass

def test_settings_on_signal_per_key():
    s = make_settings()
    a_received = []
    b_received = []
    s.on("a").connect(lambda v: a_received.append(v))
    s.on("b").connect(lambda v: b_received.append(v))
    s.set("a", 1)
    s.set("b", 2)
    app.processEvents()
    assert a_received == [1]
    assert b_received == [2]


# ===========================================================================
# ThemeStore tests
# ===========================================================================

def test_theme_register_and_get():
    t = make_theme()
    t.register("base", DARK_LIGHT)
    t.set_theme("base")
    assert t.get("color.bg") == "#000"  # default mode is dark

def test_theme_get_default():
    t = make_theme()
    t.register("base", DARK_LIGHT)
    t.set_theme("base")
    assert t.get("missing") is None
    assert t.get("missing", "fallback") == "fallback"

def test_theme_active_theme_and_mode():
    t = make_theme()
    assert t.active_theme is None
    assert t.active_mode == "dark"
    t.register("base", DARK_LIGHT)
    t.set_theme("base")
    assert t.active_theme == "base"
    assert t.active_mode == "dark"

def test_theme_set_mode():
    t = make_theme()
    t.register("base", DARK_LIGHT)
    t.set_theme("base")
    t.set_mode("light")
    assert t.get("color.bg") == "#fff"
    assert t.active_mode == "light"

def test_theme_toggle_mode():
    t = make_theme()
    t.register("base", DARK_LIGHT)
    t.set_theme("base")
    t.toggle_mode()
    assert t.active_mode == "light"
    t.toggle_mode()
    assert t.active_mode == "dark"

def test_theme_set_theme_keeps_mode():
    t = make_theme()
    t.register("base", DARK_LIGHT)
    t.register("second", SECOND_THEME)
    t.set_theme("base")
    t.set_mode("light")
    t.set_theme("second")
    assert t.active_mode == "light"
    assert t.get("color.bg") == "#eee"

def test_theme_as_dict():
    t = make_theme()
    t.register("base", DARK_LIGHT)
    t.set_theme("base")
    d = t.as_dict()
    assert d["color.bg"] == "#000"
    d["color.bg"] = "tampered"  # must not affect the store
    assert t.get("color.bg") == "#000"

def test_theme_on_signal():
    t = make_theme()
    t.register("base", DARK_LIGHT)
    t.set_theme("base")
    received = []
    t.on("color.bg").connect(lambda v: received.append(v))
    t.set_mode("light")
    app.processEvents()
    assert "#fff" in received

def test_theme_changed_per_token_signal():
    t = make_theme()
    t.register("base", DARK_LIGHT)
    t.set_theme("base")
    received = []
    t.changed.connect(lambda k, v: received.append((k, v)))
    t.set_mode("light")
    app.processEvents()
    assert ("color.bg", "#fff") in received
    assert ("color.fg", "#000") in received

def test_theme_unchanged_token_no_signal():
    t = make_theme()
    t.register("base", DARK_LIGHT)
    t.set_theme("base")
    received = []
    t.on("font.size").connect(lambda v: received.append(v))
    t.set_mode("light")  # font.size is 14 in both variants — must not emit
    app.processEvents()
    assert received == []

def test_theme_on_fires_before_changed():
    t = make_theme()
    t.register("base", DARK_LIGHT)
    t.set_theme("base")
    order = []
    t.on("color.bg").connect(lambda v: order.append("on"))
    t.changed.connect(lambda k, v: order.append("changed") if k == "color.bg" else None)
    t.set_mode("light")
    app.processEvents()
    assert order.index("on") < order.index("changed")

def test_theme_theme_changed_signal():
    t = make_theme()
    t.register("base", DARK_LIGHT)
    t.set_theme("base")
    received = []
    t.theme_changed.connect(lambda name, mode: received.append((name, mode)))
    t.set_mode("light")
    app.processEvents()
    assert received == [("base", "light")]

def test_theme_state_committed_before_signals():
    """Signal handlers must observe the new theme state, not the old one."""
    t = make_theme()
    t.register("base", DARK_LIGHT)
    t.set_theme("base")
    seen_mode = []
    t.on("color.bg").connect(lambda v: seen_mode.append(t.active_mode))
    t.set_mode("light")
    app.processEvents()
    assert seen_mode == ["light"]  # handler saw new mode, not "dark"

def test_theme_unregister():
    t = make_theme()
    t.register("base", DARK_LIGHT)
    t.register("second", SECOND_THEME)
    t.set_theme("base")
    t.unregister("second")
    try:
        t.set_theme("second")
        assert False, "Expected KeyError"
    except KeyError:
        pass

def test_theme_unregister_active_raises():
    t = make_theme()
    t.register("base", DARK_LIGHT)
    t.set_theme("base")
    try:
        t.unregister("base")
        assert False, "Expected RuntimeError"
    except RuntimeError:
        pass

def test_theme_set_theme_unknown_raises():
    t = make_theme()
    try:
        t.set_theme("nonexistent")
        assert False, "Expected KeyError"
    except KeyError:
        pass

def test_theme_set_mode_invalid_raises():
    t = make_theme()
    t.register("base", DARK_LIGHT)
    t.set_theme("base")
    try:
        t.set_mode("sepia")
        assert False, "Expected ValueError"
    except ValueError:
        pass

def test_theme_set_mode_no_theme_raises():
    t = make_theme()
    try:
        t.set_mode("light")
        assert False, "Expected RuntimeError"
    except RuntimeError:
        pass

def test_theme_toggle_no_theme_raises():
    t = make_theme()
    try:
        t.toggle_mode()
        assert False, "Expected RuntimeError"
    except RuntimeError:
        pass

def test_theme_none_token_raises():
    t = make_theme()
    try:
        t.register("bad", {"dark": {"x": None}, "light": {"x": "#fff"}})
        assert False, "Expected ValueError"
    except ValueError:
        pass

def test_theme_missing_variant_raises():
    t = make_theme()
    try:
        t.register("bad", {"dark": {"x": "#000"}})
        assert False, "Expected ValueError"
    except ValueError:
        pass

def test_theme_register_replaces_active():
    """Re-registering the active theme must refresh live tokens and fire signals."""
    t = make_theme()
    t.register("base", DARK_LIGHT)
    t.set_theme("base")
    received = []
    t.on("color.bg").connect(lambda v: received.append(v))
    updated = {
        "dark":  {**DARK_LIGHT["dark"], "color.bg": "#222"},
        "light": DARK_LIGHT["light"],
    }
    t.register("base", updated)
    app.processEvents()
    assert received == ["#222"]
    assert t.get("color.bg") == "#222"


# ===========================================================================
# @registry.reactive tests
# ===========================================================================

def _make_local_registry():
    """Return a fresh Registry with its own SettingsModel and ThemeStore."""
    from Registry.Registry import Registry as _Registry
    r = _Registry()
    r.settings.set("volume", 50)
    r.theme.register("base", DARK_LIGHT)
    r.theme.set_theme("base")
    return r


def _make_fake_widget(r):
    """Return a _FakeWidget class bound to the given registry instance."""

    class _FakeWidget(QObject):
        def __init__(self):
            super().__init__()
            self.calls = []

        @r.reactive
        def refresh(self):
            vol = r.settings.get("volume")
            bg  = r.theme.get("color.bg")
            self.calls.append((vol, bg))

    return _FakeWidget


def test_reactive_first_call_runs():
    r = _make_local_registry()
    W = _make_fake_widget(r)
    w = W()
    w.refresh()
    assert len(w.calls) == 1

def test_reactive_reruns_on_settings_change():
    r = _make_local_registry()
    W = _make_fake_widget(r)
    w = W()
    w.refresh()
    r.settings.set("volume", 99)
    app.processEvents()
    assert len(w.calls) == 2
    assert w.calls[-1][0] == 99

def test_reactive_reruns_on_theme_change():
    r = _make_local_registry()
    W = _make_fake_widget(r)
    w = W()
    w.refresh()
    r.theme.set_mode("light")
    app.processEvents()
    assert len(w.calls) == 2
    assert w.calls[-1][1] == "#fff"

def test_reactive_tracks_both_stores():
    r = _make_local_registry()
    W = _make_fake_widget(r)
    w = W()
    w.refresh()
    r.settings.set("volume", 1)
    app.processEvents()
    r.theme.set_mode("light")
    app.processEvents()
    assert len(w.calls) == 3

def test_reactive_no_duplicate_connections():
    """A key read multiple times on the first call must produce only one connection."""
    r = _make_local_registry()
    calls = []

    class W(QObject):
        def __init__(self):
            super().__init__()

        @r.reactive
        def refresh(self):
            _ = r.settings.get("volume")
            _ = r.settings.get("volume")  # duplicate — must not create a second connection
            calls.append(1)

    w = W()
    w.refresh()
    r.settings.set("volume", 2)
    app.processEvents()
    assert len(calls) == 2  # wired once, re-ran once — not twice

def test_reactive_subsequent_calls_do_not_rewire():
    """Calling a reactive method manually after wiring just runs it directly."""
    r = _make_local_registry()
    W = _make_fake_widget(r)
    w = W()
    w.refresh()  # first call — tracks deps and wires connections
    w.refresh()  # second call — direct run, no re-tracking
    w.refresh()  # third call — direct run
    assert len(w.calls) == 3
    r.settings.set("volume", 1)
    app.processEvents()
    assert len(w.calls) == 4  # signal fired exactly once, not multiple times

def test_reactive_gc_disconnects():
    """All signal connections must be cleaned up when the instance is GC'd."""
    r = _make_local_registry()
    calls = []

    class W(QObject):
        def __init__(self):
            super().__init__()

        @r.reactive
        def refresh(self):
            _ = r.settings.get("volume")
            calls.append(1)

    w = W()
    w.refresh()  # first call — wires connections
    assert len(calls) == 1

    # Verify the connection is live before GC.
    r.settings.set("volume", 1)
    app.processEvents()
    assert len(calls) == 2, "Connection was not wired — re-run did not happen before GC"

    # Drop the instance and force collection.
    ref = weakref.ref(w)
    del w
    for _ in range(3):
        gc.collect()

    assert ref() is None, "Instance was not garbage collected"

    # Connection must now be dead — a further change must not trigger a re-run.
    before = len(calls)
    r.settings.set("volume", 2)
    app.processEvents()
    assert len(calls) == before, (
        f"Handler still connected after GC: calls grew from {before} to {len(calls)}"
    )



# ===========================================================================
# @registry.reactive — additional tests
# ===========================================================================

def test_reactive_instance_isolation():
    """Two instances of the same class must have independent connections.
    A change triggers a re-run on the instance that tracks the key, not
    on unrelated instances sharing the same class."""
    r = _make_local_registry()

    class W(QObject):
        def __init__(self):
            super().__init__()
            self.calls = []

        @r.reactive
        def refresh(self):
            _ = r.settings.get("volume")
            self.calls.append(1)

    w1 = W(); w1.refresh()
    w2 = W(); w2.refresh()

    r.settings.set("volume", 99)
    app.processEvents()

    # Both instances are independently wired — each must re-run exactly once.
    assert len(w1.calls) == 2, f"w1 expected 2 calls, got {len(w1.calls)}"
    assert len(w2.calls) == 2, f"w2 expected 2 calls, got {len(w2.calls)}"


def test_reactive_exception_does_not_leave_partial_wiring():
    """If the method raises on its first call, no connections must be wired
    and the instance must not appear in _wired. A subsequent successful call
    must track and wire normally."""
    r = _make_local_registry()
    bomb = [True]  # flip to False to make the method succeed

    class W(QObject):
        def __init__(self):
            super().__init__()
            self.calls = []

        @r.reactive
        def refresh(self):
            _ = r.settings.get("volume")
            if bomb[0]:
                raise RuntimeError("intentional")
            self.calls.append(1)

    w = W()

    # First call raises — must propagate and leave no wiring.
    try:
        w.refresh()
        assert False, "Expected RuntimeError"
    except RuntimeError:
        pass

    # Instance must not be marked as wired.
    descriptor = W.__dict__["refresh"]
    assert w not in descriptor._wired, "Instance incorrectly marked wired after exception"

    # Second call succeeds — must now track and wire.
    bomb[0] = False
    w.refresh()
    assert len(w.calls) == 1

    # Connection must be live.
    r.settings.set("volume", 99)
    app.processEvents()
    assert len(w.calls) == 2


def test_reactive_zero_keys_no_crash():
    """A reactive method that reads no store keys must run without error
    and must not register a finalizer or leave any wiring state."""
    r = _make_local_registry()
    calls = []

    class W(QObject):
        def __init__(self):
            super().__init__()

        @r.reactive
        def refresh(self):
            # Reads no keys — nothing to track, nothing to wire.
            calls.append(1)

    w = W()
    w.refresh()   # first call — no keys tracked, no connections wired
    w.refresh()   # second call — runs directly, still no wiring
    assert len(calls) == 2

    # No signal change should trigger a re-run since no keys were tracked.
    r.settings.set("volume", 99)
    app.processEvents()
    assert len(calls) == 2


def test_reactive_nested_calls_stack_safety():
    """A reactive method that calls another reactive method must track deps
    independently — the inner call must not pollute the outer call's dep list
    and each method must wire only the keys it actually reads."""
    r = _make_local_registry()

    class W(QObject):
        def __init__(self):
            super().__init__()
            self.outer_calls = []
            self.inner_calls = []

        @r.reactive
        def inner(self):
            # Reads only "color.bg" from theme.
            _ = r.theme.get("color.bg")
            self.inner_calls.append(1)

        @r.reactive
        def outer(self):
            # Reads only "volume" from settings, then calls inner().
            _ = r.settings.get("volume")
            self.inner()   # nested reactive call — must not mix dep lists
            self.outer_calls.append(1)

    w = W()
    w.outer()   # wires outer to "volume"; inner wires to "color.bg"

    # Only a volume change should re-trigger outer.
    r.settings.set("volume", 99)
    app.processEvents()
    assert len(w.outer_calls) == 2, "outer did not re-run on volume change"

    # A theme change must re-trigger inner (via its own connection) but must
    # NOT re-trigger outer (outer does not track "color.bg").
    before_outer = len(w.outer_calls)
    r.theme.set_mode("light")
    app.processEvents()
    assert len(w.inner_calls) >= 2, "inner did not re-run on theme change"
    assert len(w.outer_calls) == before_outer, (
        "outer incorrectly re-ran on theme change — dep lists were mixed"
    )


# ===========================================================================
# @registry.reactive_class tests
# ===========================================================================

def _make_class_registry():
    """Fresh Registry pre-loaded with a theme and a settings key, used by
    all @registry.reactive_class tests."""
    from Registry.Registry import Registry as _Registry
    r = _Registry()
    r.settings.set("volume", 50)
    r.theme.register("base", DARK_LIGHT)
    r.theme.set_theme("base")
    return r


def _make_class_widget(r):
    """Return a @registry.reactive_class QObject widget bound to r."""

    @r.reactive_class
    class W(QObject):
        def __init__(self):
            super().__init__()
            self.calls = []

        @r.reactive
        def refresh(self):
            vol = r.settings.get("volume")
            bg  = r.theme.get("color.bg")
            self.calls.append((vol, bg))

    return W


def test_reactive_class_first_instance_tracks_and_wires():
    """The first instance to call a class-level reactive method must trigger
    dependency tracking and wire one connection per (store, key) pair."""
    r = _make_class_registry()
    W = _make_class_widget(r)
    w = W()
    w.refresh()   # first call — must track "volume" and "color.bg"

    assert len(w.calls) == 1

    # Verify connections are live by triggering a re-run.
    r.settings.set("volume", 99)
    app.processEvents()
    assert len(w.calls) == 2
    assert w.calls[-1][0] == 99


def test_reactive_class_second_instance_does_not_rewire():
    """The second instance must join the existing WeakSet without triggering
    a new tracking pass or creating new signal connections."""
    r = _make_class_registry()
    W = _make_class_widget(r)

    w1 = W(); w1.refresh()   # first instance — tracks + wires
    w2 = W(); w2.refresh()   # second instance — join only, no new connections

    # Confirm both instances received their initial refresh call.
    assert len(w1.calls) == 1
    assert len(w2.calls) == 1

    # A single signal dispatch must re-run both instances.
    r.settings.set("volume", 77)
    app.processEvents()
    assert len(w1.calls) == 2
    assert len(w2.calls) == 2


def test_reactive_class_all_instances_rerun_on_dispatch():
    """When a tracked key changes, every living instance must re-run exactly once."""
    r = _make_class_registry()
    W = _make_class_widget(r)

    instances = [W() for _ in range(5)]
    for w in instances:
        w.refresh()

    r.settings.set("volume", 42)
    app.processEvents()

    for i, w in enumerate(instances):
        assert len(w.calls) == 2, (
            f"instance {i} expected 2 calls after dispatch, got {len(w.calls)}"
        )


def test_reactive_class_gcd_instance_skipped_on_dispatch():
    """An instance that has been GC'd must be silently skipped when a tracked
    key changes — the surviving instances must still re-run."""
    r = _make_class_registry()
    W = _make_class_widget(r)

    w1 = W(); w1.refresh()
    w2 = W(); w2.refresh()

    # Drop w1 and force GC — it should fall out of the WeakSet.
    ref = weakref.ref(w1)
    del w1
    for _ in range(3):
        gc.collect()
    assert ref() is None, "w1 was not garbage collected"

    # w2 must still re-run cleanly; no crash from the dead w1.
    r.settings.set("volume", 11)
    app.processEvents()
    assert len(w2.calls) == 2


def test_reactive_class_connections_persist_after_all_instances_gcd():
    """Class-level connections are permanent — they must survive even after
    all instances have been GC'd. A new instance created afterwards must
    immediately benefit from the existing wiring without triggering
    re-tracking or creating duplicate connections."""
    r = _make_class_registry()
    W = _make_class_widget(r)

    w1 = W(); w1.refresh()

    # GC the only instance.
    del w1
    for _ in range(3):
        gc.collect()

    # Class is still wired. Create a new instance — it must join the WeakSet
    # and react to changes without any new tracking pass.
    w2 = W(); w2.refresh()
    assert len(w2.calls) == 1

    r.settings.set("volume", 55)
    app.processEvents()
    assert len(w2.calls) == 2


def test_reactive_class_multiple_methods_wire_independently():
    """Two @registry.reactive methods on the same @registry.reactive_class
    must each track their own keys independently — a change to a key read
    only by method A must not trigger method B."""
    r = _make_class_registry()

    @r.reactive_class
    class W(QObject):
        def __init__(self):
            super().__init__()
            self.a_calls = []
            self.b_calls = []

        @r.reactive
        def method_a(self):
            # Reads only "volume".
            _ = r.settings.get("volume")
            self.a_calls.append(1)

        @r.reactive
        def method_b(self):
            # Reads only "color.bg".
            _ = r.theme.get("color.bg")
            self.b_calls.append(1)

    w = W()
    w.method_a()
    w.method_b()

    # Volume change must trigger method_a only.
    r.settings.set("volume", 99)
    app.processEvents()
    assert len(w.a_calls) == 2, "method_a did not re-run on volume change"
    assert len(w.b_calls) == 1, "method_b incorrectly re-ran on volume change"

    # Theme change must trigger method_b only.
    r.theme.set_mode("light")
    app.processEvents()
    assert len(w.b_calls) == 2, "method_b did not re-run on theme change"
    assert len(w.a_calls) == 2, "method_a incorrectly re-ran on theme change"


def test_reactive_class_does_not_affect_other_classes():
    """@registry.reactive_class on one class must have no effect on a plain
    @registry.reactive class. The two must behave independently."""
    r = _make_class_registry()

    @r.reactive_class
    class ClassWidget(QObject):
        def __init__(self):
            super().__init__()
            self.calls = []

        @r.reactive
        def refresh(self):
            _ = r.settings.get("volume")
            self.calls.append(1)

    class InstanceWidget(QObject):
        def __init__(self):
            super().__init__()
            self.calls = []

        @r.reactive
        def refresh(self):
            _ = r.settings.get("volume")
            self.calls.append(1)

    cw = ClassWidget();    cw.refresh()
    iw = InstanceWidget(); iw.refresh()

    r.settings.set("volume", 77)
    app.processEvents()

    # Both must re-run — each via their own independent mechanism.
    assert len(cw.calls) == 2, "ClassWidget did not re-run"
    assert len(iw.calls) == 2, "InstanceWidget did not re-run"


def test_reactive_class_exception_does_not_corrupt_wiring():
    """If the method raises on the very first instance call, the class must
    not be marked as wired. A subsequent successful call on any instance
    must perform tracking and wire connections normally."""
    r = _make_class_registry()
    bomb = [True]

    @r.reactive_class
    class W(QObject):
        def __init__(self):
            super().__init__()
            self.calls = []

        @r.reactive
        def refresh(self):
            _ = r.settings.get("volume")
            if bomb[0]:
                raise RuntimeError("intentional")
            self.calls.append(1)

    w1 = W()
    try:
        w1.refresh()
        assert False, "Expected RuntimeError"
    except RuntimeError:
        pass

    # Class must not be marked wired after the exception.
    descriptor = W.__dict__["refresh"]
    assert W not in descriptor._cls_wired, (
        "Class incorrectly marked wired after first-instance exception"
    )

    # Successful call on a second instance must wire the class.
    bomb[0] = False
    w2 = W()
    w2.refresh()
    assert len(w2.calls) == 1

    r.settings.set("volume", 99)
    app.processEvents()
    assert len(w2.calls) == 2


def test_reactive_class_reruns_on_theme_change():
    """Class-level reactive methods must re-run on theme store changes
    just as they do on settings store changes."""
    r = _make_class_registry()
    W = _make_class_widget(r)

    w = W(); w.refresh()

    r.theme.set_mode("light")
    app.processEvents()
    assert len(w.calls) == 2
    assert w.calls[-1][1] == "#fff"


def test_reactive_class_tracks_both_stores():
    """A class-level reactive method that reads from both stores must
    re-run when either store changes."""
    r = _make_class_registry()
    W = _make_class_widget(r)

    w = W(); w.refresh()

    r.settings.set("volume", 1)
    app.processEvents()
    assert len(w.calls) == 2

    r.theme.set_mode("light")
    app.processEvents()
    assert len(w.calls) == 3


# ===========================================================================
# Test registry
# ===========================================================================

TESTS = [
    # SettingsModel
    ("settings: get returns None for missing key",          test_settings_get_default),
    ("settings: get/set round-trip",                        test_settings_get_set),
    ("settings: defaults loaded on construction",           test_settings_defaults),
    ("settings: set emits per-key signal",                  test_settings_set_emits_on),
    ("settings: set emits changed signal",                  test_settings_set_emits_changed),
    ("settings: set no-ops when value unchanged",           test_settings_set_noop_on_equal),
    ("settings: on fires before changed",                   test_settings_on_fires_before_changed),
    ("settings: as_dict returns snapshot",                  test_settings_as_dict),
    ("settings: load_dict sets all keys",                   test_settings_load_dict),
    ("settings: load_dict signals only changed keys",       test_settings_load_dict_signals),
    ("settings: set None raises TypeError",                 test_settings_none_value_raises),
    ("settings: None default raises ValueError",            test_settings_none_default_raises),
    ("settings: per-key signals are independent",           test_settings_on_signal_per_key),

    # ThemeStore
    ("theme: register and get active token",                test_theme_register_and_get),
    ("theme: get returns default for missing token",        test_theme_get_default),
    ("theme: active_theme and active_mode",                 test_theme_active_theme_and_mode),
    ("theme: set_mode switches token set",                  test_theme_set_mode),
    ("theme: toggle_mode flips dark/light",                 test_theme_toggle_mode),
    ("theme: set_theme keeps current mode",                 test_theme_set_theme_keeps_mode),
    ("theme: as_dict returns snapshot",                     test_theme_as_dict),
    ("theme: on signal fires on token change",              test_theme_on_signal),
    ("theme: changed signal fires per changed token",       test_theme_changed_per_token_signal),
    ("theme: unchanged token emits no signal",              test_theme_unchanged_token_no_signal),
    ("theme: on fires before changed",                      test_theme_on_fires_before_changed),
    ("theme: theme_changed fires after switch",             test_theme_theme_changed_signal),
    ("theme: state committed before signals fire",          test_theme_state_committed_before_signals),
    ("theme: unregister removes theme",                     test_theme_unregister),
    ("theme: unregister active theme raises RuntimeError",  test_theme_unregister_active_raises),
    ("theme: set_theme unknown name raises KeyError",       test_theme_set_theme_unknown_raises),
    ("theme: set_mode invalid value raises ValueError",     test_theme_set_mode_invalid_raises),
    ("theme: set_mode with no active theme raises",         test_theme_set_mode_no_theme_raises),
    ("theme: toggle_mode with no active theme raises",      test_theme_toggle_no_theme_raises),
    ("theme: None token value raises ValueError",           test_theme_none_token_raises),
    ("theme: missing variant raises ValueError",            test_theme_missing_variant_raises),
    ("theme: re-registering active theme refreshes tokens", test_theme_register_replaces_active),

    # @registry.reactive
    ("reactive: first call runs method",                              test_reactive_first_call_runs),
    ("reactive: reruns on settings change",                           test_reactive_reruns_on_settings_change),
    ("reactive: reruns on theme change",                              test_reactive_reruns_on_theme_change),
    ("reactive: tracks keys from both stores",                        test_reactive_tracks_both_stores),
    ("reactive: duplicate key reads produce one connection",          test_reactive_no_duplicate_connections),
    ("reactive: subsequent calls run without rewiring",               test_reactive_subsequent_calls_do_not_rewire),
    ("reactive: GC disconnects signal connections",                   test_reactive_gc_disconnects),
    ("reactive: two instances are independently wired",               test_reactive_instance_isolation),
    ("reactive: exception on first call leaves no wiring state",      test_reactive_exception_does_not_leave_partial_wiring),
    ("reactive: zero-key method runs without crash or wiring",        test_reactive_zero_keys_no_crash),
    ("reactive: nested reactive calls track deps independently",      test_reactive_nested_calls_stack_safety),

    # @registry.reactive_class
    ("reactive_class: first instance tracks and wires",               test_reactive_class_first_instance_tracks_and_wires),
    ("reactive_class: second instance joins without rewiring",        test_reactive_class_second_instance_does_not_rewire),
    ("reactive_class: all living instances rerun on dispatch",        test_reactive_class_all_instances_rerun_on_dispatch),
    ("reactive_class: GC'd instance silently skipped on dispatch",    test_reactive_class_gcd_instance_skipped_on_dispatch),
    ("reactive_class: connections persist after all instances GC'd",  test_reactive_class_connections_persist_after_all_instances_gcd),
    ("reactive_class: multiple methods wire independently",           test_reactive_class_multiple_methods_wire_independently),
    ("reactive_class: does not affect plain @reactive classes",       test_reactive_class_does_not_affect_other_classes),
    ("reactive_class: exception on first call leaves no wiring",      test_reactive_class_exception_does_not_corrupt_wiring),
    ("reactive_class: reruns on theme change",                        test_reactive_class_reruns_on_theme_change),
    ("reactive_class: tracks both stores",                            test_reactive_class_tracks_both_stores),
]


# ===========================================================================
# Shared benchmark helpers
# ===========================================================================

def _fmt_time(seconds):
    if seconds < 1e-6:  return f"{seconds * 1e9:.1f} ns"
    if seconds < 1e-3:  return f"{seconds * 1e6:.2f} us"
    if seconds < 1:     return f"{seconds * 1e3:.2f} ms"
    return f"{seconds:.3f} s"


def _fmt_mem(nbytes):
    if nbytes < 1024:       return f"{nbytes} B"
    if nbytes < 1024 ** 2:  return f"{nbytes / 1024:.1f} KB"
    return f"{nbytes / 1024 ** 2:.2f} MB"


def _snap_mem():
    """Take a tracemalloc snapshot and return net positive bytes since the
    previous snapshot. tracemalloc must already be started by the caller."""
    return tracemalloc.take_snapshot()


def _net_bytes(before, after):
    return sum(s.size_diff for s in after.compare_to(before, "lineno") if s.size_diff > 0)


# ===========================================================================
# Benchmark 1 — Registry throughput (real PySide6 + real Registry)
#
# All four measurements share a single registry and a single pool of 10 000
# wired instances so construction cost is paid once and is not counted in the
# read or switch timings.
#
# Theme definition uses 10 fully-distinct tokens (all values differ between
# dark and light) so every set_mode() flip triggers the maximum diff and
# signal fan-out: 10 tokens x 10 000 instances = 100 000 deliveries per flip.
#
# Metrics
# -------
#   memory        — net Python heap delta (tracemalloc) while instances are
#                   live, reported as total and per-instance.
#                   Note: Qt C++ heap is not tracked by tracemalloc; actual
#                   RSS will be higher. This measures Python-side overhead only.
#
#   settings read — wall time for N x READ_ITERATIONS calls to settings.get()
#                   outside any tracking context (pure dict lookup + _record
#                   no-op), plus throughput in calls/sec and cost per call.
#
#   theme read    — same, but for theme.get() across all 10 tokens per instance.
#
#   switch        — wall time for SWITCH_ITERATIONS set_mode() flips, each
#                   followed by processEvents() to flush all signal deliveries.
#                   One warm-up flip is performed first to avoid measuring
#                   first-flip Qt machinery overhead. Reports per-switch
#                   latency and aggregate signal delivery rate.
# ===========================================================================

# 10-token theme: all values differ between dark and light so every mode flip
# changes all 10 tokens and exercises the full diff + fan-out path.
_BENCH_DARK  = {f"token.{i}": f"#dark{i:02d}" for i in range(10)}
_BENCH_LIGHT = {f"token.{i}": f"#lite{i:02d}" for i in range(10)}
_BENCH_THEME = {"dark": _BENCH_DARK, "light": _BENCH_LIGHT}

_B1_N_INSTANCES     = 10_000
_B1_READ_ITERATIONS = 100   # repeat reads to get a stable wall-clock sample
_B1_SWITCH_ITERS    = 20    # each flip is expensive; 20 gives a stable average


def _make_bench_registry():
    """Fresh Registry with a 10-token theme and one settings key."""
    from Registry.Registry import Registry as _Registry
    r = _Registry()
    r.settings.set("value", 0)
    r.theme.register("bench", _BENCH_THEME)
    r.theme.set_theme("bench")
    return r


def _make_bench_widget_class(r):
    """QObject widget that reads one settings key + all 10 theme tokens on refresh.
    Uses @registry.reactive (per-instance tracking): each instance gets its own
    11 signal connections (1 settings + 10 theme)."""

    class BenchWidget(QObject):
        def __init__(self):
            super().__init__()

        @r.reactive
        def refresh(self):
            r.settings.get("value")
            for i in range(10):
                r.theme.get(f"token.{i}")

    return BenchWidget


def run_registry_benchmarks():
    """Benchmark 1: Registry throughput with real PySide6 and 10 000 instances."""
    print(f"\n{'─' * 64}")
    print(f"  Benchmark 1 — Registry throughput  (@registry.reactive, real PySide6)")
    print(f"  {_B1_N_INSTANCES:,} QObject instances, 10-token theme")
    print(f"{'─' * 64}")

    # Create and wire all instances once. tracemalloc is active only during
    # construction so the memory delta captures exactly the Python-side cost
    # of 10 000 QObject instances + their 11 signal connections each.
    gc.collect()
    r = _make_bench_registry()
    BenchWidget = _make_bench_widget_class(r)

    tracemalloc.start()
    snap_before = _snap_mem()
    instances = [BenchWidget() for _ in range(_B1_N_INSTANCES)]
    for w in instances:
        w.refresh()  # first call — wires 11 connections (1 settings + 10 theme)
    snap_after = _snap_mem()
    tracemalloc.stop()

    # [1] Memory
    net = _net_bytes(snap_before, snap_after)
    print(f"\n  [1/4] memory  ({_B1_N_INSTANCES:,} wired QObject instances)")
    print(f"    Python heap delta : {_fmt_mem(net)}")
    print(f"    per instance      : {_fmt_mem(net / _B1_N_INSTANCES)}")
    print(f"    note              : Qt C++ heap not included; actual RSS will be higher")

    # [2] Settings read throughput
    # One settings.get("value") per instance per iteration, outside any
    # tracking context — pure dict lookup + _record no-op overhead.
    t0 = time.perf_counter()
    for _ in range(_B1_READ_ITERATIONS):
        for _ in instances:
            r.settings.get("value")
    elapsed_s = time.perf_counter() - t0
    total_s = _B1_N_INSTANCES * _B1_READ_ITERATIONS
    print(f"\n  [2/4] settings read  ({_B1_N_INSTANCES:,} instances x {_B1_READ_ITERATIONS} iterations)")
    print(f"    total calls  : {total_s:,}")
    print(f"    elapsed      : {_fmt_time(elapsed_s)}")
    print(f"    throughput   : {total_s / elapsed_s:,.0f} calls/sec")
    print(f"    per call     : {_fmt_time(elapsed_s / total_s)}")

    # [3] Theme read throughput
    # 10 theme.get() calls per instance per iteration (all 10 tokens).
    t0 = time.perf_counter()
    for _ in range(_B1_READ_ITERATIONS):
        for _ in instances:
            for i in range(10):
                r.theme.get(f"token.{i}")
    elapsed_t = time.perf_counter() - t0
    total_t = _B1_N_INSTANCES * 10 * _B1_READ_ITERATIONS
    print(f"\n  [3/4] theme read  ({_B1_N_INSTANCES:,} instances x 10 tokens x {_B1_READ_ITERATIONS} iterations)")
    print(f"    total calls  : {total_t:,}")
    print(f"    elapsed      : {_fmt_time(elapsed_t)}")
    print(f"    throughput   : {total_t / elapsed_t:,.0f} calls/sec")
    print(f"    per call     : {_fmt_time(elapsed_t / total_t)}")

    # [4] Switch throughput
    # Each set_mode() flip changes all 10 tokens -> 10 x 10 000 = 100 000
    # signal deliveries. processEvents() flushes the full Qt signal queue.
    # Alternates dark->light->dark so every flip is a genuine diff with no
    # short-circuit. One warm-up flip precedes the timed loop.
    r.theme.set_mode("light")  # warm-up: dark -> light
    app.processEvents()
    t0 = time.perf_counter()
    for i in range(_B1_SWITCH_ITERS):
        r.theme.set_mode("dark" if i % 2 == 0 else "light")
        app.processEvents()
    elapsed_sw = time.perf_counter() - t0
    deliveries_per_flip = _B1_N_INSTANCES * 10
    total_del = deliveries_per_flip * _B1_SWITCH_ITERS
    print(f"\n  [4/4] switch  ({_B1_SWITCH_ITERS} set_mode() flips, "
          f"{_B1_N_INSTANCES:,} instances x 10 tokens)")
    print(f"    signal deliveries : {total_del:,} total  ({deliveries_per_flip:,} per flip)")
    print(f"    total elapsed     : {_fmt_time(elapsed_sw)}")
    print(f"    per flip          : {_fmt_time(elapsed_sw / _B1_SWITCH_ITERS)}")
    print(f"    delivery rate     : {total_del / elapsed_sw:,.0f} signals/sec")

    del instances
    gc.collect()
    print(f"\n{'─' * 64}")


# ===========================================================================
# Benchmark 2 — per-instance vs class-level comparison
#               (real Registry + real PySide6 signals)
#
# Directly compares @registry.reactive (per-instance tracking) against
# @registry.reactive_class (class-level tracking) using identical QObject-
# based widgets and real SettingsModel stores so numbers are directly
# comparable with Benchmark 1 and with each other.
#
# Each timing run creates a completely fresh SettingsModel + Registry +
# Widget class so runs are fully isolated — no shared signal state bleeds
# between iterations.
#
# Timing and memory are measured in separate passes:
#   - Timing uses gc.disable() during each phase to eliminate GC jitter.
#   - Memory uses a standalone tracemalloc pass without gc.collect() inside
#     the window, which avoids the multi-second stall that running the full
#     GC cycle on 10 000 QObject instances with 100 000 weakref finalizers
#     would cause inside a tracemalloc context.
#
# Phases
# ------
#   Wiring      — create N QObject instances, each calls refresh() once.
#                 Per-instance: N tracking passes + N x K PySide6 signal
#                 connections wired via store.on(key).connect().
#                 Class-level:  1 tracking pass + K connections total; every
#                 subsequent instance just joins the shared WeakSet — free.
#
#   Dispatch    — fire each of the K keys once. Both modes call refresh() on
#                 every instance (N x K total calls) so the raw work is
#                 identical. The cost difference is N PySide6 signal dispatches
#                 (per-instance) vs K dispatches + 1 WeakSet iteration
#                 (class-level).
#
#   GC/cleanup  — del instances + gc.collect(). Per-instance: N weakref
#                 finalizers each disconnect K signal handlers = N x K
#                 disconnect() calls through PySide6. Class-level: zero
#                 per-instance cleanup; the K class-level connections are
#                 permanent for the class lifetime.
#
#   Memory      — tracemalloc net bytes while N instances are alive (after
#                 wiring, before deletion). The gap between modes reflects the
#                 N x K handler closures, weakref objects, and connection list
#                 entries that per-instance tracking allocates per instance
#                 but class-level does not. Both use QObject instances so the
#                 baseline QObject overhead is identical and cancels out,
#                 making the memory delta a clean measure of the reactivity
#                 machinery cost alone.
# ===========================================================================

_B2_N_INSTANCES = 10_000
_B2_N_KEYS      = 10
_B2_KEYS        = [f"key_{i}" for i in range(_B2_N_KEYS)]
_B2_RUNS        = 3

_b2_call_counter = 0


def _b2_make_env(use_class_level):
    """Fresh SettingsModel + descriptor + QObject Widget class per run.

    Each call produces a fully isolated environment: a new SettingsModel with
    its own PySide6 signals, a new descriptor instance with empty wiring
    state, and a new Widget class. This ensures no signal connections or
    wiring state leak between benchmark runs.

    The Widget class uses @registry.reactive_class (class-level) or
    @registry.reactive (per-instance) depending on use_class_level. In both
    cases the widget inherits from QObject so the QObject construction
    overhead is identical between the two modes.
    """
    global _b2_call_counter
    store = SettingsModel()
    for k in _B2_KEYS:
        store.set(k, 0)

    def refresh(self):
        global _b2_call_counter
        for k in _B2_KEYS:
            store.get(k)
        _b2_call_counter += 1

    if use_class_level:
        # Build a fresh ReactiveDescriptor and apply the class-level marker
        # directly so the benchmark doesn't depend on @registry.reactive_class
        # being used as a decorator at class-definition time.
        d = ReactiveDescriptor(refresh, stores=[store])

        @reactive_class_decorator
        class Widget(QObject):
            def __init__(self):
                super().__init__()
            refresh = d
    else:
        d = ReactiveDescriptor(refresh, stores=[store])

        class Widget(QObject):
            def __init__(self):
                super().__init__()
            refresh = d

    return store, Widget


def _b2_bench_timing(use_class_level):
    """Time wiring, dispatch, and GC for one complete N-instance lifecycle."""
    global _b2_call_counter
    store, Widget = _b2_make_env(use_class_level)

    # --- Wiring ---
    # Create N QObject instances; each calls refresh() once.
    # Per-instance: N tracking passes + N x K PySide6 signal connections.
    # Class-level:  1 tracking pass + K connections; rest join the WeakSet.
    _b2_call_counter = 0
    gc.disable()
    t0 = time.perf_counter()
    instances = [Widget() for _ in range(_B2_N_INSTANCES)]
    for inst in instances: inst.refresh()
    t_wire = time.perf_counter() - t0
    gc.enable()

    # --- Dispatch ---
    # Fire each key once; measure the full fan-out to all N instances.
    # Both modes deliver refresh() to every instance — same total work.
    # Cost difference: N signal dispatches (per-instance) vs K dispatches
    # + 1 WeakSet iteration (class-level).
    _b2_call_counter = 0
    gc.disable()
    t0 = time.perf_counter()
    for k in _B2_KEYS: store.set(k, store.get(k) + 1)
    t_dispatch = time.perf_counter() - t0
    gc.enable()
    dispatch_calls = _b2_call_counter

    # --- GC ---
    # Per-instance: N weakref finalizers each call disconnect() K times
    # through PySide6 = N x K total disconnect() calls.
    # Class-level: no per-instance finalizers; K class connections are
    # permanent and never disconnected here.
    gc.disable()
    t0 = time.perf_counter()
    del instances; gc.collect()
    t_gc = time.perf_counter() - t0
    gc.enable()

    return t_wire, t_dispatch, t_gc, dispatch_calls


def _b2_bench_memory(use_class_level):
    """Measure net Python heap bytes while N wired QObject instances are alive.

    Run in a separate pass from timing so gc.collect() is never called inside
    a tracemalloc window. Calling gc.collect() inside tracemalloc on 10 000
    QObject instances with 100 000 weakref finalizers causes a multi-second
    stall that would dominate the measurement.

    Because both modes use QObject instances, the baseline QObject Python-side
    overhead is identical. The delta between per-instance and class-level
    therefore isolates the reactivity machinery cost: handler closures,
    weakref objects, and connection list entries allocated per instance by
    per-instance tracking but not by class-level tracking.
    """
    store, Widget = _b2_make_env(use_class_level)

    tracemalloc.start()
    snap_before = _snap_mem()
    instances = [Widget() for _ in range(_B2_N_INSTANCES)]
    for inst in instances: inst.refresh()
    snap_after = _snap_mem()
    tracemalloc.stop()

    mem_bytes = _net_bytes(snap_before, snap_after)
    del instances
    return mem_bytes


def _b2_avg(fn, n, label):
    """Run fn n times, printing progress, and return column-wise averages."""
    results = []
    for i in range(n):
        print(f"  {label} run {i+1}/{n}...", flush=True)
        results.append(fn())
    cols = list(zip(*results))
    return [sum(c) / n for c in cols]


def run_comparison_benchmark():
    """Benchmark 2: per-instance vs class-level with real PySide6 QObjects."""
    print(f"\n{'─' * 64}")
    print(f"  Benchmark 2 — per-instance vs class-level  (real PySide6 QObjects)")
    print(f"  {_B2_N_INSTANCES:,} instances x {_B2_N_KEYS} keys  |  timing averaged over {_B2_RUNS} runs")
    print(f"{'─' * 64}")

    print("\n  Warming up...", flush=True)
    _b2_bench_timing(False); _b2_bench_timing(True)

    print("\n  Timing — per-instance:")
    pi_t = _b2_avg(lambda: _b2_bench_timing(False), _B2_RUNS, "per-instance")
    print("\n  Timing — class-level:")
    cl_t = _b2_avg(lambda: _b2_bench_timing(True),  _B2_RUNS, "class-level")

    print("\n  Memory — per-instance...", flush=True)
    pi_mem = _b2_bench_memory(False)
    print("  Memory — class-level...", flush=True)
    cl_mem = _b2_bench_memory(True)

    def ms(t):       return f"{t*1000:8.2f} ms"
    def mb(b):       return f"{b/1024/1024:8.2f} MB"
    def ratio(a, b): return f"{a/b:8.1f}x" if b > 0 else "     inf"

    W = 68
    print(f"\n{'='*W}")
    print(f"  {_B2_N_INSTANCES:,} instances x {_B2_N_KEYS} keys  |  timing averaged over {_B2_RUNS} runs")
    print(f"{'='*W}")
    print(f"\n  {'Phase':<20} {'per-instance':>16} {'class-level':>16} {'ratio':>10}")
    print(f"  {'-'*(W-2)}")

    for label, v_pi, v_cl, fmt in [
        ("Wiring",       pi_t[0], cl_t[0], ms),
        ("Dispatch",     pi_t[1], cl_t[1], ms),
        ("GC / cleanup", pi_t[2], cl_t[2], ms),
    ]:
        print(f"  {label:<20} {fmt(v_pi):>16} {fmt(v_cl):>16} {ratio(v_pi, v_cl):>10}")

    print(f"  {'-'*(W-2)}")
    t_pi = sum(pi_t[:3]); t_cl = sum(cl_t[:3])
    print(f"  {'Total time':<20} {ms(t_pi):>16} {ms(t_cl):>16} {ratio(t_pi, t_cl):>10}")
    print(f"  {'-'*(W-2)}")
    print(f"  {'Memory (live)':<20} {mb(pi_mem):>16} {mb(cl_mem):>16} {ratio(pi_mem, cl_mem):>10}")
    print(f"{'='*W}")
    print(f"\n  Dispatch calls:  per-instance={int(pi_t[3]):,}   class-level={int(cl_t[3]):,}")
    print(f"  Signal connections held:")
    print(f"    per-instance : {_B2_N_INSTANCES*_B2_N_KEYS:,}  ({_B2_N_INSTANCES:,} instances x {_B2_N_KEYS} keys)")
    print(f"    class-level  : {_B2_N_KEYS}  (1 class x {_B2_N_KEYS} keys)")
    print(f"{'─' * 64}\n")


# ===========================================================================
# Entry point
# ===========================================================================

if __name__ == "__main__":
    print(f"\nRunning {len(TESTS)} tests\n")
    for name, fn in TESTS:
        run(name, fn)

    print(f"\n{len(passed)} passed, {len(failed)} failed")
    if failed:
        print("\nFailed tests:")
        for name in failed:
            print(f"  - {name}")

    run_registry_benchmarks()
    run_comparison_benchmark()

    if failed:
        sys.exit(1)
    else:
        print("All tests passed.")
        sys.exit(0)
