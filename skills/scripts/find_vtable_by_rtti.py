#!/usr/bin/env python3
"""Locate a class vtable in a stripped ARM64 shared library through its RTTI name.

Usage:
    python find_vtable_by_rtti.py <lib.so> <exact-typeinfo-name> [--slots 120] [--json out.json]

Itanium RTTI layout used here:
    typeinfo:   [ +0 ] vptr to typeinfo vtable   [ +8 ] pointer to mangled name
    vtable:     [ -8 ] pointer to typeinfo        [ 0 ] first virtual function

The script finds the name string, finds the pointer to it, derives the typeinfo address, then finds
the vtable that references that typeinfo. Slot entries are printed with their virtual addresses.
"""

from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path


def parse_segments(path: Path):
    data = path.read_bytes()
    if data[:4] != b"\x7fELF":
        raise SystemExit("not an ELF file")
    e_phoff = struct.unpack_from("<Q", data, 0x20)[0]
    e_phentsize = struct.unpack_from("<H", data, 0x36)[0]
    e_phnum = struct.unpack_from("<H", data, 0x38)[0]
    segments = []
    for index in range(e_phnum):
        base = e_phoff + index * e_phentsize
        p_type = struct.unpack_from("<I", data, base)[0]
        if p_type != 1:
            continue
        p_flags = struct.unpack_from("<I", data, base + 4)[0]
        p_offset = struct.unpack_from("<Q", data, base + 8)[0]
        p_vaddr = struct.unpack_from("<Q", data, base + 16)[0]
        p_filesz = struct.unpack_from("<Q", data, base + 32)[0]
        segments.append((p_offset, p_vaddr, p_filesz, p_flags))
    return data, segments


def vaddr_of(data: bytes, segments, file_offset: int) -> int | None:
    for p_offset, p_vaddr, p_filesz, _ in segments:
        if p_offset <= file_offset < p_offset + p_filesz:
            return p_vaddr + (file_offset - p_offset)
    return None


def file_offset_of(segments, vaddr: int) -> int | None:
    for p_offset, p_vaddr, p_filesz, _ in segments:
        if p_vaddr <= vaddr < p_vaddr + p_filesz:
            return p_offset + (vaddr - p_vaddr)
    return None


def find_all(haystack: bytes, needle: bytes) -> list[int]:
    hits = []
    start = 0
    while True:
        found = haystack.find(needle, start)
        if found < 0:
            return hits
        hits.append(found)
        start = found + 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("so", type=Path)
    parser.add_argument("name", help="mangled typeinfo name, e.g. 15ChestBlockActor")
    parser.add_argument("--slots", type=int, default=120)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()

    data, segments = parse_segments(args.so)
    executable = [(o, v, s) for o, v, s, f in segments if f & 1]
    if not executable:
        raise SystemExit("no executable segment")
    exec_start, exec_end = executable[0][1], executable[0][1] + executable[0][2]

    name_hits = find_all(data, args.name.encode("ascii"))
    print(f"name occurrences: {len(name_hits)}")
    candidates = []
    for hit_offset in name_hits:
        name_vaddr = vaddr_of(data, segments, hit_offset)
        if name_vaddr is None:
            continue
        # Typeinfo stores the name pointer; find where that value lives.
        needle = struct.pack("<Q", name_vaddr)
        for pointer_offset in find_all(data, needle):
            pointer_vaddr = vaddr_of(data, segments, pointer_offset)
            if pointer_vaddr is None:
                continue
            typeinfo_vaddr = pointer_vaddr - 8
            vtable_needle = struct.pack("<Q", typeinfo_vaddr)
            for vtable_ref_offset in find_all(data, vtable_needle):
                vtable_ref_vaddr = vaddr_of(data, segments, vtable_ref_offset)
                if vtable_ref_vaddr is None:
                    continue
                candidates.append({
                    "name": args.name,
                    "name_vaddr": name_vaddr,
                    "typeinfo_vaddr": typeinfo_vaddr,
                    "vtable_vaddr": vtable_ref_vaddr + 8,
                })

    if not candidates:
        print("no vtable found")
        return 1

    seen = set()
    result = []
    for candidate in candidates:
        vtable = candidate["vtable_vaddr"]
        if vtable in seen:
            continue
        seen.add(vtable)
        slots = []
        for index in range(args.slots):
            entry_offset = file_offset_of(segments, vtable + index * 8)
            if entry_offset is None or entry_offset + 8 > len(data):
                slots.append(None)
                continue
            value = struct.unpack_from("<Q", data, entry_offset)[0]
            if exec_start <= value < exec_end:
                slots.append(value)
            else:
                slots.append(None)
        candidate["slots"] = slots
        result.append(candidate)
        print(f"\nvtable {hex(vtable)}  typeinfo {hex(candidate['typeinfo_vaddr'])}")
        for index, value in enumerate(slots):
            if value is None:
                continue
            print(f"  slot {index:3d}  {hex(value)}")

    if args.json:
        args.json.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"\nreport: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
