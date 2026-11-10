# Rendering a live entity model in a HUD panel

How to draw a real 3D mob (the engine's own model, animation and textures) inside a custom HUD
panel, and the pitfalls hit on the way. Written from a look-at info overlay that shows the
targeted mob in the panel's icon slot. No addresses or build numbers. Find the engine functions
in your build with the anchors in §1.

## 1. Copy the engine's own UI actor renderers

The engine already draws actors into UI in at least two places. Find them, read them, and copy
one of them call for call. Do not invent a render path.

| Renderer | Where it is used | What it does with the matrices |
|---|---|---|
| HUD player renderer ("paper doll") | the player model on the HUD | pushes only the **view** stack and keeps the screen projection; translate to the box, scale `(s, -s, s)`, fixed rotation |
| Live horse renderer | the horse / llama / camel model in the inventory screen | pushes **all three** stacks, writes its own orthographic projection (its own near/far depth range), places the model at a small negative view z |

Anchors for finding them: their RTTI names (custom UI renderer classes), the molang variable
strings the paper doll sets (`variable.is_first_person`, `variable.player_x_rotation`, ...), and
the `setInUi` call that both make right before the dispatcher.

Both renderers follow the same sequence:

1. Copy the screen context (the UI renderer's context copy function, alpha 1.0), and set the HUD
   flag in the copy.
2. Construct the actor render context on the **copy**. Check its vtable afterwards.
3. Take the actor render dispatcher from the render context. Skip if it is null.
4. Push the matrix stack(s), then multiply in the placement and fit transform.
5. `setInUi(true)`, call the dispatcher's render (`actor, position 0, rotation 0, true`), then
   `setInUi(false)`.
6. Pop the stack(s), destroy the render context, then destroy the screen-context copy.

## 2. Preconditions (each one was a crash or a hang)

- **World settled.** Never call this while a world is loading or right after leaving one. Gate on
  "the local player ticked recently" plus a settle delay, and drop every cached actor pointer on a
  world change.
- **HUD on top.** Draw only while the HUD screen is the top scene. Read the scene stack's top
  scene and ask it whether it is the screen named `hud_screen`, through the scene's own vtable
  method. Check the vtables of the client, the scene stack and the scene first. A different scene
  type on top (for example a web-UI scene) means do not draw.
- **The actor.** Use only the actor pointer resolved and validated in this frame. Pre-check
  everything the dispatcher terminates on: the components it requires, a non-zero bounding box, a
  rotation component. Do not call optional setters that terminate on a missing component.
- **The context storage.** Give the render context an aligned stack buffer of the exact size,
  destroy it on every path, and destroy the screen-context copy last.

## 3. Pose and size

- Pose: save the actor's body / head / rotation fields, set a fixed preview pose (for example a
  front-right three-quarter view), render, and restore every saved field. The engine's own
  preview pose is a good default.
- Size: fit the bounding box into the box. `s = min(inner / footprint, inner / height)`, with
  footprint = the larger horizontal side times a margin for the turn. Change size only through
  this transform. Never render at another resolution and rescale.
- Origin: place the feet at `boxCentreY + height * s / 2`, so the model is centred vertically.

## 4. Putting the model inside the panel

- Reserve the panel's icon slot for the model and lay text out to its right, as for a block
  icon. Give the model box a fixed size for every mob.
- Draw the panel frame with a **hole** over the model box. A nine-slice with an optional hole
  draws its centre piece as four bands around the hole, with proportional UVs.
- Fill the hole with the panel's body colour so the model does not look cut out. The fill and the
  model must obey the single-pass rule in §5.
- Order within the pass: frame (with the hole), fill, flush images, block icon (if any), model,
  text.

## 5. The pitfall: the overlay hook runs several times per frame

A hook placed after the ScreenView render fires **once per screen view rendered**, which is
several times per frame. Text and 2D images look the same however often they are redrawn. A 3D
model does not.

What happened:
- Pass 1 filled the box and drew the model. Pass 2 filled the box again, covering the model, and
  drew the model again at exactly the same depth. That draw failed the depth test against pass 1,
  so only the fill remained.
- Opaque mobs (cow, sheep, horse) vanished. A mob whose material does not reject equal depth
  (villager) kept showing.
- The bug appeared only when something was drawn under the model. With an empty hole there was
  nothing to cover the model from pass 1, so it showed.

Fix: draw the model, and anything drawn under it, only in the **first pass of each frame**.
Detect it as a pass that starts a few milliseconds (3 ms worked) after the previous pass ended.
Later passes draw the panel with the hole and nothing in it.

## 6. Wrong theories, and why they should have been rejected sooner

Each of these cost at least one build and one device round:

| Theory | Test | Why it could not have been right |
|---|---|---|
| Mesh submission is batched and flushed after the UI | cleared a screen-context field that selects batching | It forced the batch path instead and broke the mob that worked. The batch queue comes from a render-context field that its constructor zeroes, so this path never batches. |
| Depth scaled with the model puts parts behind the UI depth | left depth unscaled | It did not explain why one mob type always worked. |
| The HUD projection's depth range is wrong | copied the inventory horse's own projection | Same objection. Nothing changed. |

Rules from this:
- **A theory must explain the working cases too.** Before building, write down what the theory
  predicts for every observation: which mobs show, with and without the fill, inside and outside
  the panel. If it does not explain why one mob keeps working, it is not the cause.
- **Read the decision site before changing a field.** The batching decision was a condition on
  two fields. Reading the caller chain to see where the batcher came from would have ruled the
  theory out with no build at all.
- **A symptom that depends on change over time points to time.** "Ghosts appear only while the
  slider is dragged fast, the value does not matter" means multiple passes, frame boundaries or
  stale state, not a wrong number.

## 7. Debugging without logs

The tester had no logcat, so every observation came from the screen:
- Ask for comparisons, not descriptions: which mobs show, inside vs outside the panel, with vs
  without the background.
- A temporary slider for one numeric unknown settles it in one device session. Even when the value
  turns out not to matter, watching what changes while the slider moves can reveal the mechanism.
  Remove the slider in the build that fixes the value.
- Change one variable per build, so each device answer rules something in or out.

## 8. Checklist

- [ ] Render path copied from an engine UI actor renderer; every called function pinned and checked.
- [ ] World-settled gate, HUD-on-top gate, per-frame actor validation, terminate preconditions.
- [ ] Render context and screen-context copy destroyed on every path.
- [ ] Pose saved and restored; size changed only by the fit transform.
- [ ] Model and its background drawn once per frame, not once per overlay pass.
- [ ] Panel frame drawn with a hole over the model box.
- [ ] Tested with an opaque mob (cow, sheep), a mob with another material (villager), a large mob
      (horse) and an add-on mob.
