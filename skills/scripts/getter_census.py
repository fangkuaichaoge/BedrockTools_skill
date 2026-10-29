#!/usr/bin/env python3
"""Trivial-getter census: find `ldr x0,[x0,#DISP]; ret` accessors and count
their direct BL callers.

A field of a ubiquitous base class has many direct callers; an unrelated getter
has a handful. Comparing the caller count for the same displacement across two
builds is a fast, strong way to prove that a field moved rather than
disappeared. Example shape (all values are placeholders):

    field          old build   new build
    <field>              <N1>        <N2>     <d-old> -> <d-new>

The evidence is `<N1>` and `<N2>` being nearly equal: same accessor, same field,
new displacement. A count that explodes or disappears indicates a struct
reshuffle, not a move.

Usage:
    python getter_census.py <lib.so> <disp,disp,...> [label]

Example:
    python getter_census.py <lib.so> <disp1,disp2,...> [label]
"""
import struct
import sys

LDRX = (0xFFC00000, 0xF9400000)   # LDR Xt, [Xn, #imm12*8]
RET = 0xD65F03C0


def parse_elf(path):
    data = open(path, "rb").read()
    e_phoff = struct.unpack_from("<Q", data, 0x20)[0]
    e_phentsize = struct.unpack_from("<H", data, 0x36)[0]
    e_phnum = struct.unpack_from("<H", data, 0x38)[0]
    segs = []
    for i in range(e_phnum):
        off = e_phoff + i * e_phentsize
        p_type, p_flags = struct.unpack_from("<II", data, off)
        p_offset, p_vaddr, p_paddr, p_filesz, p_memsz = struct.unpack_from("<QQQQQ", data, off + 8)
        if p_type == 1 and (p_flags & 1):
            segs.append((p_offset, p_filesz, p_vaddr))
    return data, segs


def find_getters(path, candidates):
    data, segs = parse_elf(path)
    getters = {}
    for p_offset, p_filesz, p_vaddr in segs:
        end = p_offset + (p_filesz & ~3)
        for off in range(p_offset, end - 8, 4):
            w = struct.unpack_from("<I", data, off)[0]
            if (w & LDRX[0]) != LDRX[1]:
                continue
            if (w & 0x1F) != 0 or ((w >> 5) & 0x1F) != 0:
                continue                      # only ldr x0, [x0, #imm]
            disp = ((w >> 10) & 0xFFF) * 8
            if disp not in candidates:
                continue
            if struct.unpack_from("<I", data, off + 4)[0] == RET:
                getters[p_vaddr + (off - p_offset)] = disp
    return data, segs, getters


def count_callers(data, segs, getter_vaddrs):
    counts = {addr: 0 for addr in getter_vaddrs}
    for p_offset, p_filesz, p_vaddr in segs:
        end = p_offset + (p_filesz & ~3)
        for off in range(p_offset, end, 4):
            w = struct.unpack_from("<I", data, off)[0]
            if (w & 0xFC000000) != 0x94000000:      # BL
                continue
            imm = w & 0x03FFFFFF
            if imm & 0x02000000:
                imm -= 0x04000000
            target = p_vaddr + (off - p_offset) + imm * 4
            if target in counts:
                counts[target] += 1
    return counts


if __name__ == "__main__":
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    path = sys.argv[1]
    candidates = [int(x, 0) for x in sys.argv[2].split(",")]
    label = sys.argv[3] if len(sys.argv) > 3 else path
    data, segs, getters = find_getters(path, candidates)
    counts = count_callers(data, segs, getters)
    print(f"== {label}")
    for addr, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        if n == 0:
            continue
        print(f"  getter @0x{addr:x} (field +0x{getters[addr]:X}) callers = {n}")
