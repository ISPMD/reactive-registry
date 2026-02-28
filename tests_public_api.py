"""
test_public_api.py
------------------
Tests and benchmarks for the Registry package.

Usage
-----
    python test_public_api.py

Runs all unit tests first, then two benchmark suites:

  1. Registry benchmarks  — real Registry + real PySide6 signals. Measures
     memory, settings read throughput, theme read throughput, translation read
     throughput, and theme-switch / language-switch signal fan-out across
     10 000 wired instances using @registry.reactive (per-instance tracking).

  2. Comparison benchmark — real Registry + real PySide6 signals. Directly
     compares @registry.reactive (per-instance tracking) against
     @registry.reactive_class (class-level tracking) on wiring time, dispatch
     time, GC cleanup time, and live memory. Both modes use QObject-based
     widgets and real stores so numbers are directly comparable and reflect
     what you would actually pay in a PySide6 application.

  3. Persistence benchmark — measures save() and load() throughput across
     varying store sizes (small / medium / large) and reports per-call timing
     and file sizes.

Covers
------
    SettingsModel    — get, set, on, changed, as_dict, load_dict, None policy
    ThemeStore       — register, unregister, set_theme, set_mode, toggle_mode,
                       get, on, changed, theme_changed, as_dict, active_theme,
                       active_mode, None policy, state-before-signals ordering
    TranslationStore — register, unregister, set_language, get, fallback,
                       interpolation, as_dict, active_language, None policy,
                       language_changed signal
    @registry.reactive       — dependency tracking, auto re-run, multi-store
                               (settings + theme + translations), deduplication,
                               GC cleanup, one-connection-per-language semantics
    @registry.reactive_class — class-level equivalents of the above
    Persistence      — save/load round-trip, file format, missing-file,
                       malformed JSON, missing sections, None values in load,
                       invalid theme definitions in load, result.ok / result.error
                       / result.warnings contract
"""

import sys
import gc
import weakref
import time
import json
import tempfile
import tracemalloc
from pathlib import Path

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QObject

# QApplication must exist before any QObject or Signal is instantiated.
app = QApplication.instance() or QApplication(sys.argv)

# Import after QApplication is created.
from Registry import settings, theme, registry
from Registry.Settings import SettingsModel
from Registry.Theme import ThemeStore
from Registry.Translation import TranslationStore
from Registry.Reactive import ReactiveDescriptor, reactive_class_decorator
from Registry.Persistence import save, load, SaveResult, LoadResult


# ===========================================================================
# Helpers
# ===========================================================================

def make_settings(**defaults):
    """Return a fresh SettingsModel with the given defaults."""
    return SettingsModel(defaults=defaults or None)


def make_theme():
    """Return a fresh ThemeStore (no active theme)."""
    return ThemeStore()


def make_translations():
    """Return a fresh TranslationStore (no active language)."""
    return TranslationStore()


DARK_LIGHT = {
    "dark":  {"color.bg": "#000", "color.fg": "#fff", "font.size": 14},
    "light": {"color.bg": "#fff", "color.fg": "#000", "font.size": 14},
}

SECOND_THEME = {
    "dark":  {"color.bg": "#111", "color.fg": "#eee", "font.size": 16},
    "light": {"color.bg": "#eee", "color.fg": "#111", "font.size": 16},
}

EN_PACK = {"greeting": "Hello", "farewell": "Goodbye", "welcome": "Hello, {name}!"}
ES_PACK = {"greeting": "Hola",  "farewell": "Adiós",   "welcome": "Hola, {name}!"}

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
# TranslationStore tests
# ===========================================================================

def test_translations_get_returns_value():
    tr = make_translations()
    tr.register("en", EN_PACK)
    tr.set_language("en")
    assert tr.get("greeting") == "Hello"

def test_translations_get_missing_key_returns_key():
    """Missing key with no custom fallback returns the key itself."""
    tr = make_translations()
    tr.register("en", EN_PACK)
    tr.set_language("en")
    assert tr.get("no.such.key") == "no.such.key"

def test_translations_get_missing_key_custom_fallback():
    tr = make_translations()
    tr.register("en", EN_PACK)
    tr.set_language("en")
    assert tr.get("missing", fallback="???") == "???"

def test_translations_get_empty_string_fallback():
    """An explicit empty-string fallback must be returned, not the key."""
    tr = make_translations()
    tr.register("en", EN_PACK)
    tr.set_language("en")
    assert tr.get("missing", fallback="") == ""

def test_translations_get_interpolation():
    tr = make_translations()
    tr.register("en", EN_PACK)
    tr.set_language("en")
    assert tr.get("welcome", name="Alice") == "Hello, Alice!"

def test_translations_get_interpolation_bad_key_returns_raw():
    """A format call with wrong kwargs must return the raw string, not raise."""
    tr = make_translations()
    tr.register("en", EN_PACK)
    tr.set_language("en")
    result = tr.get("welcome", wrong_kwarg="x")
    assert result == "Hello, {name}!"

def test_translations_active_language():
    tr = make_translations()
    assert tr.active_language is None
    tr.register("en", EN_PACK)
    tr.set_language("en")
    assert tr.active_language == "en"

def test_translations_set_language_switches_pack():
    tr = make_translations()
    tr.register("en", EN_PACK)
    tr.register("es", ES_PACK)
    tr.set_language("en")
    assert tr.get("greeting") == "Hello"
    tr.set_language("es")
    assert tr.get("greeting") == "Hola"

def test_translations_as_dict_returns_snapshot():
    tr = make_translations()
    tr.register("en", EN_PACK)
    tr.set_language("en")
    d = tr.as_dict()
    assert d["greeting"] == "Hello"
    d["greeting"] = "tampered"  # mutation must not affect the store
    assert tr.get("greeting") == "Hello"

def test_translations_language_changed_signal():
    tr = make_translations()
    tr.register("en", EN_PACK)
    tr.register("es", ES_PACK)
    tr.set_language("en")
    received = []
    tr.language_changed.connect(lambda lang: received.append(lang))
    tr.set_language("es")
    app.processEvents()
    assert received == ["es"]

def test_translations_language_changed_state_committed_before_signal():
    """The language_changed handler must see the new language and new strings."""
    tr = make_translations()
    tr.register("en", EN_PACK)
    tr.register("es", ES_PACK)
    tr.set_language("en")
    seen = []
    tr.language_changed.connect(lambda lang: seen.append((lang, tr.get("greeting"))))
    tr.set_language("es")
    app.processEvents()
    assert seen == [("es", "Hola")]

def test_translations_none_value_raises():
    tr = make_translations()
    try:
        tr.register("bad", {"greeting": None})
        assert False, "Expected ValueError"
    except ValueError:
        pass

def test_translations_set_language_unknown_raises():
    tr = make_translations()
    try:
        tr.set_language("xx")
        assert False, "Expected KeyError"
    except KeyError:
        pass

