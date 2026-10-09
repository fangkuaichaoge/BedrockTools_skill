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

## 9. Generating patterns mechanically (and the traps in doing so)

Hand-writing long patterns is error-prone. Generate them from the function head
with a small script (see `../scripts/mksig.py`): disassemble, keep the opcode
structure and register moves, and wildcard everything that encodes an address or
a branch displacement. Two modes are useful:

* **house style** — wildcard every instruction carrying an immediate or memory
  operand. Longer-lived across builds, weaker.
* **tight** — wildcard only absolute-address and branch operands, keep field
  offsets fixed. Much stronger, more version-sensitive.

Verify the generated pattern immediately: it must match exactly once, and that
match must be the intended function head (not merely "some" unique site).

### Trap 1: adjacent string literals fuse without a separator

If you emit a pattern across multiple C++ string literals, adjacent literals
**concatenate with no separator inserted**:

```cpp
inline constexpr std::string_view Sig =
    "EA 0F 1C FC E9 A3 00 6D"     // no trailing space!
    "FD FB 01 A9 F5 17 00 F9";    // -> "...00 6DFD FB 01 A9..."
```

The result contains the malformed token `6DFD`, which can never match. The bug
is invisible in the source and only shows up as "the signature never resolves".

**Fix:** put the separator at the **start** of each continuation line. Leading
whitespace survives formatters; trailing whitespace does not.

```cpp
inline constexpr std::string_view Sig =
    "EA 0F 1C FC E9 A3 00 6D"
    " FD FB 01 A9 F5 17 00 F9";   // leading space keeps the tokens apart
```

**Guard:** verify the header by *parsing it the way a compiler would* —
concatenate the literals, then assert every token is either `??` or exactly two
hex digits. A malformed token means the concatenation is wrong. Do this in the
build's verification script so it cannot regress.

Note that `inline constexpr` variables the linker does not need are **dropped**
from the shipped `.so`, so "the pattern string is absent from the binary" is not
evidence of a problem. Verify against the header, not the binary.

### Trap 2: verify a hash algorithm, do not assume it

When the game looks something up by a name hash, the exact algorithm and the
exact operation order matter. FNV-1 and FNV-1a differ only in the order of the
multiply and the xor, and produce completely different values:

```cpp
// FNV-1a: xor THEN multiply
h = (h ^ byte) * prime;

// FNV-1:  multiply THEN xor        <-- the one some builds actually use
h = (h * prime) ^ byte;
```

Getting this wrong means every lookup misses silently.

**Method:** find one or two values the game is known to use (a reference mod that
asserts them on a real device, or a value you can read out of the binary), then
pin them as **compile-time assertions** so the order cannot be changed by
accident:

```cpp
constexpr std::uint64_t fnv1_64(const char* s) { /* multiply, then xor */ }

static_assert(fnv1_64("head")   == 0xF153217ED86AE247ULL, "hash mismatch");
static_assert(fnv1_64("hat")    == 0xD8C4C1186B9B0C08ULL, "hash mismatch");
static_assert(fnv1_64("helmet") == 0x3B70E40D69930B2EULL, "hash mismatch");
```

This caught a real mistake at compile time instead of shipping a feature that
silently did nothing.

### Trap 3: a small accessor is a bad signature, but a fine offset

A three-instruction accessor such as `add x0, x0, #0x70; ret` is the generic
shape a compiler emits for *every* small getter, so it will never be unique —
one such pattern matched 60+ sites.

That is not a problem for the feature: once you hold the object pointer, the
field is just `base + 0x70`. Use the accessor as **evidence for the offset**, and
do the arithmetic yourself rather than trying to resolve or call it.

### Trap 4: prove a struct layout from the copy width

To find a struct's true size, find the code that copies it. A block copied by

```
ldp q1, q0, [x1]        ; 32 bytes
ldr w8, [x1, #0x20]     ; +4  -> 36 bytes total
stp q1, q0, [x0, #0x70]
str w8,  [x0, #0x90]
```

is **36 bytes**, not the 40 a first guess suggested. Deriving the size from the
load/store widths is decisive; guessing from a "looks about right" offset is not.

### Trap 5: check what the return value actually is

A function ending in `add x0, x11, #0x18; ret` looks like "returns the record at
`node+0x18`". Confirm it by reading the *callers* before believing it: if every
caller immediately does `ldp x8, x9, [x0]` (a begin/end pair) and null-checks
`x0`, the function returns a **container**, and `node+0x18` is merely where that
container lives. Treating it as a record would corrupt memory.

**Rule: read the callers before writing to a return value.** The callee's tail
tells you where the value starts; the callers tell you what it *is*.
