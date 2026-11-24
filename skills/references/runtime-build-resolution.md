# Running on Game Builds You Do Not Have (runtime build resolution)

A mod that whitelists builds by GNU build-id and bakes one address column per build stops working
on the first hotfix the user installs, even when that hotfix changed nothing the mod uses. This
document is the method for making one `.so` work on *unlisted* builds, by resolving addresses at
start and choosing the object layout from code evidence, without ever guessing.

It extends `porting-a-live-mod.md` (adding a build you *do* have) and `version-porting.md`
(signature and layout mechanics). Every number below is a placeholder: `<build-A>`, `<slot>`,
`<class>`, `<count>`. Read the real values out of your own binaries.

---

## 0. Diagnose first: is it the gate or the code?

"The mod shows nothing on version X" has two very different causes:

| Symptom | Likely cause | How to tell |
|---|---|---|
| nothing at all, no hooks, no panel | **the build gate**: the build-id is not in the table, so the mod deliberately installs nothing | the mod's own design says unknown builds are a no-op; read `detectGameBuild()` before reversing anything |
| some features work, others do not | a layout or capture point moved | `porting-a-live-mod.md` §4–§7 |
| crash | an unverified address reached a hook | `coexistence-and-crash-safety.md` |

When the cause is the gate, the question is not "what changed in X" but "**can the mod prove on
its own that X is close enough to a known build**". If it can, no library for X is needed.

---

## 0b. The gate itself: identify the exact image, not a "version"

A single-build mod can gate harder than a version string or a file size: identify the **exact
binary** by the linker's build-id. The gate is small, and its failure modes are all silent, so
get these details right:

* **Walk the loaded objects**, not the filesystem: enumerate the process's mapped images, match
  the game library by basename, and read the note from the **mapped** `PT_NOTE` segment
  (`offset → load address + segment vaddr`). A repacked APK means the on-disk file and the loaded
  image are not necessarily the same bytes; the image is what you are about to hook.
* **Parse the note list strictly**: every header is 12 bytes, the name and descriptor are each
  padded to 4 bytes, and both lengths must fit inside the segment before you advance. Accept only
  the build-id note: name `GNU`, type 3, descriptor length equal to the hash size (20 bytes for
  SHA-1). Bounds-check every step and treat a malformed list as "not my build".
* **Compare every byte** of the descriptor, and only then publish the object's load base to the
  rest of the mod. Publishing base earlier turns a wrong-build gate into hooks at wrong
  addresses.
* **Confirm the target image actually carries the note.** Not every shipped library does, and a
  rebuilt or stripped copy can lose it — then the gate rejects every build, including the right
  one. Check offline that the exact hash bytes occur in the file and that the note parses,
  before you ship. (If you produce the image yourself, add `-Wl,--build-id=sha1`.)
* **Log the rejection** with the expected short id and the fact that nothing was installed.
  Otherwise the user reports "it does nothing" and the next session reverses the wrong thing.
* **This is a no-op gate on purpose.** It identifies one build and refuses everything else. When
  you want a new build supported, that is §2–§7 (code evidence), not a looser gate.

---

## 1. Split what you need into "scannable" and "not scannable"

| Kind | Scannable at run time? | How |
|---|---|---|
| vtable address points | yes | RTTI (§2) |
| global data (registries, singletons, attribute keys) | yes | the instruction that references it (§3) |
| function entry points | yes | signature, or the vtable slot it lives in (§4) |
| **struct field offsets, vtable slot indices, strides** | **no** | chosen as a whole *profile* from code evidence (§5) |

Addresses differ in every build. Layout changes only when the engine changes a class. A hotfix
usually keeps the layout and moves every address, and that is the case this method covers.

---

## 2. Vtables by RTTI (exactly one hit per class)

The Itanium ABI puts `offset-to-top` (0 for the primary vtable) and a typeinfo pointer right
before the address point. Scan the writable `PT_LOAD` segments (relocations have already filled
them in memory) for a qword position `P` where:

1. `q[P-16] == 0`;
2. `q[P-8]` points inside the image, and `typeinfo+8` points to a NUL-terminated string equal to
   the class's mangled name (`<len><Name>`);
3. `q[P]` points into an executable segment.

Accept a class only when **exactly one** `P` qualifies. Secondary vtables have a non-zero
offset-to-top, and construction vtables point at a different typeinfo, so neither passes. Scanning
a few megabytes of writable data for all classes at once takes well under a second.

Also resolve the base classes your code compares against (for example the base whose slot you use
to recognise "is this an actor"). They are cheap and they feed the cross-checks in §6.

---

## 3. Global data by its referencing instruction

A global has no signature, but the code that loads it does: `ADRP xN, page` followed by
`ADD xN, xN, #lo12` or `LDR xM, [xN, #lo12]`.

* Generate a signature **anchored at the `ADRP`**, with the `ADRP` and the low-12 immediate
  wildcarded and the surrounding instructions fixed. Record the word distance (`delta`) to the
  `ADD`/`LDR`.
* At run time: the pattern must hit **exactly once**, and the decode must be strict. Check the
  `ADRP` opcode bits (`(w & 0x9F000000) == 0x90000000`), the `ADD`/`LDR` opcode, that the base
  register of the second instruction equals the `ADRP` destination, and the access scale for `LDR`.
  The result must land in a non-executable segment.
* Use **two independent sites** per global, and require every site that hits to decode to the same
  value. A single site is one changed instruction away from a confident wrong answer.
* When generating, try many candidate sites (tens). The first few often sit in code that differs
  between builds; keep the first two that are unique and decode correctly in **every** build you
  have.

---

## 4. Functions: signature or vtable slot

