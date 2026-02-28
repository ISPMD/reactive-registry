"""
Registry.Persistence
--------------------
Save and load Registry stores to and from JSON files.
Part of the Registry package — import alongside the stores:

    from Registry import settings, theme, translations
    from Registry.Persistence import (
        save, load,
        save_state, load_state,
        save_themes, load_themes,
        save_translations, load_translations,
    )

Functions
---------
All save functions take only a file path and return a SaveResult.
All load functions take only a file path and return a LoadResult.

save(path) / load(path)
    Everything: settings values, full theme definitions, full translation
    packs, active theme/mode/language. File format:
        {
            "settings":     { "volume": 80, ... },
            "theme":        { "active_theme": "Slate", "active_mode": "dark",
                              "themes": { "Slate": { "dark": {...}, "light": {...} }, ... } },
            "translations": { "active_language": "en",
                              "packs": { "en": {...}, "es": {...}, ... } }
        }

save_state(path) / load_state(path)
    Active state only — no theme definitions, no translation pack strings.
    Use this when themes and translations are always registered from code at
    startup and only the current selection needs to persist. File format:
        {
            "settings":        { "volume": 80, ... },
            "active_theme":    "Slate",
            "active_mode":     "dark",
            "active_language": "en"
        }
    load_state() calls set_theme() and set_language() with the stored names,
    so the themes and packs must already be registered before calling it.

save_themes(path) / load_themes(path)
    Full ThemeStore: all registered definitions plus active theme and mode.
    File format:
        {
            "active_theme": "Slate",
            "active_mode":  "dark",
            "themes": { "Slate": { "dark": {...}, "light": {...} }, ... }
        }

save_translations(path) / load_translations(path)
    Full TranslationStore: all registered packs plus active language.
    File format:
        {
            "active_language": "en",
            "packs": { "en": { "greeting": "Hello", ... }, ... }
        }

Results
-------
    result.ok       — True if the operation fully succeeded with no warnings
    result.error    — Exception if a fatal error occurred, None otherwise
    result.warnings — list of non-fatal per-key/store issues

    if not result.ok:
        print(result.error, result.warnings)

None policy
-----------
    None is not a valid settings value. set() already enforces this, so it
    cannot appear in a saved file. None values encountered during load are
    skipped with a warning rather than crashing.

Thread safety
-------------
    All functions must be called from the Qt main thread. They call store
    methods (set(), register(), set_theme(), etc.) which are not thread-safe.
    See Registry.Settings for the rationale.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import registry


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class SaveResult:
    """Returned by every save function.

    Attributes
    ----------
    ok : bool
        True if the file was written with no errors or warnings.
    error : Exception | None
        Set if a fatal error occurred (e.g. permission denied). None on success.
    warnings : list[str]
        Non-fatal issues (e.g. a single bad value skipped). ok is False
        whenever warnings is non-empty.
    """
    ok: bool = True
    error: Exception | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class LoadResult:
    """Returned by every load function.

    Attributes
    ----------
    ok : bool
        True if the file was read and all data restored with no warnings.
    error : Exception | None
        Set if a fatal error occurred (missing file, bad JSON, wrong top-level
        type). When set, no stores have been modified.
    warnings : list[str]
        Non-fatal issues (e.g. a missing section, a skipped None value). ok is
        False whenever warnings is non-empty.
    """
    ok: bool = True
    error: Exception | None = None
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal: JSON I/O helpers
# ---------------------------------------------------------------------------

def _write(path: str | Path, payload: dict) -> SaveResult:
    """Serialize payload to indented JSON at path. Returns a SaveResult."""
    result = SaveResult()
    try:
        Path(path).write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as exc:
        result.ok = False
        result.error = exc
    return result


def _read(path: str | Path) -> tuple[dict | None, LoadResult]:
    """Read and parse a JSON file. Returns (payload, result).

    On any failure result.ok is False and result.error is set; payload is None.
    On success payload is a dict and result.ok is True.
    """
    result = LoadResult()
    try:
        raw = Path(path).read_text(encoding="utf-8")
        payload = json.loads(raw)
    except FileNotFoundError as exc:
        result.ok = False
        result.error = exc
        return None, result
    except json.JSONDecodeError as exc:
        result.ok = False
        result.error = exc
        return None, result
    except Exception as exc:
        result.ok = False
        result.error = exc
        return None, result

    if not isinstance(payload, dict):
        result.ok = False
        result.error = ValueError(
            f"Expected a JSON object at the top level, got {type(payload).__name__!r}."
        )
        return None, result

    return payload, result


# ---------------------------------------------------------------------------
# Internal: serialization helpers
# ---------------------------------------------------------------------------

def _serialize_settings(s) -> dict:
    return s.as_dict()


def _serialize_theme_full(t) -> dict:
    return {
        "active_theme": t.active_theme,
        "active_mode":  t.active_mode,
        "themes":       {name: defn for name, defn in t._themes.items()},
    }


def _serialize_translations_full(tr) -> dict:
    return {
        "active_language": tr.active_language,
        "packs":           dict(tr._packs),
    }


def _serialize_state(s, t, tr) -> dict:
    """Active-state-only payload: settings values + active selections."""
    return {
        "settings":        s.as_dict(),
        "active_theme":    t.active_theme,
        "active_mode":     t.active_mode,
        "active_language": tr.active_language,
    }


# ---------------------------------------------------------------------------
# Internal: restoration helpers
# ---------------------------------------------------------------------------

def _restore_settings(s, data: dict, warnings: list[str]) -> None:
    if not isinstance(data, dict):
        warnings.append(
            f"settings: expected a dict, got {type(data).__name__!r} — skipped."
        )
        return
    for key, value in data.items():
        if value is None:
            warnings.append(
                f"settings: skipping key {key!r} — None is not a valid value."
            )
            continue
        try:
            s.set(key, value)
        except Exception as exc:
            warnings.append(f"settings: could not set {key!r}: {exc}")


def _restore_theme_full(t, data: dict, warnings: list[str]) -> None:
    """Register all themes from data, then activate the stored theme/mode."""
    if not isinstance(data, dict):
        warnings.append(
            f"theme: expected a dict, got {type(data).__name__!r} — skipped."
        )
        return

    themes = data.get("themes", {})
    if not isinstance(themes, dict):
        warnings.append("theme: 'themes' is not a dict — skipped.")
        return

    for name, definition in themes.items():
        try:
            t.register(name, definition)
        except Exception as exc:
            warnings.append(f"theme: could not register theme {name!r}: {exc}")

    _restore_theme_active(t, data, warnings)


def _restore_theme_active(t, data: dict, warnings: list[str]) -> None:
    """Activate the theme and mode stored in data (no registration)."""
    active_theme = data.get("active_theme")
    active_mode  = data.get("active_mode", "dark")

    if active_theme is not None:
        try:
            t.set_theme(active_theme)
        except Exception as exc:
            warnings.append(f"theme: could not activate theme {active_theme!r}: {exc}")

    try:
        t.set_mode(active_mode)
    except Exception as exc:
        warnings.append(f"theme: could not set mode {active_mode!r}: {exc}")


def _restore_translations_full(tr, data: dict, warnings: list[str]) -> None:
    """Register all packs from data, then activate the stored language."""
    if not isinstance(data, dict):
        warnings.append(
            f"translations: expected a dict, got {type(data).__name__!r} — skipped."
        )
        return

    packs = data.get("packs", {})
    if not isinstance(packs, dict):
        warnings.append("translations: 'packs' is not a dict — skipped.")
        return

    for language, pack in packs.items():
        try:
            tr.register(language, pack)
        except Exception as exc:
            warnings.append(
                f"translations: could not register language {language!r}: {exc}"
            )

    _restore_translations_active(tr, data, warnings)


def _restore_translations_active(tr, data: dict, warnings: list[str]) -> None:
    """Activate the language stored in data (no pack registration)."""
    active_language = data.get("active_language")
    if active_language is not None:
        try:
            tr.set_language(active_language)
        except Exception as exc:
            warnings.append(
                f"translations: could not activate language {active_language!r}: {exc}"
            )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def save(path: str | Path) -> SaveResult:
    """Save everything: settings, full theme definitions, full translation packs,
    and all active selections to a single JSON file.

    Use load() to restore. Themes and translations are re-registered from the
    file, so the calling code does not need to register them at startup when
    this file is present.
    """
    try:
        payload = {
            "settings":     _serialize_settings(registry.settings),
            "theme":        _serialize_theme_full(registry.theme),
            "translations": _serialize_translations_full(registry.translations),
        }
    except Exception as exc:
        return SaveResult(ok=False, error=exc)

    result = _write(path, payload)
    if result.warnings:
        result.ok = False
    return result


def load(path: str | Path) -> LoadResult:
    """Restore everything from a file written by save().

    On a fatal read/parse error result.error is set and no stores are touched.
    Per-store failures are non-fatal and collected into result.warnings.
    """
    payload, result = _read(path)
    if payload is None:
        return result

    settings_data = payload.get("settings")
    if settings_data is None:
        result.warnings.append("settings: section missing from file — skipped.")
    else:
        _restore_settings(registry.settings, settings_data, result.warnings)

    theme_data = payload.get("theme")
    if theme_data is None:
        result.warnings.append("theme: section missing from file — skipped.")
    else:
        _restore_theme_full(registry.theme, theme_data, result.warnings)

    translations_data = payload.get("translations")
    if translations_data is None:
        result.warnings.append("translations: section missing from file — skipped.")
    else:
        _restore_translations_full(registry.translations, translations_data, result.warnings)

    if result.warnings:
        result.ok = False
    return result


def save_state(path: str | Path) -> SaveResult:
    """Save active state only: all settings values plus the active theme name,
    active mode, and active language — no theme definitions or pack strings.

    Intended for apps that always register themes and translations from code at
    startup. load_state() will call set_theme() and set_language() with the
    stored names, so the relevant themes and packs must already be registered
    before calling it.
    """
    try:
        payload = _serialize_state(
            registry.settings, registry.theme, registry.translations
        )
    except Exception as exc:
        return SaveResult(ok=False, error=exc)

    result = _write(path, payload)
    if result.warnings:
        result.ok = False
    return result


def load_state(path: str | Path) -> LoadResult:
    """Restore active state from a file written by save_state().

    Restores all settings values and calls set_theme(), set_mode(), and
    set_language() with the stored names. Themes and language packs must
    already be registered before calling this.

    On a fatal read/parse error result.error is set and no stores are touched.
    """
    payload, result = _read(path)
    if payload is None:
        return result

    settings_data = payload.get("settings")
    if settings_data is None:
        result.warnings.append("settings: section missing from file — skipped.")
    else:
        _restore_settings(registry.settings, settings_data, result.warnings)

    _restore_theme_active(registry.theme, payload, result.warnings)
    _restore_translations_active(registry.translations, payload, result.warnings)

    if result.warnings:
        result.ok = False
    return result


def save_themes(path: str | Path) -> SaveResult:
    """Save the full ThemeStore: all registered theme definitions plus the
    active theme name and mode.

    Use load_themes() to restore.
    """
    try:
        payload = _serialize_theme_full(registry.theme)
    except Exception as exc:
        return SaveResult(ok=False, error=exc)

    result = _write(path, payload)
    if result.warnings:
        result.ok = False
    return result


def load_themes(path: str | Path) -> LoadResult:
    """Restore the full ThemeStore from a file written by save_themes().

    Re-registers all theme definitions and activates the stored theme and mode.
    On a fatal read/parse error result.error is set and the store is not touched.
    """
    payload, result = _read(path)
    if payload is None:
        return result

    _restore_theme_full(registry.theme, payload, result.warnings)

    if result.warnings:
        result.ok = False
    return result


def save_translations(path: str | Path) -> SaveResult:
    """Save the full TranslationStore: all registered language packs plus the
    active language name.

    Use load_translations() to restore.
    """
    try:
        payload = _serialize_translations_full(registry.translations)
    except Exception as exc:
        return SaveResult(ok=False, error=exc)

    result = _write(path, payload)
    if result.warnings:
        result.ok = False
    return result


def load_translations(path: str | Path) -> LoadResult:
    """Restore the full TranslationStore from a file written by save_translations().

    Re-registers all language packs and activates the stored language.
    On a fatal read/parse error result.error is set and the store is not touched.
    """
    payload, result = _read(path)
    if payload is None:
        return result

    _restore_translations_full(registry.translations, payload, result.warnings)

    if result.warnings:
        result.ok = False
    return result