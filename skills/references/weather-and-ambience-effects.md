# Weather, Ambient Particles and Procedural Ambience (field notes)

Field notes from a shipped native port of a **biome-driven weather overhaul** — sandstorms,
blizzards, black rain, fog, drifting dust and wind audio — written generically: no engine
function names, no addresses, no offsets, no project names. Read `feature-workflow.md` and
`hook-engineering.md` first; this file covers what is specific to *environmental* effects.

What makes this class different from a normal hook mod: the effect is computed once per tick
from world state, then **drawn and played at a different rate, on a different thread, by a
different subsystem**. Most bugs here are not wrong hooks; they are a stale snapshot, a race
between the tick and the renderer, or a value that was replaced instead of composed.

---

## 1. Map the effect to the subsystem that already implements it

| Effect | Producer / consumer to look for |
|---|---|
| fog distance and colour | the frame's fog-setup step (called with the frame renderer and the frame context) |
| sky colour | the sky-colour producer called per dimension/frame |
| vanilla rain/snow particles | the weather renderer that emits them, plus the generic particle-spawn entry |
| your own world-space particles | the world render pass, after the engine's own geometry; draw through its tessellator |
| ambience audio | **no engine entry point** — your own output stream, fed by the same snapshot |
| biome identity, precipitation, temperature | the region/chunk accessor, the biome object, its name string and flags |

Two rules decide the architecture before any hook is written:

1. **Each effect is either "modify the engine's value" or "draw your own", never both.** Doing
   both is how a mod doubles its own rain.
2. **For anything the engine already draws, find the producer and compose with it.** Hooking a
   consumer (or a global render flag) throws away everything the producer knew.

---

## 2. One validated snapshot per tick

Read world state in *one* place, on the thread that owns it (the player-tick path), and hand a
plain struct to the renderer and the audio side. A snapshot worth having:

```
client, level, dimension, region      // identity of the world
renderer, weather object              // identity of the frame producer
camera (float3)
rain, thunder (0..1), ambient light   // weather inputs
medium flag                           // underwater / underground / enclosed
time stamp (monotonic seconds)
```

Validation before storing it — every item is a cheap check that turns "random crash" into
"feature off":

* **Identity membership**: the player this tick belongs to must be the client's local player.
* **Same world**: the dimension id matches the player's, and the renderer's dimension equals the
  player's dimension.
* **Pointers exist**: client, level, dimension, region, renderer and the weather object are all
  non-null.
* **Floats are real**: camera and player position are finite and inside the world's coordinate
  bounds; the camera is not absurdly far from the player (a few hundred blocks is already a
  teleport/other-dimension artefact).
* **Weather levels are sane**: clamp to `[0,1]` and drop non-finite values instead of subtracting
  garbage from the fog distance.

Failure policy: if any check fails, **clear the snapshot and render/play nothing**. A missing
feature is acceptable; a crash is not.

Snapshot identity (level, dimension, renderer) is what you compare to decide "the world changed".
When it changes, drop **all** transient state in one place — particles, audio, temporal smoothing
— through a single reset function that every entry point (tick, frame update, menu toggle,
disable) calls. Several call sites re-implementing the clear is how stale particles from another
dimension survive a teleport.

---

## 3. Biome-aware decisions without a registry

* Read the biome **name string** at the sampled position and guard it: empty or absurdly long
  means "unknown", and an unknown biome must produce a neutral environment, not defaults applied
  as if they were real.
* Classify names into **families** with a table (dry, red/eroded, icy, dense-fog, pale, windy),
  and accept canonical names both with and without the namespace prefix.
* **Snow is not a biome-name fact.** It is `precipitation flag && temperature <= threshold`; take
  the threshold from the biome object, not from a list of "snowy" names.
* **Sample a neighbourhood, not one point.** Evaluate the centre plus four offsets (e.g. ±8
  blocks on each horizontal axis) and blend with an incremental weighted average; a weather
  overhaul that snaps at a biome border looks broken even when every individual decision is
  right.
