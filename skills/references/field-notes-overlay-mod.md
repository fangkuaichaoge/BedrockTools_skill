# Field notes: building a look-at info overlay mod (WAILA/Jade style)

Lessons from building a native HUD overlay mod for Minecraft Bedrock on Android (arm64, preloader
SDK, launcher-loaded). The mod shows what the crosshair points at: the block or entity name,
states, contents, health, effects, and icons. It has to run next to other hooking mods.

This document is deliberately **generic**. It names no game functions, addresses, offsets, vtable
slots or mod-internal symbols. It records *how* each problem was found and solved, so the method
carries over to other builds and other mods. Where a concrete example helps, it is described by
role ("the per-frame player update", "the UI text draw method"), not by name.

---

## 1. Ground rules that paid for themselves

1. **A crash is worse than a missing feature.** Every new read or call is gated so that a wrong
   assumption makes a line disappear, never the game. "Fail open" means: return early, draw
   nothing, keep the previous value.
2. **Nothing unverified reaches a call or a hook.** An address must be inside the game image (ask
   the dynamic linker, not a guess) *and* match a byte pattern. An object must be identified by its
   vtable address point before you read its fields or call its virtuals.
3. **One change per build, and bump the version every build.** When two things change at once and
   the result is "it works" or "it broke", you cannot say which one did it. The single exception
   is pure observation (diagnostic counters), which can ride along because it changes no behavior.
4. **Ask before building when a choice is genuinely open.** Scope ("should this also cover X?"),
   semantics ("is this level 1-based?"), mechanism ("engine renderer or my own draw?"), and
   presentation ("bigger box or scaled resolution?"). A question costs seconds; a wrong guess
   costs a build, a device test and a rollback. Do not ask what the evidence already answers.
5. **Use the user's prepared material.** A reference mod, a dump or a screenshot from the user
   outranks re-deriving the same thing.
6. **Docs are machine-agnostic.** Use placeholders for paths and keep build-specific numbers in
   the analysis files, not in the methodology.

---

## 2. From feature to engine path (before searching anything)

The fastest reverse-engineering sessions started with one question: **which subsystem already
does this, and where does the game itself do it?**

| Feature | The subsystem that already owns it | What to read |
|---|---|---|
| Block / entity name in the player's language | the item / entity naming and the localization singleton | the inventory tooltip's name path |
| Item icon in the HUD | the engine's GUI item renderer, as used by the hotbar | the hotbar draw call and its arguments |
| A 3D mob in a UI panel | the engine's own UI actor renderers (paper doll, mount screen, portraits) | their render method, copied call for call |
| Crop growth, redstone power, door open, ... | the packed block state and its per-block-type layout | a block's own visual-shape or tick code that reads its state |
| Furnace / brewing progress | the block actor's tick | the tick body and the save/load (NBT) code |
| Container contents | the block actor's container interface | the save() and load() methods and their NBT keys |
| Effects, armor, attributes | ECS components and the attribute map | the HUD renderers that already draw them |
| Villager level text | the trade screen | how it builds its label key |

If the engine never does it on the client, check whether the data even exists there **before**
writing UI. Several features turned out to be impossible on the client: a growth countdown, a
breeding cooldown, and minecart contents before the container is opened. Finding that out early
is the whole point of this step.

---

## 3. Locating functions in a stripped binary

There are almost no symbols. Locating something is a matter of choosing anchors. The anchors that
worked, roughly from most to least reliable:

1. **RTTI type-name strings → typeinfo → vtable.** For any polymorphic class you get its vtable,
   and with it every virtual. The vtable's *address point* (the vptr value objects carry) is the
   identity check you will use at runtime.
2. **NBT key strings in save()/load().** Strings like "Items", "page" or "sherds" sit right next
   to the field they serialize. The load function shows the field offset, its type and the
   container layout. This was the most productive anchor for block-actor data.
3. **Localization keys and asset paths.** UI code builds keys with string concatenation, such as
   a prefix plus a level number, or a texture folder plus a name plus a suffix. Search for the
   prefix, then read the code that appends to it.
