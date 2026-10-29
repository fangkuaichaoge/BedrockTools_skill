# Container contents: finding the slots that read them

How to go from "I want to show what is inside a block" to "I can enumerate its stacks", without
guessing slot numbers. Worked through chests, hoppers and decorated pots, and written so the same
recipe applies to barrels, shulker boxes and anything else that implements the container
interface.

Read `targeted-static-analysis.md` for the tooling and `signature-forensics.md` for the general
evidence ladder; this file is the specific procedure.

---

## 1. The shape of the problem

Container contents are reached through **virtual methods on the block actor**:

* `getContainerSize()` — returns a small integer;
* `getItem(int)` — returns a pointer to the stack in that slot (or a pointer to the engine's
  global empty stack, for out-of-range indices).

The slot **indices are per class**. A hopper is not laid out like a chest, and a decorated pot is
not laid out like either. Hardcoding one class's indices and pointing them at another class is
the classic way to read garbage memory.

## 2. Find the class and its vtables

Recover the vtable layout offline from the binary, using the Itanium RTTI name
(length-prefixed: `HopperBlockActor` → `16HopperBlockActor`):

```
python scripts/vtable_walk.py <libminecraftpe.so> --classes 15ChestBlockActor,16HopperBlockActor,22DecoratedPotBlockActor
```

A block actor typically has **several vtables** (primary + one per additional base class).
The primary is the first one listed and is the one whose `vtable[0]` is the complete destructor —
that address is what you compare against `*(void**)actor` to recognise the class at run time.

## 3. Find `getContainerSize`: scan for the constant

The size accessor compiles to a literal return, so scan every vtable entry's prologue for
`mov w0, #imm; ret` (`scripts/find_constant_slots.py`). What makes the hit unambiguous is the
**neighbour**: the next slot usually returns `64` (`getMaxStackSize`).

| class | size slot | value | next slot |
|---|---|---|---|
| chest | 76 | reads a stored count (27/54) | — |
| hopper | 73 | `mov w0, #5; ret` | 74 returns 64 |
| decorated pot | 77 | `mov w0, #1; ret` | 78 returns 64 |

## 4. Find `getItem`: look for the array arithmetic

Two different functions can look like "item access":

* the **direct accessor** — does index bounds checking and address arithmetic itself;
* a **forwarding stub** a few slots later — validates, then calls a shared helper.

You want the direct one. It is self-contained and safe on empty slots:

```asm
; HopperBlockActor slot 71 (1.26.50)
mov    w8, #0x98            ; ItemStack stride
cmp    w1, #4               ; index > capacity-1 ?
adrp   x9, <empty stack>    ; engine's global empty stack
umaddl x8, w1, w8, x0       ; &mItems[index]
add    x8, x8, #0x290       ; items array offset
csel   x0, x9, x8, hi       ; out of range -> empty stack
ret
```

```asm
; DecoratedPotBlockActor slot 79 — single item, index ignored
add    x0, x0, #0x298
ret
```

Fast offline way to find it: disassemble the vtable entries and look for `umaddl`/`madd` with the
slot index, or a single `add x0, x0, #imm; ret` for a one-slot container. Confirm with a
decompile of the candidate and check it returns an `ItemStack*`.

**Do not** use the forwarding stub with the actor pointer: the stub expects a particular base
sub-object and will hand back nonsense. That mistake was made and recorded here.

## 5. Verify on every build you ship

Slot **indices** are stable across patch builds; the **internal offsets** are not. Same class, two
builds:

| what | 1.26.50 | 1.26.40 |
|---|---|---|
| hopper items array | `+0x290` | `+0x280` |
| pot item | `+0x298` | `+0x290` |

So use the slots, never the offsets. Verify each build by re-running the constant scan (indices
must come out identical) and by disassembling the new build's item slot to confirm the same shape
(`umaddl` + `csel`, or `add x0, x0, #imm; ret`).

## 6. Recognise the class at run time

```cpp
struct ContainerClass { const char* rtti; std::uintptr_t destructor; int sizeSlot; int itemSlot; int maxSlots; };
// install: destructor = resolveVtableFunction(rtti, 0, <lib>)
// per actor: compare *(void**)actor with each entry's destructor
```

Resolve the destructor through the RTTI helper rather than a byte signature: no pattern to drift,
and the launcher already has the RTTI machinery. Then call the two slots through the instance's
own vtable with the actor pointer.

Sanity checks worth keeping:

* if `getContainerSize()` returns `<= 0` or more than the class maximum, treat the actor as "not
  a container" instead of indexing with it;
* if `getItem()` returns the same pointer for every index, the class is a single-slot container.

## 7. When not to bother

A single-item container (decorated pot) also exposes an `ItemStack` member; reading it by offset
works too (it was `+664` on one build, `+656` on the next). The slot route is preferred because it
survives those offset changes, but record the offset in the notes in case a build stops exposing
the slot.

If a class does **not** implement the container interface at all, say so and stop — inventing an
offset from unrelated code will read whatever happens to be adjacent.
