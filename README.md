# Registry

A reactive key/value store system for PySide6 applications. Combines a **settings store** and a **theme store** under a single registry, with a `@registry.reactive` decorator that automatically re-runs methods when the values they read change.

# Work in progress - 0.1

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
│   └── Theme.py
└── your_app.py
```

---

## Quick Start

```python
from Registry import settings, theme, registry

# Configure stores once at startup
settings.set("volume", 75)
settings.set("muted", False)

theme.register("catppuccin", {
    "dark":  {"color.background": "#1e1e2e", "color.text": "#cdd6f4"},
    "light": {"color.background": "#eff1f5", "color.text": "#4c4f69"},
})
theme.set_theme("catppuccin")

# Use @registry.reactive on any instance method
class MyWidget(QWidget):

    @registry.reactive
    def refresh(self):
        bg  = theme.get("color.background")
        fg  = theme.get("color.text")
        vol = settings.get("volume")
        self.setStyleSheet(f"background:{bg}; color:{fg};")
        self.slider.setValue(vol)

    def __init__(self):
        super().__init__()
        self.refresh()  # first call — tracks keys, wires all signal connections
```

After this, calling `settings.set("volume", 80)` or `theme.toggle_mode()` will automatically re-run `refresh()` on every wired instance — no manual signal wiring required.

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
settings.set("volume", 80)         # emits signals if value changed

# Bulk operations
settings.load_dict({"volume": 80, "muted": True})
settings.as_dict()                  # shallow snapshot of all values

# Subscribing
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

# Subscribing
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

## Reactivity

### `@registry.reactive`

Wrap any instance method that reads from `settings` or `theme`. On the **first call** to the method on a given instance, every `.get()` access across both stores is recorded as a dependency. One Qt signal connection is wired per unique `(store, key)` pair, and the method re-runs automatically whenever any of those values change.

```python
class PlayerControls(QWidget):

    @registry.reactive
    def refresh(self):
        vol   = settings.get("volume")
        muted = settings.get("muted")
        bg    = theme.get("color.background")
        self.slider.setValue(vol)
        self.mute_btn.setChecked(muted)
        self.setStyleSheet(f"background: {bg};")

    def __init__(self):
        super().__init__()
        self.refresh()  # first call wires everything
```

### How it works

1. The first call runs the method inside a tracking context.
2. Every `.get()` call appends `(store, key)` to the active context.
3. After the method returns, one Qt signal connection is created per unique `(store, key)` pair.
4. The signal handler re-runs the method on the same instance whenever that key changes.
5. Subsequent calls to the method skip tracking and run directly.
6. When the instance is garbage collected, a `weakref` finalizer disconnects all signals automatically — **no manual cleanup needed**.

### Important constraint

> Every key that should trigger re-runs must be read **unconditionally** on the first call. A key accessed only inside a branch that does not execute on the first call will not be tracked and will not cause a re-run.

```python
@registry.reactive
def refresh(self):
    vol  = settings.get("volume")  # ✓ always read — always tracked
    mode = settings.get("mode")

    if mode == "advanced":
        eq = settings.get("equalizer")  # ✗ only read in one branch — may not be tracked
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

### Nested reactive methods

Tracking uses a per-thread stack, so nested reactive calls each collect their own dependencies independently without interfering with each other.

---

## Thread Safety

`settings.set()`, `theme.set_theme()`, `theme.set_mode()`, and `theme.register()` must be called from the **Qt main thread**. To update a setting from a worker thread, post to the main thread via `QMetaObject.invokeMethod` or a queued signal connection.

---

## Signals Reference

| Signal | Store | Args | When |
|---|---|---|---|
| `settings.on(key)` | SettingsModel | `(new_value,)` | Key changed |
| `settings.changed` | SettingsModel | `(key, value)` | Any key changed |
| `theme.on(key)` | ThemeStore | `(new_value,)` | Token changed |
| `theme.changed` | ThemeStore | `(key, value)` | Any token changed |
| `theme.theme_changed` | ThemeStore | `(name, mode)` | Theme or mode switched |

Per-key `.on(key)` always fires **before** the broader `.changed` signal. `theme.theme_changed` fires last, after all per-token signals.

---

## Imports Reference

```python
from Registry import settings   # SettingsModel singleton
from Registry import theme      # ThemeStore singleton
from Registry import registry   # Registry singleton (needed for @registry.reactive)
```

Do not import `Settings`, `Theme`, or `Reactive` directly in application code.
