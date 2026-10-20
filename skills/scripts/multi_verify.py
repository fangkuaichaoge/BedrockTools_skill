#!/usr/bin/env python3
"""Test a signature dictionary against several game builds at once.

Answers the porting question directly: which patterns are UNIQUE in *every*
build, which are MISSING, and which are AMBIGUOUS.  A pattern that is unique in
one build and missing in another is version-locked; the goal for a mod that
supports several builds is a pattern that resolves to exactly one hit in all of
them.

Usage:
    python multi_verify.py --sigs patterns.json --so a.so,b.so [--json out.json]
    python multi_verify.py --sigs BedrockTools/Signatures.cpp --so a.so,b.so
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from a64 import parse_elf, sec_by_name          # noqa: E402
from sigscan import compile_pattern, scan       # noqa: E402


def load_patterns(path):
    p = Path(path)
    text = p.read_text(encoding="utf-8", errors="replace")
    if p.suffix == ".json":
        return json.loads(text)

    # BedrockTools Signatures.cpp: SignatureDefinition{SignatureId::NAME, "..."}
    out = {}
    for m in re.finditer(r'SignatureId::(\w+)\s*,\s*"([^"]+)"', text):
        out[m.group(1)] = m.group(2)
    return out


def resolve(so, patterns):
    d = Path(so).read_bytes()
    secs = parse_elf(d)
    text = sec_by_name(secs, ".text")
    res = {}
    for name, pat in patterns.items():
        fixed, plen = compile_pattern(pat)
        hits = scan(d, text["off"], text["size"], fixed, plen)
        res[name] = {
            "n": len(hits),
            "vaddrs": [text["addr"] + (h - text["off"]) for h in hits[:6]],
        }
    return res


def main():
    def arg(f, dflt=None):
        return sys.argv[sys.argv.index(f) + 1] if f in sys.argv else dflt

    if "--sigs" not in sys.argv or "--so" not in sys.argv:
        print(__doc__)
        return 1

    patterns = load_patterns(arg("--sigs"))
    sos = arg("--so").split(",")
    print(f"patterns: {len(patterns)}   builds: {len(sos)}")
    for s in sos:
        print(f"   {s}")

    results = {s: resolve(s, patterns) for s in sos}

    print(f"\n{'name':34s} " + " ".join(f"{Path(s).name[-9:]:>10s}" for s in sos)
          + "   verdict")
    print("-" * (36 + 11 * len(sos) + 12))

    all_unique, partial, dead = [], [], []
    for name in patterns:
        cells = []
        counts = []
        for s in sos:
            r = results[s][name]
            counts.append(r["n"])
            cells.append(f"{r['n']:>10d}")
        if all(c == 1 for c in counts):
            verdict = "UNIQUE in all"
            all_unique.append(name)
        elif all(c == 0 for c in counts):
            verdict = "MISSING in all"
            dead.append(name)
        elif any(c == 0 for c in counts):
            verdict = "missing somewhere"
            partial.append(name)
        elif any(c > 1 for c in counts):
            verdict = "ambiguous somewhere"
            partial.append(name)
        else:
            verdict = "?"
        print(f"{name:34s} " + " ".join(cells) + f"   {verdict}")

    print(f"\nunique in every build : {len(all_unique)}")
    print(f"needs attention       : {len(partial)}")
    for n in partial:
        print(f"    {n}")
    if dead:
        print(f"dead everywhere       : {len(dead)}")

    if "--json" in sys.argv:
        out = arg("--json")
        json.dump({"builds": sos, "results": results}, open(out, "w"), indent=1)
        print("->", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
