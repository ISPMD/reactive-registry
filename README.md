# Registry

A reactive key/value store system for PySide6 applications. Combines a **settings store**, a **theme store**, and a **translation store** under a single registry, with a `@registry.reactive` decorator that automatically re-runs methods when the values they read change — no manual signal wiring required.


# Work in progress - 0.4

---

## Installation

Copy the `Registry/` package directory into your project. Requires **PySide6**.

```
your_project/
├── Registry/
│   ├── __init__.py
│   ├── Registry.py
│   ├── Reactive.py
│   ├── Settings.py
│   ├── Theme.py
│   ├── Translation.py
│   └── Persistence.py
└── your_app.py
```

---

## Quick Start

```python
from Registry import settings, theme, translations, registry

# Configure stores once at startup
settings.set("volume", 75)
settings.set("muted", False)

theme.register("catppuccin", {
    "dark":  {"color.background": "#1e1e2e", "color.text": "#cdd6f4"},
    "light": {"color.background": "#eff1f5", "color.text": "#4c4f69"},
})
theme.set_theme("catppuccin")

translations.register("en", {"volume.label": "Volume", "mute.label": "Mute"})
translations.register("es", {"volume.label": "Volumen", "mute.label": "Silenciar"})
translations.set_language("en")

# Use @registry.reactive on any instance method
class MyWidget(QWidget):

    @registry.reactive
    def refresh(self):
        bg    = theme.get("color.background")
        fg    = theme.get("color.text")
        vol   = settings.get("volume")
        label = translations.get("volume.label")
        self.setStyleSheet(f"background:{bg}; color:{fg};")
        self.slider.setValue(vol)
        self.label.setText(label)

    def __init__(self):
        super().__init__()
        self.refresh()  # first call — tracks keys and wires all signal connections
```

After this, calling `settings.set("volume", 80)`, `theme.toggle_mode()`, or `translations.set_language("es")` will automatically re-run `refresh()` on every wired instance — no manual signal wiring required.

---

## Stores

### `settings` — SettingsModel

A flat key/value store for application state.

```python
from Registry import settings

# Reading
settings.get("volume")             # current value, or None if not set
settings.get("volume", default=0)  # current value, or 0 if not set

# Writing
settings.set("volume", 80)         # emits signals only if value changed

# Bulk operations
settings.load_dict({"volume": 80, "muted": True})
settings.as_dict()                  # shallow snapshot of all values

# Subscribing manually
settings.on("volume").connect(cb)   # cb(new_value) — fires for this key only
settings.changed.connect(cb)        # cb(key, value) — fires for every change
```

**Construction with defaults:**

```python
from Registry.Settings import SettingsModel

settings = SettingsModel(defaults={"volume": 50, "muted": False})
```

> **None policy:** `None` is not a valid value. `set()` raises `TypeError` and `defaults` raises `ValueError` if any value is `None`. Use a sentinel (e.g. `""` or `-1`) or omit the key to represent absence.

---

### `theme` — ThemeStore

A store of design tokens organised into named themes with `"dark"` and `"light"` variants.

```python
from Registry import theme

# Register a theme
theme.register("catppuccin", {
    "dark":  {"color.background": "#1e1e2e", "color.text": "#cdd6f4"},
    "light": {"color.background": "#eff1f5", "color.text": "#4c4f69"},
})

# Switching
theme.set_theme("catppuccin")   # activate theme, keep current mode
theme.set_mode("light")         # switch mode, keep current theme
theme.toggle_mode()             # flip dark ↔ light

# Reading
theme.get("color.background")          # active token value, or None
theme.get("color.background", "#000")  # active token value, or fallback
theme.active_theme                     # name of active theme (str | None)
theme.active_mode                      # "dark" or "light"
theme.as_dict()                        # shallow snapshot of active tokens

# Subscribing manually
theme.on("color.background").connect(cb)  # cb(new_value) for this token
theme.changed.connect(cb)                 # cb(key, value) per changed token
theme.theme_changed.connect(cb)           # cb(name, mode) once per switch
```

