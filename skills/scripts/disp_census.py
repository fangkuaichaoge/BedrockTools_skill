#!/usr/bin/env python3
"""Encoded-displacement census for an AArch64 ELF (read-only, offline).

Counts, across every executable byte of a stripped library, how often each
load/store displacement appears. A struct field that moves between builds shows
the old displacement's count collapsing and a new one rising with a similar
count (normalise by image size when comparing builds); no field disappearing
rules out a struct reshuffle.

Use it to form hypotheses. Prove the field identity with a getter census or a
follow-up scan (see getter_census.py / ptr_vec_scan.py), not with counts alone.

Usage:
    python disp_census.py <lib.so> [--offsets <disp1,disp2,...>] [--json out.json]
    python disp_census.py <lib.so> --compare <other.so>

Output lists the most frequent (kind, displacement) pairs; with --offsets only
the requested displacements are shown.
"""
import json
import struct
import sys

# (name, mask, value, displacement_scale)
KINDS = [
    ("LDRx", 0xFFC00000, 0xF9400000, 8),
    ("LDRw", 0xFFC00000, 0xB9400000, 8),
    ("LDRB", 0xFFC00000, 0x39400000, 8),
    ("LDRH", 0xFFC00000, 0x79400000, 8),
    ("LDRSW", 0xFFC00000, 0xB9800000, 8),
    ("LDRq", 0xFFC00000, 0x3DC00000, 16),
    ("LDRs", 0xFFC00000, 0xBD400000, 4),
    ("STRx", 0xFFC00000, 0xF9000000, 8),
    ("STRw", 0xFFC00000, 0xB9000000, 8),
    ("STRB", 0xFFC00000, 0x39000000, 8),
    ("STRH", 0xFFC00000, 0x79000000, 8),
    ("STRq", 0xFFC00000, 0x3D800000, 16),
    ("ADDx", 0xFFC00000, 0x91000000, 1),
    ("ADDs", 0xFFC00000, 0x11000000, 1),
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


def census(path):
    data, segs = parse_elf(path)
    counts = {}
    for p_offset, p_filesz in segs:
        end = p_offset + (p_filesz & ~3)
        for off in range(p_offset, end, 4):
            w = struct.unpack_from("<I", data, off)[0]
            for name, mask, value, scale in KINDS:
                if (w & mask) != value:
                    continue
                disp = ((w >> 10) & 0xFFF) * scale
                counts[(name, disp)] = counts.get((name, disp), 0) + 1
                break
    return counts, len(data)


def main():
    args = sys.argv[1:]
    if not args:
        raise SystemExit(__doc__)
    path = args[0]
    wanted = None
    json_out = None
    compare = None
    i = 1
    while i < len(args):
        if args[i] == "--offsets":
            wanted = {int(x, 0) for x in args[i + 1].split(",")}
            i += 2
        elif args[i] == "--json":
            json_out = args[i + 1]
            i += 2
        elif args[i] == "--compare":
            compare = args[i + 1]
            i += 2
        else:
            i += 1
    counts, size = census(path)
    other = census(compare)[0] if compare else None
    rows = []
    for (kind, disp), n in counts.items():
        if wanted is not None and disp not in wanted:
            continue
        rows.append((n, kind, disp, other.get((kind, disp), 0) if other else None))
    rows.sort(reverse=True)
    print(f"== {path}  size={size}")
    if other is not None:
        print(f"   compared with {compare}")
        print(f"   {'kind':5s} {'disp':>8s} {'this':>8s} {'other':>8s}")
    for n, kind, disp, m in rows[:80]:
        if other is not None:
            print(f"   {kind:5s} 0x{disp:6X} {n:8d} {m:8d}")
        else:
            print(f"   {kind:5s} 0x{disp:6X} {n:8d}")
    if json_out:
        json.dump({"path": path, "size": size,
                   "counts": {f"{k}@0x{d:X}": v for (k, d), v in counts.items()}},
                  open(json_out, "w"), indent=1)


if __name__ == "__main__":
    main()
