# Integrating with the host's overlay surface

The C++ menu API covers buttons and modules. Some features need the **host's overlay surface** —
its overlay manager, its preferences, its input-state helpers — to behave like a host feature. This
reference is the JNI integration pattern for that, including the portable "run this on the host UI
thread" task dex.

Use it when the host exposes Java objects for overlay/menu/preferences, and when a C++-only menu
entry cannot express the interaction you need. Skip it when the C++ menu API is enough.

## Cache the host API once

- Reach the host app's classes through the **app class loader** (a class already loaded by the host,
  then its class loader), not through a context that may be absent.
- Resolve every method id and field id **once** into a struct of handles, then check every handle:
  a missing one means host drift — disable the bridge with one log line, do not crash.
- Keep global refs for the `jclass` objects only; keep nothing that belongs to an activity or a
  view.
- Prefer the host's public accessors (manager instance, overlay map, preferences get/set, input
  state predicates) over private fields. If a field is unavoidable, resolve it once and note why.

```cpp
struct HostApi {
    jclass manager{}, preferences{}, input{}, task{};
    jmethodID managerInstance{}, preferencesInstance{}, toggle{}, refresh{}, runOnUiThread{};
    jmethodID getX{}, getY{}, setPosition{}, resolveEnabled{}, mapGet{}, click{};
    jmethodID taskCtor{}, hudOpen{}, menuOpen{}, pauseOpen{};
    jfieldID activity{}, buttons{}, editor{}, active{}, view{}, iconVisible{};
};
```

## Checked JNI wrapper and environment RAII

Every JNI call can leave a pending exception that silently turns every later call into a no-op.
Wrap it:

- a small `Java` object holding `JNIEnv*`, with templated helpers for void/object/boolean/int/
  constructor calls; after each call check `ExceptionCheck` and throw a private `JavaFailure`;
- an `Environment` RAII: `GetEnv`, attach if detached, `PushLocalFrame`, and on destruction clear a
  pending exception, log one line, `PopLocalFrame`, and detach if this object attached;
- catch `JavaFailure` at the bridge boundary and convert it to one log line. No Java exception
  crosses into game code.

## The task dex: run on the host UI thread

Touching host `View`s and preferences must happen on the UI thread. A tiny embedded dex makes that
portable:

- the dex contains one final class implementing `Runnable` with a public constructor and one
  `public native void run()`;
- load it in memory with a direct buffer over the embedded bytes and a parent class loader rooted at
  the host app; resolve the class once;
- `RegisterNatives` binds `run` to a C++ function; construct the task with its constructor;
- call the host activity's `runOnUiThread(task)`; the native `run` then executes on the UI thread.

Keep a single atomic `queued` flag: a second post is dropped until the running task clears it at the
end. All state the task reads (visibility, reset request, module enabled) must be atomic; all JNI
references it uses are local to that task.

Why a dex and not just a worker thread: the work is UI work. A worker cannot touch the views, and a
Java-side helper would need recompilation when the host changes; the native `run` keeps the logic in
C++ and only borrows the thread.

## Inside the task

- Validate the activity: `isFinishing`/`isDestroyed`; re-read it from the host manager every time,
  never cache an activity or a view.
- Read the host's UI modes (HUD editor open, menu open, pause menu open): in those states *clear*
  the overlay instead of drawing or clicking.
- Drive the host overlay through its own API: enable/disable the external module, refresh
  visibility, read/write the persisted position, and click only when the host says the overlay is
  active and no other UI is open.
- Clamp a default position from the display metrics and density; persist per-button keys through
  host preferences.
- Keep the task short: heavy work belongs on your own worker; the UI thread must not block.

## Refresh discipline

- Do not post per frame. Gate with a millisecond compare-exchange (a few hundred milliseconds is
  plenty) and post immediately on explicit state changes.
- On teardown set a not-ready flag first; the task checks it and returns.
- Log one line per failure category, not per attempt.

## Pitfalls

- Host class/method drift: every cached id must be checked; a missing id disables the bridge, the
  rest of the mod keeps working.
- Activity replacement: the host activity dies and is recreated; a cached activity is a leak with a
  crash attached.
- Global refs to views: never; resolve per task.
- Pending exceptions: a single missed clear makes the whole bridge silently dead; check after every
  call.
- Posting storms: without the queued flag and the poll gate, a per-frame caller floods the UI
  thread.

## Checklist

1. Host classes resolved through the app class loader; ids cached once and all checked.
2. Checked JNI wrapper with a private failure type; environment RAII attaches/detaches and pops the
   local frame.
3. Task dex: one Runnable class, native `run` registered with `RegisterNatives`, posted with
   `runOnUiThread`.
4. Single `queued` atomic; state flags are atomic; task local refs only.
5. Activity validated per task; no cached activity or view.
6. Host UI modes respected; overlay cleared instead of drawn while a host UI is open.
7. Position persistence through host preferences with clamped defaults.
8. Posting gated by time and explicit changes; teardown marks not-ready first.
9. Failures are one log line; the rest of the mod is unaffected.