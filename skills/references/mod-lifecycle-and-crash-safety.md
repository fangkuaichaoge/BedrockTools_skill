# Mod Lifecycle and Crash Safety

Experience notes on the one class of bug that is hardest to diagnose from a
device: **the launcher/game process dies, restarts, and the launcher asks to
re-import the mod**. There is no stack trace in your log, because the crash
happens before your code has produced anything useful.

Everything here is generic. Replace `<mod>`, `<game-lib>`, `<NDK>` with real
values.

---

## 1. The load order that causes it

The preloader does **not** wait for the game library. It enables mods from the
launcher's own activity, and in the reference launcher that call sits *before*
the activity's `super.onCreate()` — i.e. before the game library is mapped:

```kotlin
// launcher activity, onCreate()
ModManager.enableLoadedMods()      // <-- your enable() runs HERE
...
super.onCreate(savedInstanceState) // <-- the game library loads AFTER this
```

So at `enable()` time:

* the game library may not be mapped at all;
* signature resolution has nothing to resolve against;
* installing an inline hook writes to an address that does not exist yet.

**Rule: `enable()` must never assume the game library is present.** It must
either attach safely or defer.

---

## 2. The wrong fix, and why it crashes

The obvious way to wait is to hook `dlopen` and attach from the detour. This is
what the failing mod did, and it crashed the launcher every time.

Why it fails:

* `dlopen` is one of the **hottest functions in the process**. The launcher is a
  Java app: ART class loading, `System.load`, and every library the launcher
  pulls in all go through it.
* Your detour therefore runs on **arbitrary threads, very early, for every load
  in the process** — including loads that happen while your own attach is
  mid-flight.
* A re-entrancy guard helps, but it is easy to get wrong: releasing the guard
  *before* the attach means the attach's own nested `dlopen` calls re-enter the
  detour and recurse until the stack overflows.
* Unhooking the watch from inside the detour frees the trampoline while you may
  still be calling through it.

Even done carefully, hooking a loader entry point in a host process you do not
own is a large blast radius for what is only a *timing* problem.

**Rule: never hook `dlopen` just to learn when a library appeared.**

---

## 3. The fix: poll, then wait for the library to settle

Two steps, both necessary.

### 3.1 Poll on a watcher thread

The working reference mod does not hook the loader at all. It starts a detached
watcher thread that probes with `RTLD_NOLOAD` (`RTLD_NOLOAD` never loads
anything, so the probe is side-effect free) and gives up after a generous
budget:

```cpp
bool libraryMapped() {
    void* h = dlopen(kGameLib, RTLD_NOW | RTLD_NOLOAD);
    if (!h) return false;
    dlclose(h);
    return true;
}

std::thread([] {
    for (int i = 0; i < 12000; ++i) {                 // ~10 min budget
        if (g_watcherStop.load()) return;
        if (libraryMapped()) { /* go to step 3.2 */ }
        std::this_thread::sleep_for(
            std::chrono::milliseconds(i < 6000 ? 1 : 50));  // 1 ms, then 50 ms
    }
    logError("timed out waiting for {}; mod inactive", kGameLib);
}).detach();
```

### 3.2 Then wait again, before touching anything

**This is the part that is easy to miss, and it was the actual fix.**

`dlopen(RTLD_NOLOAD)` starts succeeding the moment the library is **mapped** —
but at that instant the dynamic linker may still be running relocations, and the
game may still be running its own early constructors. Resolving a signature and
writing a hook trampoline into the middle of that is a race against the loader.

So after the library appears, **wait before you touch it**:

```cpp
constexpr int kSettleDelayMs = 5000;   // raise if the game still dies early

// Phase 1: wait for the library to be mapped.
// Phase 2: let the linker finish relocations and the game finish its own
//          early initialisation.
for (int waited = 0; waited < kSettleDelayMs; waited += 100) {
    if (g_watcherStop.load()) return;
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
}
// Phase 3: only now resolve signatures and install hooks.
installNow();
```

The cost is only that the mod takes effect a few seconds later. That is a very
cheap price for not crashing the host process.

**Rule: "the library is mapped" is not "the library is ready".** Wait for it to
settle.

### 3.3 Make the attach idempotent

Both `enable()` and the watcher can reach the attach path:

```cpp
void installNow() {
    std::lock_guard<std::mutex> lock(g_installMutex);   // one installer
    if (g_hooksInstalled) return;                       // published after work
    logInfo("resolving signatures");
    install();
    g_hooksInstalled = true;
    logInfo("hooks installed");
}
```

