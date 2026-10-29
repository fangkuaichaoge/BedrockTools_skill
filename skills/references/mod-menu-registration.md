# Mod Menu registration: the contract nobody documents

A module that never appears in the launcher's Mod Menu is the most common "my mod is broken"
report that is not actually about the mod. This file records the registration contract read
out of the preloader's own source, the two ways it fails **silently**, and the recipe that
works on the launcher this workspace targets.

Read this together with `levilauncher-native-mod-guide.md` (host-side layout and manifest) and
`mod-lifecycle-and-crash-safety.md` (when your code is allowed to run).

---

## 1. The two rules that reject a module

`RegisterCppModule` in the preloader's `ModMenuBridge` applies these before storing anything:

```cpp
const std::string ownerModId = CurrentOwnerModId();   // thread_local
if (!ownerModId.empty()) {
    if (modId.empty()) modId = ownerModId;            // filled in for you
    else if (modId != ownerModId) {
        preloaderLogger.error("Rejected Mod Menu module {}: declared mod_id {} does not "
                              "match owning lifecycle mod {}", ...);
        return false;                                  // rejected outright
    }
}
```

**Rule 1 — `mod_id` must equal the owning lifecycle mod id, or be empty.**
The owner id is the **mod directory name** (`ModManifest.cpp`: `storage.modId = parsedMod->id`
where `id = modDirectory.filename()`). Declaring `modId("mymod")` while the installed folder is
`MyMod-1.2.3` is an instant rejection — and the only trace is a preloader log line, not yours.

**Rule 2 — the owner id only exists while the preloader is calling you.**
It lives in `thread_local std::vector<std::string> g_currentOwnerModIds`, pushed by
`ScopedModMenuOwner owner(modInfo.modId)` around the synchronous `load()` / `enable()` call:

```cpp
{ pl::runtime::ScopedModMenuOwner owner(modInfo.modId);
  loaded = runCppLifecycle(... load ...); }
```

Register from a detached thread and the scope is empty. With `mod_id` also empty the module
still registers, but it is attributed to no mod, so it lands in the launcher's "ungrouped"
bucket instead of under your mod.

A third rule is easy to trip while iterating: a duplicate `module_id` is rejected
(`"Rejected duplicate Mod Menu module {}"`). Retry loops are therefore only safe if they stop
on the first success.

## 2. Why it looks like "the menu is broken"

`registerModule()` returns `bool`, and nothing else surfaces a failure. The launcher reads
modules on demand through `nativeGetExternalModsInfo()`, so:

* a rejected module simply does not exist — no crash, no missing feature, no error dialog;
* a module registered from a spawned thread still shows up, but with no owning mod.

Both look identical from the device: "the mod loads, the feature works, the menu entry is
missing."

## 3. The recipe that works

1. **Do not declare `modId` inside a lifecycle callback.** Let the preloader fill in the owner.
2. **Register synchronously** from `load()`, and again from `enable()` if the first attempt
   failed (some launcher versions accept modules only after the mod is enabled).
3. If you want a registration attempt before the lifecycle exists (for example from a library
   constructor, which is the earliest code you control), **declare the mod id explicitly**,
   taken from your own library path:

   ```cpp
   std::string ownModIdFromMapping() {           // <mod-dir> name, exactly what the launcher uses
       Dl_info info{};
       if (dladdr(reinterpret_cast<void*>(&ownModIdFromMapping), &info) == 0 || !info.dli_fname)
           return {};
       std::string path(info.dli_fname);
       const auto file = path.find_last_of('/');
       if (file == std::string::npos || file == 0) return {};
       path.resize(file);
       const auto dir = path.find_last_of('/');
       return dir == std::string::npos ? path : path.substr(dir + 1);
   }
   ```

   With no lifecycle owner active the mismatch check is skipped, so this is accepted. It also
   makes a short retry loop safe when the menu bridge is still starting up.

4. Log the outcome of **your** attempt in a channel you control (a file, see
   `instrumentation-and-diagnostics.md`) — the preloader's own rejection message goes to the
   preloader log, which is not where you look first.

## 4. Configuring the module

`ModuleBuilder` accepts `Toggle`, `SliderInt`, `SliderFloat`, `Radio`, `Color`, `Keybind`,
`Text`, `Button` with `defaultValue`, `minValue`, `maxValue` and `dependsOn`. Values arrive as
**strings** in `onConfigChanged`, so parse them without exceptions:

```cpp
builder.description("...")
       .defaultEnabled(true)
       .onToggle([](std::string_view, bool on) { enabled.store(on); })
       .onConfigChanged([](std::string_view, std::string_view key, std::string_view value) {
           applyConfigValue(key, value);      // parseFloat(value) etc.
       })
       .config("mode", "Display Mode", ConfigType::SliderInt, "0", "0", "2")
       .config("pos_x", "Panel X", ConfigType::SliderFloat, "8", "-400", "1200");
```

Practical notes:

* `Radio` needs its option list from the newer schema API; a `SliderInt` with a described range
  in the label ("0 grid / 1 list / 2 list no panel") is simpler and works everywhere.
* Keep the callback bodies allocation-light and exception-free; they run on the menu's thread.
* Every config the user can change should have an in-memory default that matches its declared
  default, or the first frame and the first menu visit disagree.

## 5. Manifest / build coupling

* `manifest.entry` must match the built `.so` name byte for byte; the target name in
  `CMakeLists.txt` therefore decides both.
* The **folder name** is the runtime mod id (see rule 1). Renaming the folder is fine, but then
  never hardcode a `modId` in code.
* A `.levipack` is a zip with `manifest.json` and the library at its **root** — not inside a
  folder. Verify the packaged `.so` hash equals the freshly built one before shipping.

## 6. Checklist when a module does not appear

1. Does the mod's own log show a registration attempt at all? If not, `load()` is not running
   (see `mod-lifecycle-and-crash-safety.md`).
2. Is `modId` declared? Compare it against the installed folder name.
3. Was the registration call on a spawned thread? Move it into `load()` / `enable()`.
4. Is the module id already registered (retry loop running twice)?
5. Check the preloader log for `Rejected Mod Menu module` — it names the reason.

## HUD buttons have a stricter life cycle than modules

A module registered at any point shows up the next time the menu is opened. A **button** does not:
the launcher builds its on-screen button list once, when the HUD overlay starts, and the preloader
does not notify it when a button is registered later.

Consequences, all observed:

* a button registered during `load()`/`enable()` may appear;
* a button registered later (for example when a container screen opens) **never appears**;
* a button that does appear can be equally inert, because the overlay dispatches events against the
  list it snapshotted, so `onEvent` is not reached.

There is no per-button visibility API either: `ButtonInfo::defaultVisible` is applied at
registration, and `unregisterButton` removes it from the list without triggering a refresh.

**Design rule:** if a feature needs a control that appears and disappears with game state, do not
build it on HUD buttons. Draw the control yourself (see `preview-panels-and-container-ui.md`) or
pick a gesture. Reserve HUD buttons for controls that exist for the whole session.
