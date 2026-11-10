# Container item moves (technical reference)

Read `engine-ui-buttons-and-overlays.md` for the trigger button and `container-and-slot-reversing.md`
for reading contents.

## 1. Read

`getItemStack(controller, collection, index)` → real `ItemStack*`, or the engine's global empty
stack for `index < 0`, `index >= size`, or an empty slot. Learn the sentinel once with `index = -1`;
never enumerate until the first empty slot (empty slots return the sentinel too).

| collection | slots |
|---|---:|
| `inventory_items` | 27 |
| `hotbar_items` | 9 |
| `container_items` | 27 |

Do not read the size from the container object: the registry → node → wrapper → container walk
returns garbage, and the `ContainerModel` size slot is `mov w0, #0; ret`.

## 2. Write — measured semantics

Controller vtable slots, resolved from the live controller:

| slot | role | behaviour |
|---:|---|---|
| 58 | select | records `(collection, index)` as the held stack; **no transaction** |
| 56 | `moveSelectedTo(collection, index)` | moves the held stack into the target slot; commits a real drag-and-drop transaction |
| 71 | raw `moveItem(...)` | builds a transaction; observed accepted with **no effect** — do not use without a read-back proof |

`select` + `moveSelectedTo` = **exactly one item**, verified from per-step slot deltas:

```
21 -> 22   slot21 4->3->2->1->0    slot22 60->61->62->63->64     (4 items = 4 calls)
14 -> 13   slot14 64->63           slot13  0->1                  (empty destination)
```

Consequences:

- a 64-stack move costs 64 calls → pacing and a step budget are part of the design;
- the pair ends with the cursor empty; **an extra "return the remainder" call is a bug** (it caused
  `64 → 63 + 1` splits);
- transferring onto a different-kind slot is unverified → treat as illegal, use an empty slot as
  buffer.

## 3. Screen gating (required)

"A container screen is open" also matches furnace / campfire / anvil screens with a handful of
slots. Addressing indices that do not exist there splits stacks. Read the controller's RTTI name
(`object → vtable → vtable[-1] → type_info → +8`) and allow-list it:

`ChestScreenController`, `BlockContainerScreenController`, `CraftingScreenController`.
Anything else: log and refuse, change nothing.

## 4. Planner

- Compute **one** target layout from the current contents: kinds in name order, each filled to the
  stack cap front-to-back with the remainder last, later slots empty.
- Define `distance = Σ (wrong-kind items + missing items + |count − wanted|)`; only issue steps that
  lower it. Termination proof and stall detection for free.
- Rebuild every burst from a fresh read; never keep a long-lived plan.
- Donors must be provably surplus. Never touch a settled slot (this makes the front prefix monotone
  and rules out ping-pong).
- Verify per-kind totals after each burst; any change means something moved outside the mod → stop.

Two planner shapes that fail on device: separate merge/order passes (they fight, 116 logged steps of
`13->14 / 14->13`), and front-to-back fixing with no buffer (a full grid gives 0 moves).

## 5. Pacing

8–12 single-item transfers per burst, one burst per 60–80 ms (≈ 150–200 items/s). Hundreds of
transactions in one frame desyncs the local server and drops the player ("server not found").
27 slots ≈ 1 s.

## 6. Alternative

Capture-and-replay: hook the function that turns a real drag into an `ItemStackRequest`, capture one
genuine drag, replay it with the two slot-field words patched. Inherits the engine's merge/swap
decisions but needs a calibration drag and a per-build address for the move driver (ambiguous on
the target build — 8 prologue candidates). Use it when the direct verb cannot be verified.

## 7. Experience

- Measure the primitive first (items per call, empty destination, held-stack end state); put the
  numbers in a comment above the call site.
- One line per primitive call in a file beats any amount of logcat: `slot 21: 4 -> 3 | slot 22:
  60 -> 61`. Log to a path the user can fetch; strip before release.
- Test the two boundaries explicitly: completely full grid, and a screen whose slot count is not the
  assumed one.
- A refusal is always better than a wrong move — the user can still sort by hand.
- Verify conservation after every batch; a single unexpected count difference means stop now, not
  "press on and see".