def test_translations_unregister():
    tr = make_translations()
    tr.register("en", EN_PACK)
    tr.register("es", ES_PACK)
    tr.set_language("en")
    tr.unregister("es")
    try:
        tr.set_language("es")
        assert False, "Expected KeyError"
    except KeyError:
        pass

def test_translations_unregister_active_raises():
    tr = make_translations()
    tr.register("en", EN_PACK)
    tr.set_language("en")
    try:
        tr.unregister("en")
        assert False, "Expected RuntimeError"
    except RuntimeError:
        pass

def test_translations_unregister_unknown_raises():
    tr = make_translations()
    try:
        tr.unregister("xx")
        assert False, "Expected KeyError"
    except KeyError:
        pass

def test_translations_register_replaces_active():
    """Re-registering the active language must refresh live tokens immediately."""
    tr = make_translations()
    tr.register("en", EN_PACK)
    tr.set_language("en")
    updated = {**EN_PACK, "greeting": "Hey"}
    tr.register("en", updated)
    assert tr.get("greeting") == "Hey"

def test_translations_register_replaces_inactive_no_effect():
    """Re-registering an inactive language must not change the active tokens."""
    tr = make_translations()
    tr.register("en", EN_PACK)
    tr.register("es", ES_PACK)
    tr.set_language("en")
    tr.register("es", {**ES_PACK, "greeting": "Buenos días"})
    assert tr.get("greeting") == "Hello"  # en still active, unchanged


# ===========================================================================
# @registry.reactive tests
# ===========================================================================

def _make_local_registry():
    """Return a fresh Registry with its own SettingsModel, ThemeStore, and
    TranslationStore pre-loaded with test data."""
    from Registry.Registry import Registry as _Registry
    r = _Registry()
    r.settings.set("volume", 50)
    r.theme.register("base", DARK_LIGHT)
    r.theme.set_theme("base")
    r.translations.register("en", EN_PACK)
    r.translations.register("es", ES_PACK)
    r.translations.set_language("en")
    return r


def _make_fake_widget(r):
    """Return a _FakeWidget class bound to the given registry instance.
    Reads from all three stores so all three are covered by tracking."""

    class _FakeWidget(QObject):
        def __init__(self):
            super().__init__()
            self.calls = []

        @r.reactive
        def refresh(self):
            vol      = r.settings.get("volume")
            bg       = r.theme.get("color.bg")
            greeting = r.translations.get("greeting")
            self.calls.append((vol, bg, greeting))

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

def test_reactive_reruns_on_language_change():
    """A language switch must trigger a re-run; the new greeting must be visible."""
    r = _make_local_registry()
    W = _make_fake_widget(r)
    w = W()
    w.refresh()
    r.translations.set_language("es")
    app.processEvents()
    assert len(w.calls) == 2
    assert w.calls[-1][2] == "Hola"

def test_reactive_tracks_all_three_stores():
    """A change in any of the three stores must independently trigger a re-run."""
    r = _make_local_registry()
    W = _make_fake_widget(r)
    w = W()
    w.refresh()
    r.settings.set("volume", 1)
    app.processEvents()
    r.theme.set_mode("light")
    app.processEvents()
    r.translations.set_language("es")
    app.processEvents()
    assert len(w.calls) == 4

def test_reactive_language_single_connection_regardless_of_keys_read():
    """Reading multiple translation keys must wire exactly one connection to
    the TranslationStore, not one per key. A language switch must trigger one
    re-run, not multiple."""
    r = _make_local_registry()
    calls = []

    class W(QObject):
        def __init__(self):
            super().__init__()

        @r.reactive
        def refresh(self):
            # Reads three translation keys — must still produce one connection.
            r.translations.get("greeting")
            r.translations.get("farewell")
            r.translations.get("welcome")
            calls.append(1)

    w = W()
    w.refresh()
    r.translations.set_language("es")
    app.processEvents()
    assert len(calls) == 2  # initial run + exactly one re-run

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
    """All signal connections (including the translation connection) must be
    cleaned up when the instance is GC'd."""
    r = _make_local_registry()
    calls = []

    class W(QObject):
        def __init__(self):
            super().__init__()

        @r.reactive
        def refresh(self):
            _ = r.settings.get("volume")
            _ = r.translations.get("greeting")
            calls.append(1)

    w = W()
    w.refresh()
    assert len(calls) == 1

    # Verify both connections are live before GC.
    r.settings.set("volume", 1)
    app.processEvents()
    assert len(calls) == 2, "settings connection not wired"
    r.translations.set_language("es")
    app.processEvents()
    assert len(calls) == 3, "translation connection not wired"

    ref = weakref.ref(w)
    del w
    for _ in range(3):
        gc.collect()

    assert ref() is None, "Instance was not garbage collected"

    before = len(calls)
    r.settings.set("volume", 2)
    app.processEvents()
    r.translations.set_language("en")
    app.processEvents()
    assert len(calls) == before, (
        f"Handler still connected after GC: calls grew from {before} to {len(calls)}"
    )

def test_reactive_instance_isolation():
    """Two instances of the same class must have independent connections."""
    r = _make_local_registry()

    class W(QObject):
        def __init__(self):
            super().__init__()
            self.calls = []

        @r.reactive
        def refresh(self):
            _ = r.settings.get("volume")
            _ = r.translations.get("greeting")
            self.calls.append(1)

    w1 = W(); w1.refresh()
    w2 = W(); w2.refresh()

    r.settings.set("volume", 99)
    app.processEvents()
    assert len(w1.calls) == 2, f"w1 expected 2 calls, got {len(w1.calls)}"
    assert len(w2.calls) == 2, f"w2 expected 2 calls, got {len(w2.calls)}"

def test_reactive_exception_does_not_leave_partial_wiring():
    """If the method raises on its first call, no connections must be wired."""
    r = _make_local_registry()
    bomb = [True]

    class W(QObject):
        def __init__(self):
            super().__init__()
            self.calls = []

        @r.reactive
        def refresh(self):
            _ = r.settings.get("volume")
            _ = r.translations.get("greeting")
            if bomb[0]:
                raise RuntimeError("intentional")
            self.calls.append(1)

    w = W()
    try:
        w.refresh()
        assert False, "Expected RuntimeError"
    except RuntimeError:
        pass

    descriptor = W.__dict__["refresh"]
    assert w not in descriptor._wired, "Instance incorrectly marked wired after exception"

    bomb[0] = False
    w.refresh()
    assert len(w.calls) == 1

    r.settings.set("volume", 99)
    app.processEvents()
    assert len(w.calls) == 2

def test_reactive_zero_keys_no_crash():
    """A reactive method that reads no store keys must run without error."""
    r = _make_local_registry()
    calls = []

    class W(QObject):
        def __init__(self):
            super().__init__()

        @r.reactive
        def refresh(self):
            calls.append(1)

    w = W()
    w.refresh()
    w.refresh()
    assert len(calls) == 2

    r.settings.set("volume", 99)
    r.translations.set_language("es")
    app.processEvents()
    assert len(calls) == 2

