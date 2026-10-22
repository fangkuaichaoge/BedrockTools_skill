# LeviLaunchroid Native Mods (distilled from the official docs)

Official documentation: <https://docs.levimc.org/LeviLaunchroid/zh-CN/guide/developer>
(English: `/LeviLaunchroid/guide/developer`). Public SDK:
<https://github.com/LiteLDev/preloader-android>. Reference implementation:
`examples/full-cpp-mod` in the launcher repo; standalone template:
`QYCottage/levilauncher-android-mod-template`.

This file is a **working summary, not a copy**. It keeps the facts that decide
whether a mod loads at all, and drops the parts that only matter when building
the launcher itself. Where the docs and the SDK headers disagree, the headers
win — they are the compiled contract; see §11.

---

## 1. What a native mod is, on disk

```
<mod-name>/                 <- directory name IS the runtime mod id; keep it stable
├── manifest.json
├── lib<name>.so            <- arm64-v8a, must equal manifest "entry"
└── config/                 <- optional, shipped by the mod
    ├── config.json
    └── config.schema.json
```

The mod id affects paths, Mod-Menu ownership and persisted user state, so renaming
the folder after release resets those (the same trap as renaming the library — see
`feature-rollback.md` §6).

### `manifest.json`

```json
{
  "type": "preload-native",
  "name": "My Mod",
  "author": "you",
  "version": "1.0.0",
  "entry": "libmy_mod.so",
  "icon": "icon.png",
  "minecraft_versions": [],
  "overwrite_files": [],
  "overwrite_folders": []
}
```

| field | meaning | the part that bites |
|---|---|---|
| `type` | must be `preload-native` | anything else is not a native mod |
| `entry` | relative path of the `.so` **inside the mod dir** | must match the built artifact byte-for-byte in name; a mismatch loads nothing and logs nothing useful |
| `name` | display name in the launcher | also the default logger tag |
| `version` | mod version | bump on every behaviour change; users reinstall by version |
| `icon` | optional relative icon path | invalid path is ignored, not fatal |
| `minecraft_versions` | exact versions or `*` prefix wildcards; empty/missing = all | an over-narrow list makes the mod silently unavailable |
| `overwrite_files` / `overwrite_folders` | paths that may be replaced when re-importing over an existing install | **default is "do not overwrite"**: without these, re-importing leaves old files in place, so an updated `.so` can appear not to apply |

## 2. Lifecycle

```cpp
#include <pl/Mod.hpp>

class MyMod {
public:
  static MyMod &instance();
  MyMod();
  [[nodiscard]] ll::mod::NativeMod &getSelf() const { return mSelf; }
  bool load();
  bool enable();
  bool disable();
  bool unload();
private:
  ll::mod::NativeMod &mSelf;
};

MyMod::MyMod() : mSelf(*ll::mod::NativeMod::current()) {}
PL_REGISTER_MOD(MyMod, MyMod::instance())
```

* `load()` is **required**; `enable()` / `disable()` / `unload()` are optional and
  count as successful when absent.
* `PL_REGISTER_MOD` exactly once per library, in one source file.
* The registered instance must outlive the process's mod loading; use a function-
  local `static`.
* **Both call styles are accepted** by the current SDK: `bool load()` and
  `bool load(pl::mod::ModContext &)`. The context form is the older style and is
  still supported; `ll::mod::NativeMod` is an alias of `pl::mod::NativeMod`.
* Return `false` on failure; never let an exception cross the boundary.

| method | do this | do not |
|---|---|---|
| `load()` | read config, create directories, prepare own state | install hooks (library may not be mapped yet) |
| `enable()` | install hooks / patches, register UI and input | assume the game library is loaded |
| `disable()` | remove hooks, unregister temporary UI | free state other callbacks still use |
| `unload()` | release remaining C++ state | run game calls |

> **Attach timing is not in the official docs and it is the #1 launcher-killer.**
> The preloader can enable a mod before `libminecraftpe.so` is mapped or fully
> relocated. Install hooks from a watcher that polls for the library and then
> lets it settle — see `mod-lifecycle-and-crash-safety.md`. Everything in
> `enable()` that touches the game image belongs behind that gate.

## 3. The `NativeMod` handle

```cpp
auto &self = getSelf();
std::filesystem::create_directories(self.getConfigDir());
self.getLogger().info("Loading {}", self.getName());
```

