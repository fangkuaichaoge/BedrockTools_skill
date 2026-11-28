# Reusing vanilla screens

The cheapest UI is the one the game already has. When a feature maps onto an existing screen
(container, workstation, storage), reuse the engine's own open path and keep any behavioural change
scoped to one session.

## Three routes, rising risk

| route | what it uses | works on | main cost |
|---|---|---|---|
| nearby | the game's own use/interact verbs on a real placed block/entity in reach | any world; the server validates like a real tap | needs a matching block nearby |
| simulated open | the engine's own "player opens container" dispatch, with the validity check handled only for that session | hosted/solo worlds | a server-side screen must exist for that station |
| identity substitution | a detached storage actor bound into the vanilla screen, with your own slot storage | hosted/solo only | most engine surface, most ownership care |

## Use the engine's own input path

Do not hand-build packets. Resolve the player's pick range, compute the target block face from the
dominant axis of the look vector, verify line of sight with a voxel traversal, and call the same
`use`/`interact`/`build` verbs the touch path calls. The server then validates exactly as for a real
tap, which removes a whole class of desync and rule-check surprises.

## Reconstructing an open intent

The event that opens a container on the host side is small and stack-constructible: a weak entity
reference, a type discriminator, a position, and an actor id sentinel. Stack-construct it with the
engine's own weak-reference helper, then call the dispatcher with the first argument suppressed, as
the engine's own callers do.

Rules:

- construct only with engine helpers; do not copy a guessed layout;
- pass exactly the arguments the engine's own call site passes; read that call site first;
- one open request per user action; de-duplicate repeated use for a short window;
- if the station has no host-side model, the route does not exist — detect that and fall back.

## Session-scoped validity handling

A screen closes itself when the model's validity check fails (block gone, distance, wrong position).
Hook the concrete model's `isValid` slot and return the **base class result only for the named
session**: match the model class, the block position and, where relevant, a per-model discriminator
such as a workbench byte. Everything else still runs the original check.

- Never return a blanket true; that removes the check for every container in the world.
- The session must name what it covers: station, position, owner. Expire it.
- Re-evaluate the allowance every time the check runs; do not cache "valid".
- If a new model type is added, the hook list must grow; a missing one silently closes the screen.

## Identity substitution

To back a vanilla screen with your own data, bind a detached actor (constructed, not placed) into
the screen's storage slot, exchange the container's slot vector for your own fixed-shape storage, and
persist through the player's own save/load callbacks.

- Bind only through the engine's setter, not by writing the field directly, if a setter exists.
- The slot vector has a fixed shape (size, begin, end pointers); preserve it exactly.
- Keep a token for "this actor is ours"; unbind only when the current binding is still that token.
- Tear down on player destroy, world leave and disable, in that order.

## Session lifetime and concurrency

- Liveness comes from the host side: refresh the session on every host player tick; expire it after
  a few seconds without a tick. A dead world must not leave an allowance active.
- Detect "solo/hosted" explicitly: this app's host player is ticking and no other host player has
  ticked recently. On a remote server, route or fall back — never apply the allowance there.
- Keep two request slots: a client-use request consumed once per client tick (dedupe repeats, short
  reopen cooldown), and a host-work request consumed by the host tick and dropped if stale.
- Take a config snapshot under the lock, release it, then call engine functions; never hold your
  state lock across an engine call that can re-enter you.

## Pitfalls

- Solo-only features shipped without a visible "not here" message look like broken mods.
- Two mods hooking the same validity slot: the later one wins; test coexistence.
- A per-tick allowance can keep a closed screen alive; scope and expire it.
- Workstations without a host-side model (for example crafting tables) cannot be opened this way.
- Some items carry their container through a component, not a block; the route must detect it.
- Unload cannot free code a game thread may be inside: mark the library non-deletable and make
  unload only clear a liveness flag.

## Checklist

1. Route chosen per item and world type; remote worlds get a logged fallback.
2. Engine input verbs used, not hand-built packets; pick range and line of sight from the engine.
3. Open event constructed with engine helpers; arguments copied from the engine's own call site.
4. Validity allowance scoped to model + position + session; the original result returned otherwise.
5. Identity substitution uses engine setters; ownership token checked before unbind.
6. Session expires on a missing host tick; queues drop stale work.
7. No state lock held across engine calls.
8. Device test plan states which routes were exercised on solo vs hosted vs remote.