# Porting a Live Mod to a New Game Build (playbook)

This is the end-to-end method for taking a **mod that already ships** and adding support for a
new Bedrock build, with the old builds still working from the same `.so`. It complements
`version-porting.md` (which covers the two separate porting tasks and the signature/layout
mechanics) with the parts that only show up in a real port:

* a build profile selected at run time, so the old code path stays byte-identical;
* the capture points that moved even though the old signature still "resolved";
* the diagnostic loop that finds those without guessing;
* the regressions a released mod can carry for a whole build cycle unnoticed.

**Every concrete value in this document is a placeholder.** `<old-build>`, `<new-build>`,
`<field>`, `<slot>`, `<displacement>`, `<count>`, `<record-size>`, `<offset>` and the like stand for
values you must read out of *your* binaries. The shapes and the evidence methods are what
transfer; never copy a number from here into code.

---

## 0. Decide the compatibility contract first

Two different goals need different designs:

| Contract | Design | Consequence |
|---|---|---|
| one `.so` per build | version-locked patterns and constants are acceptable | simplest, but every supported build is a separate artifact |
| **one `.so` for several builds** | build profile selected at install; old values stay untouched | the old builds keep their proven path; the new build is additive |

For the second contract, the rule that keeps the port safe:

> **Do not edit the legacy constants and patterns. Add a profile.**
> The legacy branch must stay byte-for-byte the code that already works on device.

A moved constant therefore becomes a runtime variable with the old value as its default, and a
`selectProfile()` step assigns the new values only when the new build is detected.

---

## 1. Identify every build before touching anything

* Newer builds may carry a full version string in `.rodata` (`<version-string>`); some builds carry
  none. For those, identify by file identity: the GNU build-id (`PT_NOTE`) plus behaviour, never by
  assuming a filename.
* Keep every candidate `.so` on disk for the whole port. Every check in this playbook is a
  **matrix over all supported builds**, not a single build.
* A version-like string, or a filename, is not proof either: a probe signature that resolves
  uniquely on one build and is absent on the others is a *behavioural* identification and is what
  the profile gate will use anyway.

---

## 2. Start from an upstream dictionary that declares its target build

The single biggest accelerator: an open-source mod project that publishes, per release, a
house-style signature table plus SDK offset headers, and whose launcher manifest declares the build
it targets:

```json
{ "version": "<upstream-release>", "minecraft_versions": ["<target-build>.X"] }
```

Consequences used in the port:

* every function the upstream applies on the target build has a house-style pattern that is
  **already verified on that build** (and its own mod proves them at run time);
* the matching offset header gives the new build's struct displacements and vtable slots;
* the previous upstream release is the source of the legacy constants (what the old builds were
  built against);
* its own source is a worked reference of the target build's ABI — e.g. how its code constructs a
  render context (`ctor(obj, screenContext, client, game)` plus an `itemRenderer` field at a fixed
  displacement), or which vtable slots it calls for text/images.

**Still verify every constant against the binary.** Headers drift: a header can name
`<displacement-a>` for a wrapper while the new binary's getter loads `<displacement-b>`. That kind
of error is harmless only if the mod calls the getter instead of hard-coding the field — a good
example of the next rule.

> **Prefer calling a function to hard-coding the field it reads.**
> A wrapper field can move; a resolved getter call cannot break from that move.

---

## 3. Signature port: fallbacks plus a disjoint profile gate

Give each needed function a pair: the new build's pattern and the legacy pattern. In practice the
patterns fall into three classes:

1. **new-only** (the build rewrote the function) — the old pattern no longer matches;
2. **all-builds** (a more wildcarded version of the old pattern) — can replace the legacy pattern
   outright and reduces fallbacks;
3. **legacy-only** — keep as fallback.

Use class 1 for the build gate. Pick a function the new build rewrote and verify over *all* builds
that the two patterns are mirror images:

```text
                    new-build   old-a   old-b   old-c
new pattern              1        0       0       0
legacy pattern           0        1       1       1
```

Then:

```cpp
enum class BuildProfile { Legacy, NewBuild };
BuildProfile profile = BuildProfile::Legacy;

std::uintptr_t gate = resolveSignature(newOnlyPattern, library);
profile = gate ? BuildProfile::NewBuild : BuildProfile::Legacy;
applyProfile();                    // assigns the moved constants only for NewBuild

auto resolve = [](std::string_view legacy, std::string_view modern) {
    if (profile == BuildProfile::NewBuild && !modern.empty())
        if (auto a = resolveSignature(modern, library)) return a;
    return resolveSignature(legacy, library);
};
```

Advantages over version strings or ELF build-id parsing: nothing in the mod parses the image, a
partially matched pattern cannot select the wrong profile, and an unsupported build fails closed
(the required signatures do not resolve, so nothing installs).

