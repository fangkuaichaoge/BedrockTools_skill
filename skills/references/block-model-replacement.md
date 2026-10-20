# Replacing a Block's Model (experience notes)

Distilled from a real, successful project: give a category of blocks (leaves) a
custom mesh, keeping the engine's own lighting, biome tint and neighbour
culling.

Written to be **version-agnostic**: offsets appear as "the displacement used on
the builds tested" and every one of them must be re-verified per build (see
`version-porting.md`). The *method* is the deliverable; the numbers are evidence
that the method was applied.

Read this before any "change how block X looks" task. Almost every failure below
was expensive and none of them were obvious in advance.

---

## 1. The hook layer: per-face geometry emitters

Block geometry is produced by one function per cube face
(`...TessellateFaceUp`, `...Down`, `...North`, `...South`, `...East`,
`...West`). These are the right layer for replacing a model because each call
carries everything you need:

| arg | meaning |
|---|---|
| `x0` | `BlockTessellator*` (owns the BlockSource and the shape state) |
| `x1` | `Tessellator*` (the vertex sink) |
| `x2` | `const Block*` being drawn |
| `x3` | `const Vec3*` **world-space float position**, not a BlockPos |
| `x4` | `const TextureUVCoordinateSet*` for this face |

Two consequences that decide the whole design:

* **The position is a float triple.** Convert with `floor()`, never by
  reinterpreting it as an integer BlockPos. A wrong conversion produces
  neighbour lookups in the wrong cell, which shows up as geometry appearing and
  disappearing as you walk.
* **The texture argument is per face.** It carries the atlas rectangle to sample
  (`u0, v0, u1, v1`). Six calls in a row give the *same* block six different
  rectangles. A mesh whose UVs are normalised `0..1` must be remapped into
  whichever rectangle the current call supplied.

### Alternative layers, and why they are worse

* **Vtable / model-geometry override**: the built-in model for a vanilla block
  category is engine-internal (something like a shared `geometry.<name>`), not a
  resource you can swap through a `.geo.json` — that path is for custom blocks.
* **Resource-pack geometry**: cannot apply to vanilla block ids, and cannot see
  the neighbour state that decides culling.

## 2. Always run the original; suppress only its vertices

The face routine's **caller reads back state it wrote** (shape AABB, flags). A
head-replace or an early return corrupts that state and produces geometry that
disappears, or a crash.

Correct shape:

1. Run the original with a thread-local "suppress" flag set.
2. In the detour on the low-level vertex emitter
   (`Tessellator::vertexUV(x, y, z, u, v)`), drop the call while suppressed.
3. Emit your own vertices through the *original* vertex emitter.

This is why the vertex emitter is worth a signature of its own: it also means
the mod never needs to know where the tessellator keeps its pending UV, which is
a displacement that moves between builds.

## 3. Visibility: the emitter is called for hidden faces too

**Do not add your own visibility test at this layer.** The reason matters,
because the obvious reason is wrong.

It is tempting to assume "the engine culls hidden faces before calling me, so a
second test would delete visible geometry". Disassembly of the per-block driver
shows the opposite:

```asm
bl    #<FaceDown>          ; the call is UNCONDITIONAL
tbz   w27, #1, <skip>      ; guard A
tbnz  w8,  #1, <skip>      ; guard B
```

* The `bl` has **no visibility test in front of it**. The emitter runs for every
  face of every tessellated block, including faces the player cannot see.
* The two bit tests come **after** the call and gate the surrounding
  bookkeeping, not the call itself. Together they form a **per-face visibility
  mask**: on the build examined, bit 1 = Down, 2 = Up, 3 = North, 4 = South,
  5 = West, with East tested through bit 0 of a second register.
* A neighbouring driver uses the same scheme with different registers
  (`w8`/`w9` vs `w8`/`w27`).

Consequences for a model-replacement mod:

* Emitting replacement geometry **unconditionally** is safe and correct — you
  will not punch holes, because you are not being called only for visible faces.
* Do **not** infer visibility from the fact that you were called.
* If you ever do need the engine's own visibility opinion, it is those mask
  registers at the call site — not the call itself.

This is also a lesson in carrying conclusions between builds: an earlier note
claimed the engine culls *before* the call. Re-reading the guards disproved it.
Re-derive mechanism claims from the binary you are shipping for; see
`targeted-static-analysis.md` §6.

## 4. A diagonal quad must be reachable from both of its axes

This was the root cause of a long "faces are missing" investigation.

A quad's normal decides which face emitter draws it. That is correct for the
cube sides (axis-aligned normals) but **wrong for diagonal quads**: a chamfer
with normal `(±0.707, 0, ∓0.707)` spans two axes and is visible from two
directions. Baked into the `North` group alone, it **vanishes whenever the block
is viewed from the East**, because `FaceEast` was never called for that block.

Fix: emit the quad from **every face group whose axis its normal spans**
(threshold around `|n| > 0.3` per axis), not from the single dominant one.

The fix has a mandatory companion: a quad present in two groups would otherwise
be drawn **twice** when both faces are visible, which z-fights. Give every
authored quad a **dedup id**, copy that id into each group it appears in, and
have the mod skip a quad whose id it already drew for this block.

**Verify the fix numerically**, not by eye: simulate one isolated block and check
that the total drawn area equals the authored model area exactly. A duplicate
draw makes the total *larger* than the model, which is easy to miss visually but
immediate in the numbers.

## 5. Colour: read the base colour, not the per-vertex colours

The engine sets a **per-face base colour** (biome tint × face brightness)
*before* calling the face emitter. *Inside* the emitter it then sets a separate
colour per vertex — that is **ambient occlusion**, and most corners are darker
than the base.

Taking any single one of those per-vertex colours, or their average, paints the
whole replacement mesh with an occlusion-darkened value and the block comes out
much too dark.

The diagnostic that gives this away, and that a user will report spontaneously:
**"turning smooth lighting off fixes it."** Smooth lighting is what adds the
per-vertex pass; switching it off collapses the range to the base colour. Treat
that sentence as a direct pointer to this bug.

So: read the pending colour **before** running the original, and use that value
for every vertex you emit.

## 6. UVs: an atlas tile is not a tiling texture

The mesh's UVs are normalised, and get remapped into the face's atlas rectangle:

```
u_out = u0 + u * (u1 - u0)
v_out = v0 + v * (v1 - v0)
```

Do **not** apply `frac()` to `u`/`v` before that. A leaf tile is a small
rectangle inside a larger atlas (roughly 1/64 of it), not a repeating texture:
wrapping sends `u = 1.0` to the tile's right edge and `u = 0.0` to its left
edge, so every seam jumps. **Clamp** into `[0,1]` instead.

If seams still sample wrong, the honest next step is a numeric check of the
mesh's UV range (`min`/`max` must lie inside `[0,1]`) rather than more remapping
experiments.

## 7. Batch safety: emit once per (tessellator, position)

Two facts combine badly:

* The engine re-issues face calls for one block many times (smooth-lighting
  passes). Re-emitting each time floods and corrupts the quad batch.
* The `Block*` argument is **reused across blocks** — the same pointer arrives
  for different positions. Keying a dedup table on the block pointer silently
  merges unrelated blocks.

So dedup on `(tessellator, position)`. And reset that table from a
`Tessellator::begin()` hook: the GUI/item render path reuses one tessellator and
one position every frame, so a table that is never reset suppresses the vanilla
geometry without substituting anything — the item icon goes missing.

## 8. Block identity: name lookup done safely

* `block + <blockTypeOffset>` gives the `BlockType*`.
* The block's id is a `std::string` **member of a `HashedString`**, not the
  `HashedString` itself. Reading the `HashedString` as if it were a
  `std::string` yields a wild pointer that passes a naive null check and then
  faults (observed as a crash with a tiny `si_addr`).
* Use the real `std::string` accessors (`size()`, `data()`) instead of
  hand-decoding the libc++ layout. A manual decoder got the short/long
  discriminator backwards and **silently disabled the whole feature**.
* Android heap pointers are **TBI-tagged** (top byte non-zero). Strip the top
  byte before any validation, or every pointer in the process is rejected and
  the mod looks inert.
* Validate each stage before the next dereference: pointer plausibility → size
  range → data pointer plausibility → all characters printable.

## 9. Classification by name, and its limits

Matching a substring (`"leaves"`) covers every vanilla wood type plus any add-on
that follows the convention, and it beats an exact list. Two rules learned the
hard way:

* **Strip any namespace prefix, not just `minecraft:`.** Add-on ids are
  `<addon>:<path>`; a `starts_with("minecraft:")` test leaves the prefix in
  place, and a length cap or a printable-ASCII check that rejects `:` then
  throws the name away.
* **Do not match the singular of a plural category name** just to catch more
  blocks. It also catches unrelated blocks, and the user will notice.

When a block genuinely does not follow the naming convention, the durable answer
is a small **explicit id allow-list** in the source — the id is more reliable
than a localised display name, which is translated text that changes with the
language. (Display names are also built at run time from
`tile.<id>.name`-style keys, so they are not constants you can search for in the
binary.)

## 10. Never modify the artist's model during the bake step

The bake tool converts the source mesh into a C++ table. It must be a
**verbatim copy** — no clipping, no splitting, no culling — and it must prove
it:

* Assert **area conservation** (`sum(baked) == sum(authored)`, to float
  precision) and **UV count preservation** every run.
* Never delete a quad because it looks like a duplicate. Two quads can share
  geometry and still be intentional surfaces with **different UVs**; a
  "coplanar duplicate" heuristic deleted four authored quads and the user
  correctly called it out as tampering with their model.

### The overlap-culling trap

The model was larger than one block, so adjacent blocks interpenetrated and
flickered. Three attempts to remove that overlap all failed, and the pattern is
worth internalising:

| Attempt | Result |
|---|---|
| Clip each quad to its own cell at bake time | **holes** |
| Drop a protruding piece when the neighbour has a coplanar quad | **holes** (sparse shell: the neighbour often has no geometry there) |
| Look up the neighbour block at run time and skip the protrusion | **ragged, partly missing silhouette** |

The generalisable reason: **a protrusion is not automatically covered by the
neighbour.** A block model is a sparse shell of quads, not a solid volume, so
"my geometry reaches into your cell" does not imply "your geometry is there
too". Culling the protrusion leaves a region nobody draws.

Corollary for requirements: **ask which artefact the user prefers.** Given
"complete model" vs "no flicker at contacts", the answer was complete model —
and the correct action was to delete the whole culling mechanism. Do not keep
re-tuning a mechanism the user has rejected; remove it and say so.

## 11. Diagnostics: a filter that does not filter is worse than none

A log intended to reveal unknown add-on block ids labelled **every** name
`leaf-ish`, so the one real candidate was buried among stone, dirt and granite.
The user's next report was "it still does not work" — the log had destroyed the
evidence it existed to collect.

Rules:

* A log line must mean what its label says. Filter before you print.
* Budget by **category**, not globally: a single global "first N names" cap is
  consumed by planks and ores long before the interesting block appears. Give
  the interesting category its own unbounded (or generously bounded) budget.
* Instrument the **negative** result too: if a block reaches your hook but is
  not classified, say so with the name. Silence is indistinguishable from "the
  hook was never called".

## 12. Version compatibility: verify against the build the device runs

Signatures and object layout are **two independent porting tasks** (see
`version-porting.md`). Two practical points from this project:

* **Identify which build the device actually runs** from a runtime-discovered
  value, not from the file name. Several game files may sit in one directory;
  the file whose name matched the task was *not* the one on the device. A field
  displacement discovered at run time (and printed once) is a reliable
  fingerprint.
* **Check whether your hard-coded displacements are the version-sensitive
  kind.** Some are genuinely stable across nearby builds (the `Block*` →
  `BlockType*` and `BlockType` → name displacements matched on both builds
  tested here). Others are not. Re-verify rather than assume, and prefer
  run-time discovery (scanning the function that uses a field) over a constant
  whenever the field can be found that way.

## 13. Shipping checklist for a model-replacement mod

- [ ] Bake tool is verbatim; area and UV-count assertions pass.
- [ ] Isolated-block drawn area equals the authored model area (no double draw,
      no missing group).
- [ ] Every diagonal quad is reachable from both of its axes.
- [ ] Colour comes from the per-face base value, not per-vertex AO.
- [ ] UVs are clamped, not wrapped, and lie inside the atlas rectangle.
- [ ] Emission is deduped per `(tessellator, position)` and the table is reset
      on batch begin.
- [ ] Name lookup validates every stage and strips any namespace.
- [ ] Offsets re-verified on the build the device runs.
- [ ] No logging or filesystem access left on the geometry path.
