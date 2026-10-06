# Hooking a Closed-Source Render Pipeline (experience notes)

Distilled from a real, successful project: adding a visual effect (a shell
outline with an optional glow) to a closed-source mobile GLES renderer without
touching a single game function.

Everything below is deliberately **generic and version-agnostic**: no game
function names, no addresses, no identifiers from any particular title. Treat
it as a decision guide for "the game draws things and I want to change how they
look".

---

## 1. Choose the hook layer from the effect you want

| If the effect is... | Hook here |
|---|---|
| "same geometry, different uniforms / state" (tint, outline, shell, glow, depth tricks) | the **graphics API boundary** |
| "different geometry" (ribbon trails, custom meshes, particles) | the engine's own mesh/render objects - requires engine-level hooking |
| "different pixels" (bloom, blur, edge detect) | the engine's render targets - usually unreachable, needs engine-level hooking |

Hooking the graphics API (the loader stubs the game links *and* the runtime
resolution paths) buys two large wins: **no signatures** (so it survives game
updates) and **no game code changes**. The price is that the API boundary is
blind to engine-level concepts: you cannot see bone transforms, material
objects, mesh builders or the scene graph. Design the effect so it does not
need them, and say so out loud before starting - guessing that the boundary can
do more than it can is the most expensive mistake in this area.

## 2. Enumerate every path that reaches the API, then prove which one fires

A symbol can be reached by at least three routes:

1. a static import (branch through the loader stub),
2. a lazy lookup (`dlsym`-style) that returns the same stub,
3. the API's own "get proc address" query, which may return a *different*
   (driver) address than the export.

Wrap all of them, and remember that **the entry points you assume are not
necessarily the ones used**: geometry may be submitted through instanced,
indirect or multi-draw variants that are not statically imported at all. Check
both the dynamic symbol table and the string table for name-resolved entry
points.

Then *prove* it: log the first actual arrival at each hooked entry point.
"Hook installed" is not evidence that the game ever calls you - the whole
category of "the mod does nothing" bugs lives in that gap.

## 3. Treat shader source as the extension point, defensively

Injecting GLSL text at shader-source time is the cheapest way to change
appearance, and it stays host-testable because it is a pure string transform.
Rules that came out of real failures:

* **Never hardcode identifier names.** Cross-compiled shaders rename
  everything and may add prefixes, suffixes and underscores. Match by
  *identifier family* after normalizing the candidate names (lower-case, strip
  separators), and prefer the normalised form on **both** sides of the
  comparison - a pattern that differs from reality only by an underscore will
  silently match nothing.
* **Leave unparseable shaders byte-identical.** Contain every transform so a
  parse failure cannot propagate into the renderer; an exception escaping into
  a render thread is a process abort, not a warning.
* **Classify by observed vocabulary, not by memory.** The reliable markers of
  "which pass is this" are the names of uniforms/attributes actually present in
  the source at runtime. Log the vocabulary of anything you cannot classify and
  use that log to extend the classifier - do not invent markers from an older
  version's documentation.
* **Resolve injected uniform locations at link time** and treat "location < 0"
  as "this program is not eligible", rather than assuming the injection always
  survives compilation.
* Skip shader families you cannot support, and keep the skip decision visible
  in the log. An unsupported family must stay *exactly* as the game shipped it.

## 4. A heuristic that fails silently is indistinguishable from a broken feature

This was the single biggest time sink. A plausibility check that returns "no"
without saying why looks exactly like "the feature is not implemented". Make
every gate **self-reporting**:

* one line, once per program, stating *why* it declined;
* include the values the comparison actually saw (the names, the masks, the
  flags), not just the verdict;
* cap everything (per program, per session) - a per-draw log line floods the
  output and hides the signal it was meant to provide.

Add a diagnostic release that only *observes*: shader compile/link errors, the
attribute and uniform vocabulary, the live vertex-attribute layout, which entry
point draws which program. One log per iteration, one hypothesis changed per
iteration, is far faster than several speculative patches.

## 5. GL state hygiene is not optional

Any state you touch must be saved and restored: cull enable *and* mode,
depth-write mask, blend enable *and* the separate source/destination factors,
buffer bindings. Engines cache state and assume it is unchanged; a leaked bit
produces artefacts far away from the code that leaked it.

Two specific traps:

* **Do not infer geometry orientation from the engine's winding declaration.**
  The front-face convention is a statement about the engine's own geometry, not
  a hint about which side of your shell to keep. Decide from the effect's
  intent instead (a shell keeps the far side, so it culls the near side) and
  apply it unconditionally.
* **Double-check "harmless" state changes.** Enabling blending for one pass and
  forgetting to restore it changes every later pass in the frame.

## 6. The two-pass shell, and why geometry decides the result

