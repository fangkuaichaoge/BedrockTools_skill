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
  cluster <so> --addrs 0x...             group those callers into drivers
  guards <so> --addrs 0x...              bit tests that follow each call
  ptr    <so> --addrs 0x...              8-byte data pointers equal to a target

The `func`/`callers` commands answer the two questions that matter most when
you find a candidate address: *which function is this inside*, and *who calls
it* (see `references/signature-forensics.md`).

`cluster` and `guards` answer the next two, for a *family* of related functions
(the per-face geometry emitters, say) rather than a single one: *which small set
of driver functions calls them all in sequence*, and *what decision mask does
each call site test*. Both are pure structure recovery, so they work on a
stripped binary with no symbols and no disassembler database. See
`references/targeted-static-analysis.md`.
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


def cluster_call_sites(sites, window=0x900):
    """Group sorted call-site addresses into clusters.

    A set of related functions (the per-cube-face emitters, say) is called from
    a small number of "driver" functions, each of which calls several of them in
    sequence. Grouping all call sites by proximity isolates those drivers
    without any symbol information - which is the whole point on a stripped
    binary.

    Returns a list of (first, last, [sites]) sorted by address.
    """
    if not sites:
        return []
    ordered = sorted(set(sites))
    out, cur = [], [ordered[0]]
    for s in ordered[1:]:
        if s - cur[-1] <= window:
            cur.append(s)
        else:
            out.append((cur[0], cur[-1], cur))
            cur = [s]
    out.append((cur[0], cur[-1], cur))
    return out


def guards_after_call(d, call_addr, md, max_scan=8):
    """Conditional bit tests that follow a call.

    On this engine a per-face visibility mask is tested right after each face
    emitter call: `tbz/tbnz wN, #bit, <target>`. Reading those tests back is how
    you recover both the mask register and the bit assigned to each face -
    information no symbol table will give you.

    The tests do not have to be the very next instructions (a few register moves
    or arithmetic can sit between), so keep scanning until a control-flow
    instruction appears. A second `bl` ends the window: at that point the next
    call's own prologue begins.

    Returns [(addr, mnemonic, reg, bit, target)].
    """
    out = []
    code = d[call_addr + 4:call_addr + 4 + max_scan * 4]
    for insn in md.disasm(code, call_addr + 4):
        if len(out) >= max_scan:
            break
        if insn.mnemonic in ("tbz", "tbnz"):
            parts = [p.strip() for p in insn.op_str.split(",")]
            if len(parts) == 3:
                try:
                    out.append((insn.address, insn.mnemonic, parts[0],
                                int(parts[1].lstrip("#")),
                                int(parts[2].lstrip("#"), 16)))
                except ValueError:
                    pass
        elif insn.mnemonic in ("b", "ret", "blr", "br"):
            break
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
CMDS = ("xref", "disas", "func", "callers", "cluster", "guards", "ptr")

USAGE = """\
usage: a64.py <command> <libminecraftpe.so> [options]

commands:
  func    --addr 0xADDR              enclosing function of an address
  callers --addrs 0xADDR[,0xADDR]    direct BL callers of the target(s)
  cluster --addrs 0xADDR[,...]       group those callers into driver functions
  guards  --addrs 0xADDR[,...]       bit tests following each call (a mask)
  xref    --addrs 0xADDR[,...]       ADRP(+ADD/LDR) references to data
  disas   --addr 0xADDR [--count N]  linear disassembly
  ptr     --addrs 0xADDR[,...]       8-byte data pointers equal to a target

options:
  --window 0xN   cluster grouping distance (default 0x900)
  --limit N      max rows printed per target (default 8)

Addresses are virtual addresses; on Bedrock Android file offset == vaddr, so an
address from a raw scan is usable as-is here and in a hook.
"""


def die(msg):
    """Report a usage/environment problem in one line, never a traceback.

    A Python traceback reads as "the tool is broken", which sends the reader
    off to a heavyweight disassembler instead of fixing the argument. Every
    failure this tool can hit is a one-line fix, so say it in one line and
    return a non-zero status.
    """
    print("a64.py: error: %s" % msg, file=sys.stderr)
    print(USAGE, file=sys.stderr)
    return 2


