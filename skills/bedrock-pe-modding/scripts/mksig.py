#!/usr/bin/env python3
"""Generate a wildcard byte signature from a function head in a stripped .so.

Usage
-----
    python mksig.py <so> --addr 0x... [--len 48] [--tight]
                     [--name X] [--json out.json]

Two modes, both useful (see `references/signature-forensics.md` §9):

  default (house style)
      wildcard every instruction carrying an immediate or memory operand, the
      way the BedrockTools dictionary does.  Longer-lived across builds.

  --tight
      wildcard only instructions that encode an absolute address or a branch
      displacement (ADRP / page ADD / B / BL).  Object field offsets stay
      fixed, which yields a much stronger but more version-sensitive pattern.

The tool prints the pattern and how many times it matches `.text`, so a
non-unique result is visible immediately.  Always confirm the single match is
the intended function head, not merely *some* unique site.

Two traps this tool exists to avoid:

  * Adjacent C++ string literals concatenate with **no** separator, so a
    continuation line must start with a space or the last token of one line
    fuses with the first token of the next (`... 6D` + `FD ...` -> `6DFD`).
    The emitted block therefore puts the separator at the *start* of each
    continuation line, which survives formatters.
  * `inline constexpr` patterns the linker does not need are dropped from the
    built `.so`, so "the string is absent from the binary" is not evidence of
    a problem.  Verify against the header, not the binary.
"""
import json
import sys

from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN, CS_OPT_DETAIL
from capstone.arm64 import ARM64_OP_IMM, ARM64_OP_MEM

from a64 import parse_elf, sec_by_name
from sigscan import compile_pattern, scan


def signature_for(d, addr, length, md, tight=False):
    code = d[addr:addr + length * 4]
    toks = []
    prev_adrp = False
    for insn in md.disasm(code, addr):
        m = insn.mnemonic
        if m == "adrp":
            toks += ["??"] * 4
            prev_adrp = True
            continue
        if prev_adrp and m in ("add", "ldr", "str"):
            toks += ["??"] * 4
            prev_adrp = False
            continue
        prev_adrp = False
        if m in ("b", "bl") or m.startswith("b."):
            toks += ["??"] * 4
            continue
        if not tight:
            has_imm = any(o.type == ARM64_OP_IMM for o in insn.operands)
            has_mem = any(o.type == ARM64_OP_MEM for o in insn.operands)
            # Keep pure register moves (`mov x19, x0`) fixed: they are the
            # stable, distinctive part of a prologue.
            is_reg_move = m == "mov" and not has_imm
            if (has_imm or has_mem) and not is_reg_move:
                toks += ["??"] * 4
                continue
        toks += [f"{b:02X}" for b in insn.bytes]
    return " ".join(toks)


def cpp_block(name, pattern):
    """Emit a C++ literal block with leading-space separators (trap 1)."""
    toks = pattern.split()
    chunks = [" ".join(toks[i:i + 8]) for i in range(0, len(toks), 8)]
    lines = [f"// {name}",
             f"inline constexpr std::string_view {name} ="]
    for i, c in enumerate(chunks):
        pre = "" if i == 0 else " "
        last = (i == len(chunks) - 1)
        lines.append(f'    "{pre}{c}"' + (";" if last else ""))
    return "\n".join(lines)


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        return 1

    so = sys.argv[1]
    with open(so, "rb") as fh:
        d = fh.read()
    secs = parse_elf(d)
    text = sec_by_name(secs, ".text")

    def arg(flag, default=None):
        return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else default

    md = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
    md.detail = True
    addr = int(arg("--addr"), 16)
    length = int(arg("--len", "48"))
    name = arg("--name", f"sig_{addr:x}")

    pat = signature_for(d, addr, length, md, tight="--tight" in sys.argv)
    fixed, plen = compile_pattern(pat)
    hits = scan(d, text["off"], text["size"], fixed, plen)

    print(f"{name} @ {addr:#x}  bytes={plen} fixed={len(fixed)} matches={len(hits)}")
    print(cpp_block(name, pat))
    for h in hits[:6]:
        print(f"    hit {text['addr'] + (h - text['off']):#x}")

    if "--json" in sys.argv:
        out = arg("--json")
        try:
            data = json.load(open(out, encoding="utf-8"))
        except Exception:
            data = {}
        data[name] = pat
        json.dump(data, open(out, "w", encoding="utf-8"), indent=1)
        print("->", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
