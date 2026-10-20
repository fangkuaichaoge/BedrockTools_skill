# Instrumentation & Diagnostics for Native Mods

Distilled experience from building a mod for a closed-source target where the
only observable surface is an intercepted platform API. Everything here is
generic: no game-specific names.

The core problem: you cannot attach a debugger to the user's device, you cannot
read the engine's internal state, and a wrong guess produces a build that
"does nothing" with no error. The answer is not more guessing - it is making
the mod **explain itself**, and treating the device log as the primary
instrument.

---

## 1. Design a log you can reason about

| Rule | Why |
|---|---|
| Give every stage its own line: install -> object seen -> classification -> eligibility -> first draw hit -> effect applied | A **missing** line localises the failure; a log that only prints successes cannot do that |
| Cap everything: once per object, N lines per session, heartbeat instead of per-event | An uncapped per-event line floods the output and buries the signal it was meant to carry |
| Print the **values** the decision compared, not just the verdict | "declined" is useless; "declined because the name did not match X" is actionable |
| Use stable prefixes/tags so lines can be grepped | You will be reading pasted logs, not a live console |
| Never log in the steady-state hot path without a counter | I/O in a render/audio thread is a performance and stability hazard |

Recommended skeleton for any interception layer:

```
<layer> install: <each library/symbol> -> ok/fail
<layer> first arrival: <entry point> (proves the target actually calls you)
<object> seen: id (proves you observe the target's objects)
<object> classified: <observed vocabulary, not your guess>
<object> declined: <reason> [values compared]
<object> applied: N times (counter, printed rarely)
```

## 2. Canaries: separate "not called" from "called but skipped"

Hook a call that must happen constantly (a per-frame or per-draw function) and
log it exactly once. If the canary fires but your feature's entry point never
does, the target simply does not use that entry point - it is not a bug in your
logic. If even the canary never fires, the interception layer is not reachable
at all. That single distinction saves entire sessions.

Extend the same idea to every assumption: prove the library the target links,
prove the resolution path it uses, prove which of several equivalent entry
points the target actually calls.

## 3. Silent failures are the default; instrument the API

Compilation, linking, buffer creation and uniform lookup all fail **silently**
unless you ask. Intercept the API's own error reporting and record:

* status codes (compile/link) and the driver's info text;
* whether the failing object was one *you* modified (a flag carried alongside
  the object), because that is the difference between "our transformation is
  wrong" and "the target has its own problem";
* lookups that came back empty (a location/handle that does not resolve means
  the object is ineligible, not that the code path is broken).

Normalise multi-line driver text (replace newlines) and truncate it: it is
going to be pasted into a chat window.

## 4. Ship an instrumentation build

A release that only *observes* - feature disabled, verbosity on - is the
cheapest way to turn an unknown into a fact. It answers questions like "what
vocabulary does this version use", "which entry point draws this", "what
attribute layout does this buffer have" without changing behaviour.

Then run the loop: **one log per iteration, one hypothesis changed per
iteration**. Changing three things and re-testing produces a log you cannot
interpret.

## 5. Cost discipline in the interception layer

* Cache per-object decisions at bind time (which object is patched, which
  uniform locations resolved, eligibility flags) - never recompute per draw.
* Hot path = one integer compare or one atomic load. No allocations, no
  string formatting, no file I/O, no locks.
* Use thread-local state for the thread that drives the API (usually the
  render thread) instead of shared state with mutexes.
* Guard optional diagnostics behind a flag that is off in normal builds; keep
  the code path present so it can be switched on for one test build.

## 6. Getting logs out

| Channel | Pros | Cons |
|---|---|---|
| Platform log (logcat) | no files, standard tooling, nothing left on disk | needs a host connection to read |
| File written by the mod | readable without a host connection | leaves artefacts on the user's storage, needs flushing, needs a fixed discoverable path |

Whichever you choose, **state it explicitly** in the mod's usage doc, and never
let the log silently disappear: if you remove file logging, tell the user how to
get logs instead. Flush per line if you write files, and cap the file size.

## 7. What to ask the user for

Ask for one thing at a time and make it cheap: what they did, what they
expected, what they saw, and the log. Give them the exact steps (a specific
world, a specific action) so the log contains the interesting frames. When a
log is ambiguous, add one line of instrumentation and ask again rather than
adding several speculative fixes.
