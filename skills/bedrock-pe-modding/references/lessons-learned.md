# Lessons Learned (experience notes)

Distilled from real "mod does not work" debugging sessions. These are
general principles, not version-specific answers.

For rendering / visual-effect work (outlines, tints, glow, material effects),
read `render-pipeline-hooks.md` as well — it covers the API-boundary hook
layer, shader-source injection, GL state hygiene, geometry rules for shell
effects, and rollback discipline.

## 1. The failure pattern: forcing an upstream gate

Symptom: an upstream "type / feature check" was patched to always return
true in order to unlock a downstream behavior. The mod did not work.

Why it fails (generalized):

- Head-replacing a function skips its side effects, not just its return
  value.
- One gate flag usually controls several downstream paths. In the real case
  it was also passed as a read-mode argument and enabled reading optional
  fields the object did not have.
- Enabling optional-field reads triggers validation paths that then *reject*
  the object (e.g. "Cape is missing ..."), so the feature fails instead of
  loading.

Rule: **do not force an upstream gate to unlock a downstream behavior.**
Locate the exact decision site where the behavior is dropped and patch that
site directly.

## 2. The approach that worked

- Reverse a known-working reference implementation when one exists: extract
  its signatures, patch offsets, and payloads; replicate them exactly, then
  verify each site's original bytes before writing.
- Prefer the smallest direct change at the decision site: an unconditional
  branch, a NOP, or a corrected flag / argument.
- Keep every write "read original bytes -> verify -> write -> log". A
  mismatched build must fail safely, never write garbage.

## 3. Process experience

- Error / log strings are the best anchors for finding parsers and gates:
  search the message, follow its xref, decompile the referencing function.
- Map the call chain with BL-call-graph scans. If a loader has no direct BL
  callers, it is registered indirectly (vtable / callback table); find its
  registration to reach the dispatch path.
- Signature hits from scripts may be slice-relative or absolute: always
  state the base and confirm with the raw bytes at the resolved site.
- Offline analysis (ELF parsing, BL decoding, ADRP+ADD resolution,
  longest-fixed-run signature matching) works without IDA; use it to prepare
  hypotheses, then confirm with a decompiler when available.
- On-device logs are the ground truth for which gate actually failed. Do not
  pile on speculative patches; get the logcat or diff against a working
  reference first.
- Log every applied fix (name + address) so device logs pinpoint the exact
  site that failed on a mismatched build.

## 4. Decision rules (short)

- Fix at the drop site, not at the gate.
- Smallest change wins; always verify original bytes.
- A working reference beats speculation: reverse it.
- Log what you apply; use the logs to decide the next step.

## 5. GUI item rendering

When slots render but item textures do not, reverse a working BedrockTools
preview first. Reuse its complete `ItemStack` preparation, render-context
construction, item-renderer call, render-context cleanup, and image flush
order. A successful call or nonzero counter is not proof that a texture was
submitted correctly.

Keep background, item, and text batches separate. Flush the background before
items, then flush the item batch after the render context has been cleaned up.

## 6. Use separate item paths

Ordinary items and block items may require different GUI rendering paths. If a
complete temporary stack fixes ordinary items but distorts block models, keep
block items on the original client stack path and use the working preview path
only for ordinary items. Identify the branch from the underlying item type,
not from slot number or item count.

## 7. Protect object lifetime

Keep temporary `ItemStack` objects local to the render pass. Do not create,
copy, destroy, or cache them inside inventory-update hooks until the exact
constructor and destructor ABI is verified. When synchronizing two actors,
skip identical source and destination pointers. Clear all actor caches during
actor destruction and level teardown.

## 8. Diagnose with state transitions

Log selection, stack validity, renderer availability, submitted item count,
and container open/close transitions separately. Use exact container lifecycle
hooks rather than localized text heuristics to hide a world preview while a
container screen is open, and verify that rendering resumes after close.

## 9. Rendering effects (pointer)

The full write-up lives in `render-pipeline-hooks.md`. The short version:

- Pick the hook layer from the effect: same geometry with different
  uniforms/state belongs at the graphics-API boundary; new geometry or new
  pixels needs engine-level hooking. Do not pretend the boundary can see bone
  transforms, materials or mesh builders.