def test_reactive_nested_calls_stack_safety():
    """A reactive method that calls another reactive method must track deps
    independently — the inner call must not pollute the outer call's dep list."""
    r = _make_local_registry()

    class W(QObject):
        def __init__(self):
            super().__init__()
            self.outer_calls = []
            self.inner_calls = []

        @r.reactive
        def inner(self):
            _ = r.theme.get("color.bg")
            _ = r.translations.get("greeting")
            self.inner_calls.append(1)

        @r.reactive
        def outer(self):
            _ = r.settings.get("volume")
            self.inner()
            self.outer_calls.append(1)

    w = W()
    w.outer()

    # Volume change: outer re-runs (calls inner as part of body, which is fine).
    r.settings.set("volume", 99)
    app.processEvents()
    assert len(w.outer_calls) == 2, "outer did not re-run on volume change"

    # Theme / language change: inner re-runs via its own connection; outer must NOT.
    before_outer = len(w.outer_calls)
    r.theme.set_mode("light")
    app.processEvents()
    r.translations.set_language("es")
    app.processEvents()
    assert len(w.inner_calls) >= 3, "inner did not re-run on theme/language change"
    assert len(w.outer_calls) == before_outer, (
        "outer incorrectly re-ran on theme/language change — dep lists were mixed"
    )


# ===========================================================================
# @registry.reactive_class tests
# ===========================================================================

def _make_class_registry():
    """Fresh Registry pre-loaded with a theme, a settings key, and translations."""
    from Registry.Registry import Registry as _Registry
    r = _Registry()
    r.settings.set("volume", 50)
    r.theme.register("base", DARK_LIGHT)
    r.theme.set_theme("base")
    r.translations.register("en", EN_PACK)
    r.translations.register("es", ES_PACK)
    r.translations.set_language("en")
    return r


def _make_class_widget(r):
    """Return a @registry.reactive_class QObject widget that reads from all
    three stores."""

    @r.reactive_class
    class W(QObject):
        def __init__(self):
            super().__init__()
            self.calls = []

        @r.reactive
        def refresh(self):
            vol      = r.settings.get("volume")
            bg       = r.theme.get("color.bg")
            greeting = r.translations.get("greeting")
            self.calls.append((vol, bg, greeting))

    return W


def test_reactive_class_first_instance_tracks_and_wires():
    r = _make_class_registry()
    W = _make_class_widget(r)
    w = W()
    w.refresh()
    assert len(w.calls) == 1
    r.settings.set("volume", 99)
    app.processEvents()
    assert len(w.calls) == 2
    assert w.calls[-1][0] == 99

def test_reactive_class_second_instance_does_not_rewire():
    r = _make_class_registry()
    W = _make_class_widget(r)
    w1 = W(); w1.refresh()
    w2 = W(); w2.refresh()
    assert len(w1.calls) == 1
    assert len(w2.calls) == 1
    r.settings.set("volume", 77)
    app.processEvents()
    assert len(w1.calls) == 2
    assert len(w2.calls) == 2

def test_reactive_class_all_instances_rerun_on_dispatch():
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

def test_reactive_class_reruns_on_language_change():
    """A language switch must trigger a re-run on all living class-level instances."""
    r = _make_class_registry()
    W = _make_class_widget(r)
    instances = [W() for _ in range(3)]
    for w in instances:
        w.refresh()
    r.translations.set_language("es")
    app.processEvents()
    for i, w in enumerate(instances):
        assert len(w.calls) == 2, (
            f"instance {i} expected 2 calls after language switch, got {len(w.calls)}"
        )
        assert w.calls[-1][2] == "Hola", (
            f"instance {i} did not see updated greeting"
        )

def test_reactive_class_language_single_connection():
    """Class-level tracking must wire exactly one connection to the
    TranslationStore regardless of how many translation keys are read or
    how many instances exist."""
    r = _make_class_registry()
    calls = []

    @r.reactive_class
    class W(QObject):
        def __init__(self):
            super().__init__()

        @r.reactive
        def refresh(self):
            r.translations.get("greeting")
            r.translations.get("farewell")
            r.translations.get("welcome")
            calls.append(1)

    instances = [W() for _ in range(5)]
    for w in instances:
        w.refresh()

    r.translations.set_language("es")
    app.processEvents()

    # Each instance must have re-run exactly once — 5 initial + 5 re-runs.
    assert len(calls) == 10

def test_reactive_class_gcd_instance_skipped_on_dispatch():
    r = _make_class_registry()
    W = _make_class_widget(r)
    w1 = W(); w1.refresh()
    w2 = W(); w2.refresh()
    ref = weakref.ref(w1)
    del w1
    for _ in range(3):
        gc.collect()
    assert ref() is None, "w1 was not garbage collected"
    r.settings.set("volume", 11)
    app.processEvents()
    assert len(w2.calls) == 2

def test_reactive_class_connections_persist_after_all_instances_gcd():
    r = _make_class_registry()
    W = _make_class_widget(r)
    w1 = W(); w1.refresh()
    del w1
    for _ in range(3):
        gc.collect()
    w2 = W(); w2.refresh()
    assert len(w2.calls) == 1
    r.translations.set_language("es")
    app.processEvents()
    assert len(w2.calls) == 2

def test_reactive_class_multiple_methods_wire_independently():
    r = _make_class_registry()

    @r.reactive_class
    class W(QObject):
        def __init__(self):
            super().__init__()
            self.a_calls = []
            self.b_calls = []
            self.c_calls = []

        @r.reactive
        def method_a(self):
            _ = r.settings.get("volume")
            self.a_calls.append(1)

        @r.reactive
        def method_b(self):
            _ = r.theme.get("color.bg")
            self.b_calls.append(1)

        @r.reactive
        def method_c(self):
            _ = r.translations.get("greeting")
            self.c_calls.append(1)

    w = W()
    w.method_a(); w.method_b(); w.method_c()

    # Volume → method_a only
    r.settings.set("volume", 99)
    app.processEvents()
    assert len(w.a_calls) == 2
    assert len(w.b_calls) == 1
    assert len(w.c_calls) == 1

    # Theme → method_b only
    r.theme.set_mode("light")
    app.processEvents()
    assert len(w.b_calls) == 2
    assert len(w.a_calls) == 2
    assert len(w.c_calls) == 1

    # Language → method_c only
    r.translations.set_language("es")
    app.processEvents()
    assert len(w.c_calls) == 2
    assert len(w.a_calls) == 2
    assert len(w.b_calls) == 2

def test_reactive_class_does_not_affect_other_classes():
    r = _make_class_registry()

    @r.reactive_class
    class ClassWidget(QObject):
        def __init__(self):
            super().__init__()
            self.calls = []

        @r.reactive
        def refresh(self):
            _ = r.settings.get("volume")
            _ = r.translations.get("greeting")
            self.calls.append(1)

    class InstanceWidget(QObject):
        def __init__(self):
            super().__init__()
            self.calls = []

        @r.reactive
        def refresh(self):
            _ = r.settings.get("volume")
            _ = r.translations.get("greeting")
            self.calls.append(1)

    cw = ClassWidget();    cw.refresh()
    iw = InstanceWidget(); iw.refresh()

    r.translations.set_language("es")
    app.processEvents()
    assert len(cw.calls) == 2, "ClassWidget did not re-run on language change"
    assert len(iw.calls) == 2, "InstanceWidget did not re-run on language change"

