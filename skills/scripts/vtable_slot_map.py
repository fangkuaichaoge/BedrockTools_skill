#!/usr/bin/env python3
"""Map vtable slots of one RTTI class between two builds.

Inputs are plain-text dumps produced by `vtable_walk.py`:

    python vtable_walk.py <old.so> --classes <RTTI-class> > old_vt.txt
    python vtable_walk.py <new.so> --classes <RTTI-class> > new_vt.txt

For each requested old-build slot, fingerprint the first instructions of its
function (PC-relative immediates masked: branches, ADR/ADRP, literal loads) and
look for the new-build slot whose fingerprint matches. This shows unchanged
slots, a uniform +1-style shift caused by an inserted virtual, and slots whose
function was rewritten.

Caveats baked into this tool:

* tiny functions (a two-instruction getter) have generic fingerprints and may
  match several slots or none; confirm those by decompiling the candidate and
  checking the field displacement / returned value;
* a slot index can move with no insertion at all; always confirm the hook or call
  targets by body semantics (e.g. a container size getter returns the legal sizes,
  an item getter indexes with the known element stride).

Usage:
    python vtable_slot_map.py <old_dump> <old.so> <new_dump> <new.so> <RTTI-class> <slots>

Example:
    python vtable_slot_map.py old_vt.txt old.so new_vt.txt new.so \
        <RTTI-class> <slot,slot,...>
"""
import re
import struct
import sys

LDRX = (0xFFC00000, 0xF9400000)


def slots_from_dump(path, cls):
    text = None
    for encoding in ("utf-8-sig", "utf-16", "utf-8"):
        try:
            candidate = open(path, encoding=encoding, errors="replace").read()
        except UnicodeError:
            continue
        if "vtable @" in candidate:
            text = candidate
            break
    if text is None:
        raise SystemExit(f"no vtable dump content in {path}")
    block = re.search(r"^=== " + re.escape(cls) + r".*?(?=^=== |\Z)", text, re.S | re.M)
    if not block:
        raise SystemExit(f"{cls} not found in {path}")
    vtables = re.findall(r"vtable @ (0x[0-9a-f]+)\s+(\d+) slot", block.group(0))
    if not vtables:
        raise SystemExit(f"no vtables for {cls} in {path}")
    primary = max(vtables, key=lambda t: int(t[1]))
    for section in re.split(r"vtable @ ", block.group(0))[1:]:
        if section.split()[0] != primary[0]:
            continue
        return {int(m.group(1)): int(m.group(2), 16)
                for m in re.finditer(r"\[\s*(\d+)\]\s+(0x[0-9a-f]+)", section)}
    raise SystemExit(f"primary vtable for {cls} not parsed")


def mask_word(w):
    """Return the instruction with any PC-relative label/offset bits removed.

    Only instructions that encode an offset relative to their own address need
    this: their raw 32-bit word differs between builds even when the code is
    identical, so comparing raw words would report a false mismatch. Everything
    else keeps its full encoding.

        family          bits kept (= the mask)         operand dropped
        B / BL          opcode 31..26                  imm26
        B.cond          opcode 31..24 + cond 3..0      imm19
        CBZ / CBNZ      sf+opcode 31..24 + Rt 4..0     imm19
        TBZ / TBNZ      b5+opcode+b40 31..19 + Rt      imm14
        ADR / ADRP      op 31 + 10000 28..24 + Rd      immlo 30..29, immhi 23..5
        LDR literal /   opc 31..30 + 011+V+00 29..24   imm19
        PRFM literal    + Rt 4..0
        SIMD literal

    The LDR-literal keep-mask deliberately retains bits 31..24 in full so the
    W / X / SW / PRFM / SIMD variants stay distinguishable; masking that to the
    address-class only would merge them.
    """
    # B / BL: op = 000101 / 100101, imm26.
    if (w & 0xFC000000) in (0x14000000, 0x94000000):
        return w & 0xFC000000
    # B.cond: 0101010 0 imm19 0 cond.
    if (w & 0xFF000010) == 0x54000000:
        return w & 0xFF00001F
    # CBZ / CBNZ: sf 011010 0 imm19 Rt.
    if (w & 0x7E000000) == 0x34000000:
        return w & 0xFF00001F
    # TBZ / TBNZ: b5 011011 b40 imm14 Rt.
    if (w & 0x7E000000) == 0x36000000:
        return w & 0xFFF8001F
    # ADR / ADRP: op immlo 10000 immhi Rd.
    if (w & 0x9F000000) in (0x90000000, 0x10000000):
        return w & 0x9F00001F
    # LDR (literal) / PRFM (literal) / SIMD (literal): opc 011 V 00 imm19 Rt.
    if (w & 0x3B000000) == 0x18000000:
        return w & 0xFF00001F
    return w


def fingerprint(data, addr, count=6):
    words = struct.unpack_from("<%dI" % count, data, addr)
    return tuple(mask_word(w) for w in words)


def main():
    if len(sys.argv) < 7:
        raise SystemExit(__doc__)
    old_dump, old_so, new_dump, new_so, cls = sys.argv[1:6]
    slots = [int(x) for x in sys.argv[6].split(",")]
    old_data = open(old_so, "rb").read()
    new_data = open(new_so, "rb").read()
    old_slots = slots_from_dump(old_dump, cls)
    new_slots = slots_from_dump(new_dump, cls)
    print(f"{cls}: old {len(old_slots)} slots, new {len(new_slots)} slots")
    for slot in slots:
        if slot not in old_slots:
            print(f"  slot {slot:>3}: missing in old build")
            continue
        fp = fingerprint(old_data, old_slots[slot])
        matches = [s for s, a in new_slots.items() if fingerprint(new_data, a) == fp]
        old_addr = old_slots[slot]
        new_addr = new_slots.get(slot)
        label = ", ".join(str(m) for m in matches) if matches else "no fingerprint match"
        print(f"  slot {slot:>3}: old 0x{old_addr:x} -> new slot(s): {label}")
        if new_addr is not None:
            same_index = "yes" if fingerprint(new_data, new_addr) == fp else "no"
            print(f"           same-index new slot 0x{new_addr:x} fingerprint match: {same_index}")
    print("\nConfirm every slot you hook or call by decompiling its function:")
    print("  - container size getter returns the legal sizes,")
    print("  - item getter indexes with the known element stride,")
    print("  - tiny getters are proven by the displacement they return.")


if __name__ == "__main__":
    main()
