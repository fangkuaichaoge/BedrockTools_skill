#!/usr/bin/env python3
"""Check that every signature in a mod still resolves on every build you support.

Why this exists
---------------
A signature is only safe if it matches the target binary **exactly once**. Porting a mod to a
second game build by adding a signature and testing on one device hides two failure modes:

  * MISSING  - the pattern no longer exists in that build, so the hook quietly never installs;
  * AMBIGUOUS - it exists more than once, so the hook may land on an unrelated function.

Both are invisible at run time unless you log the resolution, which is why checking them offline
is worth a minute per release. A missing hook is not always a cosmetic loss: a missing level
destructor hook was the root cause of a crash on exiting a save, because the cleanup it performed
never ran.

What it does
------------
Reads the signatures out of a mod source file (adjacent `constexpr std::string_view` string
literals in the house style, `??` wildcards), then scans each supplied binary and reports the
number of matches per signature per build.

Usage
-----
    python check_mod_signatures.py --source <mod-dir>/src/main.cpp \
        --binary <libminecraftpe-old.so> --binary <libminecraftpe-new.so> \
        [--name-filter ScreenView,RenderLevel] [--json report.json]

Exit code is 1 when any signature is missing or ambiguous on any build, so it can gate a release.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

DECLARATION = re.compile(
    r'constexpr\s+std::string_view\s+(\w+)\s*=\s*((?:\s*"[^"]*"\s*)+);'
)


def load_signatures(source: Path) -> dict[str, list[int | None]]:
    text = source.read_text(encoding="utf-8", errors="replace")
    signatures: dict[str, list[int | None]] = {}
    for match in DECLARATION.finditer(text):
        name = match.group(1)
        tokens: list[int | None] = []
        for literal in re.findall(r'"([^"]*)"', match.group(2)):
            for token in literal.split():
                tokens.append(None if set(token) == {"?"} else int(token, 16))
        if tokens:
            signatures[name] = tokens
    return signatures


def anchor_of(pattern: list[int | None]) -> tuple[int, bytes]:
    """Longest contiguous run of fixed bytes - the fastest safe search key."""
    best_start, best_len = 0, 0
    start = None
    for index, byte in enumerate(pattern + [None]):
        if byte is not None:
            if start is None:
                start = index
            continue
        if start is not None and index - start > best_len:
            best_start, best_len = start, index - start
        start = None
    return best_start, bytes(b for b in pattern[best_start:best_start + best_len] if b is not None)


def scan(data: bytes, pattern: list[int | None], limit: int = 8) -> list[int]:
    anchor_index, anchor = anchor_of(pattern)
    if not anchor:
        return []
    fixed = [(index, byte) for index, byte in enumerate(pattern) if byte is not None]
    length = len(pattern)
    hits: list[int] = []
    position = 0
    while len(hits) < limit:
        found = data.find(anchor, position)
        if found < 0:
            break
        base = found - anchor_index
        position = found + 1
        if base < 0 or base + length > len(data):
            continue
        if all(data[base + index] == byte for index, byte in fixed):
            hits.append(base)
    return hits


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", required=True, type=Path, help="mod source with the signatures")
    parser.add_argument("--binary", required=True, action="append", type=Path,
                        help="target library; repeat for every supported build")
    parser.add_argument("--name-filter", default="",
                        help="comma-separated substrings; only matching signature names are checked")
    parser.add_argument("--json", type=Path, help="write a machine-readable report here")
    args = parser.parse_args()

    signatures = load_signatures(args.source)
    if not signatures:
        print(f"no signatures found in {args.source}", file=sys.stderr)
        return 1

    wanted = [part.strip() for part in args.name_filter.split(",") if part.strip()]
    if wanted:
        signatures = {
            name: pattern for name, pattern in signatures.items()
            if any(fragment in name for fragment in wanted)
        }
    print(f"signatures: {len(signatures)} from {args.source.name}")

    report: dict[str, dict[str, object]] = {}
    failures = 0
    for binary in args.binary:
        if not binary.is_file():
            print(f"\n{binary}: NOT FOUND", file=sys.stderr)
            failures += 1
            continue
        data = binary.read_bytes()
        print(f"\n=== {binary.name} ({len(data) // (1024 * 1024)} MB) ===")
        entry: dict[str, object] = {}
        for name, pattern in signatures.items():
            hits = scan(data, pattern)
            if len(hits) == 1:
                entry[name] = {"status": "UNIQUE", "address": hex(hits[0])}
                print(f"  UNIQUE    {name:40s} 0x{hits[0]:x}")
            elif not hits:
                entry[name] = {"status": "MISSING"}
                failures += 1
                print(f"  MISSING   {name:40s} pattern absent from this build")
            else:
                entry[name] = {"status": "AMBIGUOUS", "addresses": [hex(h) for h in hits]}
                failures += 1
                print(f"  AMBIGUOUS {name:40s} {len(hits)}+ matches: "
                      + " ".join(f"0x{h:x}" for h in hits))
        report[binary.name] = entry

    if args.json:
        args.json.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nreport written to {args.json}")

    if failures:
        print(f"\n{failures} signature/build combination(s) need attention before shipping")
        return 1
    print("\nall signatures resolve exactly once on every build")
    return 0


if __name__ == "__main__":
    sys.exit(main())