4. **The engine's own consumer of the data.** To read X, find the HUD or screen that already draws
   X and read how it gets X. The HUD armor bar, the effect list, the trade screen and the paper
   doll all handed over their data paths this way.
5. **Static initializers.** Registries (block states, attributes, ECS component types) are often
   built at static-init time from string literals. Reading the initializer shows the object
   layout and the real lookup key. See §5.3 for why that mattered.
6. **Cross-references from a known function.** Once one function in a subsystem is known, its
   callers and callees map out the rest.

Practical notes:
- Building IDA's full string list on a 300 MB binary times out an RPC call. Find the string's file
  offset with a plain byte search in Python, then ask IDA for the cross-references to that
  address.
- Many references are `ADRP` + `ADD`/`LDR` pairs, and only one half shows up as a data reference.
  When looking for *writers* of a global, filter the cross-references for store instructions
  (`STR`, `STUR`, `STP`).
- Keep scratch scripts and the raw decompiles next to the analysis document, so a later session
  can re-run them.

---

## 4. Proving a function (the evidence ladder)

An address is not usable just because a decompile "looks right". The bar used here:

1. **It is code inside the game image.** At runtime, check it with the linker (`dladdr` /
   program headers), never with "the address looks plausible".
2. **The bytes match.** A prologue pattern checks the start of the function. A second **deep
   pattern** checks a window well inside the body. Another mod's inline hook rewrites the
   prologue; the body survives it. Both patterns must be unique in the image.
3. **The vtable slot agrees.** For virtuals, read the slot at the class's address point and
   require that it holds exactly the expected function. Accept a runtime RTTI lookup only when it
   agrees with that table. In this project the SDK's RTTI resolver returned 0 on the current
   runtime, so a table read out of the relocations was the real source.
4. **The ABI is understood.**
   - Return conventions: a struct with a non-trivial destructor is returned through the hidden
     pointer register. Declaring it trivially gives you corrupted registers instead.
   - Hidden `this`, sret, and `std::optional` / float arguments.
   - Which thread calls it, and whether it re-enters.
5. **Its failure modes are known.** Several engine helpers end in `std::terminate` when an ECS
   component is missing. Find those branches and pre-check the condition yourself. Calling them
   blind on an entity that lacks the component kills the process with no log.

Before shipping, verify every address on the actual target binary with a script. Ten out of ten
vtable entries matching is a two-second check that rules out a whole class of "the feature just
silently does nothing" bugs.

**Keep the binaries straight.** Several game builds sat in the same folder. One session's
analysis was run against a different build than the one the user plays, and the patterns looked
"broken" until the right file was used. Name the build every analysis document targets, and
confirm on the device which build is running.

---

## 5. Reading engine data safely

### 5.1 General rules
- Guard every pointer dereference with a readable-memory check. The snapshot of readable regions
  must **refresh on a miss**, rate limited: new heap arenas appear after startup, so a check
  against a stale snapshot rejects valid objects.
- Strip pointer tags before comparing or reading.
- Bound every loop: vector length, tree depth, list length, string length (for libc++ short-string
  vs heap layout, check the heap data pointer too).
- Validate ranges semantically (an effect id within the known range, a level within 1..5) and
  drop values that are out of range instead of clamping them into something plausible.

### 5.2 Server copy vs client copy
In a local world, the client and the integrated server each have their own copy of many objects.
- **Which copy is live?** A tick hook sees both. Filter on a flag that marks the server-side
  region, and snapshot only the copy that actually advances.
- **What reaches the client?** A block actor's "update packet" method decides what is sent. Read
  it. Some classes send their full save data, some send only a name, and some send nothing.
  That tells you, before any device test, which features will work on a remote server.
- Design for both: show what you can get, and hide the line when you can't. Never show a stale
  or default value as if it were real.
- Snapshot pattern for cross-thread data: the tick thread writes under a mutex with a timestamp;
  the render thread copies it only if it is younger than about a second, and only for the
  position currently watched.

### 5.3 The registry key trap (block states)
The first block-state feature worked for two states and silently showed nothing for twenty new
ones. The lookup had assumed the per-block state map was keyed by a **hash of the state name**,
and that assumption held for the first two only by accident: those two used the engine's own key
globals.