**Token key naming:** Dotted keys like `"color.background"` are a convention only — no nesting is performed. Any string is a valid key.

**Efficient diffing:** On every theme or mode switch, only tokens whose value actually changed emit signals. Reactive methods that read unaffected tokens do not re-run.

**Unregistering:**

```python
theme.unregister("catppuccin")  # raises RuntimeError if currently active
```

---

### `translations` — TranslationStore

A store of UI strings organised into named language packs.

```python
from Registry import translations

# Register language packs
translations.register("en", {
    "volume.label": "Volume",
    "welcome":      "Hello, {name}!",
})
translations.register("es", {
    "volume.label": "Volumen",
    "welcome":      "Hola, {name}!",
})

# Switching
translations.set_language("en")   # activate a language pack

# Reading
translations.get("volume.label")                 # translated string
translations.get("missing.key")                  # returns "missing.key" if not found
translations.get("missing.key", fallback="???")  # custom fallback
translations.get("welcome", name="Alice")        # with interpolation → "Hello, Alice!"
translations.active_language                     # name of active language (str | None)
translations.as_dict()                           # shallow snapshot of active pack

# Subscribing manually
translations.language_changed.connect(cb)   # cb(language_name) on every switch
```

**Fallback behaviour:** If a key is missing from the active pack, `.get()` returns the key itself by default. This makes untranslated keys visible during development without crashing. Pass `fallback=""` or any other value to override.

**Interpolation:** Pass keyword arguments to `.get()` to format the translated string. If formatting fails (e.g. wrong or missing kwargs), the raw unformatted string is returned and a warning is printed rather than raising.

**Reactive model:** Unlike `SettingsModel` and `ThemeStore`, `TranslationStore` does not track individual keys. The entire language pack is treated as a single dependency. Any method that reads at least one translation via `.get()` is wired to re-run on every `set_language()` call — regardless of how many keys it reads. This keeps the number of signal connections small and the mental model simple: a language switch always re-runs the whole method.

**Unregistering:**

```python
translations.unregister("en")  # raises RuntimeError if currently active
```

> **None policy:** `None` is not a valid translation value. `register()` raises `ValueError` if any value in the pack is `None`.

---

## Persistence

Save and load registry state to and from JSON files. All functions take only a file path and return a result object.

```python
from Registry.Persistence import (
    save, load,
    save_state, load_state,
    save_themes, load_themes,
    save_translations, load_translations,
)
```

### Functions

There are four pairs of functions, each covering a different scope:

| Pair | What it covers |
|---|---|
| `save` / `load` | Everything: settings, full theme definitions, full translation packs, active selections |
| `save_state` / `load_state` | Active state only: settings values + active theme name, mode, and language |
| `save_themes` / `load_themes` | Full ThemeStore: all registered definitions + active theme and mode |
| `save_translations` / `load_translations` | Full TranslationStore: all registered packs + active language |

### Choosing the right pair

**`save` / `load`** — use when you want the file to be fully self-contained. Themes and translations are re-registered from the file on load, so the calling code does not need to register them at startup.

**`save_state` / `load_state`** — use when themes and translations are always registered from code at startup and only the current user selection needs to persist (which theme is active, which language is selected, what the slider values are). The file is small and load is fast. `load_state()` calls `set_theme()` and `set_language()` with the stored names, so the relevant themes and packs must already be registered before calling it.

**`save_themes` / `load_themes`** — use when you want to persist theme definitions independently, for example when themes are user-created or loaded from a separate themes file at startup.

**`save_translations` / `load_translations`** — use when language packs are loaded from a separate translations file rather than being hardcoded.

### Usage

```python
from Registry.Persistence import save, load, save_state, load_state

# Save and load everything
result = save("app_data.json")
result = load("app_data.json")

# Save and load active state only
# (themes and translations must already be registered)
result = save_state("app_state.json")
result = load_state("app_state.json")

# Save and load stores individually
result = save_themes("themes.json")
result = load_themes("themes.json")

result = save_translations("translations.json")
result = load_translations("translations.json")
```

### Results

