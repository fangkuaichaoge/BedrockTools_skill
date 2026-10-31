# Lessons Learned (experience notes)

Distilled from real "mod does not work" debugging sessions. These are
general principles, not version-specific answers.

For rendering / visual-effect work (outlines, tints, glow, material effects),
read `render-pipeline-hooks.md` as well — it covers the API-boundary hook
layer, shader-source injection, GL state hygiene, geometry rules for shell
effects, and rollback discipline.

## 0. Iron rules for working with the user

These outrank every technique below. They are also in `AGENTS.md` §0, which is
the canonical copy.

1. **When a decision is uncertain, ask the user first — before building.**
   Name the choice, the trade-off and your default, then ask what they want,
   whether they have advice, and whether they have supplementary material
   (a reference mod, a decompile, a screenshot, a spec). Ambiguity about
   *semantics* ("does this marker mean slow, or impossible?"), about *scope*
   ("should this also cover X?") and about *mechanism* ("the engine's renderer
   or my own draw call?") must be resolved by asking, not by picking one and
   shipping it. One question costs seconds; a wrong guess costs a build, a
   device test round and a rollback. Read the evidence first so you only ask
   about what is genuinely open.
2. **Before hunting evidence, work out what the feature relates to.**
   Start from the feature, not from a search: name the subsystem that owns the
   behavior — which screen, which renderer, which item or block class, which
   asset family — then find the engine function that already implements it and
   read *that* one. Verify the underlying path exists (data path, render path,
   input path) before building UI on top of it. A feature whose path does not
   exist in this engine is not fixable by trying harder, and learning that
   early is the point of the rule.
3. **When the user says they have a tool, skill or reference, use it.**
   A prepared script, skill, dump, IDA session, reference project or document
   they name is the intended input. Load and use it rather than re-deriving
   the same thing yourself; their material outranks your shortcut. If it
   proves unusable, say so with the concrete reason and ask — never silently
   substitute your own approach.

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

### From building a HUD preview mod

- **A silently rejected menu registration looks exactly like a broken menu.** The preloader
  requires `mod_id` to equal the mod *folder* name and only knows the owner while it is calling
  `load()`/`enable()` synchronously. See `mod-menu-registration.md`.
- **Do not validate pointers in the frame path.** A stale `/proc/self/maps` cache turns a safety
  check into "the feature stops drawing". Prefer tick-based lifetime rules; see
  `preview-panels-and-container-ui.md` §7.
- **Never write one actor's stacks into another to "fix" latency.** It produced both a
  use-after-free and disappearing item icons after a container screen re-synced the real container.
- **Container slot indices are per class and stable across patch builds; internal offsets are
  not.** Use the slots, verify them on every build, and prefer the direct array accessor over the
  forwarding stub: `container-and-slot-reversing.md`.
- **For names and language, ask the engine.** `ItemStackBase::getName()` and the localization
  object give both, in the player's language, with no table to maintain:
  `item-names-and-language.md`.
- **A backup directory is read-only reference.** When a known-good copy exists, diff against it and
  change only the working copy - never edit the backup to "keep them in sync".

### From an abandoned feature (worth knowing before starting one)

- **Check the input path before building the UI.** A control drawn in the game's UI space cannot be
  hit-tested against touch coordinates without the UI scale; a HUD button cannot be added at run time
  because the launcher snapshots its button list once. Both were discovered after the feature was
  built, and either one alone makes such a feature impossible as designed.
- **Give an unproven interaction two independent paths.** Here: a gesture plus a settings toggle that
  performs the same action on close. That would have separated "input never reached us" from "the
  action itself failed" in one test round instead of several.
- **Keep a restore point of the last known-good release.** Restoring it (and force-rebuilding, since
  the restored file is older than the object file) reproduced the shipped binary byte for byte,
  including its hash - which is how the rollback was proven rather than assumed.

### Container overlays: live data and icons

- **Two instances, one position.** A container can exist twice in one process (same position, same
  vtable, same write slot). The instance the crosshair resolves to is renderable but frozen; the
  instance that receives changes is live but its items cannot be drawn. Log both pointers before
  designing anything.
- **Records: deduplicate by instance, invalidate by destruction, never expire by short age, never
  clear on screen open/close.** A three-second window silently emptied the cache between opening and
  closing a chest; a per-position dedupe let the stale copy overwrite the live record.
- **One resolver for every detection path.** Crosshair, view cone and view ray returning different
  instances looks exactly like "two panels that alternate when I move the view".
- **The item renderer needs a client-side `Item`.** Learn identities by hooking the renderer the game
  itself uses (hotbar/inventory go through it), then draw a temporary stack: shallow copy + a private
  reference block pointing at the learned item. Writing the *shared* reference block in place is what
  crashes.
- **Item textures cannot be loaded by path** — they are in a packed atlas; the result is the
  missing-texture placeholder.
- **Icon source hygiene.** One global "drawable instance" gets overwritten by fallback
  detection paths; if identities are also learned from it, the name -> Item* cache is
  poisoned and icons vanish until the chest is opened again. Keep the drawable source per
  position, register it only from the crosshair path, and learn identities only from it and
  from the engine renderer hook.
- **Cleaning up "experimental" switches can delete a working path.** One experiment was the only
  thing drawing icons for tools; removing it with the others caused a new bug. Fold proven paths into
  the main code before deleting their toggles.
- **Force a rebuild after restoring an older source**, and prove a rollback by comparing hashes
  rather than assuming it.
### Container previews: input, icons and hardening rounds

- **Touch aiming breaks the engine hit result**; cast the camera ray yourself and keep the hit result
  as a secondary. Eye height ~1.62, direction from yaw/pitch, march to interaction range.
- **Icon overlay works**: flat items from the live-data instance, block items from the block-position
  instance, drawn in that order. Items neither holds are drawn from a captured per-item header + a
  private reference block; never re-read the reference block out of the copied header (dangling), and
  never fabricate block-shaped items.
- **Learn item identities only from stacks the renderer accepts** (renderer hook + drawable instance).
  Learning from the undrawable instance poisons the cache and items stop drawing permanently.
- **Never gate logic on an `isReadable`/maps probe.** False negatives silently disabled close
  detection (preview stuck hidden) and deleted live records (contents stopped updating).
- **Container-open gate**: use this frame's target, hide only when the close hooks are installed,
  count "already hooked" as success, and keep a timeout as a last resort.
- **High-frequency write hooks**: forward first, then record under a lock; no allocation/formatting.
- **Hardening rounds regress each other**: one mechanism per build, keep every known-good package,
  prove rollbacks by hash, and treat backup folders as immutable (never rename them on version bumps).

### Checkpoints

- **Checkpoint only versions that work.** One checkpoint per project (the current usable release),
  created after the user confirms it on device or a release package is produced. Do not checkpoint
  intermediate or failed builds.
- When a newer version becomes usable, create its checkpoint, verify it, then delete the superseded
  one - the workspace should never hold a pile of "maybe" folders.
- Backups stay immutable once written; if one is wrong, write the corrected folder and delete the old.
- Prove a rollback by hash (rebuild, compare with the checkpoint package).
- See `references/version-checkpoints-and-change-notes.md`.

- **The moment a fix is confirmed or a feature is accepted, offer a checkpoint** and write a change note inside it.
- Backups are immutable: new folder per checkpoint, never rename or overwrite. Renaming an old backup on a version bump lost a snapshot that had to be rebuilt from a script.
- Prove a rollback by hash (rebuild, compare with the checkpoint's package) and force a rebuild after restoring older files.
- See `references/version-checkpoints-and-change-notes.md`.

### Waila-style overlays: icons, names and staying alive

- **Queued vs immediate drawing.** Panel rectangles/images are queued and appear on flush; the
  engine item renderer draws immediately. Flush the panel first, then draw the icon, then the text.
  The other order looks like "the icon is invisible" (the panel is painted over it).
- **Prefer the engine's own entry point over hand-built setup.** The inner item-renderer call is
  not the public path: the game wraps it in a helper that prepares the stack, builds the render
  context, draws, flushes and tears down. Hand-building that context reproduces the icon for flat
  items but leaves a transparent notch in block cubes, and depth-state experiments (disabling the
  depth test, clearing the depth buffer, mask juggling) only move the artefact around. If a feature
  has one obvious owner function, call it.
- **Item icons: two classic crashes and one artefact.** An empty stack handed to the renderer
  crashes the game (validate stack object, item holder and valid flag first). A new stack instance
  every frame replays the "new item" bounce animation (cache per item id). A hand-built render
  context produces the cube notch above. Symptom -> cause, in one line each.
- **Class names are not identity.** Many mobs share a base-class RTTI name, so name keys derived
  from the class name are wrong yet plausible (and sometimes even resolve). Use the engine's own
  identity data: actor -> definition identifier -> canonical identifier string -> localized name
  through the game's translation table. Treat a translation equal to the key as "missing" and fall
  back; cache per actor.
- **"Do everything" engine helpers can be untouchable from a mod.** A single function that goes
  identifier -> key -> localized string looks perfect, but its out parameter can be a cache record
  larger than a string and it can take an options struct with hidden state; calling it with plain
  buffers crashed. Read its callers for sizes and offsets, then prefer composing verified
  primitives.
- **Addon blocks carry a namespace in their description id.** Keep it; forcing the vanilla
  namespace breaks the id. For display, the inventory item name is the reliable source (same name,
  same language), with the identifier tail as the last resort - never feed an empty stack to the
  renderer.
- **Config: load before registering the menu.** The mod builds with unwind tables disabled, so any
  throwing call (filesystem, string-to-number) can terminate the process. Use C-level file IO,
  atomic write (temp + rename), and capture the config directory from the lifecycle callback.
- **Overlay crash-safety gates.** Player ticks begin while the world is still loading: gate on a
  fresh tick, a valid block source, a short settle after the first tick, and a probe of the block
  under the player (air/placeholder means "not ready"). Guard heights before block lookups (the
  block source asserts out of range, and a stale crosshair result can carry a bad position).
  Hooks stay fail-open.
- **Reading engine memory needs its own guard rails.** The process-map snapshot goes stale within
  seconds (refresh it in the background), pointers may carry a tag in their top bits (normalize
  before comparing addresses even though dereferencing works), and the guard must cover the whole
  accessed window, not its first address - a pointed-to window crossing a mapping boundary is a
  reliable crash.
- **No I/O on the render thread.** Diagnostics go into a small snapshot; a background thread writes
  the file. Log the build banner, signature resolution results and one line per target change;
  remove the file log for release.
- **A render-path fix needs evidence.** Acceptable: unique signature verification, caller
  disassembly that shows argument types, a decompiled body, a runtime log line showing the first
  wrong value, or an A/B test where only the suspected code changed. Otherwise it is a hypothesis:
  change one variable, test, and keep a known-good backup plus a failed-attempt backup.

### 1.26.60 layout shifts

- **A base-class growth moves every derived field.** Confirm one offset from the game's own
  disassembly (e.g. Actor::mDimension 0x1C0 -> 0x1E0), then assume sibling fields moved too:
  Actor components +0x20, Player inventory proxy 0x570 -> 0x590, LevelRendererPlayer -8.
- **Mine player-side offsets from call sites**: the instruction right before the call to your
  target usually loads `this` (`ldr xN,[player,#imm]`) or `this`'s field. That is stronger
  evidence than a getter that merely happens to use the same immediate.
- **HitResult +44 is a Vec3**, not the block position; the block position stays at +32 in this
  family. Changing a working +32 to +44 breaks target detection silently.
- **`add x0,x0,#imm; ret` accessors are a cheap offset oracle**: enumerate them in both builds
  and diff the immediate sets to see exactly which fields moved and by how much.
- Use `rtti_vtables.py` (relocation image + RTTI vtables) and `align_vtables.py` (slot
  alignment) before guessing at vtable layout; file-level pointer scans do not work because
  `.data.rel.ro` is zero-filled and filled in by `.rela.dyn` at load time.

## Coexistence and crash safety (panel mod + a third-party map mod)

* "My feature disappears when mod X is installed" is usually the **signature layer**, not the hook
  layer: the preloader's `hook()` is a chain manager, so two mods on one target is supported. Prove
  it with a detour call heartbeat plus a per-pattern resolution log.
* An SDK RTTI/vtable helper can return 0 for **every** class after a launcher update. Anything on
  the critical path (font capture, draw context, identifiers) must have a fallback that does not
  depend on it, and its result must be logged per class once.
* Never hand an address you have not verified to `pl::memory::hook()`. A detour argument that
  *should* be the render context can be junk when a competing detour forwards only `x0`; reading its
  first word as a "vtable" and hooking it patches non-code memory and kills the process instantly.
  Identify objects by a fixed vtable address point, and check the target is inside the game image.
* A cached `/proc/self/maps` snapshot goes stale: refresh on a miss (rate limited). Objects created
  when a world loads can live in mappings that did not exist when the snapshot was taken, and a
  stale "unreadable" verdict silently disables the whole feature (worst kind of bug: no crash, no
  draw, no log).
* Installing hooks late is itself a crash risk: with per-pattern scanning, ~30 patterns cost ~20 s,
  so the hooks were patched while the menu was already drawing through those functions. Resolve
  everything in one pass (or from a byte-verified vaddr table) and install immediately.
* Keep the tested binary, ship the cleaned one only after re-testing, and never ship the diagnostic
  logging. Post-mortem: `references/coexistence-and-crash-safety.md`.
