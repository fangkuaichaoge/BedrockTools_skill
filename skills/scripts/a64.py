#!/usr/bin/env python3
"""Offline AArch64 / ELF helpers for a stripped Bedrock .so (read-only).

Self-contained: standard library + numpy + capstone only.  Works on a
`libminecraftpe.so` on disk, with no device and no disassembler GUI.

Image base is 0 on Bedrock Android, so file offset == virtual address for
`.text`; every address this tool prints is directly usable in a disassembler
and at runtime.

Sub-commands
------------
  xref   <so> --addrs 0x...,0x...        ADRP(+ADD/LDR) code references to data
  disas  <so> --addr 0x... [--count 60]  linear disassembly
  func   <so> --addr 0x...               enclosing function (from .eh_frame_hdr)
  callers <so> --addrs 0x...             direct BL/B callers of a target
  ptr    <so> --addrs 0x...              8-byte data pointers equal to a target

The `func`/`callers` commands answer the two questions that matter most when
you find a candidate address: *which function is this inside*, and *who calls
it* (see `references/signature-forensics.md`).
"""
import struct
import sys

import numpy as np
from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN


# --------------------------------------------------------------------------- #
# ELF
# --------------------------------------------------------------------------- #
def parse_elf(d):
    e_shoff = struct.unpack_from("<Q", d, 0x28)[0]
    e_shentsize = struct.unpack_from("<H", d, 0x3A)[0]
    e_shnum = struct.unpack_from("<H", d, 0x3C)[0]
    e_shstrndx = struct.unpack_from("<H", d, 0x3E)[0]
    secs = []
    for i in range(e_shnum):
        o = e_shoff + i * e_shentsize
        f = struct.unpack_from("<IIQQQQIIQQ", d, o)
        secs.append({"name_off": f[0], "type": f[1], "addr": f[3], "off": f[4],
                     "size": f[5], "entsize": f[9]})
    sh = secs[e_shstrndx]

    def nm(off):
        o = sh["off"] + off
        e = d.index(b"\x00", o)
        return d[o:e].decode("utf-8", "replace")

    for s in secs:
        s["name"] = nm(s["name_off"])
    return secs


def sec_by_name(secs, name):
    return next((s for s in secs if s["name"] == name), None)


# --------------------------------------------------------------------------- #
# .eh_frame_hdr -> sorted function start addresses
# --------------------------------------------------------------------------- #
def function_table(d, secs):
    hdr = sec_by_name(secs, ".eh_frame_hdr")
    if not hdr:
        return []
    base = hdr["off"]

    def deref(ptr_enc, field_off):
        """Decode a DW_EH_PE encoded pointer; returns an absolute file offset.

        Upper nibble is the application (0x10 = pcrel), lower nibble the format
        (0x03 = udata4, 0x0B = sdata4).  Image base is 0, so a field's virtual
        address equals its file offset.
        """
        if ptr_enc == 0x03:      # absptr udata4
            return struct.unpack_from("<I", d, field_off)[0]
        if ptr_enc == 0x1B:      # pcrel sdata4
            v = struct.unpack_from("<i", d, field_off)[0]
            return field_off + v
        raise ValueError(f"unsupported DW_EH_PE {ptr_enc:#x}")

    if d[base] != 1:
        raise ValueError("bad .eh_frame_hdr version")
    eh_frame_ptr_enc = d[base + 1]
    table_enc = d[base + 3]
    pos = base + 4
    deref(eh_frame_ptr_enc, pos)
    pos += 4
    count = struct.unpack_from("<I", d, pos)[0]
    pos += 4
    if table_enc != 0x3B:  # datarel sdata4
        raise ValueError(f"unsupported table encoding {table_enc:#x}")

    entries = []
    for i in range(count):
        o = pos + i * 8
        loc_rel = struct.unpack_from("<i", d, o)[0]
        fde_rel = struct.unpack_from("<i", d, o + 4)[0]
        entries.append((base + loc_rel, base + o + 4 + fde_rel))
    return entries


def enclosing_function(entries, vaddr):
    lo, hi = 0, len(entries) - 1
    best = None
    while lo <= hi:
        mid = (lo + hi) // 2
        if entries[mid][0] <= vaddr:
            best = entries[mid][0]
            lo = mid + 1
        else:
            hi = mid - 1
    return best


