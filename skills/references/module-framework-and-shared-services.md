# Module framework and shared services

A mod that ships twenty small features should not be twenty unrelated hook installers. The shipped
implementations separate "one feature" from "the mod's machinery": a module base class, a registry,
and a small set of shared services that own the cross-cutting hooks. This reference is the shape
that keeps a many-feature mod debuggable.

## Module base class

A module is a small object with metadata, three states, and virtual hooks:

```cpp
class Module {
public:
    const char* name;
    const char* description;
    std::string moduleId;        // derived from the name, stable
    bool masterEnabled = false;  // user/global switch
    bool keybindActive = true;   // momentary toggle
    bool enabled = false;        // masterEnabled && keybindActive
    bool showInMenu = true;
    bool hideInHudEditor = false;
    int keybind = 0;

    virtual void onInit() {}
    virtual void onEnable() {}
    virtual void onDisable() {}
    virtual void onFrame() {}
    virtual bool onMouseEvent(int button, bool isDown) { return false; }
    virtual bool onMenuConfigChanged(std::string_view key, std::string_view value) { return false; }
    virtual void onMenuRegistered() {}
    virtual void onKeybindEvent(const std::string& key, bool isDown);

    void setMasterEnabled(bool v);
    void setKeybindActive(bool v);
    virtual void loadConfig(const json& j);
    virtual void saveConfig(json& j);
};
```

Rules that make this work:

- **One transition point.** `enabled = masterEnabled && keybindActive`, and `onEnable`/`onDisable`
  fire only when the derived value *changes*. Config loading calls the same transition, so a cold
  start and a menu toggle take exactly one code path.
- **Metadata is data.** Name, description, menu visibility and keybind live in the object, so the
  menu builder can enumerate modules without knowing any of them.
- **Each module owns its hook handles.** `onEnable` installs, `onDisable` removes; the module never
  assumes the mod is fully enabled elsewhere.
- **Config is per module but written by the registry.** Unknown keys are ignored, missing keys keep
  defaults, and saving happens from one place.

## Registry

```cpp
class ModuleRegistry {
public:
    static ModuleRegistry& get();
    template <class T, class... Args> T& emplace(Args&&...);
    Module* find(std::string_view id) const;
    const std::vector<Module*>& modules() const;
    void initialize();
    void onFrame();
    bool onMouseEvent(int button, bool isDown);
    void setKeybindBlocked(bool);
private:
    std::vector<std::unique_ptr<Module>> owned;
    std::vector<Module*> view;
    std::unordered_map<std::string, Module*, TransparentHash, std::equal_to<>> byId;
};
```

- Own modules by `unique_ptr`, keep a raw `view` for iteration, and a **heterogeneous** lookup map
  (transparent `string_view` hash) so `find("id")` does not allocate.
- `initialize()` runs once after all modules are emplaced; `onFrame()`/`onMouseEvent()` dispatch in
  registration order and stop at the first module that consumes the event.
- Registration lives in one `registerAllModules()` unit: adding a feature edits exactly one file,
  not the lifecycle.
- A "keybind blocked" flag lets a modal UI suppress module hotkeys without every module knowing.

## Shared service: UI render-pass subscription

Twenty HUD modules must not install twenty UI hooks. Centralise it:

- Hook the UI context's **finish** slot, and optionally its **text** slot, resolved by class name +
  named slot constant (never a raw address).
- The text hook is passive: remember the current context and font in `thread_local` variables; then
  forward unchanged.
- The finish hook runs once per context per frame: dispatch a fixed array of subscribers with
  `(context, font)`. A `drawing` guard prevents a subscriber that draws from re-entering the hook.
- Registration: fixed-size array, deduplicate by function pointer, a mutex for subscription and an
  atomic count for lock-free dispatch. Install the hooks lazily on the first subscriber.
- Clear the remembered context/font at the end of finish so a stale font never leaks into the next
  frame.

Why this shape: ordering, re-entrancy and the "which font/context is current" state are solved once,
and every module just draws with the context it is handed.

## Shared service: delayed actions

Some actions must happen after a tick or a UI transition, but sleeping or per-frame flag polling is
a bug factory. Use a small scheduler bound to identity:

```cpp
schedule(void* client, void* player, int delay, std::string value);
std::optional<std::string> take(void* client, void* player);
void cancel();
void resetCooldown();
```

- Reject a new action while one is pending or inside a short cooldown (a few seconds).
- Clamp the delay (for example 0–10 s) and store an absolute due time.
- `take` drops the action if the client/player pointers changed: the world or the session is not the
  same one anymore.
- Clear before returning the value, so one action fires once.

## Shared service: frame state

Capture once per frame into a small struct (renderer, context, dimensions, UI scale, validity) and
let services read it. No module should cache its own renderer or context across frames; a captured
pointer is only valid for the pass that produced it.

## Named constants instead of magic numbers

Keep `constexpr` headers per domain — core, render, UI, world, network, inventory — with named slot
indices and field offsets. Feature code never carries an integer displacement; a build update edits
one header per domain. Keep slot indices and field offsets in *separate* namespaces: they drift
independently.

## Checklist

1. Module base class derives `enabled`, and transitions fire only on change.
2. Every module owns its hook handles; enable installs, disable removes.
3. Registry owns modules, keeps a raw view, and does allocation-free id lookup.
4. One registration unit; lifecycle does not know individual features.
5. UI drawing goes through one subscription service with a re-entrancy guard.
6. Delayed actions are identity-bound, cooldown-gated and single-shot.
7. Frame state is captured once per frame and not cached across frames.
8. Slot indices and field offsets live in separate, named constant headers.
9. Config load/save is per module but driven by one registry.
10. A modal UI can block module keybinds without module changes.