* **Smooth in time as well**: blend the new environment into the previous one
  (`mix(previous, current, k)`) only when the snapshot identity did not change. Reset the
  smoothing on an identity change, or a dimension switch drags the old fog with it.
* **Respect the world's vertical span.** A sample above the build height or below the bottom
  must be rejected, and block coordinates derived from floats must be clamped into the region's
  own bounds as reported by the region object.
* Sampling is not free: it walks chunk lookups. Budget it (once per tick per effect, not per
  particle) and cache the environment in the snapshot.

---

## 4. Compose with the engine's fog, sky and rain — never replace them

The engine's fog already accounts for dimension, time of day, water, blindness and other
effects. Replacing its output throws all of that away. Compose:

```
call original                                   // engine's own value
if (no environment or neutral modifier) return
fogColor.rgb *= env.fog.rgb                     // tint, multiply
fogStart = clamp(fogStart * env.distance, lower, fogEnd * fraction)
fogEnd   = max(minimumEnd, fogEnd * env.distance)
skyColor.rgb *= env.sky.rgb
rainAmount *= (1 - blackRainStrength)           // scale, do not overwrite
```

Rules that came out of real failures:

* **Guard every number you write back**: finite, positive, and below a plausible cap. A single
  `nan` in a fog distance can make the whole frame black.
* **Skip neutral**: if the modifier is 1.0/white, do not touch the engine values at all.
* **Skip the wrong medium**: interior/underwater fog is not weather fog; read the medium flag
  from the frame and leave the engine alone there.
* **Never write a distance the engine did not give you.** Scaling an existing value stays inside
  the engine's own scale; inventing one (e.g. "far = 64 units") breaks every dimension.
* If you colour the rain, decide once whether you tint the *particles* or the *sky*; changing
  both doubles the visible effect.

---

## 5. Suppressing the engine's own precipitation: a scoped marker, not a global switch

A black-rain feature often needs to remove some of the vanilla particles (the ones whose biome
should not show them) while leaving the rest alone. The pattern that works:

1. Hook the **emitter** that spawns the vanilla effect for the frame.
2. Around the original call, set a **thread-local scope marker** describing which region/context
   the spawn belongs to (an RAII guard, so an early return cannot leave it set).
3. Hook the **generic particle-spawn entry**. If the marker is set and the effect type is the one
   being replaced, ask the environment model for that position: keep the vanilla particle when
   the biome still wants it, return "no particle" otherwise.

Why this shape:

* it affects only the engine's own call sites — other systems that spawn particles are untouched;
* the marker is thread-local, so no lock and no race with another thread's spawns;
* the decision is asked **per position** with the same environment model that drives your own
  particles, so vanilla and mod particles agree.

Hot-path discipline: check the marker first (one thread-local read), then the effect type, then
sample. Do not log, allocate or take a lock in the suppress path.

---

## 6. Drawing your own world-space particles through the engine's own mesh path

Hook the world render pass and draw **after** the original, inside the same frame:

* **Camera basis from the engine's view matrix**, not from yaw/pitch arithmetic; take the right
  and up columns, and verify their lengths are ≈1 before using them. A matrix in a different
  convention silently tilts every quad.
* **Draw with the engine's tessellator and an engine material**: begin a batch, emit
  colour + position per vertex, submit the mesh with the material object. No GL state, no
  shader, no blend setup — that is the whole point; it also keeps you inside the engine's own
  state tracking.
* **Verify the material before drawing**: the material must report "resolved", and its name hash
  must equal the hash of the intended untextured material. If either check fails, draw nothing.
  (One observation worth re-verifying per engine: the name comparison used a 64-bit FNV-1 style
  hash; confirm the scheme once against a material you can resolve on device, then hard-code
  only the target hash.)
* **Borrow the tint safely**: save the colour holder's RGBA *and* its "has colour" flag, force
  white, draw, restore both. Restoring only the RGBA leaves the engine in a different state.
