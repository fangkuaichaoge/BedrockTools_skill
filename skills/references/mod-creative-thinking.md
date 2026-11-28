# Designing a mod: the thinking before the code

Technique tells you *how* to build; this reference is about *what* to build and *why*. It is the
design-thinking companion to `workflow-playbook.md`: the playbook runs a session, this one shapes
the idea that the session will build.

## 1. Start from friction, not from code

The best mods change a moment the player already has — a verb, an object, a screen, a pause. Three
reliable sources:

- **Friction**: something the game makes the player do twice, or walk to, or remember.
- **Absence**: something the game shows but flat, silent, static, or without context.
- **Recontext**: an existing system used somewhere it was not designed for, but would be useful.

Write the idea as one sentence with a verb and a removal:

> When I *<do the thing>*, I want *<the outcome>*, without *<the annoyance>*.

If the sentence needs "and also", it is two mods. Pick one for the first version.

## 2. The demo moment

If you cannot describe a ten-second demo, the feature is not designed yet — it is a wish. The demo
must be reachable in a fresh world, in under a minute of play, without special setup. "Open the
menu, tap once, the thing appears where you are looking" is a demo. "Edit a config file and
restart" is not.

The demo is also the acceptance test: if the demo cannot fail visibly, the feature cannot be
verified, and its bugs will be invisible.

## 3. The route ladder: choose the lowest rung that delivers

Every step up adds engine surface, failure modes and maintenance. Go up only when the feature
cannot exist lower.

| rung | what it uses | typical cost |
|---|---|---|
| 1. settings / data only | host config, local files | lowest |
| 2. observe a game event | one tick or event hook, read-only | low |
| 3. edit game state | a few object fields, guarded | medium |
| 4. reuse a vanilla screen | engine's own UI and open path | medium-high |
| 5. custom engine UI | UI primitives, layout, input | high |
| 6. world geometry | tessellator/material, camera, state guards | high |
| 7. own renderer | graphics API boundary | highest |
| 8. server / network | server authority, packets, rules | different project class |

Ask on every rung: *can a lower rung fake this well enough?* A label that explains the feature
often beats a panel that implements it.

## 4. Reuse before build, and never build a worse clone

Before designing any subsystem, ask which engine subsystem already does most of it. Driving the
engine's own path inherits its correctness, compatibility and look.

- If your version would be a worse copy of an existing engine feature, delete the idea or find the
  part the engine does not do.
- Prefer *observe* over *mutate*, and *reuse* over *reimplement*.
- The most valuable mods often expose something the engine already knows (a map, a value, a
  screen) in a place the player is already looking.

Four shipped idea-shapes worth keeping in the toolbox:

1. **Restore depth** — take a flat representation the game draws and give it relief.
2. **Remove a placement step** — let an object do its job without the world placement that gates
   it.
3. **Put the data where the eye is** — surface existing state as an in-world label or HUD line.
4. **Add atmosphere from world state** — turn a value the game already tracks into sound, light or
   text.

## 5. Feasibility is a design question, not an engineering one

Name the subsystem the feature belongs to, then prove the path exists: **data** (can I read it),
**action** (can I trigger it), **render** (can I present it), **persistence** (can it survive).

If the path does not exist, the feature is not "hard", it is impossible in this engine — redesign
now, before code. The redesign is usually one of: a different presentation, a different trigger, or
less feature.

## 6. Design for coexistence from the start

Assume another mod hooks the same function, the same slot, the same screen. Decide early:

- every path fails open and installs nothing rather than half;
- every hook forwards the original, every feature can be disabled at runtime;
- unload is inert if a game thread may still be inside your detour;
- no global "always true" overrides, no patches that another mod's signature depends on.

The regret test: *if this mod is installed next to mine, does mine still work, and does the player
still get both?* If the answer needs a diagnostic build, the design already failed.

## 7. Scope is a budget

Decide three things before writing code, and write them down:

- **one build or many?** one build costs a profile; many builds cost a porting pipeline.
- **one feature or a suite?** a suite multiplies the surface you must keep working.
- **client-only or server-involved?** server authority changes the whole risk class.

Cost = engine surface + maintenance + support. Cut any nice-to-have that adds a new subsystem.
A finished small mod beats an unfinished large one, every time.

## 8. Decide what survives, and how it fails

- Persistence: nothing (session), per world, per profile, or server-side. Each choice changes the
  format, the migration story and the failure story.
- Failure: every feature needs an "invisible failure" plan — one log line, draw nothing, behave
  like vanilla. Write the line's text now; it is the first thing a user will send you.
- Identify what the state belongs to (a world, an account, a session). Getting identity wrong is
  how features appear on the wrong save.

## 9. One page of design before the first line of code

Fill this in; an empty line is the risk you have not handled:

```
Goal:            one sentence with a verb
Demo:            ten seconds, fresh world, under a minute of play
Non-goals:       what this deliberately does not do
Player flow:     open -> act -> feedback -> close
Engine path:     subsystem, data, action, render
State:           what is stored, where, tied to which identity
Failure:         what the user sees when a step fails
Compatibility:   another mod on the same hook/slot/screen
Builds:          one exact build, or a porting plan
Tests:           what can be proven on the host; what needs a device
```

## 10. Iterate in visible increments

- Build the smallest visible thing first: one quad, one label, one toggle, one screen. Prove the
  path before adding state.
- Observe before you mutate; then add the second state; then the third.
- Delete experiments instead of hiding them behind flags.
- Keep a short verdict log — what was tried, what was wrong, why. The next session reads it.

## 11. Presentation is part of the design

Name, one-line description, menu copy, settings that explain themselves, an icon, and localization
keys from the first version. A feature the player cannot find is a feature that does not exist.
Write the player-facing text before the code that implements it; it forces the demo to be concrete.

## 12. Anti-patterns in idea-making

- An idea whose path the engine does not have ("make it work harder" is not a plan).
- A feature only its developer can verify — no visible demo, no observable failure.
- Building a UI where a setting would do; building a renderer where an engine call would do.
- Scope that needs server authority in a client-only mod.
- "It works on my build" treated as a design assumption.
- A worse clone of an engine feature.
- Silent failure modes ("it just doesn't show") with no log line.
- A second feature smuggled into the first one to "save a release".

## Checklist

1. One-sentence idea with a verb and a removed annoyance.
2. A ten-second demo reachable in a fresh world.
3. The lowest route rung that delivers it, justified.
4. Reuse beats build; no worse clone of an engine feature.
5. The engine path proven for data, action, render and persistence.
6. Coexistence and fail-open decided before code.
7. Scope, build policy and server involvement written down.
8. Persistence identity and the failure log line chosen.
9. The one-page design filled in, empty lines treated as risks.
10. First increment visible; experiments deleted, verdicts recorded.
11. Player-facing text and localization exist before the code does.