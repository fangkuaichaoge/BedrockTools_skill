# Container preview: live contents and item icons

Field notes from making a chest-preview overlay show *current* contents with *correct item icons*.
Both problems look like rendering bugs and are not: they come from there being two instances of the
same container in one process, and from the engine's item renderer requiring a client-side item
object. Everything below is anchored to evidence (logs, disassembly), because the obvious fixes each
break the other half of the feature.

Context: a native preloader mod draws a panel over the world showing the contents of the chest the
crosshair points at.

---

## 1. There are two instances of the same container

Observed with a debug build that logged every container write and every frame's drawn contents:

```
setItem: actor=0x...4db0 pos=120,64,-8 slot=0..16     <- instance A receives all changes
setItem: actor=0x...61f0 pos=120,64,-8 slot=0..16     <- instance B received one sync, then nothing
draw   : actor=0x...61f0 pos=120,64,-8 filled=16 hash=258351e2   <- hash never changes
```

Both instances report the **same block position**, have the **same vtable**, and both accept writes
through the same vtable slot. Only one of them keeps receiving changes.

Consequences, and why each "obvious" fix fails:

| Approach | Result |
|---|---|
| Read the instance the crosshair resolves to | correct item icons, contents frozen at the first sync |
| Read the instance that receives writes | live contents, item icons missing |
| Copy contents from the writing instance into the resolved one | live contents **and** icons missing (the copied stacks carry the other side's `Item`) |

So the data source and the icon source are different objects, and neither can supply both.

### Rules that follow

- **Identify instances by pointer, never by position.** A per-position cache entry gets overwritten
  by whichever instance wrote last, so the "freshest" lookup can end up pointing at the stale copy.
- **Deduplicate records by instance**; keep several records for one position.
- **Invalidate records when the actor is destroyed**, not by age. A three-second freshness window
  discarded every record before the player finished opening and closing a chest (`matched=0` in the
  log, immediately after the close).
- **Do not clear the record table when a container screen opens or closes.** Older code did that to
  avoid stale snapshots; with a read-only consumer it deletes exactly the data needed the moment the
  preview becomes visible again.
- **Resolve every detection path through one function.** Crosshair, view cone and view ray each
  returned a different instance, so a small mouse/touch movement swapped the panel between live and
  stale data — it looked like two alternating panels. A resolver that maps any candidate to the
  instance that receives writes for that position (exact position first, then a one-block tolerance)
  removes the flicker.

Threading: take the same lock the destruction hook takes **before** forgetting records, so a lookup
can never read a record whose actor is already freed.

---

## 2. The engine item renderer needs a client-side `Item`

`ItemRenderer::renderGuiItemNew(renderContext, stack, ...)` only draws when the stack's item belongs
to the client side. Stacks copied from the other instance keep the other side's `Item*`, so the
renderer draws nothing. This explains the very specific symptom *block icons fine, item icons gone*:
block-shaped items go through the block path, which does not need the client `Item`.

### Layout facts established by disassembly

`ItemStackBase::getName` is a good place to read the layout from:

```
ldr x9, [x0, #0x8]      ; stack + 0x8  -> reference block(s)
ldr x0, [x9]            ; *(ref block) -> Item*
[item + 0x2f0](item, stack)   ; sret std::string: the description id, e.g. "item.apple.name"
[item + 0x308](item, stack)   ; used first: custom-name check
```

- `ItemStack` is **152 bytes** (`0x98`); container arrays stride by exactly that.
- `stack + 0x8` points at a **shared** reference block; `*(that block)` is the `Item*`.
- Therefore writing `*refBlock = clientItem` mutates a **shared** object. That corrupts or crashes,
  and it is why the first "borrow a stack and swap its item" attempt crashed.
- `Item` vtable slot **`0x2f0`** returns the description id; it is the key the name function hands
  to the translator, so it is not a guess.

### What actually works

Learn client item identities from the engine itself:

- Hook `ItemRenderer::renderGuiItemNew`.
- Every item the game draws — hotbar, inventory, container screens — arrives with a client-side
  `Item`, exactly the identity that cannot be obtained for a freshly placed item any other way.
- Record `display name -> Item*`, deduplicated by `Item*` so each name is resolved once. The hook
  must forward through the *saved original*; the overlay itself calls that original so its drawing
  never depends on the hook.

Render an item the drawable instance has never held:

1. Look for a stack of the same item anywhere in the drawable instance; if found, draw it (the icon
   for an item type is the same wherever it is drawn from).
2. Otherwise copy a drawable stack (`memcpy` `0x98` bytes), point the copy's `+0x8` at a **private**
   0x40-byte block holding the learned `Item*`, draw the copy, and never destroy it. Only local
   memory is written; nothing shared is touched.
3. If the item has never been seen client-side, draw the live stack and accept a missing icon. It
   appears after the item has been rendered once by the game.

### Dead end: loading item textures by path

`uiGetTexture(context, ResourceLocation("textures/items/<name>"), false)` plus `uiDrawImage` fails:
item icons live in a **packed atlas**, not as loose files. The result is the missing-texture
(purple/black) placeholder — worse than no icon. Keep icons on the engine renderer.

---

## 3. Container screen open/close detection

Needed only to hide the overlay while the container UI is up.

- Long byte patterns for the screen open/destroy functions may resolve on one build and be absent on
  another; a hook that never installs silently disables the feature. Check `installNamed`-style return
  values or log which hooks installed.
- A short controller-open pattern plus runtime destructor hooks on the controller's vtable works where
  the long patterns do not — but see the crash below.
- **Only arm the destructor hooks for the class you actually care about.** Hooking `vtable[0]`/`[1]`
  of an arbitrary object and forwarding with a plain `void(*)(void*)` crashed when a *non-chest*
  container screen closed: the pointer handed over was an adjusted sub-object, so those slots were a
  base class destructor with different meaning. Gate the detector on "we were previewing a chest when
  the screen opened", so other containers are never touched.
- Keep a timeout as a last resort: if a close is ever missed, clear the "screen open" flag after a
  while instead of leaving the overlay hidden for the rest of the session.

---

## 4. Process notes from this port

- **A file logger turned a multi-round guessing game into two rounds.** What mattered: hook install
  results with addresses, every write with actor pointer and position, per-frame drawn actor plus a
  cheap content hash, screen open/close events, and the lookup result (`matched`, chosen pointer,
  whether it differs from the resolved one). Remove the logger for release; verify it is gone from
  the binary strings.
- **A cheap content hash** (`(hash ^ count * slot) * prime` over slots) is enough to prove whether
  what is drawn changes; logging item names overflowed a fixed buffer once the names were localized
  (a `snprintf` remaining-size underflow crashed the build — size arithmetic on user-visible strings
  deserves `std::string`).
- **Do not delete a working path while cleaning up "experimental" features.** Removing the
  scratch-stack icon renderer with the other experiments brought back "tools have no icons". Move such
  a path into the always-on code before deleting its toggle.
- **Back up with hashes.** Restoring the previous known-good source produced a byte-identical `.so`
  and package hash, which is what proved the rollback; and after restoring an older file, force a
  rebuild — the build system sees the target as newer and skips compilation otherwise.
- Report only facts to the tester: which lines of the log to read, and what each outcome means. Ask
  for a log rather than another build whenever the question can be answered from the log.

## 5. Icon source hygiene

A single global "instance I can draw from" is not enough. Every detection path runs each frame, so a
fallback path (view cone / view ray) can overwrite it with the instance whose items cannot be drawn —
and the icon code, which also *learns* item identities from whatever it looks at, then poisons the
shared name -> `Item*` cache with unusable identities. Symptom: after looking at another chest, icons
vanish; opening that chest again runs the crosshair path, re-learns correct identities and the icons
come back.

Rules that fixed it:

- keep one drawable source **per chest position**, not one global;
- register that source **only when the crosshair resolves the chest** — never from a fallback path;
- learn item identities only from (a) the engine renderer hook and (b) registered drawable sources;
- remove an entry when its actor is destroyed, and clear the whole table on save teardown, so a
  freed pointer can never be treated as a drawable source.

## 6. Touch aiming: prefer a camera ray over the engine hit result

While the player drags the camera with touch, the engine's hit result lags or points elsewhere, so a
preview keyed on it flickers or targets the wrong block. A ray cast from the camera is stable:

- eye position = player state vector + ~1.62 blocks;
- direction from the rotation component (yaw/pitch, degrees) with the usual Bedrock convention
  `(-sin(yaw)cos(pitch), -sin(pitch), cos(yaw)cos(pitch))`;
- march in small steps to the interaction range and take the first block position whose block actor is
  the container type you want;
- use the engine hit result only as a secondary, when the ray finds nothing (slabs, corners).

A few lateral offsets around the center ray reduce "almost on the edge" misses. Keep the offset count
small: each one multiplies the marches per frame.

## 7. Icon recipe that actually holds up

Two instances render different item categories reliably: the instance at the block position draws
block-shaped items, the instance that carries live data draws flat items. Draw both, in that order, at
the same position:

1. scan the flat-item source for a stack of the same item name, draw it;
2. scan the block-item source the same way, draw it on top;
3. if neither holds the item, build a temporary stack (below);
4. otherwise draw the live stack and accept a blank icon.

Drawing exactly once per source is fine; the failure mode people report as "two icons" comes from a
frame that draws the same category twice (for example drawing a per-item list twice), not from this
overlay.

### Temporary stack for an item neither instance holds yet

The reliable base is a **header captured from a stack the engine itself rendered for that item**:

- while hooking the item renderer, remember `name -> {0x30-byte stack header, Item*}`;
- to draw, copy that header into a zeroed 0x98-byte buffer, point its `+0x8` at a **private** 0x40-byte
  reference block holding the remembered `Item*`, set the count to 1, and render it;
- take the `Item*` from the captured value, **never** by re-reading the reference block pointer inside
  the copied header: that pointer refers to the original stack's block, which may already be freed;
- never fabricate block-shaped items; their renderer reads block data that a copied header cannot
  provide.

## 8. Identity learning hygiene

Identities (`name -> Item*`) must come only from sources the renderer accepts:

- the engine item renderer hook (everything the game draws: hotbar, inventory, container screens);
- the drawable instance at the chest's block position.

Learning from the live-data instance writes identities the renderer cannot use, and once such a bad
entry exists the same item keeps failing to draw even after good stacks appear. Prefer "first accepted
header wins" over "last write wins" for templates.

## 9. `isReadable` is not a gate

A probe based on parsing `/proc/self/maps` reports **false negatives** for valid pointers when its
cache is stale. Used as a guard it silently disables features:

- guarding the container-controller destructor hook with it meant the close was never observed, so the
  preview stayed hidden for the rest of the session;
- pruning container records by it deleted live data on every screen close, so contents stopped
  updating.

Rule: use accurate signals (destructor hooks) for lifetime, and never let a readability probe decide
whether a feature engages. At most use it to skip a pointer you are about to dereference **when a
missed dereference is optional**, and never in a way that changes user-visible state permanently.

## 10. Container screen gate

- Decide "is this a chest screen" from **the current frame's detection target**, not from a remembered
  position: re-resolving by the last aimed position can match a chest while a hopper screen is being
  opened, which installs destructor hooks on the wrong controller and crashes on close.
- Hide the preview only when the close is observable (the controller's destructor slots were actually
  hooked). If installation fails, leave the preview visible rather than risk it never coming back.
- Already-hooked counts as success, or the second open of the same chest class will not hide anything.
- Keep a timeout as a last resort: if a close is somehow missed, clear the hidden state instead of
  staying hidden for the session.

## 11. Hooks on high-frequency write paths

A container set-item hook is called by every automated transfer (a hopper above a chest, for example),
often from another thread. Inside it: forward to the original first, then record under the lock.
Nothing that allocates, formats strings, or dereferences pointers the caller does not guarantee - those
belong in the render path, not here.

## 12. Process: hardening rounds create regressions

Each round that added crash protection here fixed one crash and introduced a different regression
(stuck hidden preview, contents not updating, icons lost). Practical rules learned:

- change one mechanism per build and keep the previous package;
- prove a rollback by rebuilding and comparing hashes with the known-good package;
- **never rename or overwrite an old backup when the version number changes** - one fine-grained
  snapshot was lost that way and the release had to be reconstructed from a scripted recipe. Backups
  are immutable; create new folders.
