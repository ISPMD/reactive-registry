"""
Registry.Persistence
--------------------
Save and load Registry stores to and from JSON files.
Part of the Registry package — import alongside the stores:

    from Registry import settings, theme, translations
    from Registry.Persistence import save, load, LoadResult

All three stores are persisted in a single JSON file with the structure:

    {
        "settings": {
            "volume": 80,
            "muted": false
        },
        "theme": {
            "active_theme": "catppuccin",
            "active_mode": "dark",
            "themes": {
                "catppuccin": {
                    "dark":  {"color.background": "#1e1e2e"},
                    "light": {"color.background": "#eff1f5"}
                }
            }
        },
        "translations": {
            "active_language": "en",
            "packs": {
                "en": {"greeting": "Hello"},
                "es": {"greeting": "Hola"}
            }
        }
    }

Public API
----------
Saving:
    result = save(path)
    # Writes all three stores to a JSON file at path.
    # Returns a SaveResult. Check result.ok or result.error.

Loading:
    result = load(path)
    # Reads the JSON file and restores all three stores.
    # Returns a LoadResult. Check result.ok or result.error.
    # Partial loads are possible — see LoadResult for details.

Results:
    result.ok       — True if the operation fully succeeded
    result.error    — Exception instance if a top-level failure occurred,
                      None otherwise
    result.warnings — list of non-fatal per-store warnings (e.g. one store
                      failed but others succeeded)

    if not result.ok:
        print(result.error)

None policy:
    None values stored in SettingsModel cannot be saved (SettingsModel already
    rejects None at write time, so this situation should never arise in practice).
    None values encountered during load are skipped with a warning.

Thread safety:
    save() and load() must be called from the Qt main thread. They call store
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
    """Result returned by save().

    Attributes
    ----------
    ok : bool
        True if the file was written successfully without any errors or
        warnings. False if a top-level failure occurred (see error) or if any
        per-store warnings were raised (see warnings).
    error : Exception | None
        Set to the exception if the overall save operation failed (e.g. a
        permission error writing the file). None on success.
    warnings : list[str]
        Non-fatal issues encountered while serializing individual stores
        (e.g. a store returned unexpected data). The file may still have been
        written with partial data.
    """
    ok: bool = True
    error: Exception | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class LoadResult:
    """Result returned by load().

    Attributes
    ----------
    ok : bool
        True if the file was read and all three stores were restored without
        any errors or warnings. False if a top-level failure occurred (see
        error) or if any per-store warnings were raised (see warnings).
    error : Exception | None
        Set to the exception if the overall load operation failed (e.g. the
        file does not exist, or the top-level JSON is malformed). None on
        success. When error is set, no stores will have been modified.
    warnings : list[str]
        Non-fatal issues encountered while restoring individual stores (e.g. a
        single invalid value was skipped, or one store's section was missing).
        Other stores may have been restored successfully.
    """
    ok: bool = True
    error: Exception | None = None
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

def _serialize_settings(settings) -> dict:
    return settings.as_dict()


def _serialize_theme(theme) -> dict:
    return {
        "active_theme": theme.active_theme,
        "active_mode": theme.active_mode,
        "themes": {
            name: definition
            for name, definition in theme._themes.items()
        },
    }


def _serialize_translations(translations) -> dict:
    return {
        "active_language": translations.active_language,
        "packs": dict(translations._packs),
    }


# ---------------------------------------------------------------------------
# Deserialization helpers
# ---------------------------------------------------------------------------

def _restore_settings(settings, data: dict, warnings: list[str]) -> None:
    """Restore SettingsModel from a plain dict."""
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
            settings.set(key, value)
        except Exception as exc:
            warnings.append(f"settings: could not set {key!r}: {exc}")


def _restore_theme(theme, data: dict, warnings: list[str]) -> None:
    """Restore ThemeStore from a serialized dict."""
    if not isinstance(data, dict):
        warnings.append(
            f"theme: expected a dict, got {type(data).__name__!r} — skipped."
        )
        return

    themes = data.get("themes", {})
    if not isinstance(themes, dict):
        warnings.append("theme: 'themes' is not a dict — skipped.")
        return

    # Register all themes first so set_theme() can succeed.
    for name, definition in themes.items():
        try:
            theme.register(name, definition)
        except Exception as exc:
            warnings.append(f"theme: could not register theme {name!r}: {exc}")

    # Restore active theme and mode.
    active_theme = data.get("active_theme")
    active_mode = data.get("active_mode", "dark")

    if active_theme is not None:
        try:
            theme.set_theme(active_theme)
        except Exception as exc:
            warnings.append(f"theme: could not activate theme {active_theme!r}: {exc}")

    try:
        theme.set_mode(active_mode)
    except Exception as exc:
        warnings.append(f"theme: could not set mode {active_mode!r}: {exc}")


def _restore_translations(translations, data: dict, warnings: list[str]) -> None:
    """Restore TranslationStore from a serialized dict."""
    if not isinstance(data, dict):
        warnings.append(
            f"translations: expected a dict, got {type(data).__name__!r} — skipped."
        )
        return

    packs = data.get("packs", {})
    if not isinstance(packs, dict):
        warnings.append("translations: 'packs' is not a dict — skipped.")
        return

    # Register all packs first so set_language() can succeed.
    for language, pack in packs.items():
        try:
            translations.register(language, pack)
        except Exception as exc:
            warnings.append(
                f"translations: could not register language {language!r}: {exc}"
            )

    # Restore active language.
    active_language = data.get("active_language")
    if active_language is not None:
        try:
            translations.set_language(active_language)
        except Exception as exc:
            warnings.append(
                f"translations: could not activate language {active_language!r}: {exc}"
            )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def save(path: str | Path) -> SaveResult:
    """Serialize all three stores to a JSON file at path.

    Creates or overwrites the file. Parent directories must already exist.

    Parameters
    ----------
    path : str | Path
        Destination file path. Typically ends in ".json".

    Returns
    -------
    SaveResult
        result.ok is True if the file was written without issues.
        result.error holds the exception on failure.
        result.warnings lists any non-fatal serialization issues.
    """
    result = SaveResult()

    try:
        payload = {
            "settings":     _serialize_settings(registry.settings),
            "theme":        _serialize_theme(registry.theme),
            "translations": _serialize_translations(registry.translations),
        }
    except Exception as exc:
        result.ok = False
        result.error = exc
        return result

    try:
        path = Path(path)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        result.ok = False
        result.error = exc
        return result

    if result.warnings:
        result.ok = False

    return result


def load(path: str | Path) -> LoadResult:
    """Restore all three stores from a JSON file at path.

    Parameters
    ----------
    path : str | Path
        Source file path produced by save().

    Returns
    -------
    LoadResult
        result.ok is True if the file was read and all stores restored cleanly.
        result.error holds the exception if the file could not be read or parsed
        at the top level — in that case no stores are modified.
        result.warnings lists any non-fatal per-store issues (skipped keys,
        missing sections, etc.). Other stores may still have been restored.
    """
    result = LoadResult()

    # --- Read and parse the file ---
    try:
        path = Path(path)
        raw = path.read_text(encoding="utf-8")
        payload = json.loads(raw)
    except FileNotFoundError as exc:
        result.ok = False
        result.error = exc
        return result
    except json.JSONDecodeError as exc:
        result.ok = False
        result.error = exc
        return result
    except Exception as exc:
        result.ok = False
        result.error = exc
        return result

    if not isinstance(payload, dict):
        result.ok = False
        result.error = ValueError(
            f"Expected a JSON object at the top level, got {type(payload).__name__!r}."
        )
        return result

    # --- Restore each store, collecting per-store warnings ---
    settings_data = payload.get("settings")
    if settings_data is None:
        result.warnings.append("settings: section missing from file — skipped.")
    else:
        _restore_settings(registry.settings, settings_data, result.warnings)

    theme_data = payload.get("theme")
    if theme_data is None:
        result.warnings.append("theme: section missing from file — skipped.")
    else:
        _restore_theme(registry.theme, theme_data, result.warnings)

    translations_data = payload.get("translations")
    if translations_data is None:
        result.warnings.append("translations: section missing from file — skipped.")
    else:
        _restore_translations(registry.translations, translations_data, result.warnings)

    if result.warnings:
        result.ok = False

    return result