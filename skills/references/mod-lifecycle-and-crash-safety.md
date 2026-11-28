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

## 2. The wrong shape: hooking the loader in a host process

The obvious way to wait is to hook `dlopen` and attach from the detour. This is what the failing
mod did, **in the launcher's own Java host process**, and it crashed the launcher every time.

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

**Rule: never hook the loader in a process you do not own, just to learn when a library appeared.**
In the game process a guarded variant can work — §2b — but it remains a fallback, not the first
choice.

### 2b. The one shape where an in-band loader gate is acceptable

A different project attached from a **guarded loader gate** and shipped without the crash above.
The differences are structural, not stylistic: copy the conditions, not the shortcut.

* It runs **inside the game process** (the mod is already loaded there), not in the launcher's
  activity, so the loader traffic around it is small and known.
* It installs the gate only as a **fallback**: when `enable()` runs and the game image is already
  mapped, no loader hook is installed at all.
* It covers **both** loader entry points the platform exposes (the plain call and the
  platform-specific extended variant), because a caller may use either one.
* The detour calls the original **first** and attaches only when the original returned a
  non-null handle for the game library. By then relocations and early initializers have run: this
  is the "mapped **and settled**" state that §3.2 has to wait for with a polling probe, obtained
  here for free. That is the property that makes this shape viable at all.
* The attach path **does not load anything itself** and does not trust a pointer passed by the
  detour: it re-walks the loaded-image list, re-verifies the exact image (build gate) and only
  then publishes the base.
* Attach is idempotent — mutex plus a tri-state (waiting / ready / failed) published after the
  work — so a concurrent second load cannot double-install.
* Failure is a logged no-op, never a partial install: record every (target, detour) pair at
  install time and, when one hook in the chain fails, unhook from the recorded point backwards.

Conditions that are **not** optional:

* **Never detach the loader detour while another thread can be inside it.** The project these
  notes come from removes it at unload, which is a real crash-on-unload hazard; the safe variant
  leaves the gate installed for the process lifetime and removes only the feature hooks.
* Keep the loader detour to pointer and identity checks — no logging, no allocation, no engine
  calls.
* If the host can disable the mod while the process keeps running, the gate checks a stop flag
  and otherwise calls straight through.

---

## 3. The default attach: poll, then wait for the library to settle

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
- [ ] Default: no hook on the loader; poll with `RTLD_NOLOAD` (or the platform equivalent).
- [ ] If a loader gate is used anyway: every condition in §2b holds, and it is never detached
      while a thread can still be inside it.
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
* `weather-and-ambience-effects.md` §10 — a shipped project's build-id gate that
  only ever installs on the exact image it was built for.
* `instrumentation-and-diagnostics.md` — building a mod that reports its own
  startup progress.
* `build-deploy.md` — manifest, packaging, and deployment.
* `feature-rollback.md` — removing a feature cleanly once you decide it should
  not ship.

---

## 10. Field notes from a real port

Traps the general lifecycle text does not spell out:

- **A build-specific fallback address needs the runtime image base.** Never call the hook installer
  with a raw file virtual address; the image is relocated. Compute `runtime = dli_fbase +
  file_vaddr`, log both, and use the same rule for any engine function used only at draw time. A
  wrong address dies inside the installer before the detour exists, so there may be no FATAL line.
- **A vtable slot number does not prove the accessor.** Decompile the slot body and match the
  prototype to your call exactly; prefer a pure field read over a computing method; cross-check the
  value with a second accessor and log it.
- **Nearby-actor enumeration** returns a three-pointer vector of 16-byte records
  `{actor, distance, pad}`; classify with the RTTI name (digits prefix stripped), read the position
  from the actor's state-vector pointer, call it from a throttled tick (about once a second), and
  publish only aggregated values to the HUD.
- **Overlay draw-command details**: `Text` size is pixel font height, not scale; zero width/height
  keeps the left anchor; coordinates are surface pixels (scale thickness/radii from the HUD surface
  size); `Line` takes a delta, not an end point; `RectFilled` segments are the most portable
  primitive across host builds; clamp position sliders to the real UI space.
- **Finding the per-frame world renderer**: find a leaf that loads the tessellator from the screen
  context and calls the immediate-mesh helper; intersect its callers with the tessellator-begin
  callers; walk callers up to the frame entry; confirm the context argument by decompiling. If the
  entry or ABI cannot be confirmed, ship nothing.
- **Optional hooks and stale caches**: install a hook only when its signature resolves; if the level
  destructor is unavailable, clear tracked actors when the level pointer changes; never cache a
  failed texture fetch forever; keep a text-only fallback path.
- **Client-side approximations** of server-owned data: budget queries per tick, show progress,
  restart scans only after a movement threshold plus a cooldown, deduplicate multi-block objects,
  and label derived relations as approximations.
- **A missing FATAL line still narrows the bug**: if the last log line is immediately before a hook
  install, suspect the target address rather than the feature logic.
