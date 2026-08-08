#!/usr/bin/env python3
"""Tiny AArch64 instruction encoders for writing byte patches by hand.

All encoders return little-endian bytes (same order as IDA / BedrockTools
signature strings). Use this to generate patch payloads, then cross-check the
result against IDA's disassembly before writing to memory.

Examples:
    python aarch64_enc.py movz-w 0 0x447A 16
    python aarch64_enc.py fmov-s-w 0 0
    python aarch64_enc.py ret
    python aarch64_enc.py b 0x1000 0x1040      # branch from 0x1000 to 0x1040
"""

from __future__ import annotations

import sys


def enc(value: int, width: int = 4) -> list[str]:
    return [f"{b:02X}" for b in value.to_bytes(width, "little")]


def movz_w(rd: int, imm16: int, shift: int = 0) -> int:
    """MOVZ Wd, #imm16, LSL #shift  (shift must be 0 or 16)."""
    if shift not in (0, 16):
        raise ValueError("shift must be 0 or 16")
    return 0x52800000 | ((shift // 16) << 21) | ((imm16 & 0xFFFF) << 5) | (rd & 0x1F)


def movk_w(rd: int, imm16: int, shift: int = 0) -> int:
    """MOVK Wd, #imm16, LSL #shift (OR-in immediate)."""
    if shift not in (0, 16):
        raise ValueError("shift must be 0 or 16")
    return 0x72800000 | ((shift // 16) << 21) | ((imm16 & 0xFFFF) << 5) | (rd & 0x1F)


def movn_w(rd: int, imm16: int, shift: int = 0) -> int:
    """MOVN Wd, #imm16, LSL #shift (invert immediate)."""
    if shift not in (0, 16):
        raise ValueError("shift must be 0 or 16")
    return 0x12800000 | ((shift // 16) << 21) | ((imm16 & 0xFFFF) << 5) | (rd & 0x1F)


def fmov_s_w(sd: int, wn: int) -> int:
    """FMOV Sd, Wn (move GPR -> single precision FP)."""
    return 0x1E270000 | ((wn & 0x1F) << 5) | (sd & 0x1F)


def ret() -> int:
    return 0xD65F03C0


def nop() -> int:
    return 0xD503201F


def br(xn: int) -> int:
    """BR Xn (indirect branch)."""
    return 0xD61F0000 | ((xn & 0x1F) << 5)


def b(offset: int) -> int:
    """B +offset (pc-relative, signed 28-bit, imm26)."""
    if not -(1 << 27) <= offset < (1 << 27):
        raise ValueError("offset out of ±128MB range")
    return 0x14000000 | ((offset // 4) & 0x3FFFFFF)


def bl(offset: int) -> int:
    """BL +offset (pc-relative, signed 28-bit, imm26)."""
    if not -(1 << 27) <= offset < (1 << 27):
        raise ValueError("offset out of ±128MB range")
    return 0x94000000 | ((offset // 4) & 0x3FFFFFF)


def b_cond(offset: int, cond: int) -> int:
    """B.cond +offset (signed 21-bit, imm19)."""
    if not -(1 << 20) <= offset < (1 << 20):
        raise ValueError("offset out of ±1MB range")
    return 0x54000000 | (((offset // 4) & 0x7FFFF) << 5) | (cond & 0xF)


def ldr_literal(xn: int, offset: int) -> int:
    """LDR Xn, #offset (literal load, signed 21-bit, imm19)."""
    if not -(1 << 20) <= offset < (1 << 20):
        raise ValueError("offset out of ±1MB range")
    return 0x58000000 | (((offset // 4) & 0x7FFFF) << 5) | (xn & 0x1F)


HELP = """usage:
  aarch64_enc.py movz-w <rd> <imm16> [shift]
  aarch64_enc.py movk-w <rd> <imm16> [shift]
  aarch64_enc.py movn-w <rd> <imm16> [shift]
  aarch64_enc.py fmov-s-w <sd> <wn>
  aarch64_enc.py ret
  aarch64_enc.py nop
  aarch64_enc.py br <xn>
  aarch64_enc.py b <from> <to>          # absolute addresses -> relative
  aarch64_enc.py bl <from> <to>
  aarch64_enc.py b-cond <from> <to> <cond>
  aarch64_enc.py ldr-lit <xn> <from> <to>
"""


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(HELP)
        return 1

    def word(fn, *xs):
        vals = [int(x, 0) if isinstance(x, str) else x for x in xs]
        print(" ".join(enc(fn(*vals))))

    cmd = args[0]
    try:
        if cmd == "movz-w" and len(args) == 4:
            word(movz_w, args[1], args[2], args[3])
        elif cmd == "movz-w" and len(args) == 3:
            word(movz_w, args[1], args[2])
        elif cmd == "movk-w" and len(args) == 4:
            word(movk_w, args[1], args[2], args[3])
        elif cmd == "movk-w" and len(args) == 3:
            word(movk_w, args[1], args[2])
        elif cmd == "movn-w" and len(args) == 4:
            word(movn_w, args[1], args[2], args[3])
        elif cmd == "movn-w" and len(args) == 3:
            word(movn_w, args[1], args[2])
        elif cmd == "fmov-s-w" and len(args) == 3:
            word(fmov_s_w, args[1], args[2])
        elif cmd == "ret":
            print("C0 03 5F D6")
        elif cmd == "nop":
            print("1F 20 03 D5")
        elif cmd == "br" and len(args) == 2:
            word(br, args[1])
        elif cmd in ("b", "bl", "b-cond", "ldr-lit"):
            a, b2 = int(args[1], 0), int(args[2], 0)
            off = b2 - a
            if cmd == "b":
                word(b, off)
            elif cmd == "bl":
                word(bl, off)
            elif cmd == "b-cond":
                word(b_cond, off, int(args[3], 0))
            else:
                word(ldr_literal, int(args[1], 0), off)
        else:
            print(HELP)
            return 1
    except (IndexError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        print(HELP, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