Every function returns a `SaveResult` or `LoadResult` with three fields:

```python
result.ok        # True if the operation fully succeeded with no warnings
result.error     # Exception if a fatal error occurred, None otherwise
result.warnings  # list[str] of non-fatal per-key/store issues

if not result.ok:
    if result.error:
        print("Fatal:", result.error)
    for w in result.warnings:
        print("Warning:", w)
```

`result.ok` is `False` whenever either `result.error` is set **or** `result.warnings` is non-empty. For load functions, `result.error` means no stores were modified. Warnings mean the load completed partially — other stores or keys may have been restored successfully.

### Common patterns

**Auto-load on startup, fall back to defaults:**

```python
from Registry.Persistence import load_state

# Register themes and translations from code first
theme.register("catppuccin", { ... })
translations.register("en", { ... })
translations.register("es", { ... })

# Then restore the user's last selection
result = load_state("app_state.json")
if not result.ok:
    # File missing or unreadable — apply defaults
    theme.set_theme("catppuccin")
    theme.set_mode("dark")
    translations.set_language("en")
    settings.set("volume", 75)
```

**Save on exit:**

```python
app.aboutToQuit.connect(lambda: save_state("app_state.json"))
```

**Save with error reporting:**

```python
result = save_state("app_state.json")
if not result.ok:
    print(f"Could not save: {result.error}")
```

### File formats

`save_state` produces a flat object — the most compact format:

```json
{
  "settings": { "volume": 80, "muted": false },
  "active_theme": "catppuccin",
  "active_mode": "dark",
  "active_language": "en"
}
```

`save_themes` produces a standalone theme file:

```json
{
  "active_theme": "catppuccin",
  "active_mode": "dark",
  "themes": {
    "catppuccin": {
      "dark":  { "color.background": "#1e1e2e", "color.text": "#cdd6f4" },
      "light": { "color.background": "#eff1f5", "color.text": "#4c4f69" }
    }
  }
}
```

`save_translations` produces a standalone translations file:

```json
{
  "active_language": "en",
  "packs": {
    "en": { "volume.label": "Volume", "welcome": "Hello, {name}!" },
    "es": { "volume.label": "Volumen", "welcome": "Hola, {name}!" }
  }
}
```

`save` combines all three stores under `"settings"`, `"theme"`, and `"translations"` keys in a single file.

> **None policy:** `None` is not a valid settings value and cannot appear in a saved file. `None` values encountered in a file during load are skipped with a warning rather than crashing.

> **Thread safety:** All persistence functions must be called from the Qt main thread.

---

## Reactivity

### `@registry.reactive` — per-instance tracking

Wrap any instance method that reads from `settings`, `theme`, or `translations`. On the **first call** to the method on a given instance, every `.get()` access across all three stores is recorded as a dependency. Signal connections are wired so the method re-runs automatically whenever any of those values change.

```python
class PlayerControls(QWidget):

    @registry.reactive
    def refresh(self):
        vol   = settings.get("volume")
        muted = settings.get("muted")
        bg    = theme.get("color.background")
        label = translations.get("volume.label")
        self.slider.setValue(vol)
        self.mute_btn.setChecked(muted)
        self.label.setText(label)
        self.setStyleSheet(f"background: {bg};")

    def __init__(self):
        super().__init__()
        self.refresh()  # first call wires everything
```

For `settings` and `theme`, one connection is wired per unique `(store, key)` pair. For `translations`, one connection is wired per method regardless of how many keys it reads — the whole pack is treated as a single dependency.

Each instance gets its own set of signal connections. When the instance is garbage collected, a `weakref` finalizer disconnects all connections automatically — no manual cleanup needed.

---

### `@registry.reactive_class` — class-level tracking

For classes where many instances exist and all should update together, apply `@registry.reactive_class` to the class. The **first instance** to call the reactive method triggers dependency tracking and wires one Qt signal connection per `(store, key)` pair for the **entire class**. When a tracked key changes, a single Qt dispatch calls the method on every currently living instance via a `WeakSet`.

