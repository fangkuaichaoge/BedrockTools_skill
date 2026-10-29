# Porting to a New Game Version

Every game update can change function addresses, member offsets, and vtable
slots in `<game-lib>`. Byte signatures are more stable but must always be
re-verified.

There are **two independent things to port**, and conflating them is the most
common and most expensive mistake:

| | What can break | Symptom when it breaks |
|---|---|---|
| **Signatures** | the pattern stops matching | feature silently never installs |
| **Object layout** | a hard-coded `this + 0xNNN` moves | feature installs, then writes to the wrong field |

The second is far worse, because nothing errors. A signature that resolves is
not evidence that the struct it points into has the same shape.

For the **end-to-end playbook of adding a new build to a mod that already
ships** — runtime build profile, moving capture points, the diagnostic loop and
the release checklist — see `porting-a-live-mod.md`.

---

## 1. Decide the porting goal first

"Support two versions" can mean two very different things, and the choice
changes the whole approach:

- **One build at a time** (ship a separate `.so` per version) — you may keep
  precise, version-locked signatures.
- **One `.so` for several builds** — every pattern must resolve to exactly one
  hit in *all* of them, and every hard-coded offset must hold in all of them.

Everything below assumes the second, which is the harder and more common goal.

---

## 2. Signature porting

### 2.1 Why `--tight` patterns stop matching

A generator run in `--tight` mode keeps object field offsets fixed. That yields
a very strong pattern — and a version-locked one: it stops matching as soon as
the compiler reshuffles a struct or changes an inlining decision.

**House style instead (what BedrockTools uses):** wildcard every instruction
that carries an immediate or a memory operand, and keep only the opcode and
register structure. Displacements then take no part in the match, so the pattern
survives recompilation.

```text
tight   : ... 68 92 48 F9 E1 05 80 52 00 DD 41 F9 ...   (offsets fixed)
house   : ?? ?? ?? FC ?? ?? ?? 6D ?? ?? ?? A9 ?? ?? ?? F9 ...
```

The cost is that short patterns get ambiguous. The fix is almost never "add more
fixed bytes" — it is to extend the wildcarded pattern with the next few
*structural* instructions until it is unique again.

### 2.2 The two failure modes, and what each really means

- **MISSING (0 hits)** — the pattern was version-locked. Re-derive it in house
  style from the function's head in the new build.
- **AMBIGUOUS (2+ hits)** — the pattern is too generic. Extend it with
  structural bytes; do not "pick the first hit".

A pattern can be **UNIQUE in one build and AMBIGUOUS in another**, so uniqueness
must be checked per build, not once. Real example: a generic FOV-getter pattern
matched one function in one build and two in another. The two shared the whole
prologue up to `fmov s8, s0`; the real getter continued `mov w20, w1 / mov
x19, x0`. Appending those two structural instructions restored uniqueness
everywhere.

### 2.3 A fixed pattern is sometimes correct

Wildcarding everything is not a rule, it is a default. Keep a pattern **fixed**
when a false match would be destructive — for example the anchor for a stage
that writes matrices into an object. If the prologue is byte-identical across
the builds you support, the fixed form is both unique and portable, and it fails
loudly (MISSING) rather than silently matching the wrong function.

### 2.4 Reuse the existing dictionary before deriving anything

The BedrockTools `Signatures.cpp` is written in house style precisely so it
ports. Check it first: several functions needed no derivation at all, only a
re-verification. Deriving a pattern by hand that already exists in the
dictionary is wasted effort and usually produces a weaker pattern.

### 2.5 Verification, per build

```bash
# every pattern, every build, one table
python scripts/multi_verify.py --sigs patterns.json \
    --so <build-a.so>,<build-b.so>,<build-c.so>
```

The output is a matrix of hit counts. The only acceptable result is **1 in every
column**:

```text
name                              pe-a.so    pe-b.so    pe-c.so
GetFov                                  1          1          1
SetupCamera                             1          1          1
CameraDeps                             79         64         64   <-- ambiguous
```

That last row is the trap: the short form matched dozens of similar matrix
helpers in *every* build, so a per-build spot check would have "passed" on the
primary target and broken everywhere. Use the full pattern.

---

## 3. Object-layout porting

