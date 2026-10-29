# Preloader mod field notes

Port-specific findings from a real multi-module Android port. The general
workflow (signatures, hook choice, lifecycle, packaging, diagnostics) lives in
the sibling references; this file records only the traps that those references
do not spell out. Concrete offsets/addresses below are examples — re-derive
them per build.

## 1. Hardcoded fallback addresses need the runtime image base

Relationship to `signature-forensics.md`: patterns are preferred. When a
build-specific fallback is unavoidable, never call `hook()` with a raw file
virtual address. The image is relocated, and patching an unmapped address dies
inside the hook installer — usually **before** the detour exists, so the crash
handler produces no FATAL line and the last log entry is just the fallback
message. Compute:

```
runtime = dli_fbase + fileVaddr     // dli_fbase from dladdr() on any resolved pointer
```

and log both the file vaddr and the runtime address. Same rule for any engine
function used only at draw time.

## 2. Vtable-slot reads: match the decompiled prototype

A slot number taken from an offset table is not a guarantee that the function is
the accessor you want. A real failure: reading a dimension id from a
`BlockSource` vtable slot whose decompiled prototype was
`(this, Vec3*, char)` — a biome query. Calling it with one argument returned
garbage, which surfaced as the Overworld intermittently reporting "Nether".

Rules:

- Decompile the slot body before calling it; the prototype must match your
  call exactly.
- Prefer a slot that is a pure field read (`return *(uint32_t *)(this + off)`)
  over one that computes.
- Cross-check the value with a second independent accessor, and log it in the
  debug line so a wrong offset is visible instead of silently wrong.

## 3. Nearby-actor enumeration

`ActorFetchNearbyActorsSorted(actor, &Vec3 extent, filter)` returns a
three-pointer vector `{begin, end, cap}`. Elements are 16 bytes:

```
struct DistanceSortedActor { void* actor; float distance; float pad; };
```

Iterate `begin..end`, classify with the RTTI name (digits prefix stripped), and
read the actor position from its state-vector pointer. Call it from a throttled
game-thread tick (about once per second), not per frame, and publish only the
aggregated/nearest values to the HUD.

## 4. Overlay specifics that are not obvious

- `DrawCommand::size` on `Text` is the **pixel font height**, not a scale.
- `w == 0 && h == 0` keeps the left anchor (`y - 0.9*height`); set `w/h` only
  when centring inside a box is intended.
- Overlay coordinates are surface pixels. Scale thickness/radii from
  `getHudSurfaceSize()`; a 46-px radar is invisible on 1080p.
- `Line` uses `(x, y)` plus a **delta** `(w, h)`, not an absolute endpoint.
- For generated rings/lines, `RectFilled` segments are the most portable
  primitive; `Line`/`CircleFilled`/`TriangleFilled` depend on the host build.
- Clamp position sliders to the real UI space. A 0–2000 slider over a ~340 px
  space makes one drag throw the panel off-screen.
- If text must follow a moving panel, recompute it from the same origin every
  frame and log both positions (panel + text) while diagnosing.

## 5. Locating the per-frame world renderer

To draw in world space (not a screen projection):

1. Find a leaf that loads the tessellator from the screen context
   (`*(ScreenContext + tessOffset)`) and calls
   `MeshHelpers::renderMeshImmediately`.
2. Intersect its direct callers with the callers of `TessellatorBegin`; the
   result is a geometry renderer.
3. Walk callers upward to the per-frame entry. Confirm the screen-context
   argument by decompiling: helper calls derive it from a field of one of the
   arguments.
4. Hook the entry, call the original, then emit with
   `begin → color → vertex* → renderMesh`, using the material the original call
   site uses.
5. The vertex signature can include UV (`u, v`) in addition to position — copy
   the argument count from the call site, not from another build's typedef.
6. If the entry cannot be identified with confidence, ship nothing: a wrong
   entry or ABI crashes on the first rendered frame.

## 6. Optional engine hooks and stale caches

Some signature tables have MISSING entries on a given build (e.g. level
destructor, container-screen destructor). Do not make them mandatory:

- Install them only when resolved.
- Without the level destructor, clear tracked actors/containers when the Level
  pointer changes (check in a per-frame hook) so freed pointers are never
  reused.
- Do not cache a failed texture fetch forever; retry while the handle is null.
- If the game's item-icon renderer is unreliable, keep a text-only path that
  depends only on the container vtable and the item name getter.

## 7. Client-side world-data approximation

Server-owned data (village bounds/POI ownership, abilities, tick rate) is not
reachable from the client. When approximating with block scans:

- Budget the queries per tick and show partial progress; a full-volume scan at
  once stalls the game.
- Restart scans only after moving beyond a threshold and only after a cooldown;
  otherwise the scan never completes while walking.
- Deduplicate multi-block objects before counting (a bed is two blocks, so
  counting blocks reports double and duplicates bindings).
- Label derived relations (e.g. villager ↔ bed/workstation) as approximations.

## 8. Crash-log nuance

A missing FATAL line still narrows the bug: crashes in the hook installer or in
a render-thread setup path may not reach the signal handler at all. Check
whether the last log line is exactly before a hook install; that points at the
target address rather than the feature logic.
