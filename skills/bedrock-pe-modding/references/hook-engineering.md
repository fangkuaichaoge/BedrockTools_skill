# Hook Engineering (experience notes)

Companion to `hook-techniques.md` (which documents the *mechanisms*). This file
is about the **engineering judgement**: which mechanism to pick, where to place
the interception, how to keep it safe, and how to prove it works. Generic; no
game-specific identifiers.

---

## 1. Pick the mechanism from the goal

| Goal | Mechanism | Why |
|---|---|---|
| Read state that is already stored somewhere | **call / field read** | cheapest and least invasive; no code modification at all |
| Observe or adjust arguments / return of one behaviour | **inline hook** (with an original-call chain) | you keep the original semantics and can pass through |
| Replace an entire small behaviour whose whole logic is fixed | **byte patch** (return a constant, force a branch) | less code, no trampoline; but the blast radius is larger and the semantics of side effects are lost |
| Intercept a virtual dispatch for one family of objects | **vtable slot hook** | the vtable is data; you do not touch the code and can scope it per type |
| The effect is expressible purely in terms of a platform API | **library-boundary detour** | version tolerant, no signatures; see `hook-techniques.md` §9 |
| Force a flag/branch to unlock something downstream | **do not** | see `lessons-learned.md` §1: you skip side effects and trip validations |

Default order of preference: **observe before you intercept; intercept before
you replace; replace the narrowest site you can find.**

## 2. Choose the site, not just the function

The same behaviour is usually decided at several levels. Hook the **narrowest
place where the decision is actually made**:

* hooking a caller changes every callee's behaviour; hooking the callee changes
  one;
* for "this action is cancelled/replaced", the decision site (where the value is
  computed or the branch taken) is better than the entry point of the whole
  feature;
* for "this value is wrong", the producer is better than every consumer.

Rule of thumb: if your detour has to reconstruct information that the engine
already had one level deeper, you hooked too high.

## 3. Hook lifecycle

1. **Initialise the hook engine once**, before installing anything, and check
   that the call succeeded - an uninitialised engine makes every install a
   silent no-op.
2. **Install idempotently.** Keep a registry of what you hooked (address ->
   detour/original) so a second install cannot double-patch an address.
3. **Handle "the library is not loaded yet."** Do not fail; keep a short watcher
   and process each library once, with a per-library "done" flag. A mod that
   itself depends on a sibling library can report false success otherwise.
   **Do not hook `dlopen` to detect this**, and do not treat "mapped" as
   "ready" — see `mod-lifecycle-and-crash-safety.md`, which is the detailed
   treatment of this case.
4. **Tear down cleanly** on disable: remove detours, and null out your cached
   original pointers so a later enable re-resolves them.
5. **Never install a detour you cannot call through.** If the original pointer
   is not available at install time, resolve it lazily and keep a fallback (see
   fail-open below).

## 4. Fail-open rules for every detour

* **Never drop a call.** If you cannot resolve the original target, call the
  resolved export instead of returning early.
* **Never let an exception escape.** A detour runs inside a thread you do not
  own; an escaping exception is a process abort, not a warning. Restrict detour
  code to non-throwing operations, or contain it.
* **Never allocate, format or log in the hot path.** One integer compare is
  fine; a `std::string` is not.
* **Never recursively re-enter yourself.** Beware of resolving an original by
  name at runtime: if the name resolves to the *stub you already patched*, your
  "original" is your own detour and the call recurses until the stack dies. Keep
  the trampoline the hook engine gave you and use it.
* **Restore everything you change** that the engine can observe (bindings,
  flags, modes, buffers). Engine state is cached and assumed unchanged.
* **Be thread-aware.** Know which thread calls the function (usually one
  render/logic thread). Use thread-local state rather than locks; if you must
  observe from another thread, use atomics and accept snapshot semantics.

## 5. Proving a hook is live (before you build anything on top)

"Hook installed" is not evidence. The evidence ladder:

1. the install call reports success for the intended address;
2. a **canary** - the first real arrival at some entry point that must run -
   appears in the log;
3. the detour logs the arguments/return for the call you care about, with
   plausible values at the expected moment;
4. only then: mutate behaviour, and confirm the mutation has the predicted
   effect (and that reverting restores the original behaviour).

Do steps 2-4 in that order; skipping to 4 is how a wrong-site hook becomes a
"mystery" bug. See `instrumentation-and-diagnostics.md`.

## 6. Coexistence and priorities

* If the engine offers hook priorities/chaining, use it: several mods in one
  process may intercept the same function, and raw "overwrite the bytes" hooks
  silently break each other.
* Keep the original-call chain intact (your detour must call through exactly
  once per invocation, in the same order as the original would).
* Avoid hooking an address another mod already patched unless you own the order.
* If your hook must run before/after another, express that with the priority
  mechanism, not with install timing.

## 7. Failure modes seen in practice

| Symptom | Usual cause |
|---|---|
| Nothing happens at all, no errors | hook engine not initialised, or the install silently failed |
| Hook works in one build, not another | signature drift (see `signature-forensics.md`) |
| The game stutters or the log explodes | logging/allocation in a per-draw or per-tick detour |
| Random crash shortly after enabling | exception escaping the detour, or recursion through a name-resolved "original" |
| The launcher/game dies at startup and asks to re-import the mod | the attach ran before the game library was mapped or before it finished relocating — see `mod-lifecycle-and-crash-safety.md` |
| Effect appears far away from the hooked feature | leaked engine state (a flag/blend/binding not restored) |
| Only one of several mods works | two mods patching the same address without priorities |
| Interception works but the feature is unchanged | you hooked a path the engine does not actually use for that case (check the arrival canary per entry point) |

## 8. Rollout discipline

* Ship the interception **observe-only** first (log, no mutation), confirm
  arrival, then add the mutation behind a toggle whose default is neutral.
* Keep the "no-op" value of every injected knob truly neutral (0 / pass-through)
  so a failed injection cannot change anything.
* Keep a known-good build to hand and be ready to remove the hook entirely -
  see `feature-rollback.md`.
