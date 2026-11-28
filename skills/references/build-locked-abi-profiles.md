# Build-locked ABI profiles: one mod, one game build

Anything that touches engine internals is tied to one exact game image. Pretending otherwise is
how a mod draws nothing, calls a wrong function, or crashes. This reference is the contract for
pinning a single build, proving the pin, and refusing every other build without drama.

## When to use it

- You hook or call engine functions, read engine objects by offset, or use vtable slots.
- You ship to users whose builds you cannot enumerate.
- You want a new build added later without risking the builds that already work.

## The profile

A profile is small and static:

| part | content | why |
|---|---|---|
| build identity | the image's build-id note (20 bytes) | cheapest authoritative gate; no scanning, no guessing |
| function table | `{name, address (RVA, image-relative), byte pattern}` per engine entry | the pattern *confirms* the address; it is not a search |
| vtable identity (optional) | `{class address point, slot, expected function}` per object you call | proves the object is what you think it is |

Keep the packaging manifest broad (a version-family wildcard) so the host will load the mod at
all; the real gate lives in code and rejects everything else. A manifest-only gate either blocks
valid builds or waves through unknown ones.

## Generating the table

1. Start from **behaviour, not from a search**: find the address by reading a caller, a vtable
   slot, or an argument setup; write down that evidence.
2. Take a fixed window (128–256 bytes) at the entry.
3. Mask only what legitimately moves between builds: PC-relative immediates by instruction class
   (direct branches, address-generation pairs, conditional branches, test-and-branch, literal
   loads). Keep register allocation, stack frame shape and call structure fixed.
4. Anchor on the longest fixed run, then require **exactly one hit, at the expected address**.
   If it hits twice, lengthen or pick a different window; never loosen the pattern.
5. Emit the table and an evidence file (window, masked bytes, hit list) from the generator. A
   hand-edited table is already stale.

## Verifying it independently

A generator that shares code with the runtime proves nothing. Verify with a second pass that:

- re-derives its own mask sets and scans only executable load segments;
- asserts one hit per entry at the recorded address;
- proves **identity** where the binary keeps relocations: walk the relative relocations to show
  the function is reachable from the documented vtable address point and slot; where a typeinfo
  name exists, match it;
- records the sha256 of the image the table was generated from.

At runtime the check is cheap: resolve the image base, confirm the address lies inside an
executable segment, compare the full pattern, and (if the SDK offers it) accept a resolver result
only when it equals the recorded address. Nothing else.

## Runtime discipline

- Check **every** entry before installing the first hook. All-or-nothing: on any mismatch log one
  line naming it, install nothing, return failure. A partially installed hook set is worse than
  none.
- Identify every engine object by its vtable address point before reading a field or calling a
  slot; identify every function pointer by the executable-range test.
- Fail open per feature: a missing entry disables its feature, not the process.
- Wrap lifecycle callbacks and detour bodies so no exception crosses into the loader or the game;
  keep one deduplicated error line per failure so logs stay readable.

## Adding a second build

- One table per build id, selected by an exact match. No "closest build" fallback, no merged
  table.
- Add the new table beside the old one; leave the old branch untouched. Re-run the generator and
  the independent verifier on the new image.
- Diff the two tables: entries that changed shape (not just address) need their **callers** read
  again, because the ABI may have moved too.
- Prove the new build with a holdout: the table must hit exactly once in an image it was not
  generated from only if you deliberately generated it there; otherwise the gate should refuse.

## Checklist

1. Build id captured from the mapped image (not from the file on disk).
2. Every entry has evidence (caller/slot/argument setup), a window and a unique hit.
3. Independent verifier passes; identity proven by relocation and/or typeinfo.
4. All entries checked before the first hook; failure installs nothing and logs the name.
5. Every object read is vtable-checked; every call target is exec-range-checked.
6. Exceptions never cross a lifecycle or detour boundary.
7. A second build gets its own table, generated and verified independently.
8. Release notes name the exact build id the profile was proven on.

## Pitfalls

- A pattern that matches but points a few instructions inside the function (house-style anchors):
  hook the real entry, not the pattern hit.
- Offsets and slots drift independently of function addresses; a green signature table does not
  mean the object layout is unchanged.
- A gate that never passes looks exactly like a broken mod: log the expected and actual build id.
- Docs and headers drift; the binary and the verifier are the truth.
- A table copied into a source file by hand will diverge on the next build; always regenerate.