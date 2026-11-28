# Engine-UI buttons and overlays (Minecraft's own UI, not a launcher overlay)

Field notes from building a "container screen shows a tappable UI button" test mod and from
reversing a shipping reference mod that draws a full item browser inside the same layer.
Read this before writing any on-screen control or panel.

Companion files: `container-preview-live-contents-and-icons.md` (drawing over the HUD from a world-render
hook), `container-preview-live-contents-and-icons.md` (item icons / container data),
`hud-buttons-and-icon-art.md` (launcher HUD buttons), `render-pipeline-hooks.md` (own draw calls),
`coexistence-and-crash-safety.md` (hook invariants).

---

## 1. Pick the layer first — three different "buttons"

| Layer | Mechanism | Shows over the inventory? | Dynamic show/hide? | Taps | When to use |
|---|---|---|---|---|---|
| **Launcher HUD button** | `pl::modmenu::registerButton(ButtonInfo)` | yes (separate Android window above the game) | **no** — the launcher snapshots the button list; there is no per-button visibility API | native, `ButtonEvent` callback on the UI thread | a persistent mod toggle / shortcut |
| **Launcher draw commands** | `pl::modmenu::submitDrawCommands(moduleId, span)` + `pl::input::registerTouchCallback` | yes (full-screen Canvas view above the game) | **yes** — submit an empty span to clear | you hit-test in HUD *physical pixels* | quick overlays that may live outside the game's UI scale |
| **Engine UI (this document)** | hook `MinecraftUIRenderContext`'s per-frame finish and draw with its own primitives | yes, inside the game's UI pass and in its UI units | yes, decide per frame | you hit-test touch events (see §6) | anything that should look/behave like a game control, or is gated by game state (container open, screen open, …) |

The reference mod (**verified on a shipped build**) uses **both**: its movable "Items" button is a
launcher HUD button (`registerButton`, id `<mod>_open`, click delivered through
`ButtonInfo::onEvent`), while its item browser panel is **engine-side** — there is no
`submitDrawCommands` anywhere in it. The panel is drawn from the engine UI pass, so it moves and
scales with the game UI and can be opened only while a container screen exists.

Rule of thumb: if the feature must appear/disappear with game state, do it in the engine layer.
The launcher button is for a fixed on-screen shortcut; the launcher draw commands are for
launcher-owned HUD decoration.

---

## 2. The engine UI draw point

`MinecraftUIRenderContext` (RTTI name `24MinecraftUIRenderContext`) is the per-frame UI render
context. Its primary vtable slots used here (BedrockTools' `sdk/offsets/Core.hpp` names them; the
indices were re-verified on the target build — see §3):

| slot | role | call/hook |
|---:|---|---|
| 2 | `getLineLength(font, text)` — text width | call |
| 6 | `drawText(this, Font&, rect, string, color, align, alpha, measure, caret)` | **hook** (capture the live `Font&`) and call |
| 8 | `flushText(this, float, optional<float>)` | call after text |
| 17 | `fillRectangle(this, rect, color, alpha)` | call |
| 28 | `getFullClippingRectangle()` → `{x0,x1,y0,y1}` in UI units | call (screen size + layout anchor) |
| 31 | per-frame `finish()` — end of the UI frame | **hook** → draw here |

Shape that works:

```
finishDetour(ctx):
    if (!inDraw && ctx && enabled) {
        inDraw = true
        drawCallback(ctx)          // call original finish LAST
        inDraw = false
    }
    if (finishOriginal) finishOriginal(ctx)

drawTextDetour(ctx, font, rect, text, color, align, alpha, measure, caret):
    if (!inDraw) { lastFont[ctx] = &font }   // learn the engine Font, forward untouched
    drawTextOriginal(ctx, font, rect, text, color, align, alpha, measure, caret)
```

- Call the original **last** (the reference mod and the BedrockTools UI render pass both append
  before the original finish; the vanilla frame is already drawn, your commands land on top).
- Draw **once per frame**; guard with a thread-local re-entrancy flag. The finish entry can run
  for more than one UI scene in special cases.
- Never keep `ctx` past the callback; it is a per-frame object.
- `Font&` is only available if the frame drew text through slot 6. Cache the last observed font
  with a freshness window (a few seconds) and a readable-memory check, and fall back to
  shapes-only if no font is available. A container screen may render its labels through the
  JSON-UI text path (slot 7) and not touch slot 6 at all.
- Rects are in **UI units**, not device pixels, and the origin is top-left
  (`{x0, x1, y0, y1}`, i.e. left/right/top/bottom).

## 3. Prove the slots on the build you target

Slot indices and ABIs are version sensitive. Verify each one before calling it; a wrong `fill`
slot is a crash, not a wrong picture. Two independent checks:

1. **Name from an open-source reference** — BedrockTools ships the constants above.
2. **Body check on the binary** — resolve the RTTI vtable (`typeinfo -> vtable` via
   `pl::memory::resolveVtableFunction`, or `scripts/vtable_walk.py`/`find_vtable_by_rtti.py`),
   then read the slot function's first instructions:
   - fill: `fmov s8, s0` (alpha), `mov x20, x1` (rect), `mov x19, x2` (colour);
   - draw text: prologue + refs to the string/rect/colour args;
   - full clip: returns a 4-float aggregate in `s0..s3` (look for `scvtf s0, wzr`);
   - finish: first real work reads `[this+0x10]` (the screen context).
3. **Cross-check at runtime** — a third-party mod's embedded byte signatures for the same
   functions are excellent witnesses. On the target build the reference mod's signature hits were
   exactly `entry`, `entry+0x10`, … of the same functions: e.g. its `UiFrameFinish` hit was the
   slot-31 function `+0x10`, its `UiText` hit the slot-6 function `+0x10`. If the RTTI result and
   the signature disagree, **refuse to install** — that is the build gate.

Pitfall: house-style signatures often land a few instructions **inside** the function. Hook the
RTTI/vtable address (the real entry), never the raw signature hit. If you only have the pattern,
walk back to the nearest `stp x29, x30, [sp, #-imm]!` prologue (match it with an encoding mask and
value — e.g. `<prologue-mask>` / `<prologue-value>`), and only accept when the preceding instruction
is a `ret`/`nop`.

## 4. ABI cheat sheet (AArch64, clang, per-frame hot path)

```cpp
#pragma pack(push, 4)
struct RectangleArea { float x0, x1, y0, y1; };          // left, right, top, bottom (UI units)
struct TextMeasureData { float fontSize; float linePadding;
                         bool renderShadow; bool showColorSymbol; bool hideHyphen; };
struct CaretMeasureData { int position; bool shouldRender; };
#pragma pack(pop)
namespace mce { struct Color { float r, g, b, a; }; }
enum class TextAlignment : uint8_t { Left = 0, Right = 1, Center = 2 };

using FillRectangleFn = void (*)(void*, const RectangleArea&, const mce::Color&, float alpha);
using DrawTextFn = void (*)(void*, Font&, const RectangleArea&, const std::string&,
                            const mce::Color&, TextAlignment, float alpha,
                            const TextMeasureData&, const CaretMeasureData&);
using FlushTextFn = void (*)(void*, float, std::optional<float>);
using LineLengthFn = float (*)(void*, Font&, const std::string&, float, bool);
using FullClipFn = RectangleArea (*)(void*);
```

- `alpha` is a separate `float` argument (`s0`), not part of the colour.
- `RectangleArea` is `#pragma pack(4)`; a 4-float aggregate is returned in `s0..s3`.
- The `std::string`/`Font&` arguments are references (pointers); `std::string&&` in some third-party
  code is ABI-identical — pick one and do not mix.
- `flushText`'s exact parameter list drifts between versions. Calling it with
  `(ctx, 0.0f, std::nullopt)` is the shape that works when the slot is `(float, optional<float>)`;
  if it looks wrong, skipping the explicit flush still shows the rects (the frame finish flushes).
- `RectangleArea`/`mce::Color` are small; keep them on the stack every frame (no allocations on the
  render thread).

## 5. The whole button in ~20 lines of geometry

```cpp
void drawButton(void* ctx) {
    if (!containerScreenOpen()) return;
    RectangleArea clip = fullClip(ctx);
    if (!(clip.x1 > clip.x0) || !(clip.y1 > clip.y0)) return;      // fail open: draw nothing
    Button b;
    b.x1 = clip.x1 - margin;          // anchor to the right edge
    b.x0 = b.x1 - size;               // 44..48 UI units is a comfortable touch target
    b.y0 = clip.y0 + (clip.y1 - clip.y0 - size) * 0.5f;
    b.y1 = b.y0 + size;
    fillRectangle(ctx, BORDER, {b.x0-2,b.x1+2,b.y0-2,b.y1+2}, white, 0.8f);
    fillRectangle(ctx, BODY,   b, pressed ? accent : dark, 0.92f);
    if (Font* f = lastFont(ctx)) { drawText(ctx, *f, b, "UI", white, Center, 1.0f); flushText(ctx); }
}
```

Publish the computed rect in atomics so the input thread can hit-test it (UI units).

A worked, shipping example of this shape (an 11-unit chest-icon button in the top-right corner of a
container screen, with a halo, a pressed ring and a status line) is `InventorySortMod/src/modules/
SortButton.cpp` in this workspace; it is also the reference for §5b.

## 5b. Using a vanilla texture as the button face (nicer than a coloured rect)