Signatures and offsets are independent. A build can keep a function byte-for-
byte identical while the struct it operates on shifts.

### 3.1 Read the offsets back out of the game's own code

Do not trust a comment or a table copied from another project. Disassemble the
function you just located by signature and list the displacements it actually
uses:

```bash
python scripts/offset_check.py --sigs patterns.json --names <FunctionName> \
    --offsets <disp1,disp2,disp3,...> \
    --so <build-a.so>,<build-b.so>
```

```text
    offset    build-a.so    build-b.so
    <disp1>            -            -
    <disp2>            1            1
```

Any offset that appears in one column and not another needs a per-build offset
table rather than a shared constant.

### 3.2 A `-` does not always mean "incompatible"

An offset may simply be used by a *different* function than the one you scanned.
A camera-style function may write one displacement but never touch another one
that a nearby consumer reads.

Before concluding that a layout changed, widen the search: scan the whole
`.text` for that displacement and compare the **number of writers** across
builds. If the counts match, the field is the same field.

A stronger confirmation for a `float3` position: look for the *group*. A camera
position is written as consecutive stores at `<disp>` / `<disp + 4>` /
`<disp + 8>`. Builds writing the same group at the same displacement is solid
evidence the layout held.

### 3.3 Cross-check against a mod known to work on the device

If another mod in the workspace is confirmed working, its read displacements are
ground truth to compare against — not the only proof, but a strong cross-check.

---

## 4. Make the check unskippable

A porting check that is only run when someone remembers is a check that will be
skipped. Two cheap guards pay for themselves immediately:

- **Generate from the verified set.** Have the header generator *verify first
  and refuse to emit* unless every pattern is unique in every build passed to
  it. A pattern that was never checked then cannot reach a header.
- **Verify against the artifact, not the source.** Check the built `.so`'s
  embedded patterns, so a hand-edit that bypasses the generator is still caught.

### 4.1 Keep the verifier build-agnostic

A verifier that hard-codes absolute addresses only proves it was written for one
binary: every pattern reports FAIL on the second build even though the patterns
are fine. Check the properties that must hold in *every* build instead:

- exactly one hit, and
- the hit lands on a real function boundary.

Function boundaries come from `.eh_frame_hdr` (see
`scripts/a64.py`), which is present in stripped builds. Note that its table
yields `(start, fde)` **pairs** — using the raw rows as a set of addresses
silently reports every pattern as "mid-function".

### 4.2 One source of truth

Keep the pattern strings in exactly one place (a JSON table) and generate the
C++ header from it. A second copy in the generator, or in the verifier's
expected-address table, will drift, and the drift is invisible until a port.

---

## 5. Porting checklist

1. **Get the new `<game-lib>`** from the new APK.
2. **Identify the version** from `.rodata`.
3. **Check the dictionary first** — `Signatures.cpp` in house style.
4. **Verify every pattern in every build**:
   `scripts/multi_verify.py`. Require 1 hit per column.
5. **Re-derive only what failed.** MISSING -> house-style pattern from the new
   head. AMBIGUOUS -> extend with structural instructions.
6. **Check the object layout**:
   `scripts/offset_check.py` for the offsets your mod pokes.
7. **Resolve `-` results** by scanning the whole `.text` and comparing writer
   counts before declaring a layout change.
8. **Regenerate the header** through the verify-then-emit path.
9. **Rebuild, deploy, test** on each supported build; confirm every hook in
   logcat.
10. **Record the result** — a per-version signature report and any offset that
    needed a per-build value.

---

## 6. Experience rules

- **Wildcard immediates and memory displacements; keep opcode/register
  structure.** This is what makes a pattern survive a recompile.
- **Never bake BL call offsets into a pattern** — they move with the call site.
- **Uniqueness is per build.** A pattern unique in the primary target can be
  ambiguous in another.
- **Check both halves.** Signature porting and layout porting are separate
  tasks; doing only the first produces a mod that installs and then misbehaves.
- **A `-` in the offset table is a question, not an answer.** Find who *does*
  touch the field before changing anything.
- **Prefer the full pattern over a clever short one.** A short pattern that is
  ambiguous everywhere is not a porting win.
- **Constant patches** (e.g. `<old-value> -> <new-value>`) usually survive updates;
  **struct offsets and vtable slots** are the most volatile.
