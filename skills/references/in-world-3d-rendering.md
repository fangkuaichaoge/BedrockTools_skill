# Drawing your own 3D geometry in the game world

This is an implementation guide: how to put your own panels, labels, icons or quad-based models
into the 3D world of a closed-source renderer without breaking vanilla rendering. It assumes you
can already resolve engine functions by signature and install an inline hook (see
`hook-techniques.md` and `signature-forensics.md`).

Related references: `render-pipeline-hooks.md` (effects on a closed renderer), `engine-ui-buttons-and-overlays.md`
(2D UI drawn with the engine), `ui-actor-rendering.md` (an entity model inside a HUD panel),
`block-model-replacement.md` (replacing block geometry).

The whole approach is: **do not build a renderer — borrow the engine's**. One hook gives you the
frame's render context; the engine's own material, tessellator, font and matrix stack do the work.

---

## 0. The five entry points you need

Resolve all five at startup from your signature/name table, cache typed function pointers, and
verify each one (module check + byte check) before calling it. If one is missing, disable the
feature with a named log line — never guess an address.

| Role | What you need from it | How to confirm |
|---|---|---|
| frame world-render entry | per-frame call carrying the render context | the hook fires once per rendered frame; forwarding it keeps the world visible |
| material factory / registry | a material object addressed by a stable name | you can draw one triangle with the returned object |
| tessellator begin / vertex / mesh | geometry submission verbs with a vertex budget | a single quad appears at the right place |
| font glyph width / glyph draw | text metrics and the engine's cached-glyph draw | a string appears crisp at any distance |
| block-solidity + chunk-loaded predicates | occlusion answers for a world cell | a panel behind a wall is skipped (and only then) |

The camera comes with the frame entry; you do not need a projection matrix (see §3).

---

## 1. Capture one frame

Hook the **frame-level world-render entry**, not a low-level graphics call. Rules:

1. Call the original first, with the same arguments — you append, you never replace.
2. Cache the frame state **once**: render context, font object, tessellator pointer, camera
   (eye, basis rows, forward, valid flag), and the font's line height.
3. Draw at most **once per frame**: a frame token/sequence number is enough.
4. Add a watchdog: if a cached context is older than a second, treat it as invalid (a stalled
   frame must not leave a dangling pointer cached).
5. If another thread can read the same state (touch, input), take a mutex around the frame.

```c
static void on_world_frame(RenderCtx* ctx, FrameArgs* args) {
    original_world_frame(ctx, args);                 // never drop the engine call
    FrameState* f = begin_frame(ctx, args);          // cache ctx / font / tessellator / camera
    if (!f->valid) { end_frame(f); return; }         // fail open: draw nothing
    if (f->token != g_last_drawn) {                  // at most one append per frame
        g_last_drawn = f->token;
        rebuild_panels(f);                           // CPU side: measure, raster, sort
        draw_panels(f);                              // guarded submission (§6)
    }
    end_frame(f);                                    // release caches and the frame lock
}
```

---

## 2. Build render state safely

### 2.1 Material by name

Do not compile a shader. Ask the engine for a material **by name** from its registry and reuse
that object. Pick the variant whose state matches your intent: for world-space labels you want
**depth-tested, double-sided** text so panels are hidden by walls and readable from both sides.

Resolve it once, cache the pointer, and if it is missing: **draw nothing and log**. A missing
material means a version mismatch; substituting a guessed state is how mods corrupt the scene.

### 2.2 RAII guards around every state change

The engine's matrix stack and shader colour are global frame state. Save and restore them in
constructors/destructors so early returns and exceptions cannot leak:

```c
struct MatrixGuard {
    Font* font; mat4 previous;
    MatrixGuard(Font* f, const mat4& m) : font(f) { matrix_push(font, m); }
    ~MatrixGuard() { matrix_pop(font); }             // restore in the destructor
};

struct ColourGuard {
    Ctx* ctx; float saved[2][4];
    ColourGuard(Ctx* c) : ctx(c) { read_shader_colours(ctx, saved); }
    ~ColourGuard() { set_shader_colour(ctx, saved[0]); set_shader_colour(ctx, saved[1]); }
};
```

Restore in **reverse order** of acquisition; guards are stack objects in the draw path.

---

## 3. Place geometry in the world

### 3.1 Camera-relative coordinates

Do not build a view/projection matrix. Subtract the eye from every world position and let the
engine's world pass apply its own matrices:

```c
vec3 rel = world_pos - camera.eye;
```

### 3.2 Panel frame

A panel is a centre, two half extents, and an orthonormal basis (right, up, normal) derived from
its yaw/pitch:

```
right  = ( cosY, 0, -sinY)
up     = (-sinY*sinP,  cosP,  cosY*sinP)
normal = (-sinY*cosP, -sinP,  cosY*cosP)
```

