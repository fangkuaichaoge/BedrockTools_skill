#!/usr/bin/env python3
"""Read-only ELF facts for libminecraftpe.so (Python stdlib only).

Dumps: ELF header, PT_LOAD segments, named sections, dynamic-symbol summary,
keyword-filtered dynamic symbols, and version-string candidates. Writes
elf_facts.json next to the script (or --json <path>).

Usage:
    python elf_facts.py <libminecraftpe.so> [--json out.json]
"""

from __future__ import annotations

import argparse
import json
import re
import struct
from pathlib import Path

KEYWORDS = {
    "jni": ["JNI_OnLoad", "Java_", "RegisterNatives"],
    "libc": ["dlopen", "dlsym", "dlclose", "mprotect", "memcpy", "memset",
             "strlen", "pthread_create", "pthread_mutex_"],
    "cxx": ["__cxa_", "_ZTV", "_ZTS", "_ZN", "_ZNK"],
    "egl_gl": ["egl", "gl", "GLES"],
}


def load_elf(path: Path) -> dict:
    with open(path, "rb") as f:
        hdr = f.read(64)
    if hdr[:4] != b"\x7fELF":
        raise ValueError("not an ELF file")
    if hdr[4] != 2:
        raise ValueError("only ELF64 supported")
    little = hdr[5] == 1
    endian = "<" if little else ">"
    e_type, e_machine = struct.unpack_from(endian + "HH", hdr, 16)
    e_phoff, e_shoff = struct.unpack_from(endian + "QQ", hdr, 32)
    e_phentsize, e_phnum = struct.unpack_from(endian + "HH", hdr, 54)
    e_shentsize, e_shnum, e_shstrndx = struct.unpack_from(endian + "HHH", hdr, 58)

    with open(path, "rb") as f:
        phdrs = []
        if e_phoff and e_phnum:
            f.seek(e_phoff)
            for _ in range(e_phnum):
                raw = f.read(e_phentsize)
                p_type, p_flags = struct.unpack_from(endian + "II", raw, 0)
                p_offset, p_vaddr, p_paddr, p_filesz, p_memsz = struct.unpack_from(
                    endian + "QQQQQ", raw, 8)
                phdrs.append({"type": p_type, "flags": p_flags, "offset": p_offset,
                              "vaddr": p_vaddr, "filesz": p_filesz, "memsz": p_memsz})

        sections = []
        shstr = b""
        if e_shoff and e_shnum and e_shstrndx < e_shnum:
            f.seek(e_shoff + e_shstrndx * e_shentsize)
            raw = f.read(e_shentsize)
            sh_off = struct.unpack_from(endian + "Q", raw, 24)[0]
            sh_size = struct.unpack_from(endian + "Q", raw, 32)[0]
            f.seek(sh_off)
            shstr = f.read(sh_size)

            f.seek(e_shoff)
            for _ in range(e_shnum):
                raw = f.read(e_shentsize)
                sh_name, sh_type = struct.unpack_from(endian + "II", raw, 0)
                sh_flags, sh_addr, sh_offset = struct.unpack_from(endian + "QQQ", raw, 8)
                sh_size = struct.unpack_from(endian + "Q", raw, 32)[0]
                name = ""
                if sh_name < len(shstr):
                    end = shstr.find(b"\x00", sh_name)
                    name = shstr[sh_name:end].decode("latin-1", "replace")
                sections.append({"name": name, "type": sh_type, "addr": sh_addr,
                                 "offset": sh_offset, "size": sh_size,
                                 "flags": sh_flags})

    return {"machine": e_machine, "e_type": e_type, "endian": "little" if little else "big",
            "phdrs": phdrs, "sections": sections}


def load_dynsym(path: Path, sections: list[dict]) -> dict:
    sym = next((s for s in sections if s["name"] == ".dynsym"), None)
    strtab = next((s for s in sections if s["name"] == ".dynstr"), None)
    if not sym or not strtab:
        return {"count": 0, "named": 0, "matched": {}}
    with open(path, "rb") as f:
        f.seek(strtab["offset"])
        strings = f.read(strtab["size"])
        f.seek(sym["offset"])
        raw = f.read(sym["size"])
    entsize = 24
    count = len(raw) // entsize
    names: list[str] = []
    for i in range(count):
        st_name = struct.unpack_from("<I", raw, i * entsize)[0]
        if st_name >= len(strings):
            continue
        end = strings.find(b"\x00", st_name)
        if end == -1:
            continue
        name = strings[st_name:end].decode("utf-8", "replace")
        if name:
            names.append(name)

    matched = {}
    for key, needles in KEYWORDS.items():
        hits = [n for n in names if any(n.startswith(nd) or nd in n for nd in needles)]
        matched[key] = sorted(set(hits))[:80]
    return {"count": count, "named": len(names), "matched": matched}


def find_version_strings(path: Path) -> list[str]:
    pattern = re.compile(rb"\b(?:2[0-9]\.\d+\.\d+|1\.\d+\.\d+\.\d+|26\.\d+|1\.21\.\d+)\b")
    candidates: set[str] = set()
    with open(path, "rb") as f:
        data = f.read(64 * 1024 * 1024)  # first 64MB covers .rodata head
    for m in pattern.finditer(data):
        s = m.group(0).decode("latin-1")
        if 4 <= len(s) <= 16:
            candidates.add(s)
    return sorted(candidates)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("so", type=Path)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args()

    elf = load_elf(args.so)
    sections = elf["sections"]
    dynsym = load_dynsym(args.so, sections)
    versions = find_version_strings(args.so)

    text = next((s for s in sections if s["name"] == ".text"), None)
    report = {
        "so": str(args.so.resolve()),
        "size": args.so.stat().st_size,
        "elf": {
            "machine": hex(elf["machine"]),
            "e_type": elf["e_type"],
            "endian": elf["endian"],
        },
        "text": {"vaddr": text and hex(text["addr"]), "size": text and hex(text["size"])},
        "load_segments": [p for p in elf["phdrs"] if p["type"] == 1],
        "sections": [s for s in sections if s["name"]],
        "dynsym": dynsym,
        "version_candidates": versions,
    }
    out = args.json or (Path(__file__).resolve().parent / "elf_facts.json")
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"machine={report['elf']['machine']} e_type={report['elf']['e_type']} size={report['size']}")
    print(f".text vaddr={report['text']['vaddr']} size={report['text']['size']}")
    print(f"dynsym count={dynsym['count']} named={dynsym['named']}")
    print(f"version candidates: {versions[:20]}")
    print(f"report written: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