```python
@registry.reactive_class
class VolumeLabel(QLabel):

    @registry.reactive
    def refresh(self):
        vol   = settings.get("volume")
        bg    = theme.get("color.background")
        label = translations.get("volume.label")
        self.setText(f"{label}: {vol}")
        self.setStyleSheet(f"background: {bg};")

    def __init__(self):
        super().__init__()
        self.refresh()  # first instance: tracks + wires; later instances: join + run
```

**When to prefer `@registry.reactive_class`:**
- You create many instances of the same widget class (e.g. a row of badges or list items).
- All instances read the same keys unconditionally.
- You want a single Qt signal dispatch to update every instance rather than one dispatch per instance.

> **Constraint:** tracking is done once using the first instance. All instances must read the same keys unconditionally. Keys only read by later instances will never be tracked.

---

## The tracking constraint

Every key that should trigger re-runs must be read **unconditionally** on the first call. A key accessed only inside a branch that does not execute on the first call will not be tracked.

```python
@registry.reactive
def refresh(self):
    vol  = settings.get("volume")   # ✓ always read — always tracked
    mode = settings.get("mode")

    if mode == "advanced":
        eq = settings.get("equalizer")  # ✗ only read in one branch — may miss tracking
```

To track `"equalizer"` unconditionally, read it before any branching:

```python
@registry.reactive
def refresh(self):
    vol  = settings.get("volume")
    mode = settings.get("mode")
    eq   = settings.get("equalizer")  # ✓ read before branching — always tracked

    if mode == "advanced":
        self.eq_widget.setValue(eq)
```

This constraint applies to `theme.get()` as well. It does **not** apply to `translations.get()` — since all translation keys share a single dependency, it does not matter which keys are read or whether any are inside branches. A language switch always re-runs the whole method.

---

## How tracking works internally

1. The first call runs the method inside a tracking context.
2. Every `.get()` call appends `(store, key)` to the active context's dependency list. For `translations`, the key is always a fixed sentinel (`"_language"`) regardless of which string was requested, so all translation reads collapse into one dependency.
3. After the method returns, the list is deduplicated and one Qt signal connection is created per unique `(store, key)` pair.
4. The signal handler re-runs the method on the same instance whenever that key changes.
5. Subsequent calls skip tracking and run the method directly.
6. When the instance is GC'd, a `weakref` finalizer calls `disconnect_conns` to clean up all connections.

Tracking uses a per-thread stack of dependency lists, so nested reactive calls each collect their own dependencies without interfering with each other.

---

## Thread safety

`settings.set()`, `theme.set_theme()`, `theme.set_mode()`, `theme.register()`, `translations.set_language()`, and all persistence functions must be called from the **Qt main thread**. To update state from a worker thread, post to the main thread via `QMetaObject.invokeMethod` or a queued signal connection.

---

## Signals reference

| Signal | Store | Arguments | When it fires |
|---|---|---|---|
| `settings.on(key)` | SettingsModel | `(new_value,)` | That key changed |
| `settings.changed` | SettingsModel | `(key, value)` | Any key changed |
| `theme.on(key)` | ThemeStore | `(new_value,)` | That token changed |
| `theme.changed` | ThemeStore | `(key, value)` | Any token changed |
| `theme.theme_changed` | ThemeStore | `(name, mode)` | Theme or mode switched |
| `translations.language_changed` | TranslationStore | `(language_name,)` | Language switched |

Per-key `.on(key)` always fires **before** the broader `.changed` signal. `theme.theme_changed` fires last, after all per-token signals. State is fully committed before any signal fires, so handlers always see the new values.

---

## Imports reference

```python
from Registry import settings      # SettingsModel singleton
from Registry import theme         # ThemeStore singleton
from Registry import translations  # TranslationStore singleton
from Registry import registry      # Registry singleton (needed for @registry.reactive)

from Registry.Persistence import save, load
from Registry.Persistence import save_state, load_state
from Registry.Persistence import save_themes, load_themes
from Registry.Persistence import save_translations, load_translations
```

Do not import `Settings`, `Theme`, `Translation`, or `Reactive` directly in application code.