### Identity is not uniqueness

A unique hit proves *where*, not *what*. For every resolved address, decompile the function and
add one independent structural anchor (a vtable slot, a caller, a string literal, an exact
instruction shape):

| function kind | cheap identity evidence |
|---|---|
| tiny getter | exact instruction count/shape (`ldr x0,[x0,#<displacement>]; ret`) |
| virtual method | RTTI class + slot index, and who calls it |
| tick / render entry | the anchor string set or the argument contract it reads |
| constructor | the vtable it stores (RTTI chain) and its parameter order |
| renderer / draw call | the queue it pushes to and the record fields it writes |

Parallel verification pays: one agent can own signature identity, another the object layout; both
must hand back `function + decompiled line`, not "the pattern is unique".

### The pinned-immediate trap in "optional" signatures

A tight pattern for a non-virtual function often pins the `adrp`+`add` pair that loads a vtable.
That pair is compilation-unit specific: the pattern stops matching on a later build while the
function itself is unchanged. It is easy to miss when the signature is optional — the feature just
silently degrades for a whole build cycle.

Fix: wildcard only the `adrp`+`add` pair, keep the rest structural; a derived house-style pattern
then matched one function in **every** supported build. Before re-enabling a path that copies an
object and destroys the copy on the stack, check the destructor slot semantics on that build:
Itanium layout normally has the complete-object destructor in slot 0 and the deleting destructor
in slot 1 (the latter tail-calls `operator delete`); calling slot 0 on a stack buffer is correct,
slot 1 is not.

---

## 4. Layout port: prove each moved constant, per class of constant

### 4.1 Trivial-getter census (pointer fields of a ubiquitous base class)

Find functions of the exact shape `ldr x0,[x0,#DISP]; ret` and count their direct `BL` callers in
the whole `.text`. A ubiquitous accessor (e.g. a base-class `get<Owner>` accessor) has a large
number of callers; a random getter has a handful.

Example shape (all values are placeholders), for one class whose span grew by `<delta>`:

```text
field               old build callers   new build callers   new displacement
base-class ptr A    <N1>                <N2>                <d1>  -> <d1'>
component ptr B     <N3>                <N4>                <d2>  -> <d2'>
component ptr C     <N5>                <N6>                <d3>  -> <d3'>
```

`<N2>` and `<N1>` must be nearly equal (that is the evidence: same function, same field, moved
displacement). A displacement whose count explodes or disappears indicates a struct reshuffle,
not a move.

Tool: `scripts/getter_census.py`.

### 4.2 "Pointer field then value load" scan (component pointers)

State-vector and rotation pointers are consumed as `[component + 0]` vectors. Count, per candidate
displacement, the pattern `ldr xT,[xN,#DISP]` followed within a few instructions by a vector load
from `[xT,#0]`. The counts migrate with the field. Example shape (placeholders):

```text
candidate displacement   old build count   new build count
<d1>                     <c1>              <c1'>
<d2>                     <c2>              <c2'>
```

Same counts, new displacements — independently of any header.
Tool: `scripts/ptr_vec_scan.py`.

### 4.3 Encoded-displacement census (whole `.text`, cheap sanity)

Count every load/store displacement in the executable segments of each build. A moved field shows
the old displacement's count collapsing and a new one rising with the same order of magnitude;
normalise by build size when comparing. Use it to *form hypotheses*, never as sole proof.
Tool: `scripts/disp_census.py`.

### 4.4 Vtable slots: map, then confirm semantics

Recover class vtables from RTTI in both builds (`scripts/vtable_walk.py`), then align slots across
builds by masking the PC-relative immediates of each slot function's first instructions and matching
the remaining opcode/register structure. Exact instruction fingerprints map most slots 1:1.

The masking must cover **every** PC-relative-immediate family, not just branches: `B`/`BL`,
`B.cond`, `CBZ`/`CBNZ`, `TBZ`/`TBNZ`, `ADR`/`ADRP`, and literal loads (`LDR` W/X/SW,
`PRFM`, SIMD literal). Missing one family produces a false "no fingerprint match" exactly when
that instruction appears in a prologue; over-masking (e.g. dropping the `opc` bits of a literal
load) instead merges unrelated functions and can produce a false match. Keep the opcode/width and
register fields, drop only the displacement/target bits — the unit-check for a mask is "same
instruction with a different target must compare equal; a different opcode/width/register must
compare different".

Insertion detection:

* read the **slot count** in both builds (`<n>` vs `<n+1>`);
* if every mapped slot from some index `<k>` onward shifts by exactly `+1`, one virtual was inserted
  there — not renamed. The inserted slot itself is the one with no counterpart.