def test_reactive_class_exception_does_not_corrupt_wiring():
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
            _ = r.translations.get("greeting")
            if bomb[0]:
                raise RuntimeError("intentional")
            self.calls.append(1)

    w1 = W()
    try:
        w1.refresh()
        assert False, "Expected RuntimeError"
    except RuntimeError:
        pass

    descriptor = W.__dict__["refresh"]
    assert W not in descriptor._cls_wired, (
        "Class incorrectly marked wired after first-instance exception"
    )

    bomb[0] = False
    w2 = W(); w2.refresh()
    assert len(w2.calls) == 1

    r.translations.set_language("es")
    app.processEvents()
    assert len(w2.calls) == 2

def test_reactive_class_reruns_on_theme_change():
    r = _make_class_registry()
    W = _make_class_widget(r)
    w = W(); w.refresh()
    r.theme.set_mode("light")
    app.processEvents()
    assert len(w.calls) == 2
    assert w.calls[-1][1] == "#fff"

def test_reactive_class_tracks_all_three_stores():
    r = _make_class_registry()
    W = _make_class_widget(r)
    w = W(); w.refresh()
    r.settings.set("volume", 1)
    app.processEvents()
    assert len(w.calls) == 2
    r.theme.set_mode("light")
    app.processEvents()
    assert len(w.calls) == 3
    r.translations.set_language("es")
    app.processEvents()
    assert len(w.calls) == 4


# ===========================================================================
# Persistence tests
#
# save() and load() operate on the module-level registry singleton, so these
# tests use that singleton directly. Each test seeds the singleton stores with
# known state, exercises save/load, then asserts the expected outcome.
#
# Isolation strategy: each test function is self-contained — it writes only the
# keys/themes/packs it cares about and asserts only those. Tests do not assume
# the stores are empty, so leftover state from other tests does not cause
# spurious failures.
#
# File I/O uses tempfile.NamedTemporaryFile so no test artefacts are left on
# disk regardless of pass/fail.
# ===========================================================================

def _tmp_path():
    """Return a Path to a fresh temporary file (deleted on close by caller)."""
    f = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    f.close()
    return Path(f.name)


def test_persistence_save_returns_ok():
    """save() on a populated singleton registry must return result.ok == True."""
    registry.settings.set("persist.volume", 42)
    registry.theme.register("persist.base", DARK_LIGHT)
    registry.theme.set_theme("persist.base")
    registry.translations.register("persist.en", EN_PACK)
    registry.translations.set_language("persist.en")

    p = _tmp_path()
    try:
        result = save(p)
        assert result.ok, f"save() returned ok=False: error={result.error}, warnings={result.warnings}"
        assert result.error is None
        assert result.warnings == []
    finally:
        p.unlink(missing_ok=True)


def test_persistence_save_creates_valid_json():
    """The file written by save() must be valid JSON with the expected top-level keys."""
    registry.settings.set("persist.check", 1)
    p = _tmp_path()
    try:
        save(p)
        raw = p.read_text(encoding="utf-8")
        payload = json.loads(raw)
        assert isinstance(payload, dict), "Top-level value is not a dict"
        assert "settings" in payload
        assert "theme" in payload
        assert "translations" in payload
    finally:
        p.unlink(missing_ok=True)


def test_persistence_round_trip_settings():
    """Settings written by save() must be restored exactly by load()."""
    registry.settings.set("persist.rt.volume", 77)
    registry.settings.set("persist.rt.muted", True)

    p = _tmp_path()
    try:
        save(p)

        # Overwrite the values so we can verify load() restores them.
        registry.settings.set("persist.rt.volume", 0)
        registry.settings.set("persist.rt.muted", False)

        result = load(p)
        assert result.ok, f"load() returned ok=False: {result.error} / {result.warnings}"
        assert registry.settings.get("persist.rt.volume") == 77
        assert registry.settings.get("persist.rt.muted") == True
    finally:
        p.unlink(missing_ok=True)


def test_persistence_round_trip_theme():
    """Theme definitions, active theme, and active mode must survive a round-trip."""
    registry.theme.register("persist.rt.base", DARK_LIGHT)
    registry.theme.register("persist.rt.second", SECOND_THEME)
    registry.theme.set_theme("persist.rt.base")
    registry.theme.set_mode("light")

    p = _tmp_path()
    try:
        save(p)

        # Switch away so load() has something to restore.
        registry.theme.set_theme("persist.rt.second")
        registry.theme.set_mode("dark")

        result = load(p)
        assert result.ok, f"load() returned ok=False: {result.error} / {result.warnings}"
        assert registry.theme.active_theme == "persist.rt.base"
        assert registry.theme.active_mode == "light"
        assert registry.theme.get("color.bg") == "#fff"   # light variant of DARK_LIGHT
    finally:
        p.unlink(missing_ok=True)


def test_persistence_round_trip_translations():
    """All registered packs and the active language must survive a round-trip."""
    registry.translations.register("persist.rt.en", EN_PACK)
    registry.translations.register("persist.rt.es", ES_PACK)
    registry.translations.set_language("persist.rt.es")

    p = _tmp_path()
    try:
        save(p)

        # Switch away so load() has something to restore.
        registry.translations.set_language("persist.rt.en")

        result = load(p)
        assert result.ok, f"load() returned ok=False: {result.error} / {result.warnings}"
        assert registry.translations.active_language == "persist.rt.es"
        assert registry.translations.get("greeting") == "Hola"
    finally:
        p.unlink(missing_ok=True)


def test_persistence_round_trip_all_stores():
    """A single save/load cycle must restore all three stores simultaneously."""
    registry.settings.set("persist.all.x", 99)
    registry.theme.register("persist.all.base", DARK_LIGHT)
    registry.theme.set_theme("persist.all.base")
    registry.theme.set_mode("dark")
    registry.translations.register("persist.all.en", EN_PACK)
    registry.translations.set_language("persist.all.en")

    p = _tmp_path()
    try:
        save(p)

        registry.settings.set("persist.all.x", 0)
        registry.theme.set_mode("light")
        registry.translations.register("persist.all.es", ES_PACK)
        registry.translations.set_language("persist.all.es")

        result = load(p)
        assert result.ok, f"load() returned ok=False: {result.error} / {result.warnings}"
        assert registry.settings.get("persist.all.x") == 99
        assert registry.theme.active_mode == "dark"
        assert registry.theme.get("color.bg") == "#000"
        assert registry.translations.active_language == "persist.all.en"
        assert registry.translations.get("greeting") == "Hello"
    finally:
        p.unlink(missing_ok=True)


