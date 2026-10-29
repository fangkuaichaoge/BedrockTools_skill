# Preview panels: drawing custom UI over the HUD, fed by container data

Field notes from building a "look at a chest to see what is inside" mod: a panel drawn from
`ScreenView::render`, filled with real item icons and localized names read out of the game's own
container objects. Everything here was verified on device; the dead ends are recorded because
they cost the most time.

Companion files: `hook-techniques.md` (the hook forms), `container-and-slot-reversing.md` (where
the data lives), `instrumentation-and-diagnostics.md` (how to see what happened).

---

## 1. Getting a UI context to draw with

There is no "draw a rectangle" API from outside the engine. The workable path is:

1. Hook the **world render** entry (`RenderLevel`) — that is the per-frame point where the game
   is about to draw the world, and it runs on the render thread.
2. Hook the UI render context's **text method** (`MinecraftUIRenderContext::drawText`, reached
   through its RTTI vtable) purely to capture `self` and the active `Font`. That hook is the only
   cheap way to learn which context is live; store both, forward the call untouched.
3. Hook `ScreenView::render`: clear the captured context, call the original (which draws the HUD
   and therefore sets the context through the text hook), then draw your panel with the captured
   context.

Consequences worth knowing before starting:

* Drawing only works **after** the game has drawn something that calls `drawText`; a HUD with no
  text at all would leave the context null.
* The UI context is a **per-frame** object. Never cache it, never touch it from another thread.
* Screen-space units are **UI pixels**, not device pixels; item icons and text metrics are all in
  that space, so a layout that looks right at 1080p is small on a tablet but stays consistent
  with the rest of the HUD.

## 2. Reading container contents

* Resolve the block actor from the block position the player is looking at
  (`Level::getHitResult` → `BlockSource::getBlockActor`) — this needs no tracking at all.
* Identify the actor by comparing its `vtable[0]` with the container class's complete destructor,
  resolved from the class RTTI name. This reads no instance data, so an unexpected class simply
  does not match.
* Capacity and item slots are **per class**; see `container-and-slot-reversing.md`.
* Guard capacity: a rejected or absurd value (`0`, or far beyond the known maximum) means the
  slot index was wrong, and indexing items with it would read unrelated memory. Returning "not a
  container" is the safe answer.

Vanilla single/double chests are the easy case; hoppers and decorated pots also work but need
their own slot table, which is why the reversing recipe is a separate document.

## 3. Item icons

The engine's own item renderer (`ItemRenderer::renderGuiItemNew`, signature-resolved) draws a
stack into a `BaseActorRenderContext`:

```
BaseActorRenderContext ctx;              // construct via its resolved ctor
ctx = ctor(screenContext, client, game); // screenContext and client come from the UI context
itemRenderer = *(ctx + offset);          // the renderer lives at a fixed offset
itemRenderer->renderGuiItemNew(ctx, stack, ..., x, y, scale, scale, 1.0f);
dtor(ctx);                               // always destroy it again
```

Two rules that matter:

* **Copy non-block stacks before rendering.** The renderer may mutate the stack it is given, and
  a container's contents must not change because they were looked at. Block items render from
  the live stack in practice; if the copy constructor signature is unavailable, render live but
  treat the result as read-only.
* **Draw images/cells first, then icons, then text.** Icons are submitted through a different
  path than rectangles; interleaving them in the wrong order makes text disappear behind icons.

## 4. Text: measuring before drawing

`uiGetLineLength(context, font, text)` is the measuring call; `drawText` takes a rectangle
`{left, right, top, bottom}` plus an alignment. Layout therefore has to measure **first** and
draw second, or panels resize as contents change.

The trap that produces visibly broken rows: sizing a row by one shared "widest" value when the
row contains *two* columns (name and count). Measure each column separately and lay them out as

```
width = padding + icon + textOffset + widestName + gap + widestCount + padding
```

## 5. Lists that outgrow the screen

A double chest is 54 stacks; at a readable row height that does not fit a phone screen. Truncating
loses information, so paginate into **columns**:

```
rowsPerColumn = max(1, configured rows)
columnCount   = ceil(rowCount / rowsPerColumn)
columnX(i)    = originX + (i / rowsPerColumn) * columnWidth
rowY(i)       = originY + header + (i % rowsPerColumn) * stride
```

Keep the column width uniform (measured from the widest entry) so columns line up, and treat
`rowsPerColumn = 0` as "single column, unlimited".

## 6. Localized item names

The engine already knows the answer: `ItemStackBase::getName()` returns the display name in the
player's language, exactly as the inventory shows it. It is **non-virtual**, so it is located by
byte pattern, and on AArch64 it returns `std::string` by value (sret pointer in `x8`) — declare it
as

```cpp
using ItemDisplayNameFn = std::string (*)(const ItemStackBase*);
```

and the compiler supplies the sret pointer exactly like the engine's own callers do. Details and
the decompiled evidence are in `item-names-and-language.md`.

If the pattern is unavailable, show icons and counts only — do **not** fall back to raw
identifiers, which look broken to players.

## 7. Lifetime rules (this is where crash reports come from)

A preview mod holds pointers that the game frees at moments you do not control: leaving a save,
destroying a block, closing a screen.

* **Do not keep container pointers "for later".** Re-derive them from the block position every
  frame — that path cannot go stale.
* If you do cache an actor, require it to be **still ticking** (stamp the last tick time on every
  tick of that actor, and ignore records older than a couple of seconds). A destroyed actor stops
  ticking, so it disqualifies itself without any pointer validity check.
* Same trick for the local player: stamp the tick time, and drop every cached pointer when ticks
  stop. That replaces a level-destructor hook with a timeout and no signature.
* **Never copy one actor's stacks into another actor to compensate for latency.** It was the
  cause of two separate field bugs: a use-after-free when the source actor had been replaced, and
  disappearing item icons after a chest screen re-synced the real container.
* Clear any per-screen caches when a container screen opens or closes, because the screen
  re-synchronises the real contents.

### The per-frame validity check trap

The obvious fix — "check that the pointer is inside a readable mapping before using it" — must not
be added to the per-frame path. A `/proc/self/maps` cache goes stale (the game keeps loading
libraries and growing heaps), and a **failed** lookup then turns into "skip this frame". That
silently disables the whole feature, and the symptom looks like "rendering broke after an
unrelated change".

If you need such a check at all: refresh the cache on a miss, and **fail open** when the map cannot
be read. Better still, use the tick rules above so no check is needed.

## 8. Ship checklist for a HUD overlay mod

1. Panel appears with the crosshair on a container; disappears when the screen opens.
2. Position/scale/sensitivity reachable from the Mod Menu and take effect immediately.
3. Item icons survive opening and closing the container screen.
4. Leaving a save with the panel visible does not crash, and the panel returns after re-entering.
5. All three display modes (grid, list, list without panel) render, including a full double chest.
6. Names follow the player's language.

## 9. Drawing does not give you touch coordinates

A control drawn through the UI context lives in **engine UI pixels**. Touch arrives through
`pl::input` as **view pixels** (`MotionEvent.getX/getY`). The ratio between the two is the UI scale,
and the launcher does not expose it, so a hand-drawn button cannot reliably hit-test a tap.

Two workable options:

* **A gesture instead of a hit test.** A second finger touching down
  (`MotionEvent.ACTION_POINTER_DOWN == 5`) needs no coordinates at all. Register it through
  `pl::input::registerTouchCallback`; the callback runs before the game sees the event and returning
  `true` consumes it, so the screen underneath never reacts to the extra finger.
* **Calibrate first.** If a tap is required, find a UI-space dimension from the engine (it is not in
  the SDK headers) before promising the interaction; drawing a control you cannot hit is worse than
  no control.

Practical notes from the attempt:

* a drawn control needs its own feedback - for example highlight it for a few hundred milliseconds
  when a press is consumed - otherwise "nothing happened" is indistinguishable from "the event never
  arrived";
* always keep one path to the feature that does **not** depend on input plumbing (a settings toggle
  that performs the action on a state change), so the logic can be validated independently of the
  input layer.