| member | use |
|---|---|
| `getLogger()` | this mod's `pl::log::Logger` (fmt-style, tagged by display name) |
| `getId()` | stable runtime mod id |
| `getName()` / `getAuthor()` / `getVersion()` | manifest metadata |
| `getModDir()` | mod package root |
| `getDataDir()` | `<root>/data` |
| `getConfigDir()` | `<root>/config` |
| `getResourceDir()` | `<root>/resources` — ship data files here and read them at runtime |
| `getEntryPath()` / `getEntryFileName()` / `getLibraryPath()` | resolved paths |
| `getManifestPath()` / `getIconPath()` | resolved manifest / icon |
| `getJavaVM()` | the process `JavaVM*` |
| `getState()` / `isLoaded()` / `isEnabled()` / `isDisabled()` | lifecycle state |

Directories are **paths, not created directories**: `create_directories` yourself.
A file written anywhere outside these three roots may not survive reinstall.

## 4. Linking the SDK

```cmake
include(FetchContent)
FetchContent_Declare(preloader_android
    GIT_REPOSITORY https://github.com/LiteLDev/preloader-android.git
    GIT_TAG 0.2.2)                     # pin a release tag, never a branch
FetchContent_MakeAvailable(preloader_android)
target_link_libraries(my_mod PRIVATE preloader)
```

* Include only the public headers under `include/pl`:

```cpp
#include <pl/Mod.hpp>        #include <pl/ModMenu.hpp>
#include <pl/Config.hpp>     #include <pl/Input.hpp>
#include <pl/memory/Hook.hpp>
#include <pl/memory/Patch.hpp>
#include <pl/memory/Signature.hpp>
```

* `preloader` is a **shared** library: the mod links against it, and the launcher
  provides it at runtime. Do not vendor its `.cpp` into your mod.
* Build for `arm64-v8a` only. `-fvisibility=hidden` is fine and preferable; the one
  symbol that must stay exported is the one `PL_REGISTER_MOD` defines
  (`PLGetModRegistration`). Verify it is in `.dynsym` after the build — a hidden or
  stripped-away entry point makes the mod load and do nothing.

## 5. Typed config

Rules for the type: a simple aggregate, public fields, default member
initialisers, and an `int version` field.

```cpp
struct ModConfig {
  int version = 1;
  bool enabled = true;
  double scale = 1.25;
};

std::optional<pl::config::ConfigFile<ModConfig>> mConfig;

bool MyMod::load() {
  mConfig.emplace();          // defaults to <root>/config/config.json
  return mConfig->load();
}
```

`ConfigFile<T>::load()` merges the file **into** the C++ defaults:

| situation | result |
|---|---|
| file missing | created from the defaults |
| new field in the struct | filled from the C++ default |
| field present and type-compatible | the user's value is kept |
| unknown keys in the JSON | dropped on the next write |
| `version` present | forced back to the C++ default |
| corrupt / truncated file | normalised and rewritten |

JSON field order follows the aggregate's field order; fields whose names start
with `$` are skipped by reflection.

Metadata for the launcher's config editor comes from specialising
`pl::config::Schema<T>` (`title`, `description`, and per-field
`FieldSchema{title, description, minimum, maximum, readOnly}`).

Supported field types: aggregate struct, `std::string`, `bool`, integral, floating
point, `enum` (string-valued through `magic_enum`), `std::vector<T>`,
`std::optional<T>`. Keep config types plain — no custom constructors, no private
fields, no logic.

## 6. Mod Menu

Register in `enable()`, tear down in `disable()` if the UI is enable-scoped.

```cpp
return pl::modmenu::ModuleBuilder("mymod.hud", "My HUD")
    .modId(getSelf().getId())
    .description("...")
    .defaultEnabled(true)
    .onToggle([](std::string_view id, bool on) { /* ... */ })
    .config("refreshRate", "Refresh Rate",
            pl::modmenu::ConfigType::SliderInt, "20", "1", "60")
    .registerModule();
```

`ConfigType`: `Toggle`, `SliderInt`, `SliderFloat`, `Radio`, `Color`.

Floating buttons via `ButtonBuilder` (id, label) → `.moduleId()`, `.modId()`,
`.label()`, `.androidKeyCode()`, `.behavior(ButtonBehavior::Click|Hold|Toggle|…)`,
`.onEvent(lambda(buttonId, event, value))`, icon via `pngIcon()` / `webpIcon()` /
`svgIcon()` / `icon()`.

HUD overlay:

