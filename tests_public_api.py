"""
test_public_api.py
------------------
Tests for the Registry package public API.

Covers:
    SettingsModel  — get, set, on, changed, as_dict, load_dict, None policy
    ThemeStore     — register, unregister, set_theme, set_mode, toggle_mode,
                     get, on, changed, theme_changed, as_dict, active_theme,
                     active_mode, None policy, state-before-signals ordering
    @registry.reactive — dependency tracking, auto re-run, multi-store,
                         deduplication, GC cleanup

Run:
    python test_public_api.py
"""

import sys
import gc
import weakref
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QObject

# A QApplication must exist before any QObject or Signal is used.
app = QApplication.instance() or QApplication(sys.argv)

# Import after QApplication is created.
from Registry import settings, theme, registry
from Registry.Settings import SettingsModel
from Registry.Theme import ThemeStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# SettingsModel
# ---------------------------------------------------------------------------

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
    s.set("volume", 50)  # same value — should not emit
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


# ---------------------------------------------------------------------------
# ThemeStore
# ---------------------------------------------------------------------------

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
    t.set_mode("light")  # font.size is 14 in both variants
    app.processEvents()
    assert received == []  # no signal for unchanged token

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
    """Signal handlers must see the new theme state, not the old one."""
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
    """Re-registering the active theme refreshes tokens and fires signals."""
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
    """A key read multiple times should only produce one connection."""
    r = _make_local_registry()
    calls = []

    class W(QObject):
        def __init__(self):
            super().__init__()

        @r.reactive
        def refresh(self):
            # Read the same key twice — should still wire only one connection.
            _ = r.settings.get("volume")
            _ = r.settings.get("volume")
            calls.append(1)

    w = W()
    w.refresh()
    r.settings.set("volume", 2)
    app.processEvents()
    # Should have re-run exactly once, not twice.
    assert len(calls) == 2

def test_reactive_subsequent_calls_do_not_rewire():
    """Calling a reactive method manually after wiring just runs it directly."""
    r = _make_local_registry()
    W = _make_fake_widget(r)
    w = W()
    w.refresh()  # first call — wires
    w.refresh()  # second call — direct run, no rewiring
    w.refresh()  # third call — direct run
    assert len(w.calls) == 3
    r.settings.set("volume", 1)
    app.processEvents()
    assert len(w.calls) == 4  # signal fired once, not multiple times

def test_reactive_gc_disconnects():
    """Connections should be cleaned up when the instance is garbage collected.

    FIX 2: strengthened to assert the connection *was* wired before GC so the
    test cannot silently pass due to a wiring bug. We check that the signal
    fires while the instance is alive (proving wiring succeeded), then verify
    it does not fire after GC (proving cleanup succeeded).
    """
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

    # Verify the connection is live: a change should trigger a re-run.
    r.settings.set("volume", 1)
    app.processEvents()
    assert len(calls) == 2, "Connection was not wired — re-run did not happen before GC"

    # Now drop the instance and force GC.
    ref = weakref.ref(w)
    del w
    for _ in range(3):
        gc.collect()

    assert ref() is None, "Instance was not garbage collected"

    # The connection should now be dead — a further change must not re-run.
    before = len(calls)
    r.settings.set("volume", 2)
    app.processEvents()
    assert len(calls) == before, (
        f"Handler still connected after GC: calls grew from {before} to {len(calls)}"
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

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
    ("reactive: first call runs method",                    test_reactive_first_call_runs),
    ("reactive: reruns on settings change",                 test_reactive_reruns_on_settings_change),
    ("reactive: reruns on theme change",                    test_reactive_reruns_on_theme_change),
    ("reactive: tracks keys from both stores",              test_reactive_tracks_both_stores),
    ("reactive: duplicate key reads produce one connection",test_reactive_no_duplicate_connections),
    ("reactive: subsequent calls run without rewiring",     test_reactive_subsequent_calls_do_not_rewire),
    ("reactive: GC disconnects signal connections",         test_reactive_gc_disconnects),
]


if __name__ == "__main__":
    print(f"\nRunning {len(TESTS)} tests\n")
    for name, fn in TESTS:
        run(name, fn)

    print(f"\n{len(passed)} passed, {len(failed)} failed")
    if failed:
        print("\nFailed tests:")
        for name in failed:
            print(f"  - {name}")
        sys.exit(1)
    else:
        print("\nAll tests passed.")
        sys.exit(0)