def test_persistence_load_missing_file():
    """load() on a non-existent path must return result.ok=False with a
    FileNotFoundError set on result.error. Stores must not be modified."""
    before = registry.settings.get("persist.missing.sentinel", default="untouched")
    result = load("/tmp/__registry_no_such_file_12345.json")
    assert not result.ok
    assert isinstance(result.error, FileNotFoundError)
    assert registry.settings.get("persist.missing.sentinel", default="untouched") == before


def test_persistence_load_malformed_json():
    """load() on a file containing invalid JSON must return result.ok=False with
    a json.JSONDecodeError and must not modify any stores."""
    p = _tmp_path()
    try:
        p.write_text("{this is not valid json", encoding="utf-8")
        result = load(p)
        assert not result.ok
        assert isinstance(result.error, json.JSONDecodeError)
    finally:
        p.unlink(missing_ok=True)


def test_persistence_load_non_object_json():
    """load() on a file containing a JSON array (not an object) must fail cleanly."""
    p = _tmp_path()
    try:
        p.write_text("[1, 2, 3]", encoding="utf-8")
        result = load(p)
        assert not result.ok
        assert isinstance(result.error, ValueError)
    finally:
        p.unlink(missing_ok=True)


def test_persistence_load_missing_settings_section():
    """A file with no 'settings' key must produce a warning and still load other stores."""
    registry.translations.register("persist.sec.en", EN_PACK)
    registry.translations.set_language("persist.sec.en")

    p = _tmp_path()
    try:
        save(p)
        # Strip out the settings section from the saved file.
        payload = json.loads(p.read_text(encoding="utf-8"))
        del payload["settings"]
        p.write_text(json.dumps(payload), encoding="utf-8")

        result = load(p)
        assert not result.ok
        assert any("settings" in w for w in result.warnings), (
            f"Expected a warning about missing settings section, got: {result.warnings}"
        )
    finally:
        p.unlink(missing_ok=True)


def test_persistence_load_missing_theme_section():
    """A file with no 'theme' key must produce a warning."""
    p = _tmp_path()
    try:
        registry.settings.set("persist.theme_sec.x", 1)
        save(p)
        payload = json.loads(p.read_text(encoding="utf-8"))
        del payload["theme"]
        p.write_text(json.dumps(payload), encoding="utf-8")

        result = load(p)
        assert not result.ok
        assert any("theme" in w for w in result.warnings), (
            f"Expected a warning about missing theme section, got: {result.warnings}"
        )
    finally:
        p.unlink(missing_ok=True)


def test_persistence_load_missing_translations_section():
    """A file with no 'translations' key must produce a warning."""
    p = _tmp_path()
    try:
        registry.settings.set("persist.tr_sec.x", 1)
        save(p)
        payload = json.loads(p.read_text(encoding="utf-8"))
        del payload["translations"]
        p.write_text(json.dumps(payload), encoding="utf-8")

        result = load(p)
        assert not result.ok
        assert any("translations" in w for w in result.warnings), (
            f"Expected a warning about missing translations section, got: {result.warnings}"
        )
    finally:
        p.unlink(missing_ok=True)


def test_persistence_load_none_value_in_settings_produces_warning():
    """A None value in the settings section of the JSON must be skipped with a
    warning rather than crashing."""
    p = _tmp_path()
    try:
        registry.settings.set("persist.none.good", 1)
        save(p)
        payload = json.loads(p.read_text(encoding="utf-8"))
        payload["settings"]["persist.none.injected"] = None
        p.write_text(json.dumps(payload), encoding="utf-8")

        result = load(p)
        assert not result.ok
        assert any("persist.none.injected" in w for w in result.warnings), (
            f"Expected a warning about None value, got: {result.warnings}"
        )
        # The None key must not have been set in the store.
        assert registry.settings.get("persist.none.injected") is None
    finally:
        p.unlink(missing_ok=True)


def test_persistence_load_invalid_theme_definition_produces_warning():
    """A theme definition missing a variant must produce a warning and not crash."""
    p = _tmp_path()
    try:
        registry.settings.set("persist.bad_theme.x", 1)
        save(p)
        payload = json.loads(p.read_text(encoding="utf-8"))
        # Insert a malformed theme (missing 'light' variant).
        payload["theme"]["themes"]["persist.bad_theme"] = {"dark": {"x": "#000"}}
        p.write_text(json.dumps(payload), encoding="utf-8")

        result = load(p)
        assert not result.ok
        assert any("persist.bad_theme" in w for w in result.warnings), (
            f"Expected a warning about the bad theme, got: {result.warnings}"
        )
    finally:
        p.unlink(missing_ok=True)


def test_persistence_save_result_type():
    """save() must always return a SaveResult instance."""
    p = _tmp_path()
    try:
        result = save(p)
        assert isinstance(result, SaveResult)
    finally:
        p.unlink(missing_ok=True)


def test_persistence_load_result_type():
    """load() must always return a LoadResult instance, even on failure."""
    result = load("/tmp/__registry_no_such_file_99999.json")
    assert isinstance(result, LoadResult)


def test_persistence_save_bad_path():
    """save() to an unwritable path must return result.ok=False with result.error set."""
    result = save("/no_such_directory/registry_test.json")
    assert not result.ok
    assert result.error is not None


