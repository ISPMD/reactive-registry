"""
Registry.Theme
--------------
A reactive store of design tokens organised into named themes with dark and
light variants. Part of the Registry package — use `from Registry import theme`
rather than importing this module directly.

    theme.register("catppuccin", {...})
    theme.set_theme("catppuccin")
    theme.get("color.background")

To auto-wire a method so it re-runs whenever a token it reads changes
(including after a full theme or mode switch), use @registry.reactive.

Concepts
--------
Theme:
    A named dict with exactly two keys, "dark" and "light", each mapping to a
    flat dict of token keys to string values:
        {
            "dark":  {"color.background": "#1e1e2e", "color.text": "#cdd6f4"},
            "light": {"color.background": "#eff1f5", "color.text": "#4c4f69"},
        }
    Token keys are arbitrary strings. Dotted names like "color.background" are
    a convention only — no nesting is performed internally. Extra keys beyond
    "dark" and "light" in the definition dict are silently ignored.

Mode:
    Either "dark" or "light". Switching mode replaces the entire active token
    set with the corresponding variant of the current theme.

Active tokens:
    The flat dict from the currently active (theme, mode) pair. This is the
    source for all .get() calls. On every switch, the store diffs the old and
    new token sets and emits signals only for keys whose value actually changed,
    so reactive methods that read unaffected tokens do not re-run needlessly.

Signal ordering in _apply:
    State (self._tokens, self._active_theme, self._active_mode) is committed
    before any signals are emitted. This means any signal handler that calls
    theme.get() or reads theme.active_theme / active_mode will see the new,
    fully consistent state immediately — no partially-updated store is ever
    observable.

Public API
----------
Registration:
    theme.register("name", definition)  # add or replace a theme
    theme.unregister("name")            # remove; raises RuntimeError if active

Switching:
    theme.set_theme("name")  # activate a theme, keep current mode
    theme.set_mode("dark")   # switch mode, keep current theme
    theme.toggle_mode()      # flip dark ↔ light

Reading:
    theme.get("color.background")         # active token value, or None
    theme.get("color.background", "#000") # active token value, or fallback
    theme.active_theme                    # name of active theme (str | None)
    theme.active_mode                     # "dark" or "light"
    theme.as_dict()                       # shallow snapshot of active tokens

Subscribing to changes:
    theme.on("color.background").connect(cb)  # cb(new_value) for this token
    theme.changed.connect(cb)                 # cb(key, value) per changed token
    theme.theme_changed.connect(cb)           # cb(name, mode) once per switch

Signal ordering:
    Per-key .on(key) → .changed (once per changed token) → .theme_changed.
    State is committed before any of these fire.

None policy:
    None is not a valid token value. register() raises ValueError if any token
    value is None in either variant.

Thread safety:
    register(), unregister(), set_theme(), set_mode(), and toggle_mode() must
    be called from the Qt main thread. See Registry.Settings for the rationale.
"""

from typing import Any
from PySide6.QtCore import QObject, Signal

from .Reactive import _KeySignalEmitter, _record, _values_equal

# Sentinel for "key absent from token dict" — distinct from any real token value
# so that missing keys in the old or new set are handled unambiguously during
# the diff in _apply().
_MISSING = object()

_MODES = ("dark", "light")