The engine can draw any texture that is already in the vanilla resource pack, so a mod button can
look native without shipping any art. Slots on `MinecraftUIRenderContext` (verified alongside the
draw slots above):

| slot | role |
|---:|---|
| 33 | `getTexture(std::string const& path)` → texture handle |
| 9 | `drawImage(texture, vec2 const& position, vec2 const& size, rect const& uv, bool smooth)` |
| 11 | `flushImages(bool)` — submit the image batch |

Recipe:

```cpp
// once, cached; the handle stays valid while the resource pack does
void* tex = getTexture(ctx, "textures/blocks/chest_front");   // no ".png", no leading slash
if (tex) {
    vec2 pos{b.x0, b.y0};                  // top-left, UI units
    vec2 size{kButtonSize, kButtonSize};   // square; the atlas is 16x16 per face
    rect uv{0.0f, 0.0f, 1.0f, 1.0f};       // whole texture
    drawImage(ctx, tex, pos, size, uv, /*smooth=*/false);
    flushImages(ctx, false);
}
```

- A chest face reads as "inventory" to a Minecraft player; `textures/blocks/chest_front` is the
  right piece of the atlas and looks correct at 11 UI units.
- Keep a **silhouette fallback** (two filled rects) for when `getTexture` returns null — a resource
  pack change or a missing path must not remove the control entirely.
- Retry the texture lookup a few times across frames instead of caching a failure forever; the
  texture atlas is not necessarily warm on the first container screen after load.
- `smooth=false` keeps the pixel art crisp.
- Sizing: at ~11 UI units the icon is legible but small enough to stay clear of the screen's close
  (X) button; publish a hit box padded by ~13 units around it so it stays easy to tap.

## 5c. Where to put it, and how to keep it out of the way

- Anchor to `clip` (the full clipping rectangle), never to guessed screen dimensions.
- Top-right works because container panels are centred and leave that corner free; keep the plate
  below `clip.y0 + ~40` UI units so it never overlaps the close button's own touch area, and keep
  ~8 units of right margin.
- Draw order: shadow/backing first, icon second, outline/status last. Everything lands on top of
  the vanilla frame because the drawing happens before the original `finish`.


## 6. Making the button tappable

Drawing gives you no touch coordinates. Facts that matter:

- `pl::input::registerTouchCallback` delivers `{action, pointerId, x, y}` on the Android UI thread
  **before** the game sees the event; returning `true` consumes it. There is **no unregister** —
  register once (`std::call_once`).
- `x/y` are **view pixels** (`MotionEvent.getX/getY` in the activity), while your rect is in **UI
  units**. The ratio is not exposed as an API; derive it per frame:
  `scale = getHudSurfaceSize().width / (clip.x1 - clip.x0)` (the launcher helper returns physical
  pixels of its overlay view). Guard the result (sane range), and log it on the first hit.
- Consume **the whole gesture** once a DOWN has been consumed (MOVE/UP/CANCEL too), otherwise the
  game receives an orphan UP. Only consume when the DOWN is inside your rect, so the rest of the
  screen keeps working.
- Alternatives when the coordinate mapping cannot be trusted: use a **gesture** that needs no
  coordinates (e.g. second finger `ACTION_POINTER_DOWN`) or add an engine-side control (below).
- Engine controls: the game's own touch controls are classes like `TouchTextButtonControl`,
  `TouchGlyphButtonControl`, `TouchTapOrHoldGlyphButtonControl`, `TouchMoveAndTurnInteractControl`
  (their draw methods are vtable slot 5). The reference mod hooks those draw methods to observe
  and style vanilla controls; creating a real control and adding it to a screen's control tree is
  the "fully native" route, but it needs the control constructors/setters and the screen's control
  list — start with the finish-hook drawing recipe, upgrade only if you need engine-owned input.

## 7. Gating a control on "a container screen is open"

The container screen controllers share a `ScreenController` base. On the target build:

- `ScreenController::open` is vtable **slot 7**; most container screens (chest, anvil, beacon,
  furnace family, …) use one shared implementation, so one hook covers them all.
- `ContainerScreenController`'s complete-object destructor is **slot 0**; hook it for "closed".
- Track the screen **pointer value only** — never dereference it, so a destroyed screen cannot
  cause a use-after-free. Add on open, remove on destroy, and keep a small fixed table so
  overlapping open/close orderings cannot leave a stale "open".
- **Known limitation (found in the field):** the player-inventory screen does **not** go through
  the shared `open`. Any screen that overrides `open()` (inventory, crafting table, furnace, …)
  needs its **own class's slot 7** hooked too. Resolve the class's vtable by RTTI name and hook
  `vtable[7]` per class, or use a universal fallback: at finish time read the current screen
  controller from the client and compare its `vtable[0]` against the `ContainerScreenController`
  vtable address point (a class-identity test that reads no instance fields).