# --------------------------------------------------------------------------- #
# Scans
# --------------------------------------------------------------------------- #
def scan_adrp_xrefs(d, text):
    """Return (code_addr, target_addr, kind, dest_reg) for ADRP+ADD / ADRP+LDR."""
    words = np.frombuffer(d, dtype="<u4", count=text["size"] // 4, offset=text["off"])
    n = words.size
    pc = (text["addr"] + np.arange(n, dtype=np.uint64) * 4)

    is_adrp = (words & np.uint32(0x9F000000)) == np.uint32(0x90000000)
    idx = np.nonzero(is_adrp)[0]
    idx = idx[idx + 1 < n]
    if idx.size == 0:
        return []

    w = words[idx].astype(np.uint64)
    w2 = words[idx + 1].astype(np.uint64)
    p = pc[idx]

    immlo = (w >> 29) & 3
    immhi = (w >> 5) & 0x7FFFF
    imm = (immhi << 2) | immlo
    imm = np.where(imm >= (1 << 20), imm - (1 << 21), imm)   # sign extend 21 bits
    page = (p & ~np.uint64(0xFFF)) + (imm << 12)
    rd = w & 0x1F

    add_ok = ((w2 & 0x7F000000) == 0x11000000) & ((w2 >> 5 & 0x1F) == rd)
    add_rd = w2 & 0x1F
    add_imm = (w2 >> 10) & 0xFFF
    add_shift = (w2 >> 22) & 3
    add_target = page + np.where(add_shift == 1, add_imm << 12, add_imm)

    ldr_ok = ((w2 & 0xFF000000) == 0xF9000000) & ((w2 >> 5 & 0x1F) == rd)
    ldr_off = ((w2 >> 10) & 0xFFF) << (((w2 >> 30) & 1) * 3)
    ldr_slot = page + ldr_off
    ldr_rd = w2 & 0x1F

    out = []
    for sel, tgt, rd_, kind in ((add_ok, add_target, add_rd, "add"),
                                (ldr_ok, ldr_slot, ldr_rd, "ldr")):
        for s in np.nonzero(sel)[0]:
            out.append((int(p[s]), int(tgt[s]), kind, int(rd_[s])))
    return out


def build_addr_index(xrefs):
    table = {}
    for code, tgt, kind, reg in xrefs:
        table.setdefault(tgt, []).append((code, kind, reg))
    return table


def scan_bl_callers(d, text, targets):
    """{target_addr: [caller_addrs]} for direct BL/B instructions."""
    words = np.frombuffer(d, dtype="<u4", count=text["size"] // 4, offset=text["off"])
    n = words.size
    pc = (text["addr"] + np.arange(n, dtype=np.uint64) * 4)

    is_b = (words & np.uint32(0xFC000000)) == np.uint32(0x94000000)
    idx = np.nonzero(is_b)[0]
    if idx.size == 0:
        return {t: [] for t in targets}
    w = words[idx].astype(np.uint64)
    imm26 = w & 0x03FFFFFF
    imm26 = np.where(imm26 >= (1 << 25), imm26 - (1 << 26), imm26)
    dest = pc[idx] + (imm26 << 2)

    tset = set(targets)
    out = {t: [] for t in targets}
    for i, dst in enumerate(dest):
        d0 = int(dst)
        if d0 in tset:
            out[d0].append(int(pc[idx[i]]))
    return out


def scan_data_pointers(d, secs, targets):
    """8-byte little-endian pointers equal to any target (table membership)."""
    tset = set(targets)
    out = {t: [] for t in targets}
    for s in secs:
        if s["type"] not in (1, 23) or s["size"] < 8:      # PROGBITS / INIT_ARRAY
            continue
        if s["name"] in (".text", ".plt"):
            continue
        blob = d[s["off"]:s["off"] + s["size"]]
        n = len(blob) // 8
        words = np.frombuffer(blob[:n * 8], dtype="<u8")
        for t in targets:
            for i in np.nonzero(words == t)[0]:
                out[t].append((s["name"], int(s["addr"] + i * 8)))
    return out


# --------------------------------------------------------------------------- #
def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    # <cmd> <so> <flags...>  or  <so> <cmd> <flags...>
    if sys.argv[1] in ("xref", "disas", "func", "callers", "ptr"):
        cmd, so = sys.argv[1], sys.argv[2]
    else:
        so, cmd = sys.argv[1], sys.argv[2]

    with open(so, "rb") as fh:
        d = fh.read()
    secs = parse_elf(d)
    text = sec_by_name(secs, ".text")

    def arg(flag, default=None):
        return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else default

    if cmd == "func":
        entries = function_table(d, secs)
        addr = int(arg("--addr"), 16)
        f = enclosing_function(entries, addr)
        print(f"addr {addr:#x} -> function start {f:#x}" if f else "not found")
        return 0

    if cmd == "disas":
        md = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
        addr = int(arg("--addr"), 16)
        count = int(arg("--count", "60"))
        code = d[addr:addr + count * 4]
        for insn in md.disasm(code, addr):
            print(f"{insn.address:#012x}  {insn.bytes.hex(' '):<24} "
                  f"{insn.mnemonic:<10} {insn.op_str}")
        return 0

    if cmd == "xref":
        addrs = [int(a, 16) for a in arg("--addrs").split(",")]
        table = build_addr_index(scan_adrp_xrefs(d, text))
        entries = function_table(d, secs)
        for a in addrs:
            hits = table.get(a, [])
            fstart = enclosing_function(entries, hits[0][0]) if hits else None
            print(f"data {a:#012x}: {len(hits)} xref(s)"
                  + (f"  func={fstart:#x}" if fstart else ""))
            for code, kind, reg in sorted(hits)[:20]:
                print(f"    {code:#012x}  {kind} x{reg}")
        return 0

    if cmd == "callers":
        targets = [int(a, 16) for a in arg("--addrs").split(",")]
        res = scan_bl_callers(d, text, targets)
        entries = function_table(d, secs)
        for t in targets:
            callers = sorted(res.get(t, []))
            print(f"target {t:#012x}: {len(callers)} direct caller(s)")
            for c in callers[:25]:
                f = enclosing_function(entries, c)
                print(f"    {c:#012x}  func {f:#x}" if f else f"    {c:#012x}")
        return 0

    if cmd == "ptr":
        targets = [int(a, 16) for a in arg("--addrs").split(",")]
        res = scan_data_pointers(d, secs, targets)
        for t in targets:
            hits = res.get(t, [])
            print(f"target {t:#012x}: {len(hits)} data pointer(s)")
            for name, addr in hits[:20]:
                print(f"    {addr:#012x} [{name}]")
        return 0

    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
