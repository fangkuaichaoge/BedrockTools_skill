# Changing geometry the engine already draws

Sometimes the right move is not "draw my own thing" but "change the thing the engine is already
drawing". The vanilla renderer has already computed the transform, material, texture and culling;
you only need to alter or extend its output. This is the second family of world-space rendering,
next to appending your own quads through the tessellator.

## Decide the family first

| situation | family |
|---|---|
| The engine draws the surface you want to change (a map, a sign, a frame, a block face) | rewrite in place |
| The feature is new geometry the engine does not draw | append through the tessellator (see `in-world-3d-rendering.md`) |
| You need a different material/lighting entirely | append, with your own material object |

Rewriting in place is cheaper and inherits every bit of engine state. It is also the more fragile
family: it depends on a specific call site inside a specific function.

## Callsite-keyed interception

The tool is the caller's return address: inside a hook, `__builtin_return_address(0)` identifies
the instruction after the call. Compare it to `image base + recorded call offset + 4` to know you
are inside exactly one vanilla call site, not merely inside a shared helper called from elsewhere.

- On the **first** vertex/quad of that call site, emit your replacement geometry.
- On the **following siblings**, swallow them (return without forwarding) so the vanilla shape is
  not drawn twice.
- For every other caller, forward untouched. The same function usually serves many callers; a
  global rewrite breaks them.

Record the call-site offset in the evidence table next to the function entry. A callsite key is
build-specific in exactly the same way.

## Swapping one argument

If the vanilla path builds a temporary object (a mesh, a buffer, a descriptor), you may replace
only that argument and leave tessellator, material, texture, camera and transform vanilla. Scope
the substitution by object identity: capture the renderer/host object once (vtable + field), and
only substitute when the current call belongs to that object.

Never hand the engine an unconstructed or partially initialised object; construct it with the
engine's own helpers, or let the engine construct it and then edit fields.

## Return conventions and shims

Large struct returns (a mesh of a few hundred bytes) come back through the architecture's
structure-return (`sret`) register, not the normal return register. A C function pointer cannot
express that, so the call has to be wrapped:

- a tiny hand-written assembly shim that moves the destination buffer into the structure-return
  register, sets the remaining argument registers, calls the target, and returns;
- keep the shim in its own translation unit, with the exact register/argument mapping documented
  next to it;
- assert the struct size and alignment at compile time.

The same applies to any engine function returning a non-trivial object by value.

## Geometry from image data

When the new surface is derived from a texture or map:

- convert colour to a scalar with a documented luminance formula, and expose "real heights" as an
  optional input so the two paths can be compared;
- normalise heights between the observed min and max before scaling;
- emit a side face only where a cell rises above a neighbour; never emit hidden interior faces;
- keep winding outward and state the primitive contract (a quad list takes four vertices, a
  triangle list six, order `a,b,c,a,c,d`);
- keep UVs inside the tile and the depth offset small but non-zero, or the surface z-fights.

## Scope and state

- Keep a frame depth counter and a stack-scoped "current object" pointer; save and restore both
  around the original call.
- A hook that only observes must still forward everything unchanged.
- When the substitution is scoped to one object, unbind only if the binding is still yours; a
  second mod or a re-entrant call may have replaced it.

## Pitfalls

- A pattern/drifted offset silently rewrites the wrong call site.
- Approximate image spans (colour sampling, texture coordinates) are a real source of wrong
  shapes; state the approximation.
- The GPU lifetime of a mesh created by a native call is not proven by static analysis; verify
  that it survives until the engine draws it.
- Swallowing vertices without checking the callsite destroys other callers' geometry.
- Hand-assembled shims rot with ABI changes; keep them tested against a C replica where possible.

## Checklist

1. Callsite offset recorded per build; identity checked by return address.
2. First vertex emits, siblings swallowed, other callers forwarded.
3. Argument substitution scoped by object identity, with an ownership check before unbind.
4. Structure-return shim documented, size-asserted, isolated.
5. Geometry contract (winding, primitive size, UV/depth bounds) asserted in a host test.
6. Original always called for every non-matching call.
7. Evidence table holds function, callsite, pattern and the build id.
8. Device verification still required before claiming the visual result.