A world point on the panel is `centre + right*u + up*v`, with `u ∈ [-halfW, +halfW]`,
`v ∈ [-halfH, +halfH]`, pushed a few millimetres along `normal` so it never z-fights with the
surface it is attached to.

### 3.3 Cull by distance and draw far-to-near

Keep a distance cap (tens of blocks is plenty for labels), sort back-to-front by
`|panel_pos - eye|`, and skip panels whose backing surface is not loaded. The sort is stable so
equal distances keep insertion order.

---

## 4. Turn a panel into quads

A panel is **not a texture**: rasterize it on the CPU into a small square grid of coloured quads.

### 4.1 Grid resolution

Two resolutions are useful: a coarse one for small/distant panels and markers, a finer one for
close-up text panels. A 32×32 grid is 1024 quads before merging — merging usually removes most of
them. Pick the resolution from apparent size, not from a fixed rule.

### 4.2 Per-cell sampling

For every cell compute a colour:

- base fill colour × opacity;
- border/frame: a small RGBA pattern table (tens of pixels square) mapped through a 9-slice for
  arbitrary panel sizes;
- procedural patterns (stripes/checker): generated from the cell index;
- rounded corners: measure from the corner and drop cells outside the radius;
- icon/marker: sample another tiny RGBA table (an icon can also be a 9×9 ASCII mask);
- combine with `SRC_OVER`, applying the panel's global alpha per cell.

```c
for (int y = 0; y < cells; ++y)
  for (int x = 0; x < cells; ++x) {
      rgba c = sample_pattern(x, y, style);          // border / frame / stripes / checker / rounding
      c = src_over(c, style.fill);                   // opacity folded in here
      if (c.a) out.push(make_quad(x, y, c));         // one quad per non-empty cell
  }
merge_identical_neighbours(out);                     // greedy rows first, then columns
```

### 4.3 Why this shape

- no texture upload, no atlas UV bookkeeping, no second UI framework;
- a hard cap on vertex count (resolution × merging);
- patterns are a few hundred bytes embedded in the binary;
- the price is slight softness at very close range — the finer resolution buys it back.

---

## 5. Text with the engine's font

Do not rasterize glyphs. Use the engine font for metrics and for the glyph draw.

1. Measure the block with the engine's glyph-width call; derive the panel half-width from
   `(measured_width/2 + padding) × scale`.
2. Split into lines by width (or a simple monospace splitter for a label).
3. Push the matrix **once per text block**: rows are `scale × basis.right`,
   `-scale × basis.up` (font local +Y is screen-down, so the up axis must be negated), the third
   column is the facing axis — the reversed reference documents ambiguity in that slot, so verify
   on your build which vector the layout actually puts there — and the translation is the panel
   position pushed forward a few millimetres.
4. Draw each line with the engine's cached-glyph call, centred on X, advancing by the engine's
   line height.
5. Shadow = draw the same line again, offset by ~0.75 of a font unit, with colour multiplied down
   (~0.25). This is the classic "draw twice" trick, not a second material.
6. Pop the matrix.

```c
mat4 m = { scale * basis.right, -scale * basis.up, basis.normal,
           panel_pos - eye + basis.normal * 0.007f };
matrix_push(font, m);
float y = -0.5f * line_height * line_count;          // vertically centre the block
for (const Line& line : split_lines(text, max_width)) {
    draw_glyphs(font, line, /*x=*/-0.5f * line.width, y);
    if (style.shadow)
        draw_glyphs(font, line, -0.5f * line.width + 0.75f, y + 0.75f,
                    shade(colour, 0.25f));
    y += line_height;
}
matrix_pop(font);
```

Pitfalls: an un-negated up axis draws text upside down; inline colour codes leak past the string
unless the shadow copy ends with a format-reset marker; measure before you lay out, or long
strings overflow the panel.

---

## 6. Submit through the engine's tessellator

Group quads by identical colour, set the colour once per group, emit four vertices per quad in a
consistent winding order, and call the engine's mesh verb with the material:

```c
for (const Group& g : quads_by_colour) {
    set_shader_colour(ctx, g.colour);                // previous value saved by the guard
    tess_begin(tess, kQuads, /*has_colour=*/1, /*budget=*/4 * g.count);
    for (const Quad& q : g.quads) {
        tess_vertex(tess, q.x0, q.y0, q.z);
        tess_vertex(tess, q.x1, q.y0, q.z);
        tess_vertex(tess, q.x1, q.y1, q.z);
        tess_vertex(tess, q.x0, q.y1, q.z);
    }
    render_mesh(ctx, tess, material, /*flags=*/0);
}
// matrix and colour guards restore themselves at scope exit, in reverse order
```

The per-quad colour can also ride on the vertices if the tessellator supports vertex colour; the
grouping pass then disappears. Winding and depth test are what decide whether the panel is
visible — debug with backface culling visibly on.