def test_persistence_file_is_human_readable():
    """The saved JSON must be indented (pretty-printed), not a single line."""
    registry.settings.set("persist.readable.x", 1)
    p = _tmp_path()
    try:
        save(p)
        raw = p.read_text(encoding="utf-8")
        lines = raw.splitlines()
        assert len(lines) > 5, (
            "JSON appears to be a single line — expected indented output"
        )
    finally:
        p.unlink(missing_ok=True)


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

    # TranslationStore
    ("translations: get returns translated value",                      test_translations_get_returns_value),
    ("translations: get missing key returns key as fallback",           test_translations_get_missing_key_returns_key),
    ("translations: get missing key with custom fallback",              test_translations_get_missing_key_custom_fallback),
    ("translations: get missing key with empty-string fallback",        test_translations_get_empty_string_fallback),
    ("translations: get with interpolation",                            test_translations_get_interpolation),
    ("translations: get interpolation bad kwarg returns raw string",    test_translations_get_interpolation_bad_key_returns_raw),
    ("translations: active_language property",                          test_translations_active_language),
    ("translations: set_language switches active pack",                 test_translations_set_language_switches_pack),
    ("translations: as_dict returns snapshot",                          test_translations_as_dict_returns_snapshot),
    ("translations: language_changed signal fires",                     test_translations_language_changed_signal),
    ("translations: state committed before language_changed fires",     test_translations_language_changed_state_committed_before_signal),
    ("translations: None value raises ValueError",                      test_translations_none_value_raises),
    ("translations: set_language unknown raises KeyError",              test_translations_set_language_unknown_raises),
    ("translations: unregister removes language",                       test_translations_unregister),
    ("translations: unregister active language raises RuntimeError",    test_translations_unregister_active_raises),
    ("translations: unregister unknown raises KeyError",                test_translations_unregister_unknown_raises),
    ("translations: re-register active language refreshes tokens",      test_translations_register_replaces_active),
    ("translations: re-register inactive language has no effect",       test_translations_register_replaces_inactive_no_effect),

    # @registry.reactive
    ("reactive: first call runs method",                                           test_reactive_first_call_runs),
    ("reactive: reruns on settings change",                                        test_reactive_reruns_on_settings_change),
    ("reactive: reruns on theme change",                                           test_reactive_reruns_on_theme_change),
    ("reactive: reruns on language change",                                        test_reactive_reruns_on_language_change),
    ("reactive: tracks all three stores",                                          test_reactive_tracks_all_three_stores),
    ("reactive: multiple translation keys produce one connection",                 test_reactive_language_single_connection_regardless_of_keys_read),
    ("reactive: duplicate key reads produce one connection",                       test_reactive_no_duplicate_connections),
    ("reactive: subsequent calls run without rewiring",                            test_reactive_subsequent_calls_do_not_rewire),
    ("reactive: GC disconnects all signal connections incl. translation",          test_reactive_gc_disconnects),
    ("reactive: two instances are independently wired",                            test_reactive_instance_isolation),
    ("reactive: exception on first call leaves no wiring state",                   test_reactive_exception_does_not_leave_partial_wiring),
    ("reactive: zero-key method runs without crash or wiring",                     test_reactive_zero_keys_no_crash),
    ("reactive: nested reactive calls track deps independently",                   test_reactive_nested_calls_stack_safety),

    # @registry.reactive_class
    ("reactive_class: first instance tracks and wires",                            test_reactive_class_first_instance_tracks_and_wires),
    ("reactive_class: second instance joins without rewiring",                     test_reactive_class_second_instance_does_not_rewire),
    ("reactive_class: all living instances rerun on dispatch",                     test_reactive_class_all_instances_rerun_on_dispatch),
    ("reactive_class: reruns on language change",                                  test_reactive_class_reruns_on_language_change),
    ("reactive_class: multiple translation keys produce one class connection",     test_reactive_class_language_single_connection),
    ("reactive_class: GC'd instance silently skipped on dispatch",                 test_reactive_class_gcd_instance_skipped_on_dispatch),
    ("reactive_class: connections persist after all instances GC'd",               test_reactive_class_connections_persist_after_all_instances_gcd),
    ("reactive_class: multiple methods wire independently across all stores",      test_reactive_class_multiple_methods_wire_independently),
    ("reactive_class: does not affect plain @reactive classes",                    test_reactive_class_does_not_affect_other_classes),
    ("reactive_class: exception on first call leaves no wiring",                   test_reactive_class_exception_does_not_corrupt_wiring),
    ("reactive_class: reruns on theme change",                                     test_reactive_class_reruns_on_theme_change),
    ("reactive_class: tracks all three stores",                                    test_reactive_class_tracks_all_three_stores),

    # Persistence
    ("persistence: save() returns SaveResult with ok=True",                        test_persistence_save_returns_ok),
    ("persistence: save() writes valid JSON with all three sections",              test_persistence_save_creates_valid_json),
    ("persistence: round-trip restores settings values",                           test_persistence_round_trip_settings),
    ("persistence: round-trip restores theme definitions and active state",        test_persistence_round_trip_theme),
    ("persistence: round-trip restores translation packs and active language",     test_persistence_round_trip_translations),
    ("persistence: round-trip restores all three stores simultaneously",           test_persistence_round_trip_all_stores),
    ("persistence: load() missing file → ok=False, FileNotFoundError",            test_persistence_load_missing_file),
    ("persistence: load() malformed JSON → ok=False, JSONDecodeError",            test_persistence_load_malformed_json),
    ("persistence: load() non-object JSON → ok=False, ValueError",                test_persistence_load_non_object_json),
    ("persistence: load() missing settings section → warning, no crash",          test_persistence_load_missing_settings_section),
    ("persistence: load() missing theme section → warning, no crash",             test_persistence_load_missing_theme_section),
    ("persistence: load() missing translations section → warning, no crash",      test_persistence_load_missing_translations_section),
    ("persistence: load() None value in settings → warning, key skipped",         test_persistence_load_none_value_in_settings_produces_warning),
    ("persistence: load() invalid theme definition → warning, no crash",          test_persistence_load_invalid_theme_definition_produces_warning),
    ("persistence: save() always returns SaveResult",                              test_persistence_save_result_type),
    ("persistence: load() always returns LoadResult, even on failure",            test_persistence_load_result_type),
    ("persistence: save() to unwritable path → ok=False, error set",              test_persistence_save_bad_path),
    ("persistence: saved JSON is human-readable (indented)",                       test_persistence_file_is_human_readable),
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
    return tracemalloc.take_snapshot()


def _net_bytes(before, after):
    return sum(s.size_diff for s in after.compare_to(before, "lineno") if s.size_diff > 0)


# ===========================================================================
# Benchmark 1 — Registry throughput (real PySide6 + real Registry)
# ===========================================================================

_BENCH_DARK  = {f"token.{i}": f"#dark{i:02d}" for i in range(10)}
_BENCH_LIGHT = {f"token.{i}": f"#lite{i:02d}" for i in range(10)}
_BENCH_THEME = {"dark": _BENCH_DARK, "light": _BENCH_LIGHT}
_BENCH_EN    = {f"str.{i}": f"English string {i}" for i in range(3)}
_BENCH_ES    = {f"str.{i}": f"Cadena española {i}" for i in range(3)}

_B1_N_INSTANCES     = 10_000
_B1_READ_ITERATIONS = 100
_B1_SWITCH_ITERS    = 20


def _make_bench_registry():
    from Registry.Registry import Registry as _Registry
    r = _Registry()
    r.settings.set("value", 0)
    r.theme.register("bench", _BENCH_THEME)
    r.theme.set_theme("bench")
    r.translations.register("en", _BENCH_EN)
    r.translations.register("es", _BENCH_ES)
    r.translations.set_language("en")
    return r


def _make_bench_widget_class(r):
    class BenchWidget(QObject):
        def __init__(self):
            super().__init__()

        @r.reactive
        def refresh(self):
            r.settings.get("value")
            for i in range(10):
                r.theme.get(f"token.{i}")
            for i in range(3):
                r.translations.get(f"str.{i}")

    return BenchWidget


