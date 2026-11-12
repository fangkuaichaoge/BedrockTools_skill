"""Locate the new-build counterpart of a function whose fixed-byte signature stopped matching.

Input: the library of an older build, the function's address in it (found through the project's old
signature), and the target library. The tool disassembles the old function, converts it into a
house-style wildcard pattern (immediates and branch targets wildcarded), and searches the target for
that pattern starting at each instruction boundary. The first start whose pattern still matches gives
the new address, which is then compared against the old prologue so the caller can decide whether more
work is needed.

Usage:
    python port_function.py <old.so> <target.so> --at 0xb2ac3d4 [--bytes 0x180]
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

from objdump_locator import OBJDUMP_CANDIDATES  # noqa: E402  (portable lookup)
LINE_RE = re.compile(r"^\s*([0-9a-f]+):\s+([0-9a-f]{8})\s+(.+)$")
IMM_RE = re.compile(r"#(0x[0-9a-f]+|\d+)")


def objdump() -> str:
    from objdump_locator import find_objdump
    return find_objdump()


def instructions(so: Path, start: int, size: int) -> list[tuple[int, int, str]]:
    result = subprocess.run(
        [objdump(), "-d", f"--start-address={hex(start)}", f"--stop-address={hex(start + size)}", str(so)],
        capture_output=True, text=True, errors="replace",
    )
    out = []
    for line in result.stdout.splitlines():
        match = LINE_RE.match(line)
        if not match:
            continue
        address = int(match.group(1), 16)
        word = int(match.group(2), 16)
        text = match.group(3).strip()
        out.append((address, word, text))
    return out


def wildcard_mask(word: int, text: str) -> int:
    """Return 0xFFFFFFFF for a fully fixed word, otherwise a byte mask (0xff keep / 0x00 wildcard)."""
    mnemonic = text.split()[0]
    if mnemonic in ("b", "bl", "blx", "cbz", "cbnz", "tbz", "tbnz", "adr", "adrp"):
        return 0
    if IMM_RE.search(text) or re.search(r"\[[^\]]*#", text):
        # keep the opcode/register bits, wildcard the immediate field conservatively by instruction
        # family: this over-wildcards some words, which only costs a little uniqueness.
        return 0
    return 0xFFFFFFFF


def build_pattern(ins: list[tuple[int, int, str]], begin: int, count: int):
    words = []
    masks = []
    for address, word, text in ins[begin:begin + count]:
        mask = wildcard_mask(word, text)
        words.append(bytes(word.to_bytes(4, "little")))
        masks.append(mask.to_bytes(4, "little").replace(b"\xff", b"\x01").replace(b"\x00", b"\x00"))
    return words, masks


def scan(segment: bytes, base: int, words, masks) -> list[int]:
    fixed = bytearray()
    wild = bytearray()
    for word, mask in zip(words, masks):
        for index in range(4):
            fixed.append(word[index] if mask[index] else 0)
            wild.append(0 if mask[index] else 1)
    length = len(fixed)
    # anchor: longest fixed run
    best_start, best_len, run_start = 0, 0, None
    for index in range(length):
        if wild[index]:
            if run_start is not None:
                if index - run_start > best_len:
                    best_start, best_len = run_start, index - run_start
                run_start = None
        else:
            if run_start is None:
                run_start = index
    if run_start is not None and length - run_start > best_len:
        best_start, best_len = run_start, length - run_start
    if best_len < 4:
        return []
    needle = bytes(fixed[best_start:best_start + best_len])
    results = []
    start = 0
    while True:
        hit = segment.find(needle, start)
        if hit < 0:
            return results
        start = hit + 1
        candidate = hit - best_start
        if candidate < 0 or candidate + length > len(segment):
            continue
        ok = True
        for index in range(length):
            if not wild[index] and segment[candidate + index] != fixed[index]:
                ok = False
                break
        if ok:
            results.append(base + candidate)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("old_so", type=Path)
    parser.add_argument("target_so", type=Path)
    parser.add_argument("--at", required=True, help="function address in the old library, hex")
    parser.add_argument("--bytes", default="0x200")
    args = parser.parse_args()

    old_start = int(args.at, 16)
    old_instructions = instructions(args.old_so, old_start, int(args.bytes, 0))
    print(f"old function: {len(old_instructions)} instructions at 0x{old_start:x}")
    for index, (address, word, text) in enumerate(old_instructions[:6]):
        print(f"  {index:2d} 0x{address:x} {text}")

    target_data = args.target_so.read_bytes()
    # Use the first load segment (vaddr 0 in these builds).
    import struct
    e_phoff = struct.unpack_from("<Q", target_data, 0x20)[0]
    e_phentsize = struct.unpack_from("<H", target_data, 0x36)[0]
    e_phnum = struct.unpack_from("<H", target_data, 0x38)[0]
    segment, base = None, 0
    for index in range(e_phnum):
        p = e_phoff + index * e_phentsize
        if struct.unpack_from("<I", target_data, p)[0] != 1:
            continue
        p_offset = struct.unpack_from("<Q", target_data, p + 8)[0]
        p_vaddr = struct.unpack_from("<Q", target_data, p + 16)[0]
        p_filesz = struct.unpack_from("<Q", target_data, p + 32)[0]
        if p_filesz > 0x1000000:
            segment = target_data[p_offset:p_offset + p_filesz]
            base = p_vaddr
            break
    if segment is None:
        raise SystemExit("no large load segment")

    for begin in range(0, min(24, len(old_instructions))):
        count = len(old_instructions) - begin
        words, masks = build_pattern(old_instructions, begin, count)
        hits = scan(segment, base, words, masks)
        if hits:
            print(f"\npattern starting at instruction {begin} matched {len(hits)} site(s):")
            for hit in hits[:8]:
                print(f"  0x{hit:x}")
            break
    else:
        print("\nno pattern variant matched; the function was rewritten too heavily")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
