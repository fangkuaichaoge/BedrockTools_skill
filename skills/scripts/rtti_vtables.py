#!/usr/bin/env python3
"""Reconstruct the post-relocation memory image of a stripped Android ARM64 library and walk
RTTI class vtables on it.

Why a relocation pass is required: in these libraries `.data.rel.ro` and `.data` are zero-filled in
the file; every pointer value is supplied at load time by an `R_AARCH64_RELATIVE` entry in
`.rela.dyn`. Searching the file for pointers finds almost nothing, and RTTI class lookups appear to
fail even though the loader (and the runtime) resolves them fine. Applying the relocation table first
produces the same bytes the game sees in memory, and then the normal Itanium layout works:

    typeinfo:  [ +0 ] pointer to typeinfo vtable   [ +8 ] pointer to mangled class name
    vtable:    [ -8 ] pointer to that typeinfo      [ 0 ] first virtual function slot

Usage:
    python rtti_vtables.py <lib.so> --class 15ChestBlockActor --slots 100
    python rtti_vtables.py <lib.so> --where 0x9a89898
    python rtti_vtables.py <lib.so> --slot 15ChestBlockActor 76
    python rtti_vtables.py <lib.so> --list-prefix BlockSource
"""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

R_AARCH64_RELATIVE = 1027


class Library:
    def __init__(self, path: Path) -> None:
        self.data = bytearray(path.read_bytes())
        self.segments = self._segments()
        # PT_LOAD flags are useless for "is this an instruction address" here (the first load segment
        # is marked executable for the whole file map). Use the real executable sections instead.
        self.exec_ranges = self._executable_sections()
        self.relocations = self._relocations()
        self._apply_relocations()
        # One immutable copy for C-speed pointer searches; the bytearray is only used for patching.
        self.image = bytes(self.data)
        self._name_cache: dict[int, str] = {}

    # -- ELF -----------------------------------------------------------------
    def _segments(self):
        data = self.data
        if data[:4] != b"\x7fELF":
            raise SystemExit("not an ELF file")
        e_phoff = struct.unpack_from("<Q", data, 0x20)[0]
        e_phentsize = struct.unpack_from("<H", data, 0x36)[0]
        e_phnum = struct.unpack_from("<H", data, 0x38)[0]
        result = []
        for index in range(e_phnum):
            base = e_phoff + index * e_phentsize
            p_type = struct.unpack_from("<I", data, base)[0]
            if p_type not in (1, 2):
                continue
            p_flags = struct.unpack_from("<I", data, base + 4)[0]
            p_offset = struct.unpack_from("<Q", data, base + 8)[0]
            p_vaddr = struct.unpack_from("<Q", data, base + 16)[0]
            p_filesz = struct.unpack_from("<Q", data, base + 32)[0]
            result.append((p_offset, p_vaddr, p_filesz, p_flags if p_type == 1 else -1))
        return result

    def _executable_sections(self):
        data = self.data
        e_shoff = struct.unpack_from("<Q", data, 0x28)[0]
        e_shentsize = struct.unpack_from("<H", data, 0x3A)[0]
        e_shnum = struct.unpack_from("<H", data, 0x3C)[0]
        ranges = []
        if not e_shoff or not e_shnum:
            return ranges
        for index in range(e_shnum):
            base = e_shoff + index * e_shentsize
            sh_type = struct.unpack_from("<I", data, base + 4)[0]
            sh_flags = struct.unpack_from("<Q", data, base + 8)[0]
            sh_addr = struct.unpack_from("<Q", data, base + 16)[0]
            sh_size = struct.unpack_from("<Q", data, base + 32)[0]
            if sh_type == 1 and (sh_flags & 0x4) and sh_size:  # SHT_PROGBITS, SHF_EXECINSTR
                ranges.append((sh_addr, sh_addr + sh_size))
        return ranges

    def _dynamic_entries(self):
        for offset, vaddr, filesz, flags in self.segments:
            if flags != -1:
                continue
            for index in range(filesz // 16):
                tag, value = struct.unpack_from("<QQ", self.data, offset + index * 16)
                if tag == 0:
                    return
                yield tag, value

    def _relocations(self) -> dict[int, int]:
        rela_addr = rela_size = None
        for tag, value in self._dynamic_entries():
            if tag == 7:
                rela_addr = value
            elif tag == 8:
                rela_size = value
        if rela_addr is None or rela_size is None:
            raise SystemExit("no .rela.dyn")
        file_offset = self.vaddr_to_offset(rela_addr)
        if file_offset is None:
            raise SystemExit("cannot map DT_RELA")
        result: dict[int, int] = {}
        unpack = struct.Struct("<QQq").unpack_from
        limit = len(self.data)
        for index in range(rela_size // 24):
            r_offset, r_info, r_addend = unpack(self.data, file_offset + index * 24)
            if (r_info & 0xFFFFFFFF) != R_AARCH64_RELATIVE:
                continue
            if 0 <= r_offset <= limit - 8:
                result[r_offset] = r_addend
        return result

    def _apply_relocations(self) -> None:
        # r_offset is a virtual address; convert it to a file offset before patching, because later
        # PT_LOAD segments carry a non-zero vaddr/offset bias.
        pack = struct.Struct("<Q").pack_into
        applied = 0
        for vaddr, addend in self.relocations.items():
            offset = self.vaddr_to_offset(vaddr)
            if offset is None or offset + 8 > len(self.data):
                continue
            pack(self.data, offset, addend)
            applied += 1
        self.applied_relocations = applied

    # -- address helpers -----------------------------------------------------
    def vaddr_to_offset(self, vaddr: int):
        for offset, seg_vaddr, filesz, flags in self.segments:
            if seg_vaddr <= vaddr < seg_vaddr + filesz:
                return offset + (vaddr - seg_vaddr)
        return None

    def read_at(self, vaddr: int, length: int) -> bytes:
        offset = self.vaddr_to_offset(vaddr)
        if offset is None:
            return b""
        return bytes(self.data[offset:offset + length])

    def c_string(self, vaddr: int, limit: int = 200) -> str:
        if vaddr in self._name_cache:
            return self._name_cache[vaddr]
        raw = self.read_at(vaddr, limit)
        end = raw.find(b"\x00")
        if end >= 0:
            raw = raw[:end]
        text = raw.decode("ascii", "replace")
        self._name_cache[vaddr] = text
        return text

    def is_exec(self, vaddr: int) -> bool:
        return any(start <= vaddr < end for start, end in self.exec_ranges)

    def pointer_value(self, vaddr: int) -> int | None:
        raw = self.read_at(vaddr, 8)
        if len(raw) != 8:
            return None
        value = struct.unpack("<Q", raw)[0]
        return value or None

    def typeinfo_name(self, typeinfo: int) -> str | None:
        name_pointer = self.pointer_value(typeinfo + 8)
        if not name_pointer:
            return None
        return self.c_string(name_pointer)

    # -- RTTI -----------------------------------------------------------------
    def find_pointers_to(self, target: int, search_start: int | None = None) -> list[int]:
        needle = struct.pack("<Q", target)
        hits = []
        start = search_start or 0
        data = self.image
        while True:
            hit = data.find(needle, start)
            if hit < 0:
                break
            start = hit + 1
            vaddr = self.offset_to_vaddr(hit)
            if vaddr is None or self.is_exec(vaddr):
                continue
            hits.append(vaddr)
        return hits

    def offset_to_vaddr(self, file_offset: int):
        for offset, seg_vaddr, filesz, flags in self.segments:
            if offset <= file_offset < offset + filesz:
                return seg_vaddr + (file_offset - offset)
        return None

    def vtables_for_name(self, name: str, limit_names: int = 64) -> list[tuple[int, str]]:
        found: list[tuple[int, str]] = []
        seen_names: set[int] = set()
        for offset, seg_vaddr, filesz, flags in self.segments:
            if flags == -1:
                continue
            chunk_view = self.image[offset:offset + filesz]
            start = 0
            while len(seen_names) < limit_names:
                hit = chunk_view.find(name.encode("ascii"), start)
                if hit < 0:
                    break
                start = hit + 1
                name_vaddr = seg_vaddr + hit
                if name_vaddr in seen_names:
                    continue
                seen_names.add(name_vaddr)
                for pointer_location in self.find_pointers_to(name_vaddr):
                    typeinfo = pointer_location - 8
                    for typeinfo_location in self.find_pointers_to(typeinfo):
                        vtable = typeinfo_location + 8
                        # Reject hits inside the relocation table: entries there also contain the
                        # typeinfo address as an addend, but they are not vtables. A real vtable
                        # starts with a destructor (code pointer).
                        first_slot = self.pointer_value(vtable)
                        if first_slot is None or not self.is_exec(first_slot):
                            continue
                        found.append((vtable, self.typeinfo_name(typeinfo) or name))
        return found

    def slot_value(self, vtable: int, index: int) -> int | None:
        return self.pointer_value(vtable + index * 8)

    def vtable_slots(self, vtable: int, count: int) -> list[int | None]:
        return [self.slot_value(vtable, index) for index in range(count)]

    def where(self, function: int, max_slots: int = 300) -> list[tuple[int, str, int]]:
        results = []
        for location in self.find_pointers_to(function):
            slot = location
            while slot - 8 >= 0:
                previous = self.pointer_value(slot - 8)
                if previous is None or not self.is_exec(previous):
                    break
                slot -= 8
            typeinfo = self.pointer_value(slot - 8)
            if not typeinfo:
                continue
            name = self.typeinfo_name(typeinfo)
            if not name:
                continue
            index = (location - slot) // 8
            if 0 <= index < max_slots:
                results.append((slot, name, index))
        return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("so", type=Path)
    parser.add_argument("--class", dest="class_name")
    parser.add_argument("--slots", type=int, default=120)
    parser.add_argument("--slot", nargs=2, metavar=("NAME", "INDEX"))
    parser.add_argument("--where", help="function vaddr, hex")
    parser.add_argument("--list-prefix")
    args = parser.parse_args()

    library = Library(args.so)
    print(f"relocations applied: {len(library.relocations)}  text ranges: {len(library.exec_ranges)}")

    if args.list_prefix:
        for offset, seg_vaddr, filesz, flags in library.segments:
            if flags == -1:
                continue
            chunk = library.image[offset:offset + filesz]
            start = 0
            while True:
                hit = chunk.find(args.list_prefix.encode("ascii"), start)
                if hit < 0:
                    break
                start = hit + 1
                vaddr = seg_vaddr + hit
                # back up to the length prefix of the mangled name
                probe = vaddr
                for back in range(0, 4):
                    text = library.c_string(probe - back, 60)
                    if text and text[0].isdigit():
                        print(f"  0x{probe - back:x}  {text}")
                        break
        return 0

    if args.where:
        function = int(args.where, 16)
        results = library.where(function)
        if not results:
            print(f"0x{function:x} is not referenced by any RTTI vtable")
        for vtable, name, index in results:
            print(f"0x{function:x} -> class {name}  slot {index}  vtable 0x{vtable:x}")
        return 0

    if args.slot:
        name, index = args.slot[0], int(args.slot[1])
        for vtable, mangled in library.vtables_for_name(name):
            value = library.slot_value(vtable, index)
            print(f"class {mangled} vtable 0x{vtable:x} slot {index} = {hex(value) if value else None}")
        return 0

    if args.class_name:
        for vtable, mangled in library.vtables_for_name(args.class_name):
            print(f"\nclass {mangled}  vtable 0x{vtable:x}")
            for index, value in enumerate(library.vtable_slots(vtable, args.slots)):
                if value is None:
                    continue
                print(f"  slot {index:3d}  0x{value:x}")
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