def run_registry_benchmarks():
    print(f"\n{'─' * 68}")
    print(f"  Benchmark 1 — Registry throughput  (@registry.reactive, real PySide6)")
    print(f"  {_B1_N_INSTANCES:,} QObject instances, 10-token theme, 3-key translation pack")
    print(f"  Connections per instance: 1 settings + 10 theme + 1 translation sentinel = 12")
    print(f"{'─' * 68}")

    gc.collect()
    r = _make_bench_registry()
    BenchWidget = _make_bench_widget_class(r)

    tracemalloc.start()
    snap_before = _snap_mem()
    instances = [BenchWidget() for _ in range(_B1_N_INSTANCES)]
    for w in instances:
        w.refresh()
    snap_after = _snap_mem()
    tracemalloc.stop()

    net = _net_bytes(snap_before, snap_after)
    print(f"\n  [1/5] memory  ({_B1_N_INSTANCES:,} wired QObject instances)")
    print(f"    Python heap delta : {_fmt_mem(net)}")
    print(f"    per instance      : {_fmt_mem(net / _B1_N_INSTANCES)}")
    print(f"    note              : Qt C++ heap not included; actual RSS will be higher")

    t0 = time.perf_counter()
    for _ in range(_B1_READ_ITERATIONS):
        for _ in instances:
            r.settings.get("value")
    elapsed_s = time.perf_counter() - t0
    total_s = _B1_N_INSTANCES * _B1_READ_ITERATIONS
    print(f"\n  [2/5] settings read  ({_B1_N_INSTANCES:,} instances x {_B1_READ_ITERATIONS} iterations)")
    print(f"    total calls  : {total_s:,}")
    print(f"    elapsed      : {_fmt_time(elapsed_s)}")
    print(f"    throughput   : {total_s / elapsed_s:,.0f} calls/sec")
    print(f"    per call     : {_fmt_time(elapsed_s / total_s)}")

    t0 = time.perf_counter()
    for _ in range(_B1_READ_ITERATIONS):
        for _ in instances:
            for i in range(10):
                r.theme.get(f"token.{i}")
    elapsed_t = time.perf_counter() - t0
    total_t = _B1_N_INSTANCES * 10 * _B1_READ_ITERATIONS
    print(f"\n  [3/5] theme read  ({_B1_N_INSTANCES:,} instances x 10 tokens x {_B1_READ_ITERATIONS} iterations)")
    print(f"    total calls  : {total_t:,}")
    print(f"    elapsed      : {_fmt_time(elapsed_t)}")
    print(f"    throughput   : {total_t / elapsed_t:,.0f} calls/sec")
    print(f"    per call     : {_fmt_time(elapsed_t / total_t)}")

    t0 = time.perf_counter()
    for _ in range(_B1_READ_ITERATIONS):
        for _ in instances:
            for i in range(3):
                r.translations.get(f"str.{i}")
    elapsed_tr = time.perf_counter() - t0
    total_tr = _B1_N_INSTANCES * 3 * _B1_READ_ITERATIONS
    print(f"\n  [4/5] translation read  ({_B1_N_INSTANCES:,} instances x 3 keys x {_B1_READ_ITERATIONS} iterations)")
    print(f"    total calls  : {total_tr:,}")
    print(f"    elapsed      : {_fmt_time(elapsed_tr)}")
    print(f"    throughput   : {total_tr / elapsed_tr:,.0f} calls/sec")
    print(f"    per call     : {_fmt_time(elapsed_tr / total_tr)}")

    r.theme.set_mode("light"); app.processEvents()
    t0 = time.perf_counter()
    for i in range(_B1_SWITCH_ITERS):
        r.theme.set_mode("dark" if i % 2 == 0 else "light")
        app.processEvents()
    elapsed_sw = time.perf_counter() - t0
    deliveries_per_flip = _B1_N_INSTANCES * 10
    total_del = deliveries_per_flip * _B1_SWITCH_ITERS
    print(f"\n  [5/5] switches")
    print(f"    Theme switch  ({_B1_SWITCH_ITERS} set_mode() flips, {_B1_N_INSTANCES:,} instances x 10 tokens)")
    print(f"      signal deliveries : {total_del:,} total  ({deliveries_per_flip:,} per flip)")
    print(f"      total elapsed     : {_fmt_time(elapsed_sw)}")
    print(f"      per flip          : {_fmt_time(elapsed_sw / _B1_SWITCH_ITERS)}")
    print(f"      delivery rate     : {total_del / elapsed_sw:,.0f} signals/sec")

    r.translations.set_language("es"); app.processEvents()
    t0 = time.perf_counter()
    for i in range(_B1_SWITCH_ITERS):
        r.translations.set_language("en" if i % 2 == 0 else "es")
        app.processEvents()
    elapsed_lang = time.perf_counter() - t0
    lang_del_per_switch = _B1_N_INSTANCES * 1
    total_lang_del = lang_del_per_switch * _B1_SWITCH_ITERS
    print(f"\n    Language switch  ({_B1_SWITCH_ITERS} set_language() calls, {_B1_N_INSTANCES:,} instances x 1 sentinel)")
    print(f"      signal deliveries : {total_lang_del:,} total  ({lang_del_per_switch:,} per switch)")
    print(f"      total elapsed     : {_fmt_time(elapsed_lang)}")
    print(f"      per switch        : {_fmt_time(elapsed_lang / _B1_SWITCH_ITERS)}")
    print(f"      delivery rate     : {total_lang_del / elapsed_lang:,.0f} signals/sec")
    print(f"\n    note: language switch is ~10x fewer deliveries than theme switch")
    print(f"          because TranslationStore uses 1 sentinel connection per method,")
    print(f"          not 1 per translation key.")

    del instances
    gc.collect()
    print(f"\n{'─' * 68}")


# ===========================================================================
# Benchmark 2 — per-instance vs class-level comparison
# ===========================================================================

_B2_N_INSTANCES = 10_000
_B2_N_KEYS      = 10
_B2_TR_KEYS     = 3
_B2_KEYS        = [f"key_{i}" for i in range(_B2_N_KEYS)]
_B2_RUNS        = 3

_b2_call_counter = 0


def _b2_make_env(use_class_level):
    global _b2_call_counter
    store = SettingsModel()
    for k in _B2_KEYS:
        store.set(k, 0)

    tr_store = TranslationStore()
    tr_en = {f"str.{i}": f"en {i}" for i in range(_B2_TR_KEYS)}
    tr_es = {f"str.{i}": f"es {i}" for i in range(_B2_TR_KEYS)}
    tr_store.register("en", tr_en)
    tr_store.register("es", tr_es)
    tr_store.set_language("en")

    def refresh(self):
        global _b2_call_counter
        for k in _B2_KEYS:
            store.get(k)
        for i in range(_B2_TR_KEYS):
            tr_store.get(f"str.{i}")
        _b2_call_counter += 1

    if use_class_level:
        d = ReactiveDescriptor(refresh, stores=[store, tr_store])

        @reactive_class_decorator
        class Widget(QObject):
            def __init__(self):
                super().__init__()
            refresh = d
    else:
        d = ReactiveDescriptor(refresh, stores=[store, tr_store])

        class Widget(QObject):
            def __init__(self):
                super().__init__()
            refresh = d

    return store, tr_store, Widget