Reading the static initializer showed the truth. Each state object registers itself into a global
intrusive list, and it carries:
- a small registration id, which is the actual map key;
- the name hash;
- the name itself.

The fix was to walk that list once, build a `name hash → id` table, and **trust the table only if
it reproduces the id of one state whose id is known independently**. If that self-check fails,
the table stays empty and the feature stays off.

Lessons:
- When a lookup works for some keys and not others, suspect the key scheme before suspecting the
  data.
- A table you build at runtime needs a self-check against a known value.
- Before the first build, check every state name against the binary's string table: a typo costs
  a device round trip.

### 5.4 ECS components
- The component type hash turned out to be a standard hash of the component's type name. Mapping
  all hashes to names from the strings in the binary turns unknown lookups into readable ones.
- Replay the engine's own lookup (entity context → registry → sparse page → dense element), with
  every step bounds-checked. Return null on any mismatch.
- Missing component = feature off for that entity. That is normal: not every mob has every
  component.

### 5.5 Synched actor data
Many entity properties reach the client as a synched data item array indexed by id, where each
item has a type tag. Check the type tag before reading the value. Map ids by finding the getter
the engine itself uses for that property, not from Java-edition memory.

---

## 6. Hooking

### 6.1 Mechanism choice
- **Inline hook** for functions every frame or tick passes through (the screen render, the player
  update, UI text draw). Always call the original. Do your work after it unless you must change
  its inputs.
- **Call only** for anything you merely need to invoke (name lookups, item construction, the icon
  renderer). Calling is far less invasive than hooking.
- **Never write vtables** when you only need to observe. Read the slot, verify it, inline-hook
  the target if needed.
- Do not hook `dlopen` to detect the game library. Poll the linker's own module list from a
  watcher thread, then wait a settle delay before installing. Polling with `dlopen(RTLD_NOLOAD)`
  passes through other mods' `dlopen` detours and can trigger their re-install logic.

### 6.2 The preloader's hook chain
- Every mod's detour on one target lives in one chain, sorted by priority. Each detour's
  "original" pointer is rewritten whenever the chain changes.
- Duplicate registrations are not deduplicated. If one mod registers the same detour twice with
  the same original slot, the chain rebuild overwrites that slot and skips everything between the
  two entries. Registering at a higher priority, so you run first, keeps you out of that gap.
- Both mods share one chain only if they resolve the hook API from the **same** preloader
  library. The loaded-libraries list in a diagnostic dump settles that in one look.

### 6.3 Detour hygiene
- Make detours cheap: an atomic store, a pointer check. Do the heavy work in the frame draw.
- Never trust arguments for identity. A competing detour may forward only some registers. Accept
  a "render context" argument only if its vtable is the render context's address point; otherwise
  use the one you captured elsewhere.
- Capture shared engine objects (the font, the UI context) from the call where they are certain,
  and check they are readable before keeping them.

---

## 7. Coexistence: the full debugging story, generalized

Symptom: with another large mod loaded, the overlay never draws. Alone, it works.

### 7.1 What did not work: fixing plausible causes blind
Four careful, separate builds each removed one plausible mechanism:
- a higher hook priority;
- no `dlopen` polling;
- no unconditional signature scan at startup;
- strict vtable-slot verification.

Each was justified by static analysis of both mods. None fixed it. They were not wasted, because
each removed a real hazard, but they showed that **static reasoning had run out**.

### 7.2 What worked: an observe-only diagnostic build
One build that changes no behavior and writes a small text file into the mod's config folder
every few seconds. The file holds:
- **A call counter in every detour.** It shows which hook is actually being called.
- **A counter on every early-return gate** of the draw function. It shows exactly why a frame
  drew nothing.
- **Each hook's install result plus the live first bytes of the target, read at write time.** It
  shows whether a hook went in, and whether someone else patched over it later.
- **The loaded libraries.** It shows whether both mods share one preloader.
- **The self-check result of any runtime-built table.**

