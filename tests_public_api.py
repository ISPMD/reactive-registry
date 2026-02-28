"""
test_public_api.py
------------------
Tests and benchmarks for the Registry package.

Usage
-----
    python test_public_api.py

Runs all unit tests first, then three benchmark suites.

Covers
------
    SettingsModel    — get, set, on, changed, as_dict, load_dict, None policy
    ThemeStore       — register, unregister, set_theme, set_mode, toggle_mode,
                       get, on, changed, theme_changed, as_dict, active_theme,
                       active_mode, None policy, state-before-signals ordering
    TranslationStore — register, unregister, set_language, get, fallback,
                       interpolation, as_dict, active_language, None policy,
                       language_changed signal
    @registry.reactive       — dependency tracking, auto re-run, multi-store,
                               deduplication, GC cleanup, translation semantics
    @registry.reactive_class — class-level equivalents of the above
    Persistence      — all eight public functions:
                         save / load
                         save_state / load_state
                         save_themes / load_themes
                         save_translations / load_translations
                       Happy paths, round-trips, file format, error handling,
                       partial failure / warnings contract, store isolation,
                       interaction between pairs (e.g. save_state + load_state
                       requires pre-registered themes).
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

app = QApplication.instance() or QApplication(sys.argv)

from Registry import settings, theme, registry
from Registry.Settings import SettingsModel
from Registry.Theme import ThemeStore
from Registry.Translation import TranslationStore
from Registry.Reactive import ReactiveDescriptor, reactive_class_decorator
from Registry.Persistence import (
    save, load,
    save_state, load_state,
    save_themes, load_themes,
    save_translations, load_translations,
    SaveResult, LoadResult,
)


# ===========================================================================
# Helpers
# ===========================================================================

def make_settings(**defaults):
    return SettingsModel(defaults=defaults or None)

def make_theme():
    return ThemeStore()

def make_translations():
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
    try:
        fn()
        passed.append(name)
        print(f"  PASS  {name}")
    except Exception as e:
        failed.append(name)
        print(f"  FAIL  {name}")
        print(f"        {type(e).__name__}: {e}")

def _tmp_path():
    f = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    f.close()
    return Path(f.name)


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
    s.set("volume", 50)
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
    d["a"] = 99
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
    s.load_dict({"x": 10, "y": 99})
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
    assert t.get("color.bg") == "#000"

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
    d["color.bg"] = "tampered"
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
    t.set_mode("light")
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
    t = make_theme()
    t.register("base", DARK_LIGHT)
    t.set_theme("base")
    seen_mode = []
    t.on("color.bg").connect(lambda v: seen_mode.append(t.active_mode))
    t.set_mode("light")
    app.processEvents()
    assert seen_mode == ["light"]

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
    d["greeting"] = "tampered"
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
    tr = make_translations()
    tr.register("en", EN_PACK)
    tr.set_language("en")
    updated = {**EN_PACK, "greeting": "Hey"}
    tr.register("en", updated)
    assert tr.get("greeting") == "Hey"

def test_translations_register_replaces_inactive_no_effect():
    tr = make_translations()
    tr.register("en", EN_PACK)
    tr.register("es", ES_PACK)
    tr.set_language("en")
    tr.register("es", {**ES_PACK, "greeting": "Buenos días"})
    assert tr.get("greeting") == "Hello"


# ===========================================================================
# @registry.reactive tests
# ===========================================================================

def _make_local_registry():
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
    r = _make_local_registry()
    W = _make_fake_widget(r)
    w = W()
    w.refresh()
    r.translations.set_language("es")
    app.processEvents()
    assert len(w.calls) == 2
    assert w.calls[-1][2] == "Hola"

def test_reactive_tracks_all_three_stores():
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
    r = _make_local_registry()
    calls = []

    class W(QObject):
        def __init__(self):
            super().__init__()

        @r.reactive
        def refresh(self):
            r.translations.get("greeting")
            r.translations.get("farewell")
            r.translations.get("welcome")
            calls.append(1)

    w = W()
    w.refresh()
    r.translations.set_language("es")
    app.processEvents()
    assert len(calls) == 2

def test_reactive_no_duplicate_connections():
    r = _make_local_registry()
    calls = []

    class W(QObject):
        def __init__(self):
            super().__init__()

        @r.reactive
        def refresh(self):
            _ = r.settings.get("volume")
            _ = r.settings.get("volume")
            calls.append(1)

    w = W()
    w.refresh()
    r.settings.set("volume", 2)
    app.processEvents()
    assert len(calls) == 2

def test_reactive_subsequent_calls_do_not_rewire():
    r = _make_local_registry()
    W = _make_fake_widget(r)
    w = W()
    w.refresh()
    w.refresh()
    w.refresh()
    assert len(w.calls) == 3
    r.settings.set("volume", 1)
    app.processEvents()
    assert len(w.calls) == 4

def test_reactive_gc_disconnects():
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
    r.settings.set("volume", 1)
    app.processEvents()
    assert len(calls) == 2
    r.translations.set_language("es")
    app.processEvents()
    assert len(calls) == 3

    ref = weakref.ref(w)
    del w
    for _ in range(3):
        gc.collect()
    assert ref() is None

    before = len(calls)
    r.settings.set("volume", 2)
    app.processEvents()
    r.translations.set_language("en")
    app.processEvents()
    assert len(calls) == before

def test_reactive_instance_isolation():
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
    assert len(w1.calls) == 2
    assert len(w2.calls) == 2

def test_reactive_exception_does_not_leave_partial_wiring():
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
        assert False
    except RuntimeError:
        pass

    descriptor = W.__dict__["refresh"]
    assert w not in descriptor._wired

    bomb[0] = False
    w.refresh()
    assert len(w.calls) == 1
    r.settings.set("volume", 99)
    app.processEvents()
    assert len(w.calls) == 2

def test_reactive_zero_keys_no_crash():
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
    r.settings.set("volume", 99)
    app.processEvents()
    assert len(w.outer_calls) == 2

    before_outer = len(w.outer_calls)
    r.theme.set_mode("light")
    app.processEvents()
    r.translations.set_language("es")
    app.processEvents()
    assert len(w.inner_calls) >= 3
    assert len(w.outer_calls) == before_outer


# ===========================================================================
# @registry.reactive_class tests
# ===========================================================================

def _make_class_registry():
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
    r.settings.set("volume", 77)
    app.processEvents()
    assert len(w1.calls) == 2
    assert len(w2.calls) == 2

def test_reactive_class_all_instances_rerun_on_dispatch():
    r = _make_class_registry()
    W = _make_class_widget(r)
    instances = [W() for _ in range(5)]
    for w in instances: w.refresh()
    r.settings.set("volume", 42)
    app.processEvents()
    for i, w in enumerate(instances):
        assert len(w.calls) == 2

def test_reactive_class_reruns_on_language_change():
    r = _make_class_registry()
    W = _make_class_widget(r)
    instances = [W() for _ in range(3)]
    for w in instances: w.refresh()
    r.translations.set_language("es")
    app.processEvents()
    for w in instances:
        assert len(w.calls) == 2
        assert w.calls[-1][2] == "Hola"

def test_reactive_class_language_single_connection():
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
    for w in instances: w.refresh()
    r.translations.set_language("es")
    app.processEvents()
    assert len(calls) == 10

def test_reactive_class_gcd_instance_skipped_on_dispatch():
    r = _make_class_registry()
    W = _make_class_widget(r)
    w1 = W(); w1.refresh()
    w2 = W(); w2.refresh()
    ref = weakref.ref(w1)
    del w1
    for _ in range(3): gc.collect()
    assert ref() is None
    r.settings.set("volume", 11)
    app.processEvents()
    assert len(w2.calls) == 2

def test_reactive_class_connections_persist_after_all_instances_gcd():
    r = _make_class_registry()
    W = _make_class_widget(r)
    w1 = W(); w1.refresh()
    del w1
    for _ in range(3): gc.collect()
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

    r.settings.set("volume", 99)
    app.processEvents()
    assert len(w.a_calls) == 2
    assert len(w.b_calls) == 1
    assert len(w.c_calls) == 1

    r.theme.set_mode("light")
    app.processEvents()
    assert len(w.b_calls) == 2
    assert len(w.a_calls) == 2
    assert len(w.c_calls) == 1

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
    assert len(cw.calls) == 2
    assert len(iw.calls) == 2

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
        assert False
    except RuntimeError:
        pass

    descriptor = W.__dict__["refresh"]
    assert W not in descriptor._cls_wired

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
# Persistence helpers
#
# All persistence tests use isolated Registry instances via _patch_registry()
# so they never touch the module-level singleton. This keeps tests hermetic
# and avoids ordering/isolation issues.
# ===========================================================================

import contextlib
import Registry.Persistence as _pm

@contextlib.contextmanager
def _patch_registry(r):
    """Temporarily replace Registry.Persistence.registry with r."""
    orig = _pm.registry
    _pm.registry = r
    try:
        yield r
    finally:
        _pm.registry = orig


def _make_persist_registry():
    """Fresh Registry with two themes, two language packs, and a few settings."""
    from Registry.Registry import Registry as _Registry
    r = _Registry()
    r.settings.set("volume", 72)
    r.settings.set("muted", False)
    r.theme.register("Slate", DARK_LIGHT)
    r.theme.register("Rose", SECOND_THEME)
    r.theme.set_theme("Slate")
    r.theme.set_mode("dark")
    r.translations.register("en", EN_PACK)
    r.translations.register("es", ES_PACK)
    r.translations.set_language("en")
    return r


# ---------------------------------------------------------------------------
# save / load
# ---------------------------------------------------------------------------

def test_save_returns_ok():
    r = _make_persist_registry()
    p = _tmp_path()
    try:
        with _patch_registry(r):
            result = save(p)
        assert isinstance(result, SaveResult)
        assert result.ok
        assert result.error is None
        assert result.warnings == []
    finally:
        p.unlink(missing_ok=True)


def test_save_creates_valid_json_with_three_sections():
    r = _make_persist_registry()
    p = _tmp_path()
    try:
        with _patch_registry(r):
            save(p)
        payload = json.loads(p.read_text())
        assert isinstance(payload, dict)
        assert "settings" in payload
        assert "theme" in payload
        assert "translations" in payload
    finally:
        p.unlink(missing_ok=True)


def test_save_json_is_indented():
    r = _make_persist_registry()
    p = _tmp_path()
    try:
        with _patch_registry(r):
            save(p)
        raw = p.read_text()
        assert len(raw.splitlines()) > 5, "Expected indented JSON"
    finally:
        p.unlink(missing_ok=True)


def test_save_load_round_trip_settings():
    r = _make_persist_registry()
    p = _tmp_path()
    try:
        with _patch_registry(r):
            save(p)
        r.settings.set("volume", 0)
        r.settings.set("muted", True)
        with _patch_registry(r):
            result = load(p)
        assert result.ok
        assert r.settings.get("volume") == 72
        assert r.settings.get("muted") == False
    finally:
        p.unlink(missing_ok=True)


def test_save_load_round_trip_theme():
    r = _make_persist_registry()
    p = _tmp_path()
    try:
        with _patch_registry(r):
            save(p)
        r.theme.set_theme("Rose")
        r.theme.set_mode("light")
        with _patch_registry(r):
            result = load(p)
        assert result.ok
        assert r.theme.active_theme == "Slate"
        assert r.theme.active_mode == "dark"
        assert r.theme.get("color.bg") == "#000"
    finally:
        p.unlink(missing_ok=True)


def test_save_load_round_trip_translations():
    r = _make_persist_registry()
    p = _tmp_path()
    try:
        with _patch_registry(r):
            save(p)
        r.translations.set_language("es")
        with _patch_registry(r):
            result = load(p)
        assert result.ok
        assert r.translations.active_language == "en"
        assert r.translations.get("greeting") == "Hello"
    finally:
        p.unlink(missing_ok=True)


def test_save_load_round_trip_all_three():
    r = _make_persist_registry()
    p = _tmp_path()
    try:
        with _patch_registry(r):
            save(p)
        r.settings.set("volume", 0)
        r.theme.set_mode("light")
        r.translations.set_language("es")
        with _patch_registry(r):
            result = load(p)
        assert result.ok
        assert r.settings.get("volume") == 72
        assert r.theme.active_mode == "dark"
        assert r.translations.active_language == "en"
    finally:
        p.unlink(missing_ok=True)


def test_load_missing_file():
    r = _make_persist_registry()
    with _patch_registry(r):
        result = load("/tmp/__registry_no_such_file_11111.json")
    assert not result.ok
    assert isinstance(result.error, FileNotFoundError)


def test_load_malformed_json():
    p = _tmp_path()
    try:
        p.write_text("{bad json", encoding="utf-8")
        r = _make_persist_registry()
        with _patch_registry(r):
            result = load(p)
        assert not result.ok
        assert isinstance(result.error, json.JSONDecodeError)
    finally:
        p.unlink(missing_ok=True)


def test_load_non_object_json():
    p = _tmp_path()
    try:
        p.write_text("[1, 2, 3]", encoding="utf-8")
        r = _make_persist_registry()
        with _patch_registry(r):
            result = load(p)
        assert not result.ok
        assert isinstance(result.error, ValueError)
    finally:
        p.unlink(missing_ok=True)


def test_load_missing_settings_section_warns():
    r = _make_persist_registry()
    p = _tmp_path()
    try:
        with _patch_registry(r):
            save(p)
        payload = json.loads(p.read_text())
        del payload["settings"]
        p.write_text(json.dumps(payload), encoding="utf-8")
        with _patch_registry(r):
            result = load(p)
        assert not result.ok
        assert any("settings" in w for w in result.warnings)
    finally:
        p.unlink(missing_ok=True)


def test_load_missing_theme_section_warns():
    r = _make_persist_registry()
    p = _tmp_path()
    try:
        with _patch_registry(r):
            save(p)
        payload = json.loads(p.read_text())
        del payload["theme"]
        p.write_text(json.dumps(payload), encoding="utf-8")
        with _patch_registry(r):
            result = load(p)
        assert not result.ok
        assert any("theme" in w for w in result.warnings)
    finally:
        p.unlink(missing_ok=True)


def test_load_missing_translations_section_warns():
    r = _make_persist_registry()
    p = _tmp_path()
    try:
        with _patch_registry(r):
            save(p)
        payload = json.loads(p.read_text())
        del payload["translations"]
        p.write_text(json.dumps(payload), encoding="utf-8")
        with _patch_registry(r):
            result = load(p)
        assert not result.ok
        assert any("translations" in w for w in result.warnings)
    finally:
        p.unlink(missing_ok=True)


def test_load_none_value_in_settings_warns_and_skips():
    r = _make_persist_registry()
    p = _tmp_path()
    try:
        with _patch_registry(r):
            save(p)
        payload = json.loads(p.read_text())
        payload["settings"]["injected_none"] = None
        p.write_text(json.dumps(payload), encoding="utf-8")
        with _patch_registry(r):
            result = load(p)
        assert not result.ok
        assert any("injected_none" in w for w in result.warnings)
        assert r.settings.get("injected_none") is None
    finally:
        p.unlink(missing_ok=True)


def test_load_invalid_theme_definition_warns():
    r = _make_persist_registry()
    p = _tmp_path()
    try:
        with _patch_registry(r):
            save(p)
        payload = json.loads(p.read_text())
        payload["theme"]["themes"]["BadTheme"] = {"dark": {"x": "#000"}}  # missing light
        p.write_text(json.dumps(payload), encoding="utf-8")
        with _patch_registry(r):
            result = load(p)
        assert not result.ok
        assert any("BadTheme" in w for w in result.warnings)
    finally:
        p.unlink(missing_ok=True)


def test_save_to_unwritable_path():
    r = _make_persist_registry()
    with _patch_registry(r):
        result = save("/no_such_dir/registry.json")
    assert not result.ok
    assert result.error is not None


def test_load_returns_load_result_type_on_failure():
    r = _make_persist_registry()
    with _patch_registry(r):
        result = load("/tmp/__registry_no_such_file_22222.json")
    assert isinstance(result, LoadResult)


# ---------------------------------------------------------------------------
# save_state / load_state
# ---------------------------------------------------------------------------

def test_save_state_returns_ok():
    r = _make_persist_registry()
    p = _tmp_path()
    try:
        with _patch_registry(r):
            result = save_state(p)
        assert isinstance(result, SaveResult)
        assert result.ok
        assert result.error is None
        assert result.warnings == []
    finally:
        p.unlink(missing_ok=True)


def test_save_state_file_format():
    """save_state must produce a flat object with settings, active_theme,
    active_mode, active_language — and NO themes or packs."""
    r = _make_persist_registry()
    p = _tmp_path()
    try:
        with _patch_registry(r):
            save_state(p)
        payload = json.loads(p.read_text())
        assert "settings" in payload
        assert "active_theme" in payload
        assert "active_mode" in payload
        assert "active_language" in payload
        # Must not contain full definitions
        assert "themes" not in payload
        assert "packs" not in payload
        assert "theme" not in payload
        assert "translations" not in payload
    finally:
        p.unlink(missing_ok=True)


def test_save_state_settings_values_correct():
    r = _make_persist_registry()
    p = _tmp_path()
    try:
        with _patch_registry(r):
            save_state(p)
        payload = json.loads(p.read_text())
        assert payload["settings"]["volume"] == 72
        assert payload["settings"]["muted"] == False
        assert payload["active_theme"] == "Slate"
        assert payload["active_mode"] == "dark"
        assert payload["active_language"] == "en"
    finally:
        p.unlink(missing_ok=True)


def test_save_state_load_state_round_trip():
    r = _make_persist_registry()
    p = _tmp_path()
    try:
        with _patch_registry(r):
            save_state(p)
        r.settings.set("volume", 0)
        r.theme.set_mode("light")
        r.translations.set_language("es")
        with _patch_registry(r):
            result = load_state(p)
        assert result.ok
        assert r.settings.get("volume") == 72
        assert r.theme.active_mode == "dark"
        assert r.translations.active_language == "en"
    finally:
        p.unlink(missing_ok=True)


def test_load_state_requires_pre_registered_themes():
    """load_state uses set_theme() not register() — the theme must already exist.
    If it doesn't, the result must have a warning and ok=False."""
    from Registry.Registry import Registry as _Registry
    r = _Registry()
    r.settings.set("volume", 5)
    # Intentionally do NOT register any theme

    p = _tmp_path()
    try:
        payload = {
            "settings": {"volume": 5},
            "active_theme": "NonExistentTheme",
            "active_mode": "dark",
            "active_language": None,
        }
        p.write_text(json.dumps(payload), encoding="utf-8")
        with _patch_registry(r):
            result = load_state(p)
        assert not result.ok
        assert any("NonExistentTheme" in w for w in result.warnings)
    finally:
        p.unlink(missing_ok=True)