The classic outline (an inflated shell drawn behind the model) is:

1. draw the extruded copy first, with the near side culled and depth writes
   disabled;
2. draw the untouched original immediately after.

The original overwrites the shell wherever it covers it, leaving a silhouette
ring whose thickness grows with the extrusion. Because the shell is drawn
first, nothing about the original pass changes.

The part that is easy to get wrong is the **displacement**:

* Hard-surface meshes **duplicate vertices per face**. A per-face offset
  (along the surface normal) therefore tears the shell open at every hard edge,
  and the tear grows with the offset - it looks like "each face was expanded
  separately" instead of "the model got bigger".
* A displacement that depends **only on the vertex position** is identical for
  duplicated vertices, so the shell stays one connected surface. An affine
  scale is the simplest such displacement.
* **Growing each face inside its own plane** (around the projection of the
  origin onto that plane, which is the face centre for geometry symmetric about
  the origin) makes neighbouring faces *overlap*, which covers the tears a
  normal offset leaves behind. Overlap is harmless for a solid shell.
* **Prefer ratios over absolute units.** A proportional offset (a fraction of
  the distance from the origin) behaves sanely across geometry authored in
  different unit conventions, where a fixed absolute offset can be many times
  too large for one model and invisible on another.

If the geometry carries **no direction information at all** (no normal-like
attribute - common for baked/flat-lit world meshes), a reversed-normal shell is
simply not achievable in the vertex shader. Do not fake it with position
arithmetic: the result is uneven, drifts, and breaks on occluded seams. That
effect has to be implemented at the mesh-generation level, or not at all -
knowing when to say "not at this layer" saves a lot of churn.

## 7. Additive effects need an explicit budget

Additive layers accumulate. `layers x strength` can exceed 1 several times over
and wash the entire frame into a single colour - a user-visible "the mod broke
my screen" bug that is really an unbounded parameter. Cap the total
contribution, scale the layers down proportionally when the cap is hit, and log
when clamping happens. Never ship an "intensity" multiplier without a ceiling.

## 8. Rollback discipline

* Keep the last known-good artifact around; be able to revert in one build.
  A user asking to roll back is normal, not a failure - answer it immediately
  and completely.
* **Remove a feature by deleting its code, not by disabling it** behind a flag
  or a default. Dead paths still hook entry points and still change state.
* **Prove the removal.** Grep the sources for leftovers *and* scan the shipped
  binary's strings for the removed feature's identifiers. Assert each deletion
  site programmatically (count occurrences, fail loudly if a pattern is not
  found) instead of deleting by line range.
* **Record why it was removed**, with the evidence, in the project's technical
  notes. Otherwise the same dead end gets re-attempted later.
* When a feature cannot work at the chosen layer, say so plainly and stop -
  continuing to tune formulas to rescue a broken approach is the most expensive
  way to spend a session.

## 9. Verify the artifact, not the build log

A successful build proves nothing about the deliverable. Check:

* the packaged manifest fields (name, author, version, entry file);
* the entry file exists and matches the manifest;
* the exported registration symbol is present (dynamic symbol table);
* the target architecture is correct;
* the shipped binary's strings match the intended identity - in particular that
  **no stale name from a previous iteration survives**;
* the size movement matches the change (a large drop usually means code really
  was removed).

### Renaming a deliverable

A rename must be applied at *every* layer - manifest, entry file, build target,
deployment folder, config/log names, docs - and then verified in the binary.
Two user-facing consequences worth stating explicitly: a renamed library will
not overwrite the old one (tell the user to delete the old folder, or both mods
load), and derived paths (logs, configs) move, which resets user settings.

### Deliverable hygiene

Write nothing to the user's storage unless it is required. A diagnostic file
left on disk is a support burden and, for a mod, an unexplained artefact; if
the user asks for it to go, remove the *code* that writes it, not just the
menu entry. Keep diagnostics in the platform log, and keep them bounded.

## 10. Report honestly

Separate "implemented" from "observed working on a device". State the
limitations you know about (a heuristic that may not hold on this title, a host
test suite that could not be executed because no host compiler is present, a
geometry assumption that has not been confirmed). A confidently wrong report
costs more than an explicit uncertainty.

## 11. Short decision list

* Effect = same geometry, new uniforms/state -> API-level hook. Anything else
  -> engine level, or not at all.
* Wrap every resolution path; log the first real hit of each entry point.
* Match identifiers by normalised family; never by literal string.
* Make every gate explain its "no", with the values it compared.
* Save/restore all state you touch.
* Position-only displacement for hard-surface geometry; ratios over absolutes.
* Cap additive brightness.
* Keep a known-good artifact; delete features by deletion, and prove it.
* Verify the packaged artifact and its strings, not the build output.
* Say what is unverified.