class ThemeStore(QObject):
    """A reactive store of design tokens with named themes and dark/light variants.

    Widgets read tokens via .get() and subscribe to changes via .on(key),
    .changed, or .theme_changed. Use @registry.reactive to auto-wire a method
    to every token it reads so it re-runs automatically on any change.
    """

    # Emitted for each token whose value changed during a theme or mode switch:
    # (key, new_value). State is committed before this fires — handlers calling
    # theme.get() will always see the new values. Per-key .on(key) fires first.
    changed = Signal(str, object)

    # Emitted once after every set_theme() or set_mode() call, after all
    # per-token and .changed signals: (theme_name, mode). Use this when you need
    # to react to the switch as a whole rather than to individual token changes.
    theme_changed = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)

        self._themes: dict[str, dict[str, dict[str, Any]]] = {}
        self._active_theme: str | None = None
        self._active_mode: str = "dark"

        # Snapshot of the active variant — a copy, not a reference, so that
        # mutating a registered definition after activation does not corrupt the
        # live token set until the next explicit theme or mode switch.
        self._tokens: dict[str, Any] = {}

        # One _KeySignalEmitter per token key, created on demand in on().
        # Parented to this QObject so emitters are destroyed when the store is.
        # Stored in a dict so Reactive.disconnect_conns can reach them via
        # store._key_signals[key] without importing this module.
        self._key_signals: dict[str, _KeySignalEmitter] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, name: str, definition: dict[str, dict[str, Any]]) -> None:
        """Register or replace a theme.

        definition must contain "dark" and "light" keys, each a flat dict of
        token key → value. Extra keys in definition are silently ignored.

        Raises ValueError if either variant is absent or any token value is None.

        If the theme being registered is currently active, the active token set
        is refreshed immediately and signals fire for any tokens whose value
        changed, so live widgets pick up the updated definition automatically.
        """
        self._validate_definition(name, definition)
        self._themes[name] = definition
        if self._active_theme == name:
            # Re-apply so live widgets see updated token values immediately.
            self._apply(name, self._active_mode)

    def unregister(self, name: str) -> None:
        """Remove a theme by name.

        Raises KeyError if the name is not registered.
        Raises RuntimeError if the theme is currently active — switch to
        another theme first with set_theme().
        """
        if name not in self._themes:
            raise KeyError(f"Theme {name!r} is not registered.")
        if name == self._active_theme:
            raise RuntimeError(
                f"Cannot unregister {name!r} — it is the active theme. "
                "Switch to another theme first."
            )
        del self._themes[name]

    # ------------------------------------------------------------------
    # Switching
    # ------------------------------------------------------------------

    def set_theme(self, name: str) -> None:
        """Activate a registered theme, keeping the current mode.

        Raises KeyError if the theme is not registered.

        Signals fire for every token whose value differs between the old and
        new active token sets. Tokens that are identical in both sets are silent.
        """
        if name not in self._themes:
            raise KeyError(f"Theme {name!r} is not registered.")
        self._apply(name, self._active_mode)

    def set_mode(self, mode: str) -> None:
        """Switch the active mode to "dark" or "light", keeping the current theme.

        Raises ValueError if mode is not "dark" or "light".
        Raises RuntimeError if no theme is active yet — call set_theme() first.

        Signals fire for every token whose value differs between the two variants.
        """
        if mode not in _MODES:
            raise ValueError(f"mode must be 'dark' or 'light', got {mode!r}.")
        if self._active_theme is None:
            raise RuntimeError("No theme is active. Call set_theme() first.")
        self._apply(self._active_theme, mode)

    def toggle_mode(self) -> None:
        """Flip the active mode between "dark" and "light".

        Raises RuntimeError if no theme is active yet — call set_theme() first.
        """
        self.set_mode("light" if self._active_mode == "dark" else "dark")

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        """Return the active token value for key, or default if not present.

        Also calls _record(self, key) so that when this runs inside a
        @registry.reactive first call, the key is registered as a dependency.
        A Qt signal connection is then wired to re-run the method whenever
        this token changes — including changes caused by a theme or mode switch.
        Outside a tracking context _record is a no-op.
        """
        _record(self, key)
        return self._tokens.get(key, default)

    @property
    def active_theme(self) -> str | None:
        """Name of the currently active theme, or None if no theme has been set."""
        return self._active_theme

    @property
    def active_mode(self) -> str:
        """The currently active mode: "dark" or "light". Defaults to "dark"."""
        return self._active_mode

    def as_dict(self) -> dict[str, Any]:
        """Return a shallow snapshot of all active token key/value pairs."""
        return dict(self._tokens)

    def on(self, key: str) -> Signal:
        """Return the per-key signal for key, creating its emitter on demand.

        The emitter is parented to this QObject so it is destroyed automatically
        when the store is destroyed. Emits (new_value,) whenever that token
        changes value.

        Also used internally by ReactiveDescriptor to wire signal connections
        during the first call to a @registry.reactive method.
        """
        if key not in self._key_signals:
            self._key_signals[key] = _KeySignalEmitter(self)
        return self._key_signals[key].sig

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _apply(self, name: str, mode: str) -> None:
        """Replace the active token set with the (name, mode) variant and emit signals.

        This is the single internal method that all switching operations funnel
        through: set_theme(), set_mode(), toggle_mode(), and register() when
        the active theme is re-registered.

        State is committed first — self._tokens, self._active_theme, and
        self._active_mode are all updated before any signal fires. This means
        any handler that calls theme.get() or reads active_theme / active_mode
        during signal dispatch will see the new, consistent state.

        Only tokens whose value actually changed emit signals. Unchanged tokens
        are silent, so reactive methods that read unaffected keys do not re-run
        unnecessarily.

        Tokens present in the old set but absent from the new set are silently
        dropped — there is no meaningful "new value" to emit for a removal.

        Signal order: per-key .on(key) → .changed (per changed token) → .theme_changed.
        """
        new_tokens = self._themes[name][mode]
        old_tokens = self._tokens

        # Compute the diff before committing state so we know exactly what changed.
        changed_items = []
        for key in set(old_tokens) | set(new_tokens):
            new_val = new_tokens.get(key, _MISSING)
            if new_val is _MISSING:
                continue  # key dropped from new theme — no signal emitted
            if not _values_equal(old_tokens.get(key, _MISSING), new_val):
                changed_items.append((key, new_val))

        # Commit state before emitting so signal handlers observe the new theme/mode.
        self._tokens = dict(new_tokens)  # copy — isolates live tokens from the definition
        self._active_theme = name
        self._active_mode = mode

        # Emit per-token signals now that state is fully consistent.
        for key, new_val in changed_items:
            emitter = self._key_signals.get(key)
            if emitter:
                emitter.sig.emit(new_val)   # per-key listeners first
            self.changed.emit(key, new_val) # global listeners second

        # theme_changed fires last, after all per-token signals.
        self.theme_changed.emit(name, mode)

    @staticmethod
    def _validate_definition(name: str, definition: dict) -> None:
        """Raise ValueError if definition is missing a variant or contains None values.

        Called by register() before storing the definition, ensuring the store
        never holds an invalid theme that could cause silent failures later.
        """
        for mode in _MODES:
            if mode not in definition:
                raise ValueError(
                    f"Theme {name!r} is missing the {mode!r} variant. "
                    "Both 'dark' and 'light' must be present."
                )
            none_keys = [k for k, v in definition[mode].items() if v is None]
            if none_keys:
                raise ValueError(
                    f"Theme {name!r} ({mode}): None is not a valid token value. "
                    f"Keys with None: {none_keys}"
                )
