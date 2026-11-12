"""Generate cross-version signatures for functions whose old fixed-byte patterns stopped matching.

For each function the tool takes its address in every build, disassembles the prologue and emits a
pattern whose instructions are fixed when all builds agree byte-for-byte and wildcarded when they
differ (branch targets, field displacements). The shortest prefix that matches exactly once in every
build is emitted, which keeps the pattern precise without being version-locked.
"""

from __future__ import annotations

import re
import struct
import subprocess
import sys
from pathlib import Path

from objdump_locator import find_objdump  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent  # repo root by default
OBJDUMP = Path(find_objdump())
LINE_RE = re.compile(r"^\s*([0-9a-f]+):\s+([0-9a-f]{8})\s+(.+)$")

# Map a label to each .so you want to compare. Labels are arbitrary;
# add or remove entries freely.
BUILDS = {
    "old": ROOT / "libminecraftpe-old.so",
    "new": ROOT / "libminecraftpe-new.so",
}

# function name -> {build label: vaddr}.
# Fill these in from your own resolution step (signature scan, vtable walk, or
# a disassembler) -- they are build-specific by definition and cannot be shipped.
FUNCTIONS = {
    "ExampleFunction": {"old": 0x0000000, "new": 0x0000000},
}


def disassemble(so: Path, start: int, count: int) -> list[int]:
    result = subprocess.run(
        [str(OBJDUMP), "-d", f"--start-address={hex(start)}",
         f"--stop-address={hex(start + count * 4)}", str(so)],
        capture_output=True, text=True, errors="replace",
    )
    words = []
    for line in result.stdout.splitlines():
        match = LINE_RE.match(line)
        if match:
            words.append(int(match.group(2), 16))
    return words


def load_text(so: Path) -> bytes:
    data = so.read_bytes()
    e_shoff = struct.unpack_from("<Q", data, 0x28)[0]
    e_shentsize = struct.unpack_from("<H", data, 0x3A)[0]
    e_shnum = struct.unpack_from("<H", data, 0x3C)[0]
    best = None
    for index in range(e_shnum):
        base = e_shoff + index * e_shentsize
        sh_type = struct.unpack_from("<I", data, base + 4)[0]
        sh_flags = struct.unpack_from("<Q", data, base + 8)[0]
        sh_addr = struct.unpack_from("<Q", data, base + 16)[0]
        sh_offset = struct.unpack_from("<Q", data, base + 24)[0]
        sh_size = struct.unpack_from("<Q", data, base + 32)[0]
        if sh_type == 1 and (sh_flags & 0x4) and sh_size > 0x1000:
            if best is None or sh_size > best[2]:
                best = (sh_offset, sh_addr, sh_size)
    return data[best[0]:best[0] + best[2]], best[1]


def count_matches(text: bytes, pattern: list[int | None], expected_offset: int) -> list[int]:
    """All match offsets for a word pattern (None = wildcard word)."""
    fixed = bytearray()
    wild = bytearray()
    for word in pattern:
        if word is None:
            fixed += b"\x00\x00\x00\x00"
            wild += b"\x01\x01\x01\x01"
        else:
            fixed += word.to_bytes(4, "little")
            wild += b"\x00\x00\x00\x00"
    length = len(fixed)
    # longest fixed run as anchor
    best_start = best_len = 0
    run_start = None
    for index in range(length):
        if wild[index]:
            if run_start is not None:
                if index - run_start > best_len:
                    best_start, best_len = run_start, index - run_start
                run_start = None
        elif run_start is None:
            run_start = index
    if run_start is not None and length - run_start > best_len:
        best_start, best_len = run_start, length - run_start
    if best_len < 8:
        return []
    needle = bytes(fixed[best_start:best_start + best_len])
    hits = []
    start = 0
    while True:
        found = text.find(needle, start)
        if found < 0:
            return hits
        start = found + 1
        candidate = found - best_start
        if candidate < 0 or candidate + length > len(text) or candidate % 4:
            continue
        if all(wild[i] or text[candidate + i] == fixed[i] for i in range(length)):
            hits.append(candidate)
    return hits


def main() -> int:
    loaded = {name: load_text(path) for name, path in BUILDS.items()}
    texts = {name: value[0] for name, value in loaded.items()}
    bases = {name: value[1] for name, value in loaded.items()}
    output = {}
    for function, addresses in FUNCTIONS.items():
        per_build = {}
        for name, so in BUILDS.items():
            per_build[name] = disassemble(so, addresses[name], 96)
        chosen = None
        for count in range(24, 49):
            pattern: list[int | None] = []
            for index in range(count):
                words = [per_build[name][index] for name in BUILDS]
                pattern.append(words[0] if all(w == words[0] for w in words) else None)
            counts = {}
            for name in BUILDS:
                expected = addresses[name] - bases[name]
                hits = count_matches(texts[name], pattern, expected)
                counts[name] = hits
            if all(len(hits) == 1 and hits[0] == addresses[name] - bases[name]
                   for name, hits in counts.items()):
                chosen = (count, pattern, counts)
                break
        if chosen is None:
            print(f"{function}: no prefix matched uniquely in every build")
            return 1
        count, pattern, counts = chosen
        tokens = []
        for word in pattern:
            if word is None:
                tokens += ["??"] * 4
            else:
                tokens += [f"{b:02X}" for b in word.to_bytes(4, "little")]
        output[function] = " ".join(tokens)
        print(f"\n{function}: prefix {count} instructions, one match per build")
        print("  " + output[function])
    print()
    import json
    print(json.dumps({k: v for k, v in output.items()}, indent=1)[:400])
    return 0


if __name__ == "__main__":
    sys.exit(main())