| call | constraint |
|---|---|
| `registerImage(id, rgba, w, h)` | `rgba.size() == w * h * 4` exactly, raw RGBA |
| `registerFont(id, ttfData)` | TrueType bytes |
| `submitDrawCommands(moduleId, cmds)` | **replaces** the module's command list each time |
| `unregisterModule()` / `unregisterButton()` | call for temporary UI |

`DrawCommand` is a designated-initialiser struct: `type`, `x`, `y`, `w`, `h`,
`fontId` (for `Text`), `imageId` (for `Image`).

## 7. Input

```cpp
pl::input::registerTouchCallback([](const pl::input::TouchEvent &e) { return false; });
pl::input::registerKeyCallback  ([](const pl::input::KeyEvent   &e) { return false; });
pl::input::registerMouseCallback([](const pl::input::MouseEvent &e) { return false; });
pl::input::showKeyboard();  pl::input::hideKeyboard();
```

Returning `true` **consumes** the event. There is **no unregister API** — register
at most once per process and gate the behaviour on your own enabled flag inside the
callback, otherwise `disable()` cannot really disable it. Keep callbacks short; the
soft-keyboard helpers need the game Activity to be alive.

## 8. Memory APIs at a glance

| need | call | notes |
|---|---|---|
| resolve by signature | `pl::memory::resolveSignature(sig, "libminecraftpe.so")` | returns `0` on failure; batch with `resolveSignatures()` |
| install detour | `pl::memory::hook(target, detour, &original, HookPriority::Normal)` | `0` = success, `-1` = failure; lower priority value runs earlier |
| remove detour | `pl::memory::unhook(target, detour)` | or hold a `pl::memory::HookHandle` |
| patch bytes | `pl::memory::writeBytes(addr, "00 00 80 D2 C0 03 5F D6", "name")` | named so it can be reverted |
| revert | `revertPatch("name")` / `revertAllPatches()` | or hold a `pl::memory::PatchHandle` |

Pattern syntax: bytes separated by spaces (`48 8B ?? ?? 89`) or packed
(`488B????89`); `?` and `??` are a whole wildcard byte, `A?` wildcards the low
nibble, `?F` the high nibble.

Both handle types are RAII and belong in **mod state**, not in a local variable:
a handle destroyed early unhooks silently.

## 9. Build and package

```bash
cmake -S . -B build -G Ninja \
  -DCMAKE_TOOLCHAIN_FILE=<NDK>/build/cmake/android.toolchain.cmake \
  -DANDROID_ABI=arm64-v8a -DANDROID_PLATFORM=android-24 \
  -DCMAKE_BUILD_TYPE=Release
cmake --build build
```

A `.levipack` is a zip whose root is the mod directory:

```
manifest.json
libmy_mod.so
icon.png            (optional)
config/…            (optional)
resources/…         (optional)
```

Importing it, or copying the unpacked directory into the launcher's native-mods
folder, are equivalent. `overwrite_files` / `overwrite_folders` decide what a
re-import may replace.

## 10. Ship checklist

- `arm64-v8a`; links the `preloader` target; only `include/pl` headers used.
- One `PL_REGISTER_MOD`, one long-lived instance, `NativeMod` reference saved from
  `current()`.
- `PLGetModRegistration` present in the built `.so`'s dynamic symbols.
- Config loaded **before** any run-time UI is registered.
- Hook and patch handles live in mod state and are released in `disable()`.
- Temporary Mod Menu items unregistered in `disable()`.
- Input callbacks registered once and gated by an enabled flag (no unregister API).
- Every failure path returns `false` or falls back to vanilla behaviour; nothing
  throws across a lifecycle boundary.
- `manifest.json` `entry` equals the packaged `.so` name; version bumped on change.
- Hooks installed only after the game library is mapped and settled.

## 11. Docs vs SDK drift — check the headers

The pages above are generated from a repository whose SDK version may be newer or
older than the one you build against. Two concrete examples seen in this
workspace:

* the docs write `ll::mod::NativeMod`; the headers declare it in `pl::mod` and
  provide `namespace ll::mod { using … }` as an alias — both compile;
* the docs show `bool load()` only, while the headers also accept
  `bool load(pl::mod::ModContext &)`, and detect it with a `requires` expression.
  If your toolchain reads differently, the headers are the truth.

When a snippet does not compile, read the header before assuming the docs are
wrong about *behaviour*: usually the shape is right and the namespace or overload
moved. `grep -n "current()\|getConfigDir\|ConfigType::" include/pl/*.hpp` answers
most of it in seconds.
