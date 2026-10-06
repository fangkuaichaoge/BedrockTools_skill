# Function Forensics: finding a function and proving it is usable

Generic experience notes (取证: how to obtain **evidence** that a function is the
right one and that it is safe to use). Complements `so-analysis.md` §5 (running
the signature scan) and `feature-workflow.md` (feature -> function mapping):
this file is about the *judgement* and the *evidence ladder*.

---

## 1. What "a usable function" actually means

A function is only usable when **all** of these are established:

| Property | Question to answer |
|---|---|
| Identity | is this the function that decides the behaviour I care about? |
| Uniqueness | does the pattern match exactly one site in *this* build? |
| ABI | calling convention, argument order and types, return type, what `this` points to |
| Lifetime | how long do the arguments/`this` stay valid? who owns them? |
| Thread | which thread calls it? may I call it from elsewhere? |
| Re-entrancy | what happens if it is called while already running? |
| Side effects | what else does it change when invoked out of band? |
| Stability | will this still be findable after a version bump? |

Skipping the lower rows is what turns a "working hook" into an intermittent
crash weeks later.

## 2. Finding candidates (use several independent anchors)

Cheapest and most reliable anchors first:

1. **String / log anchors** - error and diagnostic messages are the best
   pointers into a stripped binary: find the message, follow references to the
   code that produces it, and you land inside the subsystem you want.
2. **Vocabulary in the string table** - the names of the settings, uniforms,
   events or fields involved in the feature cluster in `.rodata`; their
   neighbourhood reveals the surrounding code and the API surface it uses.
3. **A working reference implementation** - an existing mod/tool for another
   build of the same target gives you patterns, offsets and call shapes to
   replicate. Treat it as evidence to re-verify, never as truth for your build.
4. **Neighbours** - once one member of a function family is found, the others
   usually sit in the same region with the same prologue shape.
5. **Registration tables / vtables** - if a handler is not called directly, it
   is registered somewhere; finding the registration reveals the dispatch path
   and gives you a stable anchor that does not depend on the call site.

Require **agreement between at least two anchors** before you invest in a
candidate.

## 3. Turning a candidate into a signature

* Function **heads** are stable across minor versions; branch targets, literal
  pool loads and absolute addresses are not. Keep the opcode structure and the
  register usage, **wildcard everything that encodes an address or an offset**.
* Start short and **lengthen until unique**; a pattern that matches once by luck
  is not a signature.
* Prefer a run that contains at least one distinctive instruction sequence
  (unusual immediate, distinctive prologue shape) over a long run of generic
  instructions.
* Avoid regions that move between builds: thunks/PLT stubs, jump tables, literal
  pools, and code the linker may fold.
* Store the pattern **together with the expected semantics** ("returns the
  currently selected value", "advances the state machine"). A pattern without
  meaning cannot be sanity-checked later.
* Prefer a nearby **data anchor** (a stable pointer or table) when the function
  itself has no distinctive head: resolve the data, then read the function
  pointer out of it.

## 4. Verifying in this build

* Run the scan and classify: UNIQUE (safe), AMBIGUOUS (lengthen or add a second
  stage), MISSING (the dictionary and the binary disagree - re-derive).
* **State the base** of every reported address (file offset vs virtual address)
  and confirm by reading the bytes back at the resolved site.
* A UNIQUE match in a *different* build proves nothing. Verify in the build you
  are shipping against.
* Watch for **identical code folding**: several logically different functions can
  share one body, so a unique pattern may still resolve to a shared stub.
  Disambiguate with call-site context or a data/table anchor, not the bytes.

## 5. The evidence ladder for "this is the right function"

Cheapest first; stop as soon as you have enough, but never patch on step 1 alone:

1. **Shape** - does the prologue look like the kind of function you expect
   (getter, virtual override, big state machine, tiny wrapper)?
2. **Structure** - who calls it and what does it call? Does it sit in a vtable or
   a registration table? Does a string/error anchor reference it?
3. **Decompiler** - optional confirmation of the semantics.
4. **Runtime observation** - install an **observe-only** hook that logs
   arguments, return value and call frequency. Drive the feature in game. The
   call must appear at the expected moment with plausible values, and it must
   *stop* appearing when the feature is not used.
5. **Behavioural A/B** - with the observation hook proven, temporarily force a
   value/return and confirm the predicted visible change; then revert it and
   confirm the original behaviour returns.

**Never modify before step 4.** A log line is the difference between a
hypothesis and a fact - and if the log never appears, you learned that the
engine uses a different path (`hook-engineering.md` §5).

## 6. Proving it is *safe* to call (not just correct)

* **ABI**: confirm argument count/order/types from the call sites (how callers
  set up registers/stack) rather than from a guess; confirm the return type from
  how callers consume it.
* **`this`**: for member functions, confirm what object type is expected and
  whether the pointer may be null in some paths.
* **Initialisation order**: many functions are only valid after a subsystem is
  constructed; calling them earlier reads uninitialised memory. Find who
  constructs the owner and only call after that point.
* **Thread affinity**: if it mutates shared state, call it on the thread the
  engine normally uses. Calling from your own thread is how "works once, then
  corrupts" bugs are born.
* **Re-entrancy**: if it can fire a callback that reaches your hook again, guard
  it (thread-local flag) or you will recurse.
* **Side effects**: prefer the narrowest function that produces the value you
  need; calling a "do everything" function to read one field is a trap.

## 7. Version drift

* MISSING is information: the dictionary and the binary diverged - re-derive the
  pattern from this build rather than loosening the pattern until it matches.
* Loosening a pattern trades uniqueness for fragility: you will match a
  relative, not the intended function, and the failure appears as a random crash.
* Keep a short per-function record: identifier, semantics, pattern, ABI notes,
  thread, the anchors that led you there, and what you verified. That record is
  what makes porting to a new version mechanical instead of archaeological.
* Where a stable data anchor exists (a table, a pointer, a field), prefer it over
  a code pattern: data changes less often than code layout.

## 8. Minimum checklist before shipping a hook

- [ ] Two independent anchors agree on the function.
- [ ] Pattern is UNIQUE in the shipping build; the resolved site's bytes were
      read back and match.
- [ ] An observe-only hook showed the call at the right time with plausible
      arguments.
- [ ] ABI, `this`-validity, init order, thread and re-entrancy are understood.
- [ ] The mutation is behind a toggle with a neutral default.
- [ ] Failure to resolve leaves the target working (fail-open).