- **Fail loud.** When a pattern cannot be made portable, prefer MISSING (feature
  off) over a loose pattern that might match the wrong function.

## Prove every signature on every build before shipping

A signature that resolves on the build in front of you says nothing about the next one. Two
failure modes are invisible at run time and both look like "the mod works but one feature is
missing":

* **MISSING** - the pattern is gone in that build, so `resolveSignature` returns 0 and the hook
  silently never installs;
* **AMBIGUOUS** - it matches several places, so the hook may land on an unrelated function.

Neither is cosmetic. In a real port, a missing level-destructor signature was the root cause of a
crash when leaving a save: the cleanup that hook performed never ran, and stale engine pointers
survived into the next frame.

Run the check offline, for every build, before packaging:

```
python scripts/check_mod_signatures.py --source <mod-dir>/src/main.cpp \
    --binary <libminecraftpe-old.so> --binary <libminecraftpe-new.so>
```

It reads the `constexpr std::string_view` signatures out of the source, so the check can never
drift from what ships, and exits non-zero when anything is missing or ambiguous.

### Design so a missing signature is not fatal

Removing the dependency beats finding a new pattern:

* replace "clean up on level destruction" with a **timeout**: stamp the last tick of the object you
  care about and drop every cached pointer when ticks stop. Leaving a save stops ticks, so the
  cache clears itself and no signature is involved.
* track "is this actor still alive" by the same rule (only actors that still tick can still exist),
  instead of validating pointers per frame.
* if a signature is optional (a nice-to-have label, an extra effect), resolve it separately and
  degrade only that feature - never gate **all** hook installation on the hardest pattern.

### Keep the check honest

Delete the signature constants you stopped using. Unused patterns keep "failing" in the checker and
train you to ignore its output; if a constant is genuinely kept for a fallback path, record why in a
comment next to it.

## Example: what one update's layout shifts look like (shape, not values)

One update cycle can produce several independent shifts. Treat each as a hypothesis to *verify from
the game's own code*, never as a rule to copy to another version. The values below are placeholders;
substitute what your own builds show.

| structure | old build | new build | how to prove it |
|---|---|---|---|
| base-class component pointers used by many consumers | `<d1>` / `<d2>` / `<d3>` / `<d4>` | `<d1'>` / `<d2'>` / `<d3'>` / `<d4'>` | trivial-getter census (same caller counts, new displacement) plus disassembly of the class's own consumers |
| a proxy pointer reached from a very common base | `base + <p>` | `base + <p'>` | the game's own call sites (load right before the callee), or a trivial getter |
| renderer camera fields | `<c1>` / `<c2>` / `<c3>` / `<c4>` | `<c1 - δ>` / `<c2 - δ>` / ... | the same accessor set moved by one uniform delta |
| a UI-context vtable | slots `<a>`, `<b>`, `<c>`, ... | `<a>`, `<b+1>`, `<c+1>`, ... | slot-count delta plus per-slot fingerprint mapping |

A `<delta>`-sized growth in a base class moves *every* derived field after it: one confirmed offset
justifies checking the whole family, not just the values the base class happens to expose.

A result struct can contain two same-shaped vector fields (e.g. a reach vector and a block
coordinate). Block-targeting code normally reads the block coordinate; rewriting a working field to
the other vector silently breaks every block-targeting feature while all signatures still resolve.
Settle it by decompiling one consumer, never by field order.

### Tools that made this practical

- `rtti_vtables.py` - reconstructs the post-relocation memory image and walks RTTI class vtables.
  These libraries keep `.data.rel.ro` zero-filled in the file; every pointer comes from
  `R_AARCH64_RELATIVE` entries in `.rela.dyn`, so plain file scans find nothing.
- `align_vtables.py` - aligns one class's vtable across two builds by disassembly similarity and
  reports which slots moved.
- `port_function.py` - rebuilds a wildcarded (house-style) pattern from an old function's bytes and
  searches the new build, returning the new address when the body survived.
- `cross_version_signature.py` - emits one pattern that matches a function head in several builds by
  fixing only the instructions all builds agree on.
- `fast_verify_signatures.py` - verifies a whole pattern table against a
  multi-hundred-MB library in seconds (anchor search instead of scanning the text
  once per pattern).