def test_load_state_requires_pre_registered_language():
    """load_state uses set_language() not register() — the language must already exist."""
    from Registry.Registry import Registry as _Registry
    r = _Registry()
    r.theme.register("Slate", DARK_LIGHT)
    r.theme.set_theme("Slate")
    # Intentionally do NOT register any translations

    p = _tmp_path()
    try:
        payload = {
            "settings": {},
            "active_theme": "Slate",
            "active_mode": "dark",
            "active_language": "xx",
        }
        p.write_text(json.dumps(payload), encoding="utf-8")
        with _patch_registry(r):
            result = load_state(p)
        assert not result.ok
        assert any("xx" in w for w in result.warnings)
    finally:
        p.unlink(missing_ok=True)


def test_load_state_does_not_touch_theme_definitions():
    """load_state must not register or unregister any themes — only activate."""
    r = _make_persist_registry()
    p = _tmp_path()
    try:
        with _patch_registry(r):
            save_state(p)
        # Add a new theme after saving
        r.theme.register("ExtraTheme", SECOND_THEME)
        with _patch_registry(r):
            load_state(p)
        # ExtraTheme must still be registered — load_state didn't touch it
        r.theme.set_theme("ExtraTheme")  # raises if not registered
    finally:
        p.unlink(missing_ok=True)