Example shape (placeholders):

```text
old build slot   new build slot
<i>              <i>          unchanged
<k>              -            inserted virtual
<k+1>            <k+2>        +1
<k+2>            <k+3>        +1
```

A slot index can also move with **no** insertion (a virtual added earlier in the hierarchy moves
one specific method): find the only pure getter returning the field and confirm with the engine's
own call sites.

**Confirm every slot you actually hook or call by decompiling the candidate**: a container size
getter returns the two legal sizes, a slot getter indexes with the known element stride, a setter
has the expected argument registers. Body-derived semantics beat index-derived guesses.

Tool: `scripts/vtable_slot_map.py` on dumps produced by `scripts/vtable_walk.py`.

### 4.5 ABI of an entry point you hook

* Check whether the hook entry reads only `x0`/`x1` (safe for a small forwarding detour) or
  registers beyond that (a wider detour changes the stack arguments contract).
* For a constructor or multi-arg call, decompile **the engine's own call site**, not the callee:
  the call site shows the true argument order (e.g. `ctor(obj, screenContext, client, game)`) and
  what is loaded from which object field, which doubles as layout evidence.

### 4.6 Fields that usually did *not* move — still verify

Cheap checks that pay for themselves:

* the item-stack loader writing the count byte at a fixed displacement proves the count
  displacement and the element stride independently of any header;
* element stride from the container's `getItem` arithmetic proves the item-stack size;
* a container class's vtable slots (container size / get item / set item / tick) are frequently
  unchanged even across a base-class growth — verify by decompiled semantics, then keep them as
  constants in both profiles.

### 4.7 Traps seen in the same family of ports

* A wrapper field can move (`<displacement-a>` -> `<displacement-b>`) while the mod only calls the
  getter, so no constant changes. Don't turn a call into a field read during a port.
* A result struct can contain two same-shaped vector fields (e.g. a reach/position vector and a
  block coordinate). Keep the field a verified consumer reads; do not swap a working
  block-coordinate field for a similarly named vector field — signatures keep resolving while
  targeting breaks silently.
* A class's base span growing by `<delta>` moves *every* derived field after it — one confirmed
  field justifies checking the whole family, not just the values a header happens to expose.

---

## 5. Porting behaviour, not just addresses: capture points move

The most expensive failure mode in a live port is this: **all signatures resolve, all hooks install,
and the feature still does nothing**, because the new build changed *which function hands the mod the
object it needs*.

Symptom (shape from a real diagnostic log, values are placeholders):

```text
install: OK                       <- every required hook installed
frame gate closed: ... captured=<null>   <- the object the mod captures was never set
```

The old mod captured an object (e.g. the UI render context) from a virtual that the new build still
has, with the same body — and its engine simply stopped calling that virtual; the data moved to a
newly inserted entry point.

Recipe that resolves this:

1. Look at the entry point the mod already hooks and **decompile its caller**: the caller may
   construct the needed object on the stack and pass it as an argument. That contract (e.g. "the
   second argument is the frame's UI context") is the new capture point; take it there for the new
   profile, keep the old capture for the legacy profile.
2. For an object the engine uses but never passes to a hooked entry, read it from **the engine's own
   queue at the drain point**. Example shape: the engine pushes fixed-size `<record-size>` records
   into a vector at `<queue-offset>`; `record[0]` is the object pointer that produced the record,
   and a word at `<flavour-offset>` distinguishes the old and new record flavours. The drain method
   is called every frame, so hooking it and scanning the queue yields the engine's own object
   without guessing where the engine stores it.
3. Derive a new virtual's ABI from the **record it writes** when its signature is unknown: which
   source register lands in which record field, which argument is a float, which argument is on the
   stack. A detour that declares exactly that GP/FP/stack split round-trips every argument bit for
   bit, even if the semantic meaning of some arguments is still uncertain.

Rules that follow:

* When a virtual stops being called, it is **dead code**, not a broken hook. Add counters for both
  the old and the new entry in a diagnostic build; the counts say which one is live.
* Reading the engine's own queue (read-only, forwarded unchanged) is the cheapest way to learn an
  object the engine never exposes: the queue is the engine's own hand-off point.
* Once a long-lived engine object (font, singleton) is learned, cache it. Clearing the cache every
  frame only loses the value on frames with no producer call.

---

## 6. Version regressions hide in "optional" paths — differential-test the old build

After the new build works, **run the same test on the oldest supported build**. In one real port
that caught a regression introduced two releases earlier: a constructor signature had pinned an
`adrp`+`add` pair, stopped resolving on an intermediate build, and silently disabled an optional
icon path. The old build's text and layout were fine, so the problem was invisible until compared
feature-by-feature.

Checklist for the differential test:

1. capture/draw: does the panel appear on the old build, with the same modes?
2. content: counts, names, icons — including an item that was **just added** to the container;
3. lifecycle: open/close container screen, leave/re-enter the save;
4. feature parity per build in the changelog/README matrix.

---

## 7. The diagnostic loop: build a log before guessing

Do not iterate on device by guessing. One diagnostic build that explains itself usually finds the
failure in a single test round.

### 7.1 The log sink

Write a plain text file the user can retrieve without root, and to logcat:

* first choice: the launcher's shared media directory
  (`/storage/emulated/0/Android/media/<launcher-package>/<Mod>.log`);
* fallbacks: `/sdcard` variant, app-external files dir, the directory holding `config.json`, the mod
  directory itself;
* log the **process cmdline** and the chosen path as the first lines — that tells you which package
  hosts the mod (and therefore which directories are writable);
* flush after every line: a crash then leaves the last line on disk.

### 7.2 What to log (this list found the root cause in one round)

* lifecycle: constructor / load / enable / disable / unload;
* attach: game library already mapped? dlopen observed? install result;
* profile decision: `gate pattern -> address` for **both** the new-build and legacy pattern;
* every signature resolution: name -> address (or 0);
* every hook install: name -> target -> OK/FAILED;
* vtable resolutions: class, slot, address;
* once a second, a frame line: display mode, current captured object pointers, and counters
  (ticks, frames, old entry calls, new entry calls, queue records drained, item renders, container
  ticks, container set-item calls);
* gate decisions with their reason ("no live player", "container screen open", "no target");
* render failures: missing context, null renderer, bad slot count;
* container screen open/close with the controller pointer and whether it was treated as a chest.

Counter design matters: separate counters for **old vs new entry point** and for **queue records
drained** distinguish "hook installed but never called" from "called but degraded" from "queue
empty". That is what turns "nothing on screen" into a definite statement about the capture point.

### 7.3 Read the log as a decision tree

| observation | conclusion |
|---|---|
| no log file at all | mod never loaded (layout/manifest/launcher), not a code bug |
| log stops before `install:` | lifecycle/attach problem |
| required signature = `0x0` | wrong build profile / pattern mismatch |
| `install: FAILED` but hooks mostly OK | a *required* signature was not resolved |
| hook installed, but its counter stays 0 | the virtual is dead on this build, or the target is wrong |
| captured object = `0x0` every frame | capture point moved (section 5) |
| target = `0x0` with player live | detection path, not rendering |
| target set, no drawing | render context construction / font / UI slot values |

### 7.4 Remove the logger for release — and prove it

Then re-verify the artifact, not the source: no log strings, no platform log-call import, no log
paths in the packaged `.so`; only functional file I/O remains.

---

## 8. Release discipline for a multi-build `.so`

1. Old branch unchanged (profile switch only); moved constants are runtime variables; hooks in the
   legacy order.
2. Install fails closed: a missing required signature means no hooks, not partial garbage.
3. Optional hooks degrade only their own feature (e.g. no font → draw icons, skip text).
4. Update manifest/README/CHANGELOG with the build matrix, the evidence summary, known issues and
   rollback (immutable `backup-*` and `release-*` folders; the diagnostic build is a checkpoint too).
5. Package check: library byte-identical to the build output; signature strings still embedded;
   the registration export present; no diagnostic code.
6. Final device check on at least the oldest and the newest build in the matrix.

---

## 9. Pitfalls, in one list

* A unique pattern is not an identity: decompile and anchor it.
* Don't gate the profile on a version string or on filename; use a disjoint behaviour pattern.
* Don't edit the legacy constants "to make them correct for all builds" — select per profile.
* Don't hard-code a wrapper field the mod could read through the getter function.
* A slot-count change means an **insertion**; map and confirm semantics, do not rename by guess.
* Two same-shaped vector fields in one result struct are a trap; keep the one a verified consumer
  reads.
* When a virtual stops being called, look for the new entry point / queue the engine uses instead.
* A pinned `adrp`+`add` pair in a pattern is a time bomb; wildcard only that pair.
* Verify the destructor-slot semantics before destroying a copied object on the stack.
* Test the oldest build too; optional-path regressions are silent.
* Ship without the diagnostic logger, and prove it is gone from the binary.

## Tools referenced

`scripts/getter_census.py`, `scripts/ptr_vec_scan.py`, `scripts/disp_census.py`,
`scripts/vtable_slot_map.py`, `scripts/vtable_walk.py`, `scripts/a64.py`, `scripts/sigscan.py`,
`scripts/multi_verify.py`, `scripts/offset_check.py`, `scripts/check_mod_signatures.py`, plus the
optional IDA MCP workflow (`references/ida-workflow.md`) for decompiling one located function.
