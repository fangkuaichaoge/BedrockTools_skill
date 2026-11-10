#!/usr/bin/env python3
"""Align two RTTI class vtables across game builds and report which vtable slot moved.

A game update can insert or remove virtual functions, which shifts every following slot and silently
breaks hard-coded slot indices. Function addresses change between builds anyway, so slots are matched
by disassembly similarity: each slot's function is disassembled to a normalised instruction-token
stream (branch targets and call targets collapsed, small immediates kept because they carry struct
offsets), and slots are paired by sequence similarity.

Usage:
    python align_vtables.py <old.so> <new.so> --class 15ChestBlockActor --slots 0,9,76,78,79
    python align_vtables.py <old.so> <new.so> --class 24MinecraftUIRenderContext --slots 2,6,7,8,10,16,32
    python align_vtables.py <old.so> <new.so> --class 11BlockSource --slots 4
"""

from __future__ import annotations

import argparse
import difflib
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rtti_vtables import Library  # noqa: E402

OBJDUMP_CANDIDATES = [
    Path(r"C:\Users\29633\AppData\Local\Android\Sdk\ndk\29.0.14206865\toolchains\llvm\prebuilt\windows-x86_64\bin\llvm-objdump.exe"),
]
WINDOW = 0x100
MAX_INSTRUCTIONS = 64

LINE_RE = re.compile(r"^\s*[0-9a-f]+:\s+([0-9a-f]{8})\s+(.*)$")
NUMBER_RE = re.compile(r"0x[0-9a-f]+")


def objdump() -> str:
    for candidate in OBJDUMP_CANDIDATES:
        if candidate.exists():
            return str(candidate)
    raise SystemExit("llvm-objdump not found; edit OBJDUMP_CANDIDATES")


def tokens(so: Path, address: int) -> list[str]:
    """Normalised instruction tokens for the first instructions of a function."""
    result = subprocess.run(
        [objdump(), "-d", f"--start-address={hex(address)}",
         f"--stop-address={hex(address + WINDOW)}", str(so)],
        capture_output=True, text=True, errors="replace",
    )
    out: list[str] = []
    for line in result.stdout.splitlines():
        match = LINE_RE.match(line)
        if not match:
            continue
        text = match.group(2)
        mnemonic = text.split()[0].rstrip()
        rest = text[len(mnemonic):].strip()
        if mnemonic.startswith("b") or mnemonic in ("bl", "blx", "cbz", "cbnz", "tbz", "tbnz", "adr", "adrp"):
            rest = "TARGET"
        else:
            # keep small immediates (struct offsets), drop absolute-ish constants
            rest = NUMBER_RE.sub(lambda m: m.group(0) if len(m.group(0)) <= 6 else "VAL", rest)
            rest = re.sub(r"<[^>]*>", "", rest)
        out.append(f"{mnemonic} {rest}".strip())
        if len(out) >= MAX_INSTRUCTIONS:
            break
    return out


def primary_vtable(library: Library, class_name: str) -> tuple[int, str]:
    vtables = library.vtables_for_name(class_name)
    if not vtables:
        raise SystemExit(f"class {class_name!r} not found")
    best = None
    for vtable, mangled in vtables:
        count = 0
        while library.slot_value(vtable, count) is not None and count < 400:
            count += 1
        if best is None or count > best[0]:
            best = (count, vtable, mangled)
    return best[1], best[2]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("old_so", type=Path)
    parser.add_argument("new_so", type=Path)
    parser.add_argument("--class", dest="class_name", required=True)
    parser.add_argument("--slots", required=True, help="comma separated old slot indices")
    parser.add_argument("--top", type=int, default=3, help="show N best new slots per old slot")
    args = parser.parse_args()

    old_lib = Library(args.old_so)
    new_lib = Library(args.new_so)
    old_vtable, old_name = primary_vtable(old_lib, args.class_name)
    new_vtable, new_name = primary_vtable(new_lib, args.class_name)
    print(f"class {old_name}")
    print(f"  old vtable 0x{old_vtable:x}")
    print(f"  new vtable 0x{new_vtable:x}")

    old_count = 0
    while old_lib.slot_value(old_vtable, old_count) is not None and old_count < 400:
        old_count += 1
    new_count = 0
    while new_lib.slot_value(new_vtable, new_count) is not None and new_count < 400:
        new_count += 1
    print(f"  slots old={old_count} new={new_count}")

    old_tokens = {}
    for index in range(old_count):
        address = old_lib.slot_value(old_vtable, index)
        if address:
            old_tokens[index] = tokens(args.old_so, address)
    new_tokens = {}
    for index in range(new_count):
        address = new_lib.slot_value(new_vtable, index)
        if address:
            new_tokens[index] = tokens(args.new_so, address)

    print("\nslot map:")
    for slot in [int(part) for part in args.slots.split(",")]:
        reference = old_tokens.get(slot)
        if not reference:
            print(f"  old slot {slot:3d}: no function")
            continue
        scored = []
        for index, candidate in new_tokens.items():
            ratio = difflib.SequenceMatcher(None, reference, candidate).ratio()
            scored.append((ratio, index))
        scored.sort(reverse=True)
        best = scored[:args.top]
        detail = "  ".join(f"new {index:3d} ({ratio:.2f})" for ratio, index in best)
        moved = best[0][1] != slot
        mark = "MOVED" if moved else "same "
        print(f"  old slot {slot:3d} -> {detail}   [{mark}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