def test_load_state_does_not_touch_translation_packs():
    """load_state must not register or unregister any language packs."""
    r = _make_persist_registry()
    p = _tmp_path()
    try:
        with _patch_registry(r):
            save_state(p)
        r.translations.register("fr", {"greeting": "Bonjour"})
        with _patch_registry(r):
            load_state(p)
        # fr must still be registered
        r.translations.set_language("fr")
        assert r.translations.get("greeting") == "Bonjour"
    finally:
        p.unlink(missing_ok=True)


def test_load_state_missing_file():
    r = _make_persist_registry()
    with _patch_registry(r):
        result = load_state("/tmp/__registry_no_such_file_33333.json")
    assert not result.ok
    assert isinstance(result.error, FileNotFoundError)


def test_load_state_malformed_json():
    p = _tmp_path()
    try:
        p.write_text("{bad json", encoding="utf-8")
        r = _make_persist_registry()
        with _patch_registry(r):
            result = load_state(p)
        assert not result.ok
        assert isinstance(result.error, json.JSONDecodeError)
    finally:
        p.unlink(missing_ok=True)


def test_save_state_to_unwritable_path():
    r = _make_persist_registry()
    with _patch_registry(r):
        result = save_state("/no_such_dir/state.json")
    assert not result.ok
    assert result.error is not None


