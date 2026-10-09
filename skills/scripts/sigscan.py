#!/usr/bin/env python3
"""Wildcard signature scanner for a stripped ELF .so (read-only, offline).

Usage
-----
    python sigscan.py <so> <patterns.json> [--out hits.json]
    python sigscan.py <so> --strings <regex>

patterns.json maps a name to a space-separated hex pattern where `??` (or `?`)
is a wildcard byte:

    {"GetFov": "?? ?? ?? ?? 08 40 20 1E ?? ?? ..."}

The scanner anchors on the first fixed byte, so it is fast even on a
multi-hundred-MB library.  It reports every match as a virtual address; on
Bedrock Android the image base is 0, so the file offset equals the vaddr for
`.text`.

Only `UNIQUE` matches are safe to hook.  `AMBIGUOUS` means the pattern must be
lengthened; `MISSING` means the dictionary and the binary disagree (a version
mismatch) -- re-derive the pattern rather than loosening it.  See
`references/signature-forensics.md`.
"""
import json
import re
import sys

from a64 import parse_elf, sec_by_name


def compile_pattern(text):
    """Return (list of (offset, byte), token count) for a wildcard pattern."""
    fixed = []
    for i, t in enumerate(text.split()):
        t = t.strip()
        if t in ("?", "??", "**"):
            continue
        fixed.append((i, int(t, 16)))
    return fixed, len(text.split())


def scan(data, start, size, fixed, plen):
    """Naive-but-anchored scan: find the first fixed byte, then verify."""
    if not fixed:
        return []
    first_off, first_byte = fixed[0]
    hits = []
    end = start + size - plen
    i = start + first_off
    anchor = bytes([first_byte])
    while True:
        j = data.find(anchor, i, end + first_off + 1)
        if j < 0:
            break
        base = j - first_off
        if all(data[base + off] == val for off, val in fixed):
            hits.append(base)
        i = j + 1
    return hits


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 1

    so = sys.argv[1]
    with open(so, "rb") as fh:
        d = fh.read()
    secs = parse_elf(d)

    if "--strings" in sys.argv:
        rx = re.compile(sys.argv[sys.argv.index("--strings") + 1].encode())
        for s in secs:
            if s["type"] != 1 or s["size"] == 0:
                continue
            blob = d[s["off"]:s["off"] + s["size"]]
            for m in re.finditer(rb"[\x20-\x7e]{4,}", blob):
                if rx.search(m.group()):
                    print(f'{s["addr"] + m.start():#012x} [{s["name"]}] '
                          f'{m.group().decode("ascii")[:180]}')
        return 0

    pats = json.load(open(sys.argv[2], encoding="utf-8"))
    text = sec_by_name(secs, ".text") or max(
        (s for s in secs if s["type"] == 1), key=lambda s: s["size"])

    report = {}
    for name, pat in pats.items():
        fixed, plen = compile_pattern(pat)
        hits = scan(d, text["off"], text["size"], fixed, plen)
        vaddrs = [text["addr"] + (h - text["off"]) for h in hits]
        report[name] = {"pattern": pat, "bytes": plen, "fixed": len(fixed),
                        "matches": len(hits), "vaddrs": vaddrs}
        tag = {0: "MISSING", 1: "UNIQUE"}.get(len(hits), "AMBIGUOUS")
        addrs = " ".join(hex(a) for a in vaddrs[:8])
        print(f"{tag:10s} {name:52s} {len(hits)} {addrs}")

    if "--out" in sys.argv:
        out = sys.argv[sys.argv.index("--out") + 1]
        json.dump({"so": so,
                   "text": {"addr": text["addr"], "size": text["size"]},
                   "results": report},
                  open(out, "w", encoding="utf-8"), indent=1)
        print("->", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