Publish the "done" flag **after** the work completes, and hold a mutex across
the whole path.

---

## 4. Initialise the hook engine explicitly

Bring the hook engine up before installing anything, and treat failure as fatal
for the feature:

```cpp
if (!g_glossReady) {
    GlossInit(true);      // or the equivalent for your engine
    g_glossReady = true;
}
```

Some engines self-initialise on first install; do not rely on it. An
uninitialised engine turns every install into a silent no-op.

---

## 5. Diagnosing "it crashes and asks me to re-import"

Work the problem in this order. Do not guess and rebuild repeatedly.

1. **Confirm the crash is yours.** Check the launcher's mod directory: if the
   launcher asks to re-import, it usually means the process died during
   startup. Get the log with the runtime and crash tags:
   `adb logcat -s <YourTag>:* Preloader:* AndroidRuntime:* DEBUG:*`
2. **Compare against a mod that works on the same launcher.** This is the single
   most productive step. Diff the *design*, not the feature:
   * Does the working mod hook the loader? (If not, that is your answer.)
   * Does it call the hook-engine init explicitly?
   * Does it delay, poll, or assume the library is present?
   * Diff the undefined dynamic symbols of both `.so` files: a symbol your mod
     needs that the working one does not is a candidate for a load-time failure.
3. **Check the timing, not the hooks.** A user report of the form "the hooks
   definitely work, I use them in another preloader environment" points at
   *lifecycle*, not at hook correctness. Hooks that work elsewhere + a host that
   dies early = the attach happened at the wrong time.
4. **Only then suspect the interface.** If the preloader headers differ between
   what you vendored and what the launcher ships, that matters — but note that
   additive API changes (new headers, new optional fields) are ABI-compatible;
   a difference in an existing struct layout is not. Verify by diffing the
   header you compile against with the launcher's submodule copy.

A useful sanity check for step 4: compare `DT_NEEDED`, exported
`<ModEntrySymbol>`, and the undefined-symbol set of your library against a
known-working one. A quick offline script is in
`../scripts/so_compare.py`-style form: parse `.dynsym`, bucket the undefined
symbols, and diff.

---

## 6. Stale configuration can keep a fixed bug alive

If a saved config holds a value that a buggy build wrote, fixing the code is not
enough — the old value is reloaded and the symptom persists.

Version the config and repair it on load:

```cpp
constexpr int kConfigVersion = 2;

// on load:
if (loadedVersion < 2 && g_settings.perspective.load() != 0) {
    logWarn("config v{}: clearing value written by an older build", loadedVersion);
    g_settings.perspective.store(0);
    saveConfig();
}
```

This is especially important for values that *override* a game behaviour: a
stale override looks exactly like a broken feature.

---

## 7. Design rule: a preset must not lock a user-facing toggle

A feature that pins a game setting to a fixed value will look like the game's
own control is broken. Concretely: a "preset" that pinned the camera perspective
made the game's perspective button stop responding.

* Keep the pin **off by default**, and name the option so that is obvious
  ("Don't Touch", not "Vanilla").
* Let presets adjust only *additive* parameters (offsets, scales), not the
  toggles the player uses.
* If a value must override the game, make it a separate, explicit option.

---

## 8. Fail-open checklist for the attach path

- [ ] `enable()` never assumes the game library is mapped.
- [ ] No hook on `dlopen` / the loader, anywhere, for any reason.
- [ ] A watcher polls for the library with `RTLD_NOLOAD` and has a bounded
      budget plus a stop flag honoured on disable/unload.
- [ ] After the library appears, a **settle delay** elapses before any signature
      resolution or hook install.
- [ ] The hook engine is initialised explicitly before the first install.
- [ ] Attach is idempotent (mutex + a "done" flag published after the work).
- [ ] Every unresolved signature is logged and skipped; the remaining features
      still install.
- [ ] `disable()`/`unload()` unhook cleanly and stop the watcher.
- [ ] Log each phase (`mapped`, `settling`, `resolving`, `installed`) so a
      device log shows exactly how far startup got.

---

## 9. Related reading

* `hook-engineering.md` §3 — hook lifecycle, including the "library not loaded
  yet" case this file expands on.
* `instrumentation-and-diagnostics.md` — building a mod that reports its own
  startup progress.
* `build-deploy.md` — manifest, packaging, and deployment.
* `feature-rollback.md` — removing a feature cleanly once you decide it should
  not ship.