def test_save_state_file_is_indented():
    r = _make_persist_registry()
    p = _tmp_path()
    try:
        with _patch_registry(r):
            save_state(p)
        assert len(p.read_text().splitlines()) > 5
    finally:
        p.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# save_themes / load_themes
# ---------------------------------------------------------------------------

def test_save_themes_returns_ok():
    r = _make_persist_registry()
    p = _tmp_path()
    try:
        with _patch_registry(r):
            result = save_themes(p)
        assert isinstance(result, SaveResult)
        assert result.ok
        assert result.error is None
        assert result.warnings == []
    finally:
        p.unlink(missing_ok=True)


def test_save_themes_file_format():
    """save_themes must produce a standalone theme object with active_theme,
    active_mode, and themes — no settings or translations data."""
    r = _make_persist_registry()
    p = _tmp_path()
    try:
        with _patch_registry(r):
            save_themes(p)
        payload = json.loads(p.read_text())
        assert "active_theme" in payload
        assert "active_mode" in payload
        assert "themes" in payload
        assert isinstance(payload["themes"], dict)
        # Must not contain settings or translations
        assert "settings" not in payload
        assert "packs" not in payload
        assert "active_language" not in payload
    finally:
        p.unlink(missing_ok=True)


def test_save_themes_contains_all_registered_themes():
    r = _make_persist_registry()
    p = _tmp_path()
    try:
        with _patch_registry(r):
            save_themes(p)
        payload = json.loads(p.read_text())
        assert "Slate" in payload["themes"]
        assert "Rose" in payload["themes"]
        assert payload["active_theme"] == "Slate"
        assert payload["active_mode"] == "dark"
    finally:
        p.unlink(missing_ok=True)


def test_save_themes_load_themes_round_trip():
    r = _make_persist_registry()
    p = _tmp_path()
    try:
        with _patch_registry(r):
            save_themes(p)
        r.theme.set_theme("Rose")
        r.theme.set_mode("light")
        with _patch_registry(r):
            result = load_themes(p)
        assert result.ok
        assert r.theme.active_theme == "Slate"
        assert r.theme.active_mode == "dark"
        assert r.theme.get("color.bg") == "#000"
    finally:
        p.unlink(missing_ok=True)


def test_load_themes_re_registers_definitions():
    """load_themes must register theme definitions so they are available via set_theme()."""
    from Registry.Registry import Registry as _Registry
    r_src = _make_persist_registry()
    r_dst = _Registry()
    # r_dst starts with an empty theme store

    p = _tmp_path()
    try:
        with _patch_registry(r_src):
            save_themes(p)
        with _patch_registry(r_dst):
            result = load_themes(p)
        assert result.ok
        assert r_dst.theme.active_theme == "Slate"
        # Both themes from r_src must be available in r_dst
        r_dst.theme.set_theme("Rose")
        assert r_dst.theme.get("color.bg") == "#111"
    finally:
        p.unlink(missing_ok=True)


def test_load_themes_on_empty_store():
    """load_themes on an empty ThemeStore must succeed without warnings."""
    from Registry.Registry import Registry as _Registry
    r_src = _make_persist_registry()
    r_dst = _Registry()

    p = _tmp_path()
    try:
        with _patch_registry(r_src):
            save_themes(p)
        with _patch_registry(r_dst):
            result = load_themes(p)
        assert result.ok, f"Expected ok=True on empty store, got warnings={result.warnings}"
    finally:
        p.unlink(missing_ok=True)


def test_load_themes_does_not_touch_settings_or_translations():
    """load_themes must leave settings and translations completely untouched."""
    r = _make_persist_registry()
    p = _tmp_path()
    try:
        with _patch_registry(r):
            save_themes(p)
        r.settings.set("volume", 99)
        r.translations.set_language("es")
        with _patch_registry(r):
            load_themes(p)
        assert r.settings.get("volume") == 99
        assert r.translations.active_language == "es"
    finally:
        p.unlink(missing_ok=True)


def test_load_themes_invalid_definition_warns():
    r = _make_persist_registry()
    p = _tmp_path()
    try:
        with _patch_registry(r):
            save_themes(p)
        payload = json.loads(p.read_text())
        payload["themes"]["BadTheme"] = {"dark": {"x": "#000"}}  # missing light
        p.write_text(json.dumps(payload), encoding="utf-8")
        with _patch_registry(r):
            result = load_themes(p)
        assert not result.ok
        assert any("BadTheme" in w for w in result.warnings)
    finally:
        p.unlink(missing_ok=True)


def test_load_themes_unknown_active_theme_warns():
    """If active_theme names a theme that failed to register, a warning must appear."""
    r = _make_persist_registry()
    p = _tmp_path()
    try:
        with _patch_registry(r):
            save_themes(p)
        payload = json.loads(p.read_text())
        payload["active_theme"] = "GhostTheme"  # not in themes dict
        p.write_text(json.dumps(payload), encoding="utf-8")
        with _patch_registry(r):
            result = load_themes(p)
        assert not result.ok
        assert any("GhostTheme" in w for w in result.warnings)
    finally:
        p.unlink(missing_ok=True)


def test_save_themes_missing_file():
    r = _make_persist_registry()
    with _patch_registry(r):
        result = load_themes("/tmp/__registry_no_such_file_44444.json")
    assert not result.ok
    assert isinstance(result.error, FileNotFoundError)


def test_save_themes_to_unwritable_path():
    r = _make_persist_registry()
    with _patch_registry(r):
        result = save_themes("/no_such_dir/themes.json")
    assert not result.ok
    assert result.error is not None