def _b2_bench_timing(use_class_level):
    global _b2_call_counter
    store, tr_store, Widget = _b2_make_env(use_class_level)

    _b2_call_counter = 0
    gc.disable()
    t0 = time.perf_counter()
    instances = [Widget() for _ in range(_B2_N_INSTANCES)]
    for inst in instances: inst.refresh()
    t_wire = time.perf_counter() - t0
    gc.enable()

    _b2_call_counter = 0
    gc.disable()
    t0 = time.perf_counter()
    for k in _B2_KEYS: store.set(k, store.get(k) + 1)
    t_dispatch_s = time.perf_counter() - t0
    gc.enable()
    dispatch_calls_s = _b2_call_counter

    _b2_call_counter = 0
    gc.disable()
    t0 = time.perf_counter()
    tr_store.set_language("es")
    t_dispatch_l = time.perf_counter() - t0
    gc.enable()
    dispatch_calls_l = _b2_call_counter

    t_dispatch = t_dispatch_s + t_dispatch_l

    gc.disable()
    t0 = time.perf_counter()
    del instances; gc.collect()
    t_gc = time.perf_counter() - t0
    gc.enable()

    return t_wire, t_dispatch, t_gc, dispatch_calls_s + dispatch_calls_l


def _b2_bench_memory(use_class_level):
    store, tr_store, Widget = _b2_make_env(use_class_level)
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
    results = []
    for i in range(n):
        print(f"  {label} run {i+1}/{n}...", flush=True)
        results.append(fn())
    cols = list(zip(*results))
    return [sum(c) / n for c in cols]


def run_comparison_benchmark():
    print(f"\n{'─' * 68}")
    print(f"  Benchmark 2 — per-instance vs class-level  (real PySide6 QObjects)")
    print(f"  {_B2_N_INSTANCES:,} instances x {_B2_N_KEYS} settings/theme keys + {_B2_TR_KEYS} translation keys")
    print(f"  Connections: per-instance={_B2_N_INSTANCES*(_B2_N_KEYS+1):,}  "
          f"class-level={_B2_N_KEYS+1}  (1 sentinel per class)")
    print(f"  Timing averaged over {_B2_RUNS} runs")
    print(f"{'─' * 68}")

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
    conns_pi = _B2_N_INSTANCES * (_B2_N_KEYS + 1)
    conns_cl = _B2_N_KEYS + 1
    print(f"\n{'='*W}")
    print(f"  {_B2_N_INSTANCES:,} instances x {_B2_N_KEYS} keys + {_B2_TR_KEYS} translation keys")
    print(f"  Timing averaged over {_B2_RUNS} runs")
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
    print(f"    per-instance : {conns_pi:,}  "
          f"({_B2_N_INSTANCES:,} instances x ({_B2_N_KEYS} keys + 1 translation sentinel))")
    print(f"    class-level  : {conns_cl}  "
          f"({_B2_N_KEYS} keys + 1 translation sentinel, shared for whole class)")
    print(f"{'─' * 68}\n")


# ===========================================================================
# Benchmark 3 — Persistence throughput
#
# Measures save() and load() across three store sizes:
#   small  —   10 settings keys,  1 theme (3 tokens),  2 language packs ( 10 keys)
#   medium —  100 settings keys,  3 themes (20 tokens), 5 language packs ( 50 keys)
#   large  — 1000 settings keys, 10 themes (50 tokens), 10 language packs (200 keys)
#
# Each size is run _B3_RUNS times; timing is averaged. File size is also
# reported so you can see how payload size scales.
# ===========================================================================

_B3_RUNS = 5


def _b3_make_registry(n_settings, n_themes, tokens_per_theme, n_langs, keys_per_lang):
    """Return a fresh Registry populated to the requested scale."""
    from Registry.Registry import Registry as _Registry
    r = _Registry()

    for i in range(n_settings):
        r.settings.set(f"key.{i}", i)

    for t in range(n_themes):
        definition = {
            "dark":  {f"token.{k}": f"#dark{t:02d}{k:03d}" for k in range(tokens_per_theme)},
            "light": {f"token.{k}": f"#lite{t:02d}{k:03d}" for k in range(tokens_per_theme)},
        }
        r.theme.register(f"theme.{t}", definition)
    r.theme.set_theme("theme.0")

    for lang_idx in range(n_langs):
        lang = f"lang.{lang_idx}"
        pack = {f"str.{k}": f"Translation {lang_idx}-{k}" for k in range(keys_per_lang)}
        r.translations.register(lang, pack)
    r.translations.set_language("lang.0")

    return r


def _b3_bench_save(r, path, runs):
    times = []
    for _ in range(runs):
        from Registry.Persistence import save as _save
        # Temporarily monkey-patch the module's registry reference so save()
        # uses our isolated registry instance rather than the singleton.
        import Registry.Persistence as _pm
        _orig = _pm.registry
        _pm.registry = r
        try:
            t0 = time.perf_counter()
            _save(path)
            times.append(time.perf_counter() - t0)
        finally:
            _pm.registry = _orig
    return sum(times) / len(times), path.stat().st_size


def _b3_bench_load(r, path, runs):
    times = []
    for _ in range(runs):
        from Registry.Persistence import load as _load
        import Registry.Persistence as _pm
        _orig = _pm.registry
        _pm.registry = r
        try:
            t0 = time.perf_counter()
            _load(path)
            times.append(time.perf_counter() - t0)
        finally:
            _pm.registry = _orig
    return sum(times) / len(times)


def run_persistence_benchmark():
    print(f"\n{'─' * 68}")
    print(f"  Benchmark 3 — Persistence throughput  (save / load, {_B3_RUNS} runs each)")
    print(f"{'─' * 68}")

    sizes = [
        ("small",  10,   1,  3,  2,  10),
        ("medium", 100,  3, 20,  5,  50),
        ("large",  1000, 10, 50, 10, 200),
    ]
    # (label, n_settings, n_themes, tokens_per_theme, n_langs, keys_per_lang)

    W = 68
    print(f"\n  {'Size':<8} {'Settings':>10} {'Themes':>8} {'Langs':>7} "
          f"{'File':>9} {'save()':>10} {'load()':>10}")
    print(f"  {'-'*(W-2)}")

    p = _tmp_path()
    try:
        for label, n_s, n_t, tok, n_l, kpl in sizes:
            r = _b3_make_registry(n_s, n_t, tok, n_l, kpl)
            avg_save, file_size = _b3_bench_save(r, p, _B3_RUNS)
            avg_load            = _b3_bench_load(r, p, _B3_RUNS)

            print(
                f"  {label:<8} {n_s:>10,} {n_t:>8} {n_l:>7} "
                f"{_fmt_mem(file_size):>9} {_fmt_time(avg_save):>10} {_fmt_time(avg_load):>10}"
            )
    finally:
        p.unlink(missing_ok=True)

    print(f"  {'-'*(W-2)}")
    print(f"\n  note: file sizes include indentation (json.dumps indent=2).")
    print(f"        load() re-registers all themes/packs and re-activates the")
    print(f"        active theme/language on every call — it is not purely I/O.")
    print(f"\n{'─' * 68}\n")


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
    run_persistence_benchmark()

    if failed:
        sys.exit(1)
    else:
        print("All tests passed.")
        sys.exit(0)