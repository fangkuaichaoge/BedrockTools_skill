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

### Trap 6: `--tight` is precise, not portable

`--tight` mode wildcards only absolute-address and branch operands, leaving
object field offsets fixed. That is the strongest possible pattern against the
build it came from — and the most fragile against any other. It stops matching
as soon as the compiler reshuffles a struct or changes an inlining decision,
which is routine between two builds of the same game.

The failure is easy to misread as "the function moved" or "the feature was
removed", when in fact the pattern was simply over-fitted. Before re-deriving
anything, regenerate the pattern in **house style** (wildcard every immediate and
memory operand) from the same function head and try again — that alone recovers
most of a version port.

When a house-style pattern then turns out AMBIGUOUS, the fix is to append the
next few **structural** instructions (register moves, distinctive opcodes), not
to pin more displacements. A worked example: a generic FOV-getter pattern
matched 1 site in one build and 2 in another because both functions shared the
prologue up to `fmov s8, s0`. The real getter continued `mov w20, w1 / mov
x19, x0`; adding those two instructions restored uniqueness in every build
without fixing a single displacement.

**Rule: choose the mode by how many builds must match.** One build -> `--tight`
is fine. Several builds from one `.so` -> house style, verified per build. See
`references/version-porting.md`.

### Trap 7: a resolved signature does not mean a compatible layout

This is the expensive one. A pattern can resolve to the correct function in a new
build while the struct that function operates on has shifted — so the feature
installs cleanly and then writes to the wrong field. Nothing errors; the bug
looks like a logic problem.

Signatures and object layout are **independent porting tasks**:

* the pattern proves *which function* you found;
* only the displacements inside it prove *which fields* it touches.

Check both. `scripts/offset_check.py` reads the displacements back out of the
game's own disassembly so a hard-coded `this + 0xNNN` can be compared against
ground truth in each build.

One subtlety: an offset missing from your scan is a question, not a verdict. The
field may simply be written by a different function. Widen to the whole `.text`
and compare **writer counts** across builds — matching counts mean the same
field. For a `float3` position, look for the *group* (`#0x61c` / `#0x620` /
`#0x624` written consecutively); the group surviving at the same displacement in
several builds is strong evidence the layout held.

---

## 10. Identifying a function that nothing in the binary names

You will be handed lists of function names — from a symbolized build, a newer
signature table, a decompiler session on another version — and asked to produce
signatures for **this** binary. Frequently the binary has no symbol for them and
never did. This section is the evidence ladder for that case, and the point at
which the correct answer is "not derivable from the binary alone".

### 10.1 The ladder, strongest first

**1. A prototype string used by an assert.** The strongest anchor that exists.
Bedrock asserts pass `(message, expression, line, file, __PRETTY_FUNCTION__)`, so
a function that asserts **names itself in rodata**, with the source file and line
number. Find the string, `xref` it, and you have the function — no guessing.

```
0x25e7143  static const TextureAtlasItem &BlockGraphics::getTextureItem(
               const std::string &, std::weak_ptr<AtlasItemManager>,
               std::unordered_map<std::string, TextureAtlasItem> &)
```

Cross-check that the referencing code really is the assert pattern
(`bl <AssertFailed>` then a deliberate `str 0xdeadc0de` trap store), so you are
not looking at a logger that merely mentions the function.

**2. RTTI -> typeinfo -> vtable.** Gives the virtual methods of a class, in slot
order. Use `scripts/vtable_walk.py`. Two facts to read off it before anything
else:

* **slot count decides feasibility.** A class whose vtable has only two slots is
  a destructor pair; every other member is non-virtual and a vtable walk will
  never find it. Stop early instead of hunting.
* **the PIE relocation trap.** On an image with base 0 the chain is stored in
  `R_AARCH64_RELATIVE` relocations, so the 8-byte slots contain **zero** in the
  file and the pointer lives in the `.rela.dyn` addend. Scanning raw qwords finds
  nothing and looks identical to "this class has no RTTI". Read relocations.

**3. An embedded source path groups a translation unit.** `.../BlockGraphics.cpp`
in rodata does not name a function, but it tells you that every function
referencing it lives in that one file — which turns "find it in 328 MB" into
"find it in a few KB of code". Several functions in the same file may share the
literal; treat the set as the candidate set.

**4. A distinctive literal the function must use.** `blocks.json`,
`texture_data`, `"No texture Atlas for block graphics."` — these identify a
**role** rather than a name, and roles are often enough: the only function that
parses `blocks.json` is the block-texture initialiser.

**5. Call-graph position.** "the only callee that takes a `std::string` and
returns a 176-byte struct", "called three times from the initialiser", "has one
caller, and that caller is reached from a vtable slot". Position narrows a
candidate set; it does not identify a member.