def test_save_themes_file_is_indented():
    r = _make_persist_registry()
    p = _tmp_path()
    try:
        with _patch_registry(r):
            save_themes(p)
        assert len(p.read_text().splitlines()) > 5
    finally:
        p.unlink(missing_ok=True)


def test_save_load_themes_preserves_dark_light_tokens():
    """Both dark and light variant tokens must survive the round-trip."""
    r = _make_persist_registry()
    p = _tmp_path()
    try:
        with _patch_registry(r):
            save_themes(p)
        r.theme.set_mode("light")
        with _patch_registry(r):
            load_themes(p)
        # active_mode restored to dark; light tokens still accessible
        assert r.theme.active_mode == "dark"
        r.theme.set_mode("light")
        assert r.theme.get("color.bg") == "#fff"
    finally:
        p.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# save_translations / load_translations
# ---------------------------------------------------------------------------

def test_save_translations_returns_ok():
    r = _make_persist_registry()
    p = _tmp_path()
    try:
        with _patch_registry(r):
            result = save_translations(p)
        assert isinstance(result, SaveResult)
        assert result.ok
        assert result.error is None
        assert result.warnings == []
    finally:
        p.unlink(missing_ok=True)


def test_save_translations_file_format():
    """save_translations must produce a standalone translations object with
    active_language and packs — no settings or theme data."""
    r = _make_persist_registry()
    p = _tmp_path()
    try:
        with _patch_registry(r):
            save_translations(p)
        payload = json.loads(p.read_text())
        assert "active_language" in payload
        assert "packs" in payload
        assert isinstance(payload["packs"], dict)
        assert "settings" not in payload
        assert "themes" not in payload
        assert "active_theme" not in payload
        assert "active_mode" not in payload
    finally:
        p.unlink(missing_ok=True)


def test_save_translations_contains_all_registered_packs():
    r = _make_persist_registry()
    p = _tmp_path()
    try:
        with _patch_registry(r):
            save_translations(p)
        payload = json.loads(p.read_text())
        assert "en" in payload["packs"]
        assert "es" in payload["packs"]
        assert payload["active_language"] == "en"
        assert payload["packs"]["en"]["greeting"] == "Hello"
        assert payload["packs"]["es"]["greeting"] == "Hola"
    finally:
        p.unlink(missing_ok=True)


def test_save_translations_load_translations_round_trip():
    r = _make_persist_registry()
    p = _tmp_path()
    try:
        with _patch_registry(r):
            save_translations(p)
        r.translations.set_language("es")
        with _patch_registry(r):
            result = load_translations(p)
        assert result.ok
        assert r.translations.active_language == "en"
        assert r.translations.get("greeting") == "Hello"
    finally:
        p.unlink(missing_ok=True)


def test_load_translations_re_registers_packs():
    """load_translations must register packs so they are available via set_language()."""
    from Registry.Registry import Registry as _Registry
    r_src = _make_persist_registry()
    r_dst = _Registry()

    p = _tmp_path()
    try:
        with _patch_registry(r_src):
            save_translations(p)
        with _patch_registry(r_dst):
            result = load_translations(p)
        assert result.ok
        assert r_dst.translations.active_language == "en"
        r_dst.translations.set_language("es")
        assert r_dst.translations.get("greeting") == "Hola"
    finally:
        p.unlink(missing_ok=True)


def test_load_translations_on_empty_store():
    """load_translations on an empty TranslationStore must succeed."""
    from Registry.Registry import Registry as _Registry
    r_src = _make_persist_registry()
    r_dst = _Registry()

    p = _tmp_path()
    try:
        with _patch_registry(r_src):
            save_translations(p)
        with _patch_registry(r_dst):
            result = load_translations(p)
        assert result.ok, f"Expected ok=True on empty store, warnings={result.warnings}"
    finally:
        p.unlink(missing_ok=True)


def test_load_translations_does_not_touch_settings_or_theme():
    """load_translations must leave settings and theme completely untouched."""
    r = _make_persist_registry()
    p = _tmp_path()
    try:
        with _patch_registry(r):
            save_translations(p)
        r.settings.set("volume", 55)
        r.theme.set_mode("light")
        with _patch_registry(r):
            load_translations(p)
        assert r.settings.get("volume") == 55
        assert r.theme.active_mode == "light"
    finally:
        p.unlink(missing_ok=True)


def test_load_translations_unknown_language_warns():
    """If active_language names a language not in the packs, a warning must appear."""
    r = _make_persist_registry()
    p = _tmp_path()
    try:
        with _patch_registry(r):
            save_translations(p)
        payload = json.loads(p.read_text())
        payload["active_language"] = "xx"  # not in packs
        p.write_text(json.dumps(payload), encoding="utf-8")
        with _patch_registry(r):
            result = load_translations(p)
        assert not result.ok
        assert any("xx" in w for w in result.warnings)
    finally:
        p.unlink(missing_ok=True)


def test_load_translations_none_value_in_pack_warns():
    """A None value inside a pack must produce a warning (register() will reject it)."""
    r = _make_persist_registry()
    p = _tmp_path()
    try:
        with _patch_registry(r):
            save_translations(p)
        payload = json.loads(p.read_text())
        payload["packs"]["fr"] = {"greeting": None}
        p.write_text(json.dumps(payload), encoding="utf-8")
        with _patch_registry(r):
            result = load_translations(p)
        assert not result.ok
        assert any("fr" in w for w in result.warnings)
    finally:
        p.unlink(missing_ok=True)


def test_load_translations_packs_not_a_dict_warns():
    p = _tmp_path()
    try:
        payload = {"active_language": "en", "packs": ["not", "a", "dict"]}
        p.write_text(json.dumps(payload), encoding="utf-8")
        r = _make_persist_registry()
        with _patch_registry(r):
            result = load_translations(p)
        assert not result.ok
        assert any("packs" in w for w in result.warnings)
    finally:
        p.unlink(missing_ok=True)


def test_save_translations_missing_file():
    r = _make_persist_registry()
    with _patch_registry(r):
        result = load_translations("/tmp/__registry_no_such_file_55555.json")
    assert not result.ok
    assert isinstance(result.error, FileNotFoundError)


def test_save_translations_to_unwritable_path():
    r = _make_persist_registry()
    with _patch_registry(r):
        result = save_translations("/no_such_dir/translations.json")
    assert not result.ok
    assert result.error is not None


def test_save_translations_file_is_indented():
    r = _make_persist_registry()
    p = _tmp_path()
    try:
        with _patch_registry(r):
            save_translations(p)
        assert len(p.read_text().splitlines()) > 5
    finally:
        p.unlink(missing_ok=True)


def test_load_translations_interpolation_strings_preserved():
    """String templates with {placeholders} must survive the round-trip intact."""
    r = _make_persist_registry()
    p = _tmp_path()
    try:
        with _patch_registry(r):
            save_translations(p)
        with _patch_registry(r):
            load_translations(p)
        assert r.translations.get("welcome", name="Bob") == "Hello, Bob!"
    finally:
        p.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Cross-pair isolation tests
