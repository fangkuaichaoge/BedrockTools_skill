#!/usr/bin/env python3
"""Fast multi-signature verifier for large `libminecraftpe.so` builds.

The existing verifier checks one whole pattern against the whole binary; with ~140 patterns and a
300 MB+ library that is tens of gigabytes of scanning. This tool makes it linear-ish:

  * parse each pattern into (fixed byte, wildcard) tokens;
  * pick the longest run of fixed bytes as the anchor;
  * find anchor candidates with `bytes.find` (C speed), then verify the full pattern at each
    candidate offset;
  * classify UNIQUE / AMBIGUOUS / MISSING and optionally dump the table as JSON.

Usage:
    python fast_verify_signatures.py <lib.so> --sigs Signatures.cpp [--json out.json]
    python fast_verify_signatures.py <lib.so> --patterns patterns.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

DEFINITION_RE = re.compile(
    r"SignatureDefinition\{\s*SignatureId::(\w+)\s*,\s*\"([^\"]+)\"\s*\}",
    re.S,
)


def parse_pattern(text: str) -> list[int | None]:
    tokens = text.split()
    out: list[int | None] = []
    for token in tokens:
        token = token.strip()
        if token in ("?", "??"):
            out.append(None)
        else:
            out.append(int(token, 16))
    return out


def parse_signatures_from_cpp(path: Path) -> list[tuple[str, list[int | None]]]:
    body = path.read_text(encoding="utf-8", errors="replace")
    entries = []
    for match in DEFINITION_RE.finditer(body):
        entries.append((match.group(1), parse_pattern(match.group(2))))
    return entries


def parse_signatures_from_json(path: Path) -> list[tuple[str, list[int | None]]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = []
    items = data.items() if isinstance(data, dict) else [(f"sig{index}", value) for index, value in enumerate(data)]
    for name, value in items:
        pattern = value.get("pattern") if isinstance(value, dict) else value
        entries.append((name, parse_pattern(pattern)))
    return entries


def longest_fixed_run(pattern: list[int | None]) -> tuple[int, int]:
    """Return (offset, length) of the longest run of fixed bytes."""
    best_offset, best_length = 0, 0
    index = 0
    while index < len(pattern):
        if pattern[index] is None:
            index += 1
            continue
        start = index
        while index < len(pattern) and pattern[index] is not None:
            index += 1
        if index - start > best_length:
            best_offset, best_length = start, index - start
    return best_offset, best_length


def load_text_segment(so_path: Path) -> tuple[bytes, int]:
    """Return the first executable segment's bytes and its virtual address."""
    import struct

    data = so_path.read_bytes()
    if data[:4] != b"\x7fELF" or data[4] != 2:
        raise SystemExit("not a 64-bit ELF")
    e_phoff = struct.unpack_from("<Q", data, 0x20)[0]
    e_phentsize = struct.unpack_from("<H", data, 0x36)[0]
    e_phnum = struct.unpack_from("<H", data, 0x38)[0]
    for index in range(e_phnum):
        base = e_phoff + index * e_phentsize
        p_type = struct.unpack_from("<I", data, base)[0]
        if p_type != 1:  # PT_LOAD
            continue
        p_flags = struct.unpack_from("<I", data, base + 4)[0]
        if not (p_flags & 1):  # PF_X
            continue
        p_offset = struct.unpack_from("<Q", data, base + 8)[0]
        p_vaddr = struct.unpack_from("<Q", data, base + 16)[0]
        p_filesz = struct.unpack_from("<Q", data, base + 32)[0]
        return data[p_offset:p_offset + p_filesz], p_vaddr
    raise SystemExit("no executable PT_LOAD segment")


def verify_all(segment: bytes, base_vaddr: int, entries, verbose: bool) -> dict:
    results = {}
    for name, pattern in entries:
        if not pattern:
            results[name] = {"status": "MISSING", "matches": 0}
            continue
        anchor_offset, anchor_length = longest_fixed_run(pattern)
        needle = bytes(pattern[anchor_offset:anchor_offset + anchor_length])
        hits = []
        if not needle:
            results[name] = {"status": "MISSING", "matches": 0}
            continue
        search_from = 0
        while True:
            found = segment.find(needle, search_from)
            if found < 0:
                break
            search_from = found + 1
            candidate = found - anchor_offset
            if candidate < 0 or candidate + len(pattern) > len(segment):
                continue
            ok = True
            for index, expected in enumerate(pattern):
                if expected is not None and segment[candidate + index] != expected:
                    ok = False
                    break
            if ok:
                hits.append(candidate)
                if len(hits) > 64:
                    break
        status = "MISSING" if not hits else ("UNIQUE" if len(hits) == 1 else "AMBIGUOUS")
        entry = {
            "status": status,
            "matches": len(hits),
            "vaddr": hex(base_vaddr + hits[0]) if hits else None,
        }
        results[name] = entry
        if verbose:
            print(f"{status:10s} {name:52s} matches={len(hits)} vaddr={entry['vaddr']}")
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("so", type=Path)
    parser.add_argument("--sigs", type=Path, help="BedrockTools Signatures.cpp")
    parser.add_argument("--patterns", type=Path, help="JSON: name -> pattern (or {pattern: ...})")
    parser.add_argument("--json", type=Path, help="write the result table here")
    parser.add_argument("--failures-only", action="store_true")
    args = parser.parse_args()

    if args.sigs:
        entries = parse_signatures_from_cpp(args.sigs)
    elif args.patterns:
        entries = parse_signatures_from_json(args.patterns)
    else:
        raise SystemExit("provide --sigs or --patterns")

    print(f"signatures: {len(entries)}")
    segment, base_vaddr = load_text_segment(args.so)
    print(f"text segment: {len(segment)} bytes, vaddr {hex(base_vaddr)}")
    results = verify_all(segment, base_vaddr, entries, verbose=not args.failures_only)

    counts: dict[str, int] = {}
    for entry in results.values():
        counts[entry["status"]] = counts.get(entry["status"], 0) + 1
    print("summary:", counts)

    if args.failures_only:
        for name, entry in results.items():
            if entry["status"] != "UNIQUE":
                print(f"{entry['status']:10s} {name} matches={entry['matches']}")

    if args.json:
        args.json.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print("report:", args.json)

    return 0 if counts.get("UNIQUE", 0) == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