---

## 7. Occlusion and picking

Do not raycast real geometry. Partition the world into integer cells and march the ray from the
eye to the panel with a grid traversal (Amanatides–Woo). Per visited cell ask two engine
questions: is the chunk loaded, and is the block solid.

```c
bool ray_blocked(vec3 eye, vec3 target) {
    ivec3 cell = floor(eye), last = floor(target);
    ivec3 step = sign(last - cell);
    vec3 t_max = ..., t_delta = ...;                 // Amanatides–Woo setup
    while (cell != last) {
        int axis = smallest(t_max);
        cell[axis] += step[axis];
        t_max[axis] += t_delta[axis];
        if (length(cell - eye) > max_range) break;
        if (!region_loaded(cell)) return false;      // unknown terrain: fail open, draw
        if (block_is_solid(cell)) return true;
    }
    return false;
}
```

Cache answers per frame in a small hash map keyed by cell, and **share the ray with picking** so a
panel behind a wall can neither be drawn nor selected. On invalid input return "not blocked":
drawing through a wall for one frame is cheaper than hiding a feature or dereferencing a null.

---

## 8. Performance and correctness checklist

1. One frame hook, original always called, one append per frame.
2. Material resolved once; missing → no draw + one log line.
3. Matrix and shader-colour saved/restored by guards in reverse order.
4. Geometry camera-relative; panel pushed along its normal a few millimetres.
5. Panels rasterized to a bounded grid; identical cells merged before submission.
6. Text measured, one matrix push per block, shadow as a second pass.
7. Occlusion by cell traversal, cached, fail-open, shared with picking.
8. Distance cap + back-to-front order + stable sort.
9. No allocation, no file I/O, no exception on the render thread.
10. Every hook target verified (module + bytes) before the first call.

---

## 9. Symptom → cause

| Symptom | Likely cause |
|---|---|
| Nothing appears | material unresolved; wrong winding/backface; drawn behind the depth test; camera-relative offset wrong |
| Z-fighting / flicker | panel not pushed along its normal; two draws in the same frame |
| Vanilla UI or world looks corrupted after the draw | matrix or shader-colour state not restored (guard missing or wrong order) |
| Text mirrored/upside down | up axis not negated (font +Y is screen-down) |
| Panels visible through walls | occlusion not wired, or fail-open everywhere, or distance cap too large |
| Crash on frame 1 | unverified hook target; null font/context; NaN in the basis |
| Everything appears twice | hook installed on two passes, or the frame token is reset per pass |

---

## 10. Verified engine-pipeline details (from shipped implementations)

These are the facts a shipped in-world renderer actually depends on. The exact numbers belong in
your **build profile** (see `build-locked-abi-profiles.md`), not in this document.

- **One capture object per frame.** The frame entry hands you a context that owns the tessellator,
  two shader-colour holders (main and darkened), a matrix-stack owner, a GUI-data reference and a
  validity flag. Cache those roles once per frame. Re-derive the camera from the client's **local
  render player** (eye and forward vectors), not from the renderer object.
- **Material by name, not by shader text.** The engine material registry is keyed by a hashed
  string (`{u64 hash, std::string, void*}`; FNV-1a with multiply-then-xor). The constructor writes
  a shared-pointer pair into your wrapper; pass the wrapper's address to the mesh call. Resolve
  each material once and fail open when it is absent.
- **Two colour holders.** The main and darkened slots are separate objects holding a float4. Save
  both, set the colour before each batch, restore both on scope exit — a missed restore tints the
  engine's next draws.
- **Matrix stack.** Push returns a small guard `{owner, pointer-to-top}` by structure return. Copy
  the current matrix out of `top`, write your columns into it, draw, then pop through the guard.
  Two passes at different depths keep the engine's text cache keys distinct.
- **Submission.** `begin(tessellator, primitive, has_colour, vertex_count, flags)` takes a
  **vertex count** (four per quad for a quad list); then four `vertex(x,y,z)` calls per quad in
  camera-relative space; then `mesh(context, tessellator, material-wrapper, capture)`. Group
  quads by identical colour and set the colour once per group.
- **Text.** The glyph draw takes the font, context, a string view, positions, colour, flags, a
  material pointer, a capture struct and a long optional tail. One matrix push per block, one draw
  per line, a shadow line offset by a fraction of a glyph with a reset code appended and the
  colour multiplied down.
- **Occlusion and aim.** Voxel DDA against a solidity predicate, cached per frame; the aim ray is
  additionally capped by the engine's latest hit result when it agrees with the view direction.

> Do not substitute your own GL program inside the engine's render pass. A shipped attempt at that
> produced nothing visible, while the same geometry submitted through the engine's tessellator and
> material appeared. The pass owns framebuffer state you would otherwise have to guess.