# ---------------------------------------------------------------------------

def test_save_state_file_not_loadable_by_load():
    """A save_state file must not be confused with a save file.
    load() on a save_state file should warn about missing theme/translations sections."""
    r = _make_persist_registry()
    p = _tmp_path()
    try:
        with _patch_registry(r):
            save_state(p)
        with _patch_registry(r):
            result = load(p)
        # save_state file has no "theme" or "translations" top-level sections
        assert not result.ok
        assert result.warnings  # at least one warning about missing sections
    finally:
        p.unlink(missing_ok=True)


def test_save_themes_file_not_loadable_by_load_translations():
    """A save_themes file passed to load_translations() must not crash.
    A themes file has no 'packs' key, so data.get('packs', {}) returns an
    empty dict — no packs are registered and no warnings are produced.
    active_language is also absent so no activation is attempted.
    The result is ok=True with an empty store, not an error."""
    r = _make_persist_registry()
    p = _tmp_path()
    try:
        with _patch_registry(r):
            save_themes(p)
        from Registry.Registry import Registry as _Registry
        r_dst = _Registry()
        with _patch_registry(r_dst):
            result = load_translations(p)
        assert result.ok, f"Expected ok=True (graceful no-op), got warnings={result.warnings}"
        assert result.warnings == []
        assert r_dst.translations.active_language is None
    finally:
        p.unlink(missing_ok=True)


def test_save_translations_file_not_loadable_by_load_themes():
    """A save_translations file passed to load_themes() must not crash.
    A translations file has no 'themes' key → no themes registered.
    active_theme is absent → set_theme skipped.
    active_mode is absent → defaults to 'dark' → set_mode('dark') is called,
    which raises RuntimeError on an empty store (no active theme), producing
    one warning. The store is otherwise untouched."""
    r = _make_persist_registry()
    p = _tmp_path()
    try:
        with _patch_registry(r):
            save_translations(p)
        from Registry.Registry import Registry as _Registry
        r_dst = _Registry()
        with _patch_registry(r_dst):
            result = load_themes(p)
        # set_mode("dark") fails on empty store → one warning, ok=False, no crash
        assert not result.ok
        assert len(result.warnings) == 1
        assert result.error is None
        assert r_dst.theme.active_theme is None
    finally:
        p.unlink(missing_ok=True)