* **Check the tessellator's flags first** (in-use / recording): if another pass is mid-batch, skip
  this frame instead of corrupting someone else's geometry.
* **Static scratch buffers**: a `thread_local` vertex vector that is `clear()`ed each frame, not
  reallocated. The render thread is the one place where allocation shows up as stutter.
* **Geometry**: camera-relative quads, sized in world units, with the per-particle scale and
  alpha driven by a phase (age / lifetime) so the fade-in and fade-out are smooth; clamp the
  near and far range so a particle at zero distance cannot fill the screen.
* **Interpolate tick state into frame time**: logic advances at tick rate, rendering runs
  faster. Draw at `position + velocity * clamp((now - tickStamp) * tickRate, 0, 1)`. This removes
  the visible tick stepping without any engine support.
* **Fold ambient light into the tint** with a time-of-day curve clamped to a floor, so particles
  do not glow at night; clamp the curve, do not trust the dimension clock.

---

## 7. Procedural ambience audio without assets

An ambience layer can be synthesised instead of shipped: no asset ids, no package weight, full
control of layering. Use the platform's low-latency output stream (AAudio on Android), and treat
it as a separate subsystem with a tiny contract:

* **The game thread publishes gains; the audio callback only reads them.** Use atomics for every
  value the callback consumes (soft, strong, breeze, enclosure). The callback must never take a
  lock and never allocate.
* **Shape noise instead of sampling files**: white noise through one-pole low / band / high
  filters; wind = weighted mix of the three; a slow phase-modulated sine gives gusts; an
  enclosure gain damps the high band for interiors; a small anti-phase component gives stereo
  width. Constants are tuning, not science — expose one volume and keep the rest internal.
* **Slew every gain inside the callback** (`x += k * (target - x)`); stepping a gain is audible as
  a click. Read the target once per callback, not per frame.
* **Lifecycle**: open lazily on the first audible request, retry with a backoff on failure, and
  close after a quiet timeout so the stream (and its thread) is not held forever. Never open on
  the render thread.
* **Safety**: clamp the output to `[-1,1]`; if channel count or sample rate is unexpected, close
  and stay silent; gate everything behind the snapshot's "audible" state (no menu, no interior,
  no underwater).
* **Known hazards, do not copy blindly**: resetting filter state from the game thread while the
  callback runs is a data race (publish a reset generation, or reset inside the callback); stop
  the stream and wait for its state before closing; opening a stream blocks long enough to
  hitch a tick.

---

## 8. Settings, menu and rules from a single source of truth

* Declare every setting **once**, as a table row: key, title, group, description, control kind
  (toggle/slider/integer), member pointer, min/max/step. Generate from that row: the default
  value, persistence, the menu schema, validation and clamping. Four hand-maintained copies of
  the same list is four places for a typo to hide.
* Persist as `key=value` text: write a temporary file and rename it over the old one; ignore
  unknown keys; clamp on load; never crash on a missing file.
* Keep the **menu value** and the **declared default** separate: the entry shows the current
  value, the schema keeps the row's default, and a toggle only writes the file after the value
  actually changed.
* User-editable rules (per-biome overrides) belong in their own text file, hot-reloaded by
  polling its last-write time at a low rate (once per second is plenty). Parse defensively:
  bound the line length and the rule count, reject unknown keys, and **keep the previous rules
  when the new parse fails** — a typo in a config file must not wipe the feature.
* A toggle that changes rendering clears the environment snapshot and particles; a slider does
  not. Make that difference explicit in the change handler.

---

## 9. Threads and locks that survive a shipping build

Three threads are typical: game/tick (mutating), render (reads a copy), audio callback (reads
atomics). The shape that holds:

* one mutex around the snapshot + particle store;
* the render hook **copies** what it needs under the mutex, releases it, and only then builds
  geometry and draws — never hold the lock while calling an engine function;