The user ran it twice, alone and with the other mod, and sent both files. The diff was immediate:
- three hooks reported "not installed" (their target had resolved to 0);
- the player-update counter was zero;
- every frame stopped at the "no recent player update" gate.

The only thing those three hooks had in common was that the other mod hooks the same functions
first.

### 7.3 Root cause
Because the other mod had already patched those prologues, the prologue check failed, which was
expected. The deep body-pattern fallback existed for exactly this case. It **had never worked**:
- each deep pattern was written as several adjacent string literals;
- with no space at the joins, the compiler glued the last token of one line to the first token of
  the next (`"?? ??" "?? 00"` becomes `"?? ???? 00"`);
- the tokenizer rejected the malformed token, so the whole pattern failed to parse and the
  fallback returned nothing.

The offline checker that "proved" the fallback worked had used its **own** tokenizer, which was
more forgiving than the one in the mod.

### 7.4 Lessons
- **Count before you change.** A one-page diagnostic file from the device beat four rounds of
  reasoning.
- **A fallback that is never exercised is broken until proven otherwise.** Test it by simulating
  the condition it exists for: overwrite 4, 16, 20, 24 and 64 bytes of the head, then re-run the
  exact check.
- **Verify with a replica of the shipped code, not a lookalike.** The offline check must use the
  same parsing and matching rules as the C++.
- **Multi-line byte patterns need an explicit separator at every join.** Better still, make the
  parser split long hex tokens or reject them loudly in a host-side test.
- Keep the diagnostic build until the fix is confirmed on device, then remove it in its own
  build.

### 7.5 A coexistence decision tree
1. Is my detour being called? (counter) If not, did the install fail, or did someone cut me off
   the chain? (install result, head bytes, chain order)
2. Install failed: which check rejected the target? (prologue patched → the deep pattern must
   carry the fallback)
3. Detour called but nothing drawn: which gate stops the frame? (gate counters)
4. Two hook implementations in the process? (library list)

---

## 8. Rendering in the HUD

### 8.1 Engine item icons
- Draw icons through the engine's own GUI item renderer, the same call the hotbar uses. Blocks,
  items and addon content then come out right automatically.
- **Depth/layer argument.** A visual artifact, a triangle cut out of block icons, came from
  drawing at the lowest layer, where the cube's near corner was clipped by the depth range. The
  investigation added a temporary slider for the layer value. The user tested values on the
  device, one value fixed it, and it was then hard-coded and the slider removed. A temporary
  tuning control is a good way to settle a single numeric unknown in one device session.
- The record passed to the renderer has a fixed size and alignment. Allocate it exactly, and
  zero it.

### 8.2 A live 3D mob in the panel
- Do not invent a render path. Copy the call sequence of the engine's own UI actor renderer (the
  HUD paper doll):
  1. build the render context;
  2. take the dispatcher from it, and skip if it is null;
  3. push the matrix stack and multiply in a fit transform;
  4. call the dispatcher's render;
  5. pop the matrix stack;
  6. destroy the context.
- Use only the actor pointer resolved and validated in the same frame.
- Pre-check every condition that would make the dispatcher terminate (required components, a
  non-null bounding box, a non-null rotation). Do not call optional setters, such as a rotation
  override, that terminate on a missing component.
