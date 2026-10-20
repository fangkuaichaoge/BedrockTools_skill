#!/usr/bin/env python3
"""Check that the struct offsets a mod hard-codes are valid in every build.

Signatures proving a *function* is the same does not prove the *object layout*
is the same.  A mod that pokes `self+0xEB8` keeps resolving its signature on a
new build while silently writing to the wrong field -- the worst kind of port
bug, because nothing errors.

This tool reads the offsets back out of the game's own code: it disassembles
each build's SetupCamera (located by signature) and reports the displacements it
actually uses, so the mod's constants can be compared against ground truth.

Usage:
    python offset_check.py --sigs patterns.json --names SetupCamera \\
        --offsets 0x61c,0x624,0xeb8,0xef8,0xf38 --so a.so,b.so
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from a64 import parse_elf, sec_by_name                      # noqa: E402
from sigscan import compile_pattern, scan                   # noqa: E402
from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN  # noqa: E402

LOAD_STORE = ("str", "stp", "stur", "ldr", "ldp", "ldur", "ldrb", "ldrh")


def object_offsets(d, addr, span, keys):
    """Offsets in `keys` touched by load/store instructions in [addr, addr+span)."""
    md = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
    found = {}
    for insn in md.disasm(d[addr:addr + span], addr):
        if not insn.mnemonic.startswith(LOAD_STORE):
            continue
        op = insn.op_str
        if "[" not in op:
            continue
        for chunk in op.split("["):
            if "]" not in chunk:
                continue
            inner = chunk.split("]")[0]
            if "#" not in inner:
                break
            tail = inner.split("#")[-1].strip()
            try:
                disp = int(tail, 16) if tail.startswith("0x") else int(tail)
            except ValueError:
                break
            if disp in keys:
                reg = inner.split(",")[0].strip()
                found.setdefault(disp, set()).add(reg)
            break
    return found


def main():
    def arg(f, dflt=None):
        return sys.argv[sys.argv.index(f) + 1] if f in sys.argv else dflt

    if not all(f in sys.argv for f in ("--sigs", "--names", "--offsets", "--so")):
        print(__doc__)
        return 1

    patterns = json.loads(Path(arg("--sigs")).read_text(encoding="utf-8"))
    names = arg("--names").split(",")
    keys = {int(x, 16) for x in arg("--offsets").split(",")}
    builds = arg("--so").split(",")
    span = int(arg("--span", "4000"))

    print(f"checking {len(keys)} offset(s) across {len(builds)} build(s)\n")

    per_build = {}
    for so in builds:
        d = Path(so).read_bytes()
        secs = parse_elf(d)
        text = sec_by_name(secs, ".text")
        res = {}
        for name in names:
            fixed, plen = compile_pattern(patterns[name])
            hits = scan(d, text["off"], text["size"], fixed, plen)
            if len(hits) != 1:
                print(f"  {Path(so).name}: {name} has {len(hits)} hits; skipped")
                continue
            addr = text["addr"] + hits[0] - text["off"]
            res.update(object_offsets(d, addr, span, keys))
        per_build[so] = res

    # Table: one row per offset, one column per build.
    hdr = f"{'offset':>10s} " + " ".join(f"{Path(b).name[-9:]:>12s}" for b in builds)
    print(hdr)
    print("-" * len(hdr))

    mismatches = []
    for k in sorted(keys):
        cells = []
        present = []
        for b in builds:
            regs = per_build[b].get(k)
            cells.append(f"{len(regs):>12d}" if regs else f"{'-':>12s}")
            present.append(bool(regs))
        flag = ""
        if any(present) and not all(present):
            flag = "   <-- MISSING in some build"
            mismatches.append(k)
        print(f"{k:#10x} " + " ".join(cells) + flag)

    if mismatches:
        print(f"\n{len(mismatches)} offset(s) are not used by every build:")
        for k in mismatches:
            print(f"    {k:#x}")
        print("These need a per-build offset table, not a shared constant.")
        return 1
    print("\nall offsets appear in every build (layout is compatible)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