def test_separate_files_compose_correctly():
    """save_themes + save_translations + save_state can be loaded independently
    and together produce the same state as a single save()."""
    from Registry.Registry import Registry as _Registry
    r_src = _make_persist_registry()

    p_themes = _tmp_path()
    p_trans  = _tmp_path()
    p_state  = _tmp_path()
    try:
        with _patch_registry(r_src):
            save_themes(p_themes)
            save_translations(p_trans)
            save_state(p_state)

        # Restore into a fresh registry in the correct order:
        # 1. themes (defines available themes)
        # 2. translations (defines available languages)
        # 3. state (activates the saved selections and restores settings)
        r_dst = _Registry()
        with _patch_registry(r_dst):
            rt = load_themes(p_themes)
            rl = load_translations(p_trans)
            rs = load_state(p_state)

        assert rt.ok, f"load_themes failed: {rt.warnings}"
        assert rl.ok, f"load_translations failed: {rl.warnings}"
        assert rs.ok, f"load_state failed: {rs.warnings}"

        assert r_dst.settings.get("volume") == 72
        assert r_dst.settings.get("muted") == False
        assert r_dst.theme.active_theme == "Slate"
        assert r_dst.theme.active_mode == "dark"
        assert r_dst.translations.active_language == "en"
        assert r_dst.translations.get("greeting") == "Hello"
    finally:
        p_themes.unlink(missing_ok=True)
        p_trans.unlink(missing_ok=True)
        p_state.unlink(missing_ok=True)


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

    # Persistence — save / load
    ("persistence save: returns SaveResult with ok=True",                          test_save_returns_ok),
    ("persistence save: writes valid JSON with three sections",                    test_save_creates_valid_json_with_three_sections),
    ("persistence save: output is indented",                                       test_save_json_is_indented),
    ("persistence save/load: round-trip settings",                                 test_save_load_round_trip_settings),
    ("persistence save/load: round-trip theme definitions and active state",       test_save_load_round_trip_theme),
    ("persistence save/load: round-trip translation packs and active language",    test_save_load_round_trip_translations),
    ("persistence save/load: round-trip all three stores simultaneously",          test_save_load_round_trip_all_three),
    ("persistence load: missing file → ok=False, FileNotFoundError",              test_load_missing_file),
    ("persistence load: malformed JSON → ok=False, JSONDecodeError",              test_load_malformed_json),
    ("persistence load: non-object JSON → ok=False, ValueError",                  test_load_non_object_json),
    ("persistence load: missing settings section → warning",                      test_load_missing_settings_section_warns),
    ("persistence load: missing theme section → warning",                         test_load_missing_theme_section_warns),
    ("persistence load: missing translations section → warning",                  test_load_missing_translations_section_warns),
    ("persistence load: None value in settings → warning, key skipped",           test_load_none_value_in_settings_warns_and_skips),
    ("persistence load: invalid theme definition → warning",                      test_load_invalid_theme_definition_warns),
    ("persistence save: unwritable path → ok=False, error set",                   test_save_to_unwritable_path),
    ("persistence load: always returns LoadResult on failure",                     test_load_returns_load_result_type_on_failure),

    # Persistence — save_state / load_state
    ("persistence save_state: returns SaveResult with ok=True",                    test_save_state_returns_ok),
    ("persistence save_state: correct file format (flat, no defs)",                test_save_state_file_format),
    ("persistence save_state: values correct in file",                             test_save_state_settings_values_correct),
    ("persistence save_state/load_state: round-trip restores all active state",    test_save_state_load_state_round_trip),
    ("persistence load_state: warns when theme not pre-registered",                test_load_state_requires_pre_registered_themes),
    ("persistence load_state: warns when language not pre-registered",             test_load_state_requires_pre_registered_language),
    ("persistence load_state: does not touch theme definitions",                   test_load_state_does_not_touch_theme_definitions),
    ("persistence load_state: does not touch translation packs",                   test_load_state_does_not_touch_translation_packs),
    ("persistence load_state: missing file → ok=False, FileNotFoundError",        test_load_state_missing_file),
    ("persistence load_state: malformed JSON → ok=False, JSONDecodeError",        test_load_state_malformed_json),
    ("persistence save_state: unwritable path → ok=False",                        test_save_state_to_unwritable_path),
    ("persistence save_state: output is indented",                                 test_save_state_file_is_indented),

    # Persistence — save_themes / load_themes
    ("persistence save_themes: returns SaveResult with ok=True",                   test_save_themes_returns_ok),
    ("persistence save_themes: correct file format (no settings/translations)",    test_save_themes_file_format),
    ("persistence save_themes: contains all registered themes",                    test_save_themes_contains_all_registered_themes),
    ("persistence save_themes/load_themes: round-trip restores active state",      test_save_themes_load_themes_round_trip),
    ("persistence load_themes: re-registers definitions in empty store",           test_load_themes_re_registers_definitions),
    ("persistence load_themes: succeeds on empty ThemeStore",                      test_load_themes_on_empty_store),
    ("persistence load_themes: does not touch settings or translations",           test_load_themes_does_not_touch_settings_or_translations),
    ("persistence load_themes: invalid definition → warning",                      test_load_themes_invalid_definition_warns),
    ("persistence load_themes: unknown active_theme → warning",                    test_load_themes_unknown_active_theme_warns),
    ("persistence load_themes: missing file → ok=False, FileNotFoundError",       test_save_themes_missing_file),
    ("persistence save_themes: unwritable path → ok=False",                       test_save_themes_to_unwritable_path),
    ("persistence save_themes: output is indented",                                test_save_themes_file_is_indented),
    ("persistence save_themes/load_themes: dark and light tokens preserved",       test_save_load_themes_preserves_dark_light_tokens),

    # Persistence — save_translations / load_translations
    ("persistence save_translations: returns SaveResult with ok=True",             test_save_translations_returns_ok),
    ("persistence save_translations: correct file format (no settings/theme)",     test_save_translations_file_format),
    ("persistence save_translations: contains all registered packs",               test_save_translations_contains_all_registered_packs),
    ("persistence save_translations/load_translations: round-trip active lang",    test_save_translations_load_translations_round_trip),
    ("persistence load_translations: re-registers packs in empty store",           test_load_translations_re_registers_packs),
    ("persistence load_translations: succeeds on empty TranslationStore",          test_load_translations_on_empty_store),
    ("persistence load_translations: does not touch settings or theme",            test_load_translations_does_not_touch_settings_or_theme),
    ("persistence load_translations: unknown active_language → warning",           test_load_translations_unknown_language_warns),
    ("persistence load_translations: None in pack → warning",                      test_load_translations_none_value_in_pack_warns),
    ("persistence load_translations: packs not a dict → warning",                  test_load_translations_packs_not_a_dict_warns),
    ("persistence load_translations: missing file → ok=False, FileNotFoundError", test_save_translations_missing_file),
    ("persistence save_translations: unwritable path → ok=False",                  test_save_translations_to_unwritable_path),
    ("persistence save_translations: output is indented",                          test_save_translations_file_is_indented),
    ("persistence save/load translations: interpolation strings preserved",        test_load_translations_interpolation_strings_preserved),

    # Persistence — cross-pair isolation
    ("persistence isolation: save_state file fails gracefully in load()",              test_save_state_file_not_loadable_by_load),
    ("persistence isolation: save_themes file is a no-op in load_translations",        test_save_themes_file_not_loadable_by_load_translations),
    ("persistence isolation: save_translations file in load_themes → set_mode warning",    test_save_translations_file_not_loadable_by_load_themes),
    ("persistence isolation: separate files compose correctly (full restore)",         test_separate_files_compose_correctly),
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
# Benchmark 1 — Registry throughput
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
    for w in instances: w.refresh()
    snap_after = _snap_mem()
    tracemalloc.stop()

    net = _net_bytes(snap_before, snap_after)
    print(f"\n  [1/5] memory  ({_B1_N_INSTANCES:,} wired QObject instances)")
    print(f"    Python heap delta : {_fmt_mem(net)}")
    print(f"    per instance      : {_fmt_mem(net // _B1_N_INSTANCES)}")

    t0 = time.perf_counter()
    for _ in range(_B1_READ_ITERATIONS):
        for _ in instances: r.settings.get("value")
    elapsed_s = time.perf_counter() - t0
    total_s = _B1_N_INSTANCES * _B1_READ_ITERATIONS
    print(f"\n  [2/5] settings read  ({total_s:,} calls)")
    print(f"    throughput : {total_s / elapsed_s:,.0f} calls/sec  /  {_fmt_time(elapsed_s / total_s)} per call")

    t0 = time.perf_counter()
    for _ in range(_B1_READ_ITERATIONS):
        for _ in instances:
            for i in range(10): r.theme.get(f"token.{i}")
    elapsed_t = time.perf_counter() - t0
    total_t = _B1_N_INSTANCES * 10 * _B1_READ_ITERATIONS
    print(f"\n  [3/5] theme read  ({total_t:,} calls)")
    print(f"    throughput : {total_t / elapsed_t:,.0f} calls/sec  /  {_fmt_time(elapsed_t / total_t)} per call")

    t0 = time.perf_counter()
    for _ in range(_B1_READ_ITERATIONS):
        for _ in instances:
            for i in range(3): r.translations.get(f"str.{i}")
    elapsed_tr = time.perf_counter() - t0
    total_tr = _B1_N_INSTANCES * 3 * _B1_READ_ITERATIONS
    print(f"\n  [4/5] translation read  ({total_tr:,} calls)")
    print(f"    throughput : {total_tr / elapsed_tr:,.0f} calls/sec  /  {_fmt_time(elapsed_tr / total_tr)} per call")

    r.theme.set_mode("light"); app.processEvents()
    t0 = time.perf_counter()
    for i in range(_B1_SWITCH_ITERS):
        r.theme.set_mode("dark" if i % 2 == 0 else "light")
        app.processEvents()
    elapsed_sw = time.perf_counter() - t0
    total_del = _B1_N_INSTANCES * 10 * _B1_SWITCH_ITERS
    print(f"\n  [5/5] switches  ({_B1_SWITCH_ITERS} theme flips + {_B1_SWITCH_ITERS} language flips)")
    print(f"    theme    : {total_del:,} deliveries / {_fmt_time(elapsed_sw / _B1_SWITCH_ITERS)} per flip")

    r.translations.set_language("es"); app.processEvents()
    t0 = time.perf_counter()
    for i in range(_B1_SWITCH_ITERS):
        r.translations.set_language("en" if i % 2 == 0 else "es")
        app.processEvents()
    elapsed_lang = time.perf_counter() - t0
    lang_del = _B1_N_INSTANCES * _B1_SWITCH_ITERS
    print(f"    language : {lang_del:,} deliveries / {_fmt_time(elapsed_lang / _B1_SWITCH_ITERS)} per switch")

    del instances; gc.collect()
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
    for k in _B2_KEYS: store.set(k, 0)

    tr_store = TranslationStore()
    tr_store.register("en", {f"str.{i}": f"en {i}" for i in range(_B2_TR_KEYS)})
    tr_store.register("es", {f"str.{i}": f"es {i}" for i in range(_B2_TR_KEYS)})
    tr_store.set_language("en")

    def refresh(self):
        global _b2_call_counter
        for k in _B2_KEYS: store.get(k)
        for i in range(_B2_TR_KEYS): tr_store.get(f"str.{i}")
        _b2_call_counter += 1

    if use_class_level:
        d = ReactiveDescriptor(refresh, stores=[store, tr_store])
        @reactive_class_decorator
        class Widget(QObject):
            def __init__(self): super().__init__()
            refresh = d
    else:
        d = ReactiveDescriptor(refresh, stores=[store, tr_store])
        class Widget(QObject):
            def __init__(self): super().__init__()
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
    dispatch_s = _b2_call_counter

    _b2_call_counter = 0
    gc.disable()
    t0 = time.perf_counter()
    tr_store.set_language("es")
    t_dispatch_l = time.perf_counter() - t0
    gc.enable()
    dispatch_l = _b2_call_counter

    gc.disable()
    t0 = time.perf_counter()
    del instances; gc.collect()
    t_gc = time.perf_counter() - t0
    gc.enable()

    return t_wire, t_dispatch_s + t_dispatch_l, t_gc, dispatch_s + dispatch_l


