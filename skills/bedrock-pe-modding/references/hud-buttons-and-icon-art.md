# On-Screen HUD Buttons and Icon Art

Adding a launcher HUD button (a tappable on-screen toggle) from a native mod,
and producing the icon art it needs without an image editor.

## API shape (what the headers and the bridge actually allow)

The launcher exposes buttons through `pl::modmenu` (`pl/ModMenu.hpp`).  Read the
**bridge implementation** (`ModMenuBridge.cpp`) as well as the header: the
header shows what you can *ask for*, the bridge shows what actually *happens*
(and what is silently rejected).  Points that only show up in the bridge:

- **The builder has no active-PNG setter.**  `ButtonBuilder` offers
  `pngIcon(...)` and `activeSvgIcon(...)` only -- there is no
  `activePngIcon`.  But `ButtonInfo::activeIconData` is a **public field**, so
  build the `ButtonInfo` directly instead of via the builder and assign both
  icon vectors yourself.  A toggle button needs the pressed-state art; do not
  let the missing setter talk you into a one-state icon.
- **Every field is validated at registration**; an invalid enum or an oversized
  icon makes `registerButton` return `false` (and logs a rejection).  There is
  no partial success, so treat a `false` return as "feature absent", not as an
  error to retry.
- **Toggle semantics live in the launcher.**  `ButtonBehavior::Toggle` makes the
  launcher flip the visual state itself and deliver
  `ButtonEvent::StateChanged` to your callback.  Do not track "is it pressed"
  in your own state machine; read the event, then read/flip the setting.
- **Icon bytes have a hard size cap** (a few MB; the bridge logs the rejected
  size).  A 512x512 optimized PNG is a few KB -- nowhere near it.  Do not
  pre-compress or downscale out of fear.

A button is registered once (typically from `enable()`) and unregistered in
`unload()`, mirroring the module lifecycle.  Registration failure on an older
preloader must be **fail-open**: log it and keep the menu row as the fallback
control.

## Embedding the art in the `.so`

Ship the PNG as a `static const unsigned char[]` in a generated header, not as
an asset file:

- the levipack needs no resource plumbing and no runtime file paths;
- the byte arrays are the exact bytes the bridge receives, so what you verified
  on disk is what ships;
- generate the header from the PNGs with a small script, and mark it
  "do not hand-edit".

## Drawing the icon without an image editor (Pillow)

**The launcher's own button art is bundled with this skill** in
`assets/hud-button-templates/` -- use it as the style reference and as the
compositing base, no APK extraction needed:

| File | What it is |
|---|---|
| `more_button_base_normal.png` / `_pressed.png` | the empty keycap base; the compositing background for a framed icon |
| `ic_camera.png` / `ic_camera_pressed.png` | stock camera glyph (solid eye motif) |
| `ic_snaplook_normal.png` / `_pressed.png` | stock snaplook glyph (crossed eye) |
| `ic_sprint_normal.png` / `_pressed.png` | stock sprint glyph |
| `ic_toggle_hud_normal.png` / `_pressed.png` | stock HUD-toggle glyph |
| `ic_zoom_normal.png` / `_pressed.png` | stock zoom glyph |
| `ic_gyro_normal.png` / `_pressed.png` | stock gyro glyph |

All are 512x512 RGBA PNGs.  Read a few of them as images before drawing: they
define the target style (chunky pixel-art line icons, near-black ink on grey
keycap, pale ink on the pressed keycap).  A generator script should take the
base + ink colours from this directory, not from a copy of the launcher sitting
somewhere on disk.

The style rules they encode:

1. **Match the ink colour by sampling, not guessing.**  Open a stock
   pressed-state icon and read a pixel that is definitely ink (the centre of a
   solid ring works).  Use that exact RGBA for your pressed glyph.  The normal
   state is near-black.
2. **Draw the glyph on a transparent canvas** with plain `ImageDraw`
   rectangles/ellipses and a thick uniform stroke (a line-art camera is: body
   rectangle outline + solid viewfinder bump + lens ring + solid pupil + flash
   dot).  Keep the stroke weight constant and chunky -- the stock icons are
   pixel-art style, thin anti-aliased lines read as smudges at HUD size.
3. **Recolour by alpha mask.**  Draw once in black, then for the pressed
   variant create a solid tint image and `putalpha(ink_mask)` -- one drawing
   routine, two colours, guaranteed identical geometry.
4. **Composite onto the keycap base** (`more_button_base_normal.png` /
   `_pressed.png`) with `alpha_composite`, centred, if the button should carry
   its own frame.  Scale the glyph to sit inside the base's inner plate rather
   than touching the bevel; the right fraction depends on how much the host
   shrinks the icon area, so make it one named constant in the script.

The whole pipeline is a single Python file that regenerates both PNGs and the
C++ header; iterating on the art is then "change one number, rerun".

## Verifying the art offline

Never iterate by installing to the device.  The generated PNGs are ordinary
files -- view them directly (or have an agent read them as images) to check
style match, ink colour, and framing before packaging.  Device time is spent
only on the final look at real HUD scale.

## Checklist

- [ ] Read `ModMenuBridge.cpp` for validation rules before writing the call.
- [ ] Build `ButtonInfo` directly if you need both icon states.
- [ ] Handle only `StateChanged` for a toggle; let the launcher own the visual
      state.
- [ ] Fail-open: button API missing -> log, menu row still works.
- [ ] Unregister in `unload()`, symmetric with `enable()`.
- [ ] Embed art as generated byte arrays; keep the generator script in `tools/`.
- [ ] Take the keycap base and ink colours from `assets/hud-button-templates/`.
- [ ] Sample ink colours from stock icons instead of eyeballing them.
- [ ] Inspect the generated PNGs offline; do not burn device cycles on art.