- **Size:** fit the model into a fixed box, scaling it by its bounding box (height and the
  footprint after yaw). Change the model's size with the fit transform. Do not render to a
  texture at a different resolution and rescale it. (The user asked explicitly for "change the
  size, don't scale the resolution".)
- Write down which points can only be settled on the device, before the first test: vertical
  origin, depth range for large mobs, whether a name tag is drawn as a side effect.
- **The overlay hook can run several times per frame.** A hook after the ScreenView render fires
  once per screen view rendered, not once per frame. Text and images do not care, but a 3D model
  does. A second pass filled the model's box again, then drew the model again at exactly the same
  depth, and that draw failed the depth test against the first one, so the box showed only the
  fill. Opaque mobs vanished while a mob with a different material kept showing, and the bug
  appeared only when something was drawn under the model. Fix: draw the model and anything under
  it only in the first pass of a frame, detected as a pass that starts a few milliseconds after
  the previous pass ended. Later passes leave a hole in the panel over the model.
- **Diagnosing it.** Three plausible theories (mesh batching, depth scaling, the projection's
  depth range) each cost a build and none explained why one mob type kept working. The decisive
  clue came from a temporary depth slider: ghosts appeared only while the slider was dragged
  fast, and the value did not matter. A symptom that depends on change over time rather than on
  a value points to multiple passes or frame boundaries. Before building a theory, check that it
  explains every observation, including the cases that work.
- Draw the model inside the panel by giving the nine-slice frame an optional hole: draw the
  centre piece as four bands around the hole, with proportional UVs.

### 8.3 Layout
- Keep a list of "main" lines and a list of "tail" lines drawn after icon rows (hearts, armor), so
  new features add lines without touching the layout math.
- Every feature checks its own toggle at both fill time and draw time.
- Cap list lengths (effects, state lines) so a pathological target cannot grow the panel off
  screen.

---

## 9. Localization

- Names always come from the engine (the item name accessor, the localization singleton with the
  game's own keys), never from a table in the mod. Every language, and addon content, then works.
- Before relying on a key, check that the key string or its prefix exists in the binary. Some
  keys live only in resource-pack `.lang` files and may not exist for a given entity.
- The mod's own few labels can carry a small zh/en table keyed by the detected game language.
- Fall back in a fixed order: the engine translation, then the key's last segment, then the raw
  identifier. Never show an empty string.

---

## 10. Working with parallel analysis (subagents)

- Split reverse engineering by subsystem (block actors, entity data, rendering) and run the
  analyses in parallel while the main thread builds and ships the independent fixes.
- Each analysis prompt contains: the existing layout documents to read first, the tool usage
  details, the exact questions, the required output file, and "do not edit source".
- Require evidence for every claim (an address plus the decompile line) and a confidence level.
  Require an explicit list of unknowns. A "client-side?" answer per item was the most valuable
  single field.
- Two analyses sharing one disassembler session must keep their calls short. Long calls such as
  building the string list time out.
- Re-verify the critical claims yourself before building on them, especially "this slot is X" and
  "this field is synced".

---

## 11. Build and release discipline

- Bump the version on every build, and keep every package. Testers can then step back one version
  and bisect on the device.
- The changelog entry states what changed, why, what evidence supports it, and what the tester
  should see if it works.
- Back up the whole mod folder before a large feature series, and never modify a backup.
- Re-run the build-system configure step whenever a source file is added.
- In shell scripts on Windows, avoid one-letter helper names: `R`, `h`, `r` and others are
  built-in aliases, and a "replace" helper named `R` silently ran the history command instead,
  producing a package identical to the previous one. Use explicit names, or the editor tool.
- Toolchain traps: `-fno-unwind-tables` means any throwing call (`std::stoi`, `std::filesystem`)
  terminates the process. Use stdio and `strtof`. In C++20, `requires` is a keyword.
- Diagnostic code ships only in diagnostic builds and is removed in its own build afterwards.

---

## 12. Checklists

**Before calling a new engine function**
- [ ] It is inside the game image (linker check at runtime).
- [ ] The prologue pattern and the deep pattern both match, and both are unique.
- [ ] For a virtual, the vtable slot at the verified address point equals it.
- [ ] The ABI is settled: arguments, sret, the struct's destructor triviality.
- [ ] Every terminate branch it can hit is pre-checked.
- [ ] Its thread and re-entrancy are known.

**Before reading a new field**
- [ ] The object is identified by its vtable address point.
- [ ] Every dereference is guarded by a readable-memory check that refreshes on a miss.
- [ ] Lengths and counts are bounded; the value range is validated.
- [ ] Which copy you're reading is known (client or server), and whether the field is synced.

**Before shipping a fallback path**
- [ ] It has been exercised by simulating the failure it exists for.
- [ ] The offline check uses the shipped parser and matcher, not a lookalike.

**When something "just doesn't show"**
- [ ] Count detour calls and gate exits on the device before changing logic.
- [ ] Compare against a run without the other mod.
- [ ] Check the head bytes and the loaded libraries.
