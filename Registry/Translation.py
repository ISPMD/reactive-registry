"""
Registry.Translation
--------------------
A reactive store of translation strings organised into named language packs.
Part of the Registry package — use `from Registry import translations` rather
than importing this module directly.

    translations.register("en", {"greeting": "Hello", "farewell": "Goodbye"})
    translations.register("es", {"greeting": "Hola",  "farewell": "Adiós"})
    translations.set_language("en")
    translations.get("greeting")           # "Hello"
    translations.get("greeting.formal")    # falls back to key if missing

To auto-wire a method so it re-runs whenever the language changes, use
@registry.reactive — any method that calls translations.get() at least once
will be automatically re-run on every language switch.

Unlike SettingsModel and ThemeStore, individual keys are NOT tracked — the
entire language pack is treated as a single dependency. This is intentional:
translations are always switched as a whole, so per-key granularity would add
overhead without benefit. One signal connection per reactive method is all
that is wired, regardless of how many translation keys the method reads.

Concepts
--------
Language pack:
    A flat dict mapping arbitrary string keys to string values:
        {"greeting": "Hello", "farewell": "Goodbye", "app.title": "My App"}
    Dotted key names are a convention only — no nesting is performed.

Active language:
    The name of the currently active pack. All .get() calls read from this
    pack. Switching language emits language_changed once.

Fallback behaviour:
    If a key is missing from the active pack, .get() returns the key itself
    by default (rather than None). This makes missing translations visible
    during development without crashing. Pass a custom fallback to override:
        translations.get("missing.key", fallback="???")

Interpolation:
    Pass keyword arguments to .get() to format the translated string:
        translations.get("welcome", name="Alice")
        # pack: {"welcome": "Hello, {name}!"}  →  "Hello, Alice!"
    If the format call fails (e.g. wrong keys), the raw unformatted string is
    returned and a warning is printed rather than raising.

Public API
----------
Registration:
    translations.register("en", {"key": "value", ...})  # add or replace
    translations.unregister("en")   # remove; raises RuntimeError if active

Switching:
    translations.set_language("en")  # activate; emits language_changed

Reading:
    translations.get("greeting")              # translated string or key as fallback
    translations.get("greeting", fallback="") # translated string or custom fallback
    translations.get("welcome", name="Alice") # with interpolation
    translations.active_language              # name of active language (str | None)
    translations.as_dict()                    # shallow copy of active pack

Subscribing:
    translations.language_changed.connect(cb)  # cb(language_name) on every switch

None policy:
    None is not a valid translation value. register() raises ValueError if any
    value in the pack is None.

Thread safety:
    register(), unregister(), and set_language() must be called from the Qt
    main thread. See Registry.Settings for the rationale.
"""

from typing import Any
from PySide6.QtCore import QObject, Signal

from .Reactive import _KeySignalEmitter, _record

# Sentinel key used to register a single dependency for the entire language pack.
# Any method that reads at least one translation is wired to this one key so it
# re-runs on every language switch — no per-key tracking needed.
_LANG_KEY = "_language"


class TranslationStore(QObject):
    """A reactive store of translation strings with named language packs.

    Widgets read strings via .get() and are auto-wired to re-run on language
    changes when their method is decorated with @registry.reactive.
    """

    # Emitted once after every set_language() call: (language_name,).
    language_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)

        self._packs: dict[str, dict[str, str]] = {}
        self._active_language: str | None = None
        self._tokens: dict[str, str] = {}

        # Single _KeySignalEmitter keyed by _LANG_KEY.
        # Stored in _key_signals so disconnect_conns in Reactive.py can reach
        # it without any special-casing — it uses store._key_signals[key] for
        # all stores, and TranslationStore is no different.
        self._key_signals: dict[str, _KeySignalEmitter] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, language: str, pack: dict[str, str]) -> None:
        """Register or replace a language pack.

        pack is a flat dict of translation key → string value.
        Raises ValueError if any value is None.

        If the language being registered is currently active, the active token
        set is refreshed immediately so live widgets pick up changes on the
        next language_changed signal — call set_language() again to trigger
        the signal explicitly if needed.
        """
        none_keys = [k for k, v in pack.items() if v is None]
        if none_keys:
            raise ValueError(
                f"Language pack {language!r}: None is not a valid translation value. "
                f"Keys with None: {none_keys}"
            )
        self._packs[language] = pack
        if self._active_language == language:
            self._tokens = dict(pack)

    def unregister(self, language: str) -> None:
        """Remove a language pack by name.

        Raises KeyError if the language is not registered.
        Raises RuntimeError if the language is currently active.
        """
        if language not in self._packs:
            raise KeyError(f"Language {language!r} is not registered.")
        if language == self._active_language:
            raise RuntimeError(
                f"Cannot unregister {language!r} — it is the active language. "
                "Switch to another language first."
            )
        del self._packs[language]

    # ------------------------------------------------------------------
    # Switching
    # ------------------------------------------------------------------

    def set_language(self, language: str) -> None:
        """Activate a registered language pack.

        Raises KeyError if the language is not registered.
        Emits language_changed(language) after updating the active pack so
        any handler that calls translations.get() will see the new strings.
        """
        if language not in self._packs:
            raise KeyError(f"Language {language!r} is not registered.")

        self._tokens = dict(self._packs[language])
        self._active_language = language

        # Emit on the sentinel key so ReactiveDescriptor re-runs any method
        # that read at least one translation during its first tracked call.
        emitter = self._key_signals.get(_LANG_KEY)
        if emitter:
            emitter.sig.emit(language)

        self.language_changed.emit(language)

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def get(self, key: str, fallback: str | None = None, **kwargs: Any) -> str:
        """Return the translated string for key in the active language.

        If the key is missing, returns fallback. If fallback is None (the
        default), the key itself is returned so missing translations are
        visible during development rather than silently empty.

        Pass keyword arguments for interpolation:
            translations.get("welcome", name="Alice")
            # pack has "welcome": "Hello, {name}!"  →  "Hello, Alice!"

        If string formatting fails (e.g. wrong or missing kwargs), the raw
        unformatted string is returned and a warning is printed.

        Also calls _record(self, _LANG_KEY) so that a @registry.reactive
        method that reads any translation gets wired to re-run on every
        language switch. Outside a tracking context this is a no-op.
        """
        _record(self, _LANG_KEY)

        raw = self._tokens.get(key, fallback if fallback is not None else key)

        if kwargs and isinstance(raw, str):
            try:
                return raw.format(**kwargs)
            except (KeyError, ValueError) as exc:
                print(f"[TranslationStore] Format failed for key {key!r}: {exc}")
                return raw

        return raw

    @property
    def active_language(self) -> str | None:
        """Name of the currently active language, or None if none has been set."""
        return self._active_language

    def as_dict(self) -> dict[str, str]:
        """Return a shallow copy of all key/value pairs in the active language pack."""
        return dict(self._tokens)

    # ------------------------------------------------------------------
    # Internal — used by ReactiveDescriptor via store.on(key)
    # ------------------------------------------------------------------

    def on(self, key: str) -> Signal:
        """Return the signal for key, creating its emitter on demand.

        For TranslationStore the only key ever used is _LANG_KEY, but the
        interface matches SettingsModel and ThemeStore so ReactiveDescriptor
        can treat all stores uniformly without special-casing.
        """
        if key not in self._key_signals:
            self._key_signals[key] = _KeySignalEmitter(self)
        return self._key_signals[key].sig