def main():
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0

    # Accept "<cmd> <so> ..." or "<so> <cmd> ...".
    if argv[0] in CMDS:
        cmd, rest = argv[0], argv[1:]
    elif len(argv) >= 2 and argv[1] in CMDS:
        cmd, rest = argv[1], [argv[0]] + argv[2:]
    else:
        return die("unknown command %r (expected one of: %s)"
                   % (argv[0], ", ".join(CMDS)))

    if not rest:
        return die("missing path to the .so")
    so, flags = rest[0], rest[1:]

    try:
        with open(so, "rb") as fh:
            d = fh.read()
    except OSError as e:
        return die("cannot read %s: %s" % (so, e.strerror or e))
    if d[:4] != b"\x7fELF":
        return die("%s is not an ELF file" % so)

    secs = parse_elf(d)
    text = sec_by_name(secs, ".text")
    if text is None:
        return die("%s has no .text section" % so)

    def arg(flag, default=None):
        if flag in flags:
            i = flags.index(flag)
            return flags[i + 1] if i + 1 < len(flags) else None
        return default

    def addrs():
        v = arg("--addrs")
        if not v:
            return None
        try:
            return [int(a, 16) for a in v.replace(" ", "").split(",") if a]
        except ValueError:
            return None

    def hexarg(flag):
        v = arg(flag)
        if v is None:
            return None
        try:
            return int(v, 16)
        except ValueError:
            return None

    if cmd == "func":
        addr = hexarg("--addr")
        if addr is None:
            return die("func needs --addr 0xADDR")
        entries = function_table(d, secs)
        f = enclosing_function(entries, addr)
        if f:
            print(f"addr {addr:#x} -> function start {f:#x}")
        else:
            print(f"addr {addr:#x} -> no function record "
                  f"(not code, or .eh_frame_hdr is absent)")
        return 0

    if cmd == "disas":
        addr = hexarg("--addr")
        if addr is None:
            return die("disas needs --addr 0xADDR")
        try:
            count = int(arg("--count", "60"))
        except ValueError:
            return die("--count must be a decimal number")
        code = d[addr:addr + count * 4]
        if not code:
            return die("address %#x is past the end of the file" % addr)
        md = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
        for insn in md.disasm(code, addr):
            print(f"{insn.address:#012x}  {insn.bytes.hex(' '):<24} "
                  f"{insn.mnemonic:<10} {insn.op_str}")
        return 0

    if cmd == "xref":
        targets = addrs()
        if not targets:
            return die("xref needs --addrs 0xADDR[,0xADDR]")
        table = build_addr_index(scan_adrp_xrefs(d, text))
        entries = function_table(d, secs)
        for a in targets:
            hits = table.get(a, [])
            fstart = enclosing_function(entries, hits[0][0]) if hits else None
            print(f"data {a:#012x}: {len(hits)} xref(s)"
                  + (f"  func={fstart:#x}" if fstart else ""))
            if not hits:
                print("    (none: the address may be reached from a data table, "
                      "or the literal is namespaced - search the full "
                      "NUL-terminated string instead of a substring)")
            for code, kind, reg in sorted(hits)[:20]:
                print(f"    {code:#012x}  {kind} x{reg}")
        return 0

    if cmd == "callers":
        targets = addrs()
        if not targets:
            return die("callers needs --addrs 0xADDR[,0xADDR]")
        res = scan_bl_callers(d, text, targets)
        entries = function_table(d, secs)
        for t in targets:
            callers = sorted(res.get(t, []))
            print(f"target {t:#012x}: {len(callers)} direct caller(s)")
            if not callers:
                print("    (none: reached indirectly, via a vtable or callback)")
            for c in callers[:25]:
                f = enclosing_function(entries, c)
                print(f"    {c:#012x}  func {f:#x}" if f else f"    {c:#012x}")
        return 0

    if cmd == "cluster":
        targets = addrs()
        if not targets:
            return die("cluster needs --addrs 0xADDR[,0xADDR]")
        try:
            window = int(arg("--window", "0x900"), 16)
        except ValueError:
            return die("--window must be a hex number such as 0x900")
        res = scan_bl_callers(d, text, targets)
        all_sites = sorted({c for t in targets for c in res.get(t, [])})
        if not all_sites:
            print("no direct call sites found for the given target(s); these "
                  "functions may be reached indirectly (vtable or callback).")
            return 0
        entries = function_table(d, secs)
        for (lo, hi, _sites) in cluster_call_sites(all_sites, window):
            found = {}
            for t in targets:
                for c in res.get(t, []):
                    if lo <= c <= hi:
                        found[t] = c
            f = enclosing_function(entries, lo)
            print(f"cluster {lo:#012x}..{hi:#012x}  "
                  f"{len(found)}/{len(targets)} target(s)"
                  + (f"  func={f:#x}" if f else ""))
            for t in targets:
                if t in found:
                    print(f"    {t:#012x} called at {found[t]:#012x}")
        return 0

    if cmd == "guards":
        targets = addrs()
        if not targets:
            return die("guards needs --addrs 0xADDR[,0xADDR]")
        try:
            limit = int(arg("--limit", "8"))
        except ValueError:
            return die("--limit must be a decimal number")
        res = scan_bl_callers(d, text, targets)
        md = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
        for t in targets:
            sites = sorted(res.get(t, []))
            guarded = [(c, gs) for c in sites
                       for gs in [guards_after_call(d, c, md)] if gs]
            print(f"target {t:#012x}: {len(sites)} call site(s), "
                  f"{len(guarded)} with bit-test(s) after the call")
            for (c, gs) in guarded[:limit]:
                txt = "  ".join(f"{g[1]} {g[2]},{g[3]} -> {g[4]:#x}" for g in gs)
                print(f"    call {c:#012x}:  {txt}")
        return 0

    if cmd == "ptr":
        targets = addrs()
        if not targets:
            return die("ptr needs --addrs 0xADDR[,0xADDR]")
        res = scan_data_pointers(d, secs, targets)
        for t in targets:
            hits = res.get(t, [])
            print(f"target {t:#012x}: {len(hits)} data pointer(s)")
            for name, addr in hits[:20]:
                print(f"    {addr:#012x} [{name}]")
        return 0

    return die("unhandled command %r" % cmd)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
    except BrokenPipeError:
        raise SystemExit(0)