**6. Read the pseudocode.** The only step that can *distinguish* semantically
similar candidates. See §10.2 for why this is not optional.

### 10.2 Ruling out is part of the job

A candidate list is not progress until every entry is either confirmed or
**eliminated with evidence**. Machine code lies about identity: four consecutive
plausible candidates for `AtlasItemManager::getTextureItem` turned out to be
`std::unordered_map` internals — bucket-array growth, node relinking, and a node
move that copies a `std::string` at `node+40`. From byte patterns alone all four
look like "a function that takes (this, something) and copies strings"; only the
pseudocode says which.

Two eliminations worth memorising:

* **An indirect call is not automatically your virtual method.** In one function
  the only `(*vptr + 0x10)` call was followed immediately by
  `__shared_weak_count::__release_weak(this)` — so the object was the
  **shared_ptr control block**, and the call was `_M_dispose`. The `this` being
  released names the true type of the object; read one instruction further.
* **No RTTI name is a fact about the class, not a search failure.** A class whose
  name appears only inside mangled template arguments
  (`__shared_ptr_emplace<AtlasItemManager>`) has no `type_info` name of its own;
  the substring is not the RTTI name and will never resolve.

### 10.3 Three ways a "function" can have no signature

Before spending an afternoon, decide which of these you are looking at:

| Situation | Evidence | What to do instead |
|---|---|---|
| **Inlined getter.** A one-line `return mField;` at every call site. | the inlinee's assert string appears in several unrelated functions; no standalone callee exists | hook the field access or the callers; do not chase a signature |
| **Non-virtual method with no string/RTTI.** e.g. a lookup on a class that has only a virtual destructor. | class RTTI exists, its vtable has 2 slots, and no `Class::` string exists | you need a **call site** (see below) |
| **Stale dictionary entry.** A pattern from an older build that matches nowhere, including its own baseline. | `MISSING` in every build tested | delete it; re-derive from a located call site rather than loosening |

These are not failures of effort. Saying "not derivable from this binary, and
here is what would make it derivable" is a better deliverable than a pattern that
happens to be unique on one build and was never proven to be the right function.

### 10.4 What actually unblocks the remainder

Ask for these, in this order — each is cheap for the asker and decisive for you:

1. **one call site address** for the named function. From a call site the callee
   is one decompile away, and the signature follows mechanically.
2. **the source of the name list** (which build, which tool, which version). If
   it came from a symbolized image, half the list stops being a hunt.
3. **a behavioural session**: hook a confirmed neighbour, log the arguments, and
   let the log say which candidate the real path goes through.

### 10.5 Ship decision: lead vs verified

Every pattern carries an identity confidence independent of its hit count:

* **verified** — identity proven by §10.1's ladder *and* the pattern resolves to
  exactly one address in **every** build you support.
* **lead** — pattern is unique but identity is unproven, or it resolves in only
  some builds.

Ship `verified` freely. Ship a `lead` only behind an observe-only mode, and say
so. Never let a pattern's uniqueness stand in for its identity: those are two
different questions, and a signature tool can only answer the second.

## Coexistence: another mod's inline hook rewrites the prologue

When a second mod inline-hooks the same function, the bytes your pattern anchored on are gone and
`resolveSignature()` returns 0 - with no error. The fix is a **deep-anchored** pattern: wildcard the
leading `K` bytes (`K` >= the largest plausible prologue patch; 128 is comfortably safe), anchor the
constrained bytes in the function body at head + `K`, and keep the original pattern as the primary.
`resolveSignature` returns `hit - firstConstrainedByte`, which is the function head again, so the
wildcard prefix costs nothing.

Verify offline on a clean image **and** on copies with simulated head patches of 16 / 24 / 32 / 48 /
96 bytes: the pattern must stay UNIQUE and resolve to the same head in every case. Literal body
bytes are legitimate pattern data only if `.text` has no dynamic relocations - confirm once per
build by parsing `.rela.dyn` and checking no `r_offset` falls inside `.text`.

## When the runtime's RTTI resolver returns 0: a vaddr table from the ELF

If `resolveVtableFunction()` returns 0 for *every* class on a launcher build (log each one before
concluding), the entry points can be read straight out of the image instead: from the typeinfo name
string -> the typeinfo object -> the vtable whose `[-1]` slot is that typeinfo and `[-2]` is zero;
the **address point** (`vtable + 16`) is exactly the value stored in objects, so it doubles as the
identity test, and slot `n` is the word at `addressPoint + 8n`. Take the module base from the
linker's program headers (`dl_iterate_phdr`), not from a signature. Verify each baked address
against the pattern's bytes - prologue first, then the deep-anchored body - before calling it, and
keep a single-pass scanner (Aho-Corasick over the anchors) as the fallback for other builds.
Details and the full checklist: `coexistence-and-crash-safety.md`.
