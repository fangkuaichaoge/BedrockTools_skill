#!/usr/bin/env python3
"""Pointed-field vector scan: prove where a pointer field lives by finding
`ldr xT, [xN, #DISP]` followed by a vector load from `[xT, #0]`.

Component pointers (a state-vector / rotation component) are consumed as
"load the pointer, then read a Vec3/Vec2 at offset 0 of that object". Counting
that pair per candidate displacement shows a field moving between builds: the
old displacement's count disappears and reappears at the new one with the same
number. Example shape (all values are placeholders):

    candidate displacement   old build count   new build count
    <d-old>                  <N1>              -
    <d-new>                  -                 <N1>

Usage:
    python ptr_vec_scan.py <lib.so> <candidate displacements, comma separated>

Example:
    python ptr_vec_scan.py <lib.so> <disp1,disp2,...>
"""
import struct
import sys

LDRX = (0xFFC00000, 0xF9400000, 8)     # LDR Xt, [Xn, #imm12*8]
VECTOR_LOADS = [
    (0xFFC00000, 0x3DC00000, 16),      # LDR Qt (128-bit)
    (0xFFC00000, 0xFD400000, 8),       # LDR Dt
    (0xFFC00000, 0xBD400000, 4),       # LDR St
]


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
            segs.append((p_offset, p_filesz))
    return data, segs


def count(path, candidates, window=4):
    data, segs = parse_elf(path)
    hits = {c: 0 for c in candidates}
    for p_offset, p_filesz in segs:
        end = p_offset + (p_filesz & ~3)
        for off in range(p_offset, end, 4):
            w = struct.unpack_from("<I", data, off)[0]
            if (w & LDRX[0]) != LDRX[1]:
                continue
            disp = ((w >> 10) & 0xFFF) * LDRX[2]
            if disp not in hits:
                continue
            rt = w & 0x1F
            for step in range(1, window + 1):
                nxt = off + step * 4
                if nxt + 4 > end:
                    break
                w2 = struct.unpack_from("<I", data, nxt)[0]
                matched = False
                for mask, value, scale in VECTOR_LOADS:
                    if (w2 & mask) != value:
                        continue
                    if ((w2 >> 5) & 0x1F) == rt and ((w2 >> 10) & 0xFFF) * scale == 0:
                        hits[disp] += 1
                        matched = True
                        break
                if matched:
                    break
    return hits


if __name__ == "__main__":
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    path = sys.argv[1]
    candidates = [int(x, 0) for x in sys.argv[2].split(",")]
    hits = count(path, candidates)
    print(f"== {path}")
    for disp in sorted(hits):
        print(f"  ptr@0x{disp:X} -> vector-load count = {hits[disp]}")