* A function with a cross-build signature (`version-porting.md`) is resolved by that signature,
  with unique-hit semantics on this path (see §8).
* A virtual function is read out of the vtable resolved in §2, at the slot of the candidate
  profile. Slot indices are layout, so this read happens per profile (§5).
* Every resolved address must lie in an executable segment.

---

## 5. Choose the layout profile from code, strictly

The layout of an unknown build is taken from the known build whose code it matches. The test is
a **strict probe** per function and per profile:

* Take the function's instructions from word 4 up to the first `RET` (cap it, e.g. 48 words). The
  first four words are skipped so that another mod's inline hook on the prologue cannot change the
  result.
* Mask what legitimately differs between builds: branch and call targets (`B`, `BL`, `B.cond`,
  `CBZ`/`CBNZ`, `TBZ`/`TBNZ` immediates), `ADRP` page immediates, and the low-12 of an `ADD`/`LDR`
  that follows an `ADRP`.
* **Keep every struct displacement and every immediate that is not an address.** A moved field
  shows up as a changed `LDR`/`STR` offset, and that is the whole point of the probe.
* Trim trailing wildcards. An empty probe (a function that is all branches) passes trivially, so
  do not rely on such a function alone.

A profile passes when **every** function resolves into code and matches that profile's probe, and
every cross-check (§6) holds. Then:

* **exactly one profile passes** → write the runtime column and use that profile's layout;
* **zero or two or more pass** → install nothing. Two passing profiles means the probes cannot tell
  the layouts apart, and picking one would be a guess.

This is why a build you have never seen is safe to support: a changed field changes a
displacement in at least one probed function, the probe fails, and the mod stays off. That costs a
missing feature on that build, never a crash.

---

## 6. Cross-checks tie the independent results together

Each resolution path can be fooled on its own. Make them confirm each other:

* the function that a signature found must equal the function at `<class>` vtable slot `<slot>`
  for this profile;
* several classes that share a virtual (for example sibling block-actor classes with the same
  tick) must hold the same function at that slot;
* the code that consumes a resolved value later (a slot comparison inside a detour) must compare
  against the **runtime** value, not the baked one.

---

## 7. Prove it with a holdout build

You do not have the target build, so you cannot test on it. Do this instead:

1. Take a build you **do** have but that is **not** in the baked table (an older or newer
   neighbour). Keep it out of the generator's inputs.
2. Run the resolver on it. It must select the profile you expect, and must **reject** the others
   for a stated reason (which probe or cross-check failed).
3. Compare every resolved value with an independent port of that build (signature port or manual
   evidence). All of them must be equal.
4. Run the resolver on every **known** build too. Each must select its own profile, and every value
   must equal its baked value.

If a neighbour on the same branch passes as a given profile, a hotfix between them is very likely
to pass too. Say exactly that to the user, and say what would make it fail (a changed layout →
the mod stays off).

---

## 8. Engineering rules for the resolver

* **Generate the tables.** One generator script reads the baked values and the libraries, chooses
  every pattern and probe, simulates the whole resolver, and only then writes the C++ include
  file. Hand-written tables drift.
* **A coverage assertion in the generator**: every address constant declared in the sources must
  be covered by a recipe. A constant without one would silently stay 0 on the runtime path.
* **One definition per address.** Move module-local address tables into the shared header. A
  duplicate of the same name in another namespace compiles fine and is never filled in.
* **Two indices, not one.** Keep a *layout profile* index (which known build's field offsets and
  slots) separate from an *address column* index (a known build's baked column, or the runtime
  column). Layout values read the profile; addresses read the column.
* **Unique-hit scanning on this path.** A scanner that returns the first hit is fine for a known
  build (the address is verified against the baked value anyway). On an unknown build, a second hit
  must make the request fail. Add an opt-in flag and leave the existing behaviour alone.
* **The known-build path stays unchanged.** Runtime resolution runs only when the build-id lookup
  failed.
* **Cost.** The unknown-build path scans for many more patterns than the normal path. Expect hooks
  to install a few seconds later on first start, and tell the user.
* **Verify the generated file, not only the generator.** Replay the resolver from the emitted
  include file (parse it back) and compare with the generator's in-memory result. This is the
  `verifying-unrun-cpp.md` §2.2 technique applied to data. When the C++ cannot be run on the host,
  say so in the report.

---

## 9. What to tell the user

* Why it failed before (the gate, in one sentence).
* How the new path decides (RTTI, reference sites, signatures, strict code match, exactly one
  profile).
* The evidence (holdout build → which profile, how many values equal the independent port; known
  builds → each picked its own profile).
* What can still fail and what it looks like: a changed layout means no panel, never a crash.
* What to send if it fails: that build's `<libminecraftpe.so>`. If the user already declined to
  send it, do not ask again before they have tested. Ask only after a failed test.

---

## Pitfalls

* Reversing the "changed code" before checking the build gate. The gate explained everything.
* Gating on a version string, a file size or a mod-catalog label instead of the image's own
  identity. Those change with packaging and launcher builds; the build-id note does not.
* A build-id gate that parses the note loosely (no bounds check, wrong alignment, accepting a
  truncated descriptor) either accepts a foreign build or rejects the right one — both look like
  "the mod does nothing".
* Publishing the image base before the gate has matched.
* Treating "the generator ran" as proof. The generated include file is what ships; replay it.
* A data site that decodes in one build and not in the others. Keep only sites that decode in all
  of them.
* An empty probe counted as evidence.
* Accepting a profile when two pass.
* Comparing a detour's slot against the baked value on a runtime build.
* Hunting for a host C++ toolchain when the machine lacks the platform SDK. Compile with the
  project's real cross toolchain, verify with the runnable twin, and report the gap
  (`verifying-unrun-cpp.md`).