* the audio callback is lock-free (atomics only);
* clearing transient state goes through one function;
* the render hook re-verifies the frame identity (renderer pointer equals the snapshot's) before
  drawing, so a frame from another world shows nothing rather than the wrong particles.

---

## 10. Gate, build and package discipline (what this class of mod got right)

* **Gate on the exact image, not a version string**: walk the loaded objects, match the library
  by basename, read the linker's build-id note from its **mapped** `PT_NOTE` segment (name
  `GNU`, type 3, descriptor length = hash size), compare every byte, and only then publish the
  image base the rest of the mod uses. A repacked file cannot fool it, and a wrong build is a
  logged no-op instead of a crash. Implementation detail and its traps:
  `runtime-build-resolution.md` §0b.
* **Confirm the image actually carries the note.** The gate is only usable when the shipped
  library has the build-id; a rebuilt or stripped copy may not, and then every build is rejected,
  including the right one. Check offline that the exact hash bytes are in the file before treating
  the gate as protection (and if you build the image yourself, add `-Wl,--build-id=sha1`).
* **Embed the version string at build time** and make the packaging step refuse to package when
  the binary does not contain the manifest version. It catches the "forgot to rebuild" release.
* **Export only the host's lifecycle symbols** through a version script, hide everything else,
  build with `-fvisibility=hidden`, link `--no-undefined`, and align to 16 KB pages for modern
  Android. See `build-deploy.md`.
* **Package to a temporary file, reopen and validate it, then replace the output** — a truncated
  archive is silently unusable.

---

## 11. Checklist before shipping an environmental effect

- [ ] One validated snapshot per tick; failure clears state and draws nothing.
- [ ] Snapshot identity change routes through a single clear function.
- [ ] Biome sampling respects the region's vertical span and rejects unknown biomes.
- [ ] Fog/sky/rain compose (multiply/scale) and restore the engine's own values.
- [ ] Every borrowed engine object (material, tint holder, tessellator) is verified and restored.
- [ ] Vanilla suppression uses a thread-local scope marker with an RAII restorer, not a global flag.
- [ ] Particles are interpolated between ticks at frame time; scratch buffers are reused.
- [ ] Audio gains are atomic, slewed in the callback, and the stream lifecycle is bounded.
- [ ] Settings have one table source; rules hot-reload keeps the previous parse on failure.
- [ ] The build gate reads the mapped image's build-id, and that image is confirmed to carry it.
- [ ] The packaged archive contains the built binary and the manifest's version, verified.

---

## 12. Anti-patterns (each one observed, each one cost a device round)

| Anti-pattern | What it looks like |
|---|---|
| Replacing the engine's fog/sky | dimension/time/water handling disappears; underwater fog wrong |
| Global "cancel rain" flag instead of a scope marker | other effects that use the same spawn entry break with you |
| Reading engine state in the render hook | one-frame-behind or torn reads; crashes after a dimension change |
| Resetting audio state from the game thread | rare clicks, then a crash on the audio thread |
| Guessing a material name hash | feature silently never draws (open the resolution check, do not guess) |
| Letting a stale `last_write_time` drive a per-frame reload | disk churn + stutter, no visible benefit |
| A config parse error resetting the rules | user's file typo wipes the feature |
| Clearing transients in only one entry point | particles from another dimension after a teleport |
| Returning a large engine struct by value from a hook | per-call multi-KB copies and an ABI you do not own (see `verifying-unrun-cpp.md` §2.6) |
| Uninstalling a loader detour while another thread may be inside it | crash on unload, no log |

---

## 13. Related reading

* `feature-workflow.md` — how to choose the hook point for a feature.
* `hook-engineering.md` — hook lifecycle and fail-open rules.
* `render-pipeline-hooks.md` — layer choice and state hygiene for visual effects.
* `mod-lifecycle-and-crash-safety.md` §2b — the guarded loader-gate variant this class of mod
  can use when `enable()` runs before the game image is mapped.
* `verifying-unrun-cpp.md` §2.6 — layout tripwires and return-value rules.
* `build-deploy.md` — link flags, export scripts, packaging verification.