- Wrap every way the API can be reached, and log the first *real* arrival at
  each entry point — "hook installed" is not evidence that the game calls you.
- Match shader identifiers by normalised family, never by literal string
  (cross-compiled shaders rename and re-punctuate everything).
- Make every rejection self-reporting with the values it compared; a silent
  "no" is indistinguishable from an unimplemented feature.
- Save/restore all GL state you touch; do not infer geometry orientation from
  the engine's winding declaration.
- Shell outlines: a per-face normal offset tears hard-surface meshes apart; a
  position-only (affine) displacement stays connected, in-plane face growth
  overlaps and hides the tears, and ratios beat absolute units.
- If the geometry has no direction information at all, the effect is not
  achievable at this layer — stop and say so instead of tuning formulas.
- Cap additive brightness; an uncapped intensity multiplier washes out frames.
- Revert on request by deleting code and proving it (source grep + strings
  scan of the shipped binary), and write down why a feature was removed.

## 10. Startup crashes: "the launcher dies and asks me to re-import"

The full write-up lives in `mod-lifecycle-and-crash-safety.md`. The short
version, because this one is expensive to rediscover:

- **Never hook `dlopen` to learn when the game library appeared.** It is the
  hottest function in a Java host process; your detour runs on arbitrary threads
  for every load, and getting the re-entrancy/unhook ordering wrong recurses
  until the stack dies. A *timing* problem does not justify that blast radius.
- The preloader enables mods **before** the game library is mapped, so `enable()`
  must never assume it is present. Poll with `dlopen(RTLD_NOLOAD)` (which never
  loads anything) on a watcher thread with a bounded budget.
- **"Mapped" is not "ready".** Right after the library appears the linker may
  still be running relocations. Wait a few seconds (a settle delay) before
  resolving signatures or installing hooks. This was the actual fix for a
  crash that survived several other corrections.
- Initialise the hook engine explicitly before the first install; do not rely on
  it self-initialising.
- When a user says "the hooks work in my other environment but this host dies",
  the problem is **lifecycle, not hook correctness**. Compare against a mod that
  works on the same host and diff the *design*, not the feature.
- Fixing the code is not enough when a saved config holds a value written by the
  buggy build: version the config and repair the stale value on load.
- A preset must not pin a game toggle the player uses (e.g. the camera
  perspective), or the game's own control looks broken. Keep such pins off by
  default and named so that is obvious ("Don't Touch").

## 11. Replacing a block's model

The full write-up lives in `block-model-replacement.md`. The parts worth
carrying in your head, because each one cost a separate debugging round:

- Hook the **per-face geometry emitters**, always run the original, and suppress
  only its vertices. Its caller reads back the state it writes.
- **The engine already culled.** It skips hidden faces before calling you; a
  second visibility test at your layer deletes visible geometry.
- **A diagonal quad must be emitted from every axis its normal spans.** Baked
  into one group, it disappears whenever the block is viewed from the other
  direction. Pair the fix with a per-quad dedup id, or the two copies z-fight.
  Prove it numerically: an isolated block's drawn area must equal the model's.
- **Colour comes from the per-face base value, not the per-vertex colours.**
  Those are ambient occlusion and are darker. The tell is the user saying
  "turning smooth lighting off fixes it".
- **An atlas tile is not a tiling texture.** Clamp UVs into the rectangle; do
  not wrap.
- Dedup emission on `(tessellator, position)` — the `Block*` is reused across
  blocks — and reset the table from a `begin()` hook or the GUI item icon
  disappears.
- **Never cull a protruding piece just because a neighbour exists.** A block
  model is a sparse shell, so the neighbour may have no geometry there; the
  result is holes. Ask which artefact the user prefers (completeness vs no
  flicker), and when a mechanism is rejected, delete it rather than re-tune it.
- **The bake step must be verbatim**, with area and UV-count assertions. Two
  quads can share geometry and still be distinct surfaces with different UVs.
- **A diagnostic filter that does not filter is worse than no log.** It buries
  the evidence it exists to collect. Budget by category, and log the negative
  result (reached the hook but was not classified) with its name.
- **Identify the build the device actually runs** from a run-time-discovered
  value, not from a file name; several game files may share a directory and the
  one you analysed may not be the one running.


