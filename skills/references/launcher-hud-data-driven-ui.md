# Launcher-HUD UI, one settings table, one worker

A mod does not need an engine render hook to show labels. If the host exposes an overlay surface
and a draw-command list, rendering can be pure data on a tick, and all heavy work can live on one
worker thread. This is the lowest-risk UI route, and it scales from a single label to a full menu.

## HUD as data

Build a vector of draw commands once per frame (or throttled) and submit it; submit an empty list
to clear.

- Commands are text/rect/line/circle/image with position, size, packed colour, optional font id.
- Centred text: use the host renderer's alignment convention (often a sentinel width) instead of
  estimating glyph widths yourself.
- Shadow: a second text command offset by `size × shadowOffset`, darker colour, scaled alpha.
- De-duplicate against the previous frame's list and skip identical submissions.
- Rate-cap the rebuild (a few tens of Hz is plenty for labels); re-read the surface size each
  frame and clear instead of drawing when it is degenerate.
- An empty vector is a valid "clear"; keep that path explicit or a stale overlay stays on screen.

## Fonts

- Register an embedded TrueType face once at enable for stable base text.
- For user-supplied fonts, enumerate by metadata only, then validate the file by its table
  directory (signature, bounds, required tables) before registering; fall back on invalid input.
- The host usually has no unregister: keep stable ids, dedupe registrations, and treat the set as
  process-lifetime.

## One settings table

Derive four things from a single table so UI and runtime cannot drift:

1. defaults (persisted keys only — action buttons are not values);
2. the persisted file merge (unknown keys dropped, missing keys keep defaults);
3. the menu schema published to the host (categories, node ids, types, ranges, dependencies);
4. the clamped, typed settings struct used by the runtime.

Precedence: defaults < file < menu edit < canonicalize/clamp. Parse booleans permissively
(`true`/`1`), parse numbers in a fixed locale, clamp to the table's range, write the clamped value
back, treat invalid input as default. Add migration markers when a release must force a value
once. Save with tmp file + rename.

## Threading

- The tick side samples and builds commands; it never blocks on IO. Take the state mutex with a
  try-lock and skip the frame if a worker holds it.
- One worker owns filesystem, font loading, catalogs and network. It does IO outside the lock and
  moves results in under a short lock.
- Defer world-dependent initialisation: the worker waits on a condition variable until the first
  valid in-world sample, so the title screen performs no storage scans.
- Coalesce writes through a dirty flag; flush on a timer and once at shutdown.
- Shutdown order: stop and join the worker **before** unhooking, so no worker can touch state
  after the hook is gone.

## Localization

- Try a ladder of key shapes (mod-specific, inherited, type-qualified, bare name) so catalogs from
  different generations keep working.
- Map legacy ids to current ones before lookup; keep the alias table beside the loader.
- Fallback chain: active locale → base language → raw id → humanised words. A display function
  must never throw; a missing key is a normal outcome.
- Colours and other metadata can live in the base-language catalog only, to avoid per-language
  drift.
- Optional online translation: queue small jobs, one per worker loop, short timeout, cache by
  `language|source`, and silently keep the local text on failure.

## Pitfalls

- Holding the state lock while calling host APIs (schema republish, menu registration) can
  re-enter you; snapshot, release, then publish.
- Registering fonts per frame or per world leaks registrations with no unregister API.
- A menu schema generated separately from the runtime defaults drifts on the first added setting.
- Submitting commands from two threads without a memory barrier can drop the last frame.
- Treating an empty draw list as "no change" leaves the previous overlay visible.

## Checklist

1. Draw commands rebuilt from state, deduped, rate-capped, empty list clears.
2. Alignment sentinel used instead of glyph-width estimation.
3. Fonts registered once, validated, with a stable id table.
4. One settings table drives defaults, file merge, schema and clamped runtime struct.
5. Tick never blocks on IO; worker owns IO; deferred world init.
6. Worker joined before unhook; final settings flush on shutdown.
7. Localization ladder with aliases and a never-throwing fallback.
8. No host API called while holding the state lock.