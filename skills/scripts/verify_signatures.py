#!/usr/bin/env python3
"""
Verify BedrockTools signature patterns against a Minecraft Bedrock
libminecraftpe.so (ARM64, ELF64) on disk.

This is the offline counterpart of the in-game
pl::memory::resolveSignatures() step: it scans the executable bytes of the
ELF and reports, for every signature id extracted from
BedrockTools-main's Signatures.cpp, the first match virtual address (imagebase
0, same convention as IDA), the file offset, the number of matches, and a
UNIQUE / AMBIGUOUS / MISSING verdict.

Usage:
    python verify_signatures.py <libminecraftpe.so> \
        --sigs <path/to/BedrockTools .../core/memory/Signatures.cpp> \
        [--json out.json]

--sigs defaults to ./BedrockTools-main/src/core/memory/Signatures.cpp
relative to the current directory, so point it at wherever you keep a copy of
the BedrockTools source tree.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import struct
from pathlib import Path

DEFAULT_SIGNATURES_CPP = Path.cwd() / "BedrockTools-main" / "src" / "core" / "memory" / "Signatures.cpp"

SIG_LINE_RE = re.compile(r"SignatureId::([A-Za-z0-9_]+)\s*,\s*\"([0-9A-Fa-f? ]+)\"")


def parse_signatures(cpp_path: Path) -> list[dict]:
    """Extract (id, pattern) pairs from BedrockTools' Signatures.cpp."""
    text = cpp_path.read_text(encoding="utf-8", errors="replace")
    sigs = []
    for match in SIG_LINE_RE.finditer(text):
        pattern = " ".join(match.group(2).split()).upper()
        sigs.append({"id": match.group(1), "pattern": pattern})
    return sigs


def parse_pattern(pattern: str) -> tuple[bytes, list[bool]]:
    """Split a hex pattern into (fixed bytes, wildcard mask)."""
    tokens = pattern.split()
    fixed = bytearray()
    mask = []
    for token in tokens:
        if token == "?":
            # '?' represents one wildcard byte
            fixed.append(0)
            mask.append(True)
        else:
            fixed.append(int(token, 16))
            mask.append(False)
    return bytes(fixed), mask


def find_pattern(data: bytes, pattern: str, start: int = 0, end: int | None = None) -> list[int]:
    """Return all offsets where the wildcard pattern matches inside data[start:end]."""
    fixed, mask = parse_pattern(pattern)
    if end is None:
        end = len(data)
    if len(fixed) == 0:
        return []

    # Locate the first fixed byte to use C-speed bytes.find()
    first_fixed = next((i for i, w in enumerate(mask) if not w), None)
    if first_fixed is None:
        return []
    first_byte = fixed[first_fixed]
    matches = []
    pos = start
    limit = end - len(fixed)
    while True:
        rel = data.find(first_byte, pos + first_fixed, limit + first_fixed + 1)
        if rel == -1:
            break
        cand = rel - first_fixed
        if cand < start:
            pos = rel + 1
            continue
        # Verify the whole pattern at cand
        ok = True
        for i, w in enumerate(mask):
            if not w and data[cand + i] != fixed[i]:
                ok = False
                break
        if ok:
            matches.append(cand)
            pos = cand + 1
        else:
            pos = rel + 1
    return matches


def load_elf(path: Path) -> dict:
    """Parse minimal ELF64 info: class, endianness, machine, program headers."""
    with open(path, "rb") as f:
        hdr = f.read(64)
    if hdr[:4] != b"\x7fELF":
        raise ValueError("not an ELF file")
    ei_class = hdr[4]
    ei_data = hdr[5]
    if ei_class != 2:
        raise ValueError("only ELF64 supported")
    little = ei_data == 1
    endian = "<" if little else ">"
    e_type, e_machine = struct.unpack_from(endian + "HH", hdr, 16)
    e_phoff, e_shoff = struct.unpack_from(endian + "QQ", hdr, 32)
    e_phentsize, e_phnum = struct.unpack_from(endian + "HH", hdr, 54)
    e_shentsize, e_shnum, e_shstrndx = struct.unpack_from(endian + "HHH", hdr, 58)

    with open(path, "rb") as f:
        f.seek(e_phoff)
        phdrs = []
        for _ in range(e_phnum):
            raw = f.read(e_phentsize)
            p_type, p_flags = struct.unpack_from(endian + "II", raw, 0)
            p_offset, p_vaddr, p_paddr, p_filesz, p_memsz, p_align = struct.unpack_from(
                endian + "QQQQQQ", raw, 8
            )
            phdrs.append(
                {
                    "type": p_type,
                    "flags": p_flags,
                    "offset": p_offset,
                    "vaddr": p_vaddr,
                    "filesz": p_filesz,
                    "memsz": p_memsz,
                }
            )

        # Section headers (may be absent in stripped-but-ok ELF; usually present)
        sections = []
        shstrtab_name = ""
        if e_shoff and e_shnum and e_shstrndx < e_shnum:
            f.seek(e_shoff + e_shstrndx * e_shentsize)
            shstr_raw = f.read(e_shentsize)
            shstr_off = struct.unpack_from(endian + "Q", shstr_raw, 24)[0]
            shstr_size = struct.unpack_from(endian + "Q", shstr_raw, 32)[0]
            f.seek(shstr_off)
            shstrtab_name = f.read(shstr_size)

            f.seek(e_shoff)
            shdrs = []
            for _ in range(e_shnum):
                raw = f.read(e_shentsize)
                sh_name, sh_type = struct.unpack_from(endian + "II", raw, 0)
                sh_flags, sh_addr, sh_offset = struct.unpack_from(endian + "QQQ", raw, 8)
                sh_size = struct.unpack_from(endian + "Q", raw, 32)[0]
                shdrs.append(
                    {
                        "name": sh_name,
                        "type": sh_type,
                        "addr": sh_addr,
                        "offset": sh_offset,
                        "size": sh_size,
                        "flags": sh_flags,
                    }
                )
            for sh in shdrs:
                if sh["name"] < len(shstrtab_name):
                    end = shstrtab_name.find(b"\x00", sh["name"])
                    name = shstrtab_name[sh["name"] : end].decode("latin-1", "replace")
                    sh["name"] = name
                    sections.append(sh)

    return {
        "machine": hex(e_machine),
        "endian": "little" if little else "big",
        "phdrs": phdrs,
        "sections": sections,
    }


def vaddr_to_offset(phdrs: list[dict], vaddr: int) -> int | None:
    for p in phdrs:
        if p["type"] == 1 and p["vaddr"] <= vaddr < p["vaddr"] + p["filesz"]:
            return p["offset"] + (vaddr - p["vaddr"])
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("so", nargs="?", default=None, help="path to libminecraftpe.so")
    parser.add_argument("--sigs", default=None,
                        help="path to BedrockTools Signatures.cpp "
                             "(default: ./BedrockTools-main/src/core/memory/Signatures.cpp)")
    parser.add_argument("--json", default=None, help="write JSON report to this path")
    args = parser.parse_args()

    so_path = Path(args.so) if args.so else Path.cwd() / "libminecraftpe.so"
    if not so_path.is_file():
        print(f"libminecraftpe.so not found: {so_path}", file=sys.stderr)
        return 1
    sigs_path = Path(args.sigs) if args.sigs else DEFAULT_SIGNATURES_CPP
    if not sigs_path.is_file():
        print(f"Signatures.cpp not found: {sigs_path}", file=sys.stderr)
        return 1

    elf = load_elf(so_path)
    print(f"ELF64 {elf['endian']}-endian machine={elf['machine']} size={so_path.stat().st_size}")

    # Build the executable byte image (union of PF_X PT_LOAD segments)
    exec_regions = [p for p in elf["phdrs"] if p["type"] == 1 and (p["flags"] & 1)]
    print("executable PT_LOAD segments:")
    for p in exec_regions:
        print(
            f"  vaddr {p['vaddr']:#x} .. {p['vaddr'] + p['filesz']:#x} "
            f"file {p['offset']:#x}..{p['offset'] + p['filesz']:#x}"
        )

    # Load all executable bytes into memory (the .text is ~190 MB, fine)
    with open(so_path, "rb") as f:
        exec_bytes = bytearray()
        for p in sorted(exec_regions, key=lambda x: x["offset"]):
            f.seek(p["offset"])
            exec_bytes += f.read(p["filesz"])

    sigs = parse_signatures(sigs_path)
    print(f"\nparsed {len(sigs)} signatures from {sigs_path.name}")

    report = []
    for sig in sigs:
        pattern = sig["pattern"]
        fixed, mask = parse_pattern(pattern)
        byte_count = len(fixed)
        fixed_count = sum(1 for w in mask if not w)
        matches = find_pattern(bytes(exec_bytes), pattern)
        if matches:
            first_vaddr = None
            first_off = None
            for m in matches:
                # find which executable segment contains it
                for p in exec_regions:
                    if p["offset"] <= m < p["offset"] + p["filesz"]:
                        first_vaddr = p["vaddr"] + (m - p["offset"])
                        first_off = m
                        break
                if first_vaddr is not None:
                    break
            status = "UNIQUE" if len(matches) == 1 else "AMBIGUOUS"
            va_text = f"{first_vaddr:#x}" if first_vaddr is not None else "n/a"
            off_text = f"{first_off:#x}" if first_off is not None else "n/a"
            print(
                f"{status:9s} {sig['id']:52s} vaddr={va_text} "
                f"offset={off_text} matches={len(matches)} bytes={byte_count} fixed={fixed_count}"
            )
            report.append(
                {
                    "id": sig["id"],
                    "pattern": pattern,
                    "status": status,
                    "vaddr": first_vaddr,
                    "file_offset": first_off,
                    "matches": len(matches),
                    "pattern_bytes": byte_count,
                    "fixed_bytes": fixed_count,
                }
            )
        else:
            print(f"MISSING   {sig['id']:52s} pattern_len={byte_count} fixed={fixed_count}")
            report.append(
                {
                    "id": sig["id"],
                    "pattern": pattern,
                    "status": "MISSING",
                    "vaddr": None,
                    "file_offset": None,
                    "matches": 0,
                    "pattern_bytes": byte_count,
                    "fixed_bytes": fixed_count,
                }
            )

    missing = [r["id"] for r in report if r["status"] == "MISSING"]
    ambiguous = [r["id"] for r in report if r["status"] == "AMBIGUOUS"]
    print(f"\nsummary: {len(report) - len(missing) - len(ambiguous)} unique, "
          f"{len(ambiguous)} ambiguous, {len(missing)} missing")

    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(
            json.dumps(
                {
                    "so": str(so_path),
                    "signatures_source": str(sigs_path),
                    "elf": {"machine": elf["machine"], "endian": elf["endian"]},
                    "summary": {
                        "total": len(report),
                        "unique": len(report) - len(missing) - len(ambiguous),
                        "ambiguous": len(ambiguous),
                        "missing": len(missing),
                    },
                    "signatures": report,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"report written: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