- The reference mod additionally stores the live controller from `ContainerOpen` **and** the four
  `ContainerTick*` hooks (slot 3 of the specialised controllers) to keep it fresh, and clears it in
  `ContainerDestroy`, `LevelDestroy` and `UiDestroy`. Copy that shape if you need robustness
  across controller flavours.

## 8. Failure-proofing the installation

- Resolve everything through RTTI at attach time; **cross-check against byte signatures**; if any
  check fails, install nothing and log why (safe no-op on a different game build).
- Poll for `libminecraftpe.so` (`dl_iterate_phdr`) and require it to be settled; never hook
  `dlopen` from the launcher process.
- Every hook forwards to the original unconditionally; no exceptions across the boundary
  (the mod is built without unwind tables).
- Guard every pointer read with a readable-map check whose snapshot can refresh; treat "cannot
  read the map" as readable (fail open).
- Remove the hooks on disable/unload and never touch the context afterwards.
- One change per build. A UI that draws nothing, a button that never appears and a tap that
  never fires are three different faults: the first is the draw path, the second is gating, the
  third is the coordinate mapping. Log each boundary once (slots resolved, hooks installed,
  gating transitions, geometry, first hit) instead of guessing.

## 9. The whole recipe, end to end

Adding one extra button the way Minecraft draws its own UI — the order that works:

1. **Decide the layer.** If the control must appear/disappear with game state (only while a
   container is open), it belongs in the engine UI layer, not in a launcher button.
2. **Resolve four things by RTTI + signature, and cross-check them**: `MinecraftUIRenderContext`
   slot 31 (`finish`), slot 6 (`drawText`, to learn a `Font&`), slot 28 (`getFullClip`), and the
   screen gate (`ScreenController::open` slot 7 + the destructor). Anything that disagrees between
   the two evidence paths → install nothing.
3. **Hook `finish`, draw before calling the original.** One draw per frame, re-entrancy guarded.
4. **Derive geometry from the clip rect** each frame; publish the touch box in atomics.
5. **Draw**: backing rect → optional vanilla texture via `getTexture`/`drawImage`/`flushImages` →
   outline → optional text via `drawText`/`flushText`.
6. **Hit-test** in `pl::input::registerTouchCallback` (register once, no unregister), converting
   view pixels to UI units with a runtime-derived ratio; consume the whole DOWN/MOVE/UP gesture so
   the game never sees an orphan UP.
7. **Gate on game state read at call time** (is that controller still alive?), never on a cached
   pointer; if the state is unreadable, draw nothing.
8. **Fail open everywhere.** A missing slot, an unreadable pointer or an unknown screen class must
   degrade to "button not shown / tap refused", never to a wrong action or a crash.
9. **Log one line per boundary while developing** (slots resolved, hooks installed, first geometry,
   first hit, action result) into a file the user can fetch, then strip it for release.

## 10. Checklist

1. Layer chosen deliberately (engine UI vs launcher button vs launcher draw commands).
2. Target slots resolved by RTTI and **verified by body/signature** on the build you ship.
3. Hook installed on the real function entry (not a signature's mid-function hit).
4. Draw before the original `finish`, once per frame, re-entrancy guarded.
5. Geometry derived from `getFullClippingRectangle()`, not from guessed screen sizes.
6. Gate reads engine state (container/screen), never a stale cached pointer.
7. Input: consume the full gesture, map pixels→UI units with a runtime ratio, log the first hit.
8. Fail open everywhere; uninstall cleanly; the vanilla UI is never dropped or reordered.
9. Button face: a vanilla texture if it reads clearly, with a shape fallback when the lookup fails.
10. Never let the button's action run on a screen whose layout the action does not understand — see
    `container-item-moves-and-sorting.md` §4 for why that splits stacks.

## 11. Reference implementations

- `InventoryUiTest/` in this workspace is the minimal working skeleton this document was distilled
  from (6 source files, ~700 lines): `src/core/Ui.cpp` (finish/drawText hooks + primitives),
  `src/core/Screen.cpp` (container gating), `src/modules/Button.cpp` (geometry + touch), and
  `analysis/UI-SLOT-EVIDENCE.md` (slot evidence for that build).
- `InventorySortMod/` is the same skeleton grown into a shipping mod: it adds a vanilla chest-texture
  face (`src/modules/SortButton.cpp`), a screen-class gate (`src/core/Screen.cpp`,
  `CurrentControllerClass`), and a paced container-action engine
  (`src/modules/SortExecutor.cpp`, documented in `container-item-moves-and-sorting.md`).
- The reverse-engineering of a shipping mod that draws a full panel in this layer is in
  `InventoryUiTest/analysis/JEI-MECHANISM.md`.