def _b2_bench_memory(use_class_level):
    store, tr_store, Widget = _b2_make_env(use_class_level)
    tracemalloc.start()
    snap_before = _snap_mem()
    instances = [Widget() for _ in range(_B2_N_INSTANCES)]
    for inst in instances: inst.refresh()
    snap_after = _snap_mem()
    tracemalloc.stop()
    mem = _net_bytes(snap_before, snap_after)
    del instances
    return mem


def _b2_avg(fn, n, label):
    results = []
    for i in range(n):
        print(f"  {label} run {i+1}/{n}...", flush=True)
        results.append(fn())
    return [sum(c) / n for c in zip(*results)]


def run_comparison_benchmark():
    print(f"\n{'─' * 68}")
    print(f"  Benchmark 2 — per-instance vs class-level")
    print(f"  {_B2_N_INSTANCES:,} instances x {_B2_N_KEYS} keys + {_B2_TR_KEYS} translation keys")
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
    print(f"\n{'='*W}")
    print(f"  {'Phase':<20} {'per-instance':>16} {'class-level':>16} {'ratio':>10}")
    print(f"  {'-'*(W-2)}")
    for label, v_pi, v_cl, fmt in [
        ("Wiring",       pi_t[0], cl_t[0], ms),
        ("Dispatch",     pi_t[1], cl_t[1], ms),
        ("GC / cleanup", pi_t[2], cl_t[2], ms),
    ]:
        print(f"  {label:<20} {fmt(v_pi):>16} {fmt(v_cl):>16} {ratio(v_pi, v_cl):>10}")
    t_pi = sum(pi_t[:3]); t_cl = sum(cl_t[:3])
    print(f"  {'-'*(W-2)}")
    print(f"  {'Total time':<20} {ms(t_pi):>16} {ms(t_cl):>16} {ratio(t_pi, t_cl):>10}")
    print(f"  {'Memory (live)':<20} {mb(pi_mem):>16} {mb(cl_mem):>16} {ratio(pi_mem, cl_mem):>10}")
    print(f"{'='*W}")
    print(f"\n{'─' * 68}\n")


# ===========================================================================
# Benchmark 3 — Persistence throughput
#
# Measures all four save/load pairs across three store sizes:
#   small  —   10 settings,  1 theme  ( 3 tokens),  2 langs ( 10 keys)
#   medium —  100 settings,  3 themes (20 tokens),  5 langs ( 50 keys)
#   large  — 1000 settings, 10 themes (50 tokens), 10 langs (200 keys)
#
# Each combination is run _B3_RUNS times; timing is averaged.
# ===========================================================================

_B3_RUNS = 5


def _b3_make_registry(n_settings, n_themes, tokens_per_theme, n_langs, keys_per_lang):
    from Registry.Registry import Registry as _Registry
    r = _Registry()
    for i in range(n_settings):
        r.settings.set(f"key.{i}", i)
    for t in range(n_themes):
        r.theme.register(f"theme.{t}", {
            "dark":  {f"token.{k}": f"#dark{t:02d}{k:03d}" for k in range(tokens_per_theme)},
            "light": {f"token.{k}": f"#lite{t:02d}{k:03d}" for k in range(tokens_per_theme)},
        })
    r.theme.set_theme("theme.0")
    for lang_idx in range(n_langs):
        r.translations.register(
            f"lang.{lang_idx}",
            {f"str.{k}": f"T{lang_idx}-{k}" for k in range(keys_per_lang)},
        )
    r.translations.set_language("lang.0")
    return r


def _b3_time(fn, runs):
    times = []
    for _ in range(runs):
        t0 = time.perf_counter()
        fn()
        times.append(time.perf_counter() - t0)
    return sum(times) / len(times)


def run_persistence_benchmark():
    print(f"\n{'─' * 68}")
    print(f"  Benchmark 3 — Persistence throughput  ({_B3_RUNS} runs each)")
    print(f"  Four pairs: save/load  save_state/load_state")
    print(f"              save_themes/load_themes  save_translations/load_translations")
    print(f"{'─' * 68}")

    sizes = [
        ("small",  10,   1,  3,  2,  10),
        ("medium", 100,  3, 20,  5,  50),
        ("large",  1000, 10, 50, 10, 200),
    ]

    pairs = [
        ("save/load",              save,              load),
        ("save_state/load_state",  save_state,        load_state),
        ("save_themes/load_themes",save_themes,       load_themes),
        ("save_tr/load_tr",        save_translations, load_translations),
    ]

    p = _tmp_path()
    try:
        for label, n_s, n_t, tok, n_l, kpl in sizes:
            r = _b3_make_registry(n_s, n_t, tok, n_l, kpl)
            print(f"\n  Size: {label}  ({n_s} settings / {n_t} themes x {tok} tokens / {n_l} langs x {kpl} keys)")
            print(f"  {'Pair':<28} {'File':>8}  {'save':>10}  {'load':>10}")
            print(f"  {'-'*60}")
            for pair_label, save_fn, load_fn in pairs:
                with _patch_registry(r):
                    avg_save = _b3_time(lambda: save_fn(p), _B3_RUNS)
                file_size = p.stat().st_size
                with _patch_registry(r):
                    avg_load = _b3_time(lambda: load_fn(p), _B3_RUNS)
                print(
                    f"  {pair_label:<28} {_fmt_mem(file_size):>8}"
                    f"  {_fmt_time(avg_save):>10}  {_fmt_time(avg_load):>10}"
                )
    finally:
        p.unlink(missing_ok=True)

    print(f"\n  note: load functions re-register definitions and re-activate")
    print(f"        selections on every call — not purely I/O.")
    print(f"        save_state/load_state files are smallest because they")
    print(f"        contain no theme definitions or translation pack strings.")
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

    sys.exit(1 if failed else 0)