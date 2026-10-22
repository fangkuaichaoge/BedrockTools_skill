#!/usr/bin/env python3
"""vtable_walk.py - recover C++ vtables from an AArch64 ELF offline.

Why this exists
---------------
Recovering "which class owns this vtable" normally means scanning *relocated*
memory at runtime for the pointer chain

    RTTI name string <- typeinfo <- vtable's RTTI slot <- virtual function slots

On a PIE image (Bedrock Android uses image base 0) that chain is stored in
R_AARCH64_RELATIVE relocations: **the 8-byte slot in the file is ZERO and the
real target lives in the .rela.dyn addend**. Scanning raw qwords therefore finds
nothing, which looks exactly like "this class has no RTTI".

This script does the same walk offline by reading `.rela.dyn`:

    addend -> slot offset      (so a value can be resolved to where it is stored)
    slot offset -> addend      (so a slot can be resolved to its relocated value)

so a virtual function address can be recovered without running the binary.

Useful facts it establishes
---------------------------
* how many virtual slots a class has. A class with **only two** slots (a
  destructor pair) has no other virtual methods - so any other member you are
  looking for is non-virtual and this tool cannot find it.
* the addresses of the virtual methods, which double as anchors for signatures.

Usage
-----
    py -3 vtable_walk.py <libminecraftpe.so> --classes 12TextureAtlas,13BlockGraphics
    py -3 vtable_walk.py <libminecraftpe.so> --names 0x2e3e15b

`--classes` takes the Itanium length-prefixed RTTI name (the mangled form:
`12TextureAtlas` is `TextureAtlas`). `--names` takes a known name-string address.

Notes
-----
* The name must be NUL-terminated at that address; a substring of a longer
  mangled name (e.g. `16AtlasItemManager` inside
  `__shared_ptr_emplace<AtlasItemManager>`) is NOT a typeinfo name and will not
  resolve.
* A class with no RTTI name (non-polymorphic, or a private typeinfo) cannot be
  found this way at all.
"""
import argparse
import struct
import sys

R_AARCH64_RELATIVE = 1027


class Elf:
    def __init__(self, data):
        self.d = data
        if data[:4] != b'\x7fELF':
            raise SystemExit('not an ELF file')
        if data[4] != 2 or data[5] != 1:
            raise SystemExit('expects ELF64 little-endian')
        if struct.unpack_from('<H', data, 18)[0] != 0xB7:
            raise SystemExit('expects AArch64')

        (self.shoff,) = struct.unpack_from('<Q', data, 0x28)
        # ELF64 header: e_shentsize @0x3A, e_shnum @0x3C, e_shstrndx @0x3E
        (self.shentsize,) = struct.unpack_from('<H', data, 0x3A)
        (self.shnum,) = struct.unpack_from('<H', data, 0x3C)
        (self.shstrndx,) = struct.unpack_from('<H', data, 0x3E)

        self.sections = []
        for i in range(self.shnum):
            off = self.shoff + i * self.shentsize
            name, typ, flags, addr, offset, size = struct.unpack_from(
                '<IIQQQQ', data, off)
            self.sections.append({'name_off': name, 'type': typ,
                                  'addr': addr, 'offset': offset,
                                  'size': size})

        strtab = self.sections[self.shstrndx]
        for s in self.sections:
            end = data.find(b'\0', strtab['offset'] + s['name_off'])
            s['name'] = data[strtab['offset'] + s['name_off']:end].decode(
                'utf-8', 'replace')

        # RELA entries: map addend -> slots holding it, and slot -> addend.
        self.rel_by_addend = {}
        self.rel_by_offset = {}
        for s in self.sections:
            if s['type'] != 4:                      # SHT_RELA
                continue
            for i in range(s['size'] // 24):
                off = s['offset'] + i * 24
                r_offset, r_info, r_addend = struct.unpack_from(
                    '<QQq', data, off)
                if (r_info & 0xFFFFFFFF) != R_AARCH64_RELATIVE:
                    continue
                self.rel_by_addend.setdefault(r_addend, []).append(r_offset)
                self.rel_by_offset[r_offset] = r_addend

    def find_string(self, needle):
        """NUL-terminated ASCII occurrences (file offset == vaddr here)."""
        out, start = [], 0
        while True:
            i = self.d.find(needle, start)
            if i < 0:
                return out
            if self.d[i + len(needle)] == 0:
                out.append(i)
            start = i + 1

    def typeinfo_for_name(self, name_vaddr):
        """typeinfo start = (slot holding &name) - 8."""
        return [h - 8 for h in self.rel_by_addend.get(name_vaddr, [])]

    def vtables_for_typeinfo(self, typeinfo_vaddr):
        """vtable base = (slot holding &typeinfo) + 8."""
        return [h + 8 for h in self.rel_by_addend.get(typeinfo_vaddr, [])]

    def read_vtable(self, base, max_slots=256):
        """Virtual function addresses in slot order, until a non-relocated slot."""
        out = []
        for i in range(max_slots):
            slot = base + i * 8
            if slot not in self.rel_by_offset:
                break
            out.append(self.rel_by_offset[slot])
        return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('so')
    ap.add_argument('--names', default='',
                    help='comma-separated RTTI name-string addresses')
    ap.add_argument('--classes', default='',
                    help='comma-separated mangled RTTI names, e.g. 12TextureAtlas')
    ap.add_argument('--max-slots', type=int, default=256)
    args = ap.parse_args()

    elf = Elf(open(args.so, 'rb').read())

    targets = []
    for tok in [t for t in args.names.split(',') if t]:
        targets.append((hex(int(tok, 16)), int(tok, 16)))
    for cls in [c for c in args.classes.split(',') if c]:
        hits = elf.find_string(cls.encode())
        if not hits:
            print('=== %s: no NUL-terminated RTTI name found' % cls)
            continue
        targets.extend((cls, a) for a in hits)

    if not targets:
        print('nothing to do: pass --classes or --names')
        return 2

    for label, name_vaddr in targets:
        print('=== %s  (RTTI name @ %#x)' % (label, name_vaddr))
        tis = elf.typeinfo_for_name(name_vaddr)
        if not tis:
            print('    no typeinfo: name string is not referenced as a type name')
            continue
        for ti in tis:
            vts = elf.vtables_for_typeinfo(ti)
            print('  typeinfo @ %#x -> %d vtable(s)' % (ti, len(vts)))
            for base in vts:
                slots = elf.read_vtable(base, args.max_slots)
                print('    vtable @ %#x  %d slot(s)' % (base, len(slots)))
                if len(slots) <= 2:
                    print('      (<=2 slots: a destructor pair. Every other '
                          'member of this class is NON-virtual.)')
                for i, fn in enumerate(slots):
                    print('      [%3d] %#x' % (i, fn))
    return 0


if __name__ == '__main__':
    sys.exit(main())
