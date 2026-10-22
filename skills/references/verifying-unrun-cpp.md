# Verifying Code You Cannot Run

Practical notes for the common situation in this workspace: the target is an
Android arm64 `.so`, there is **no host C++ toolchain**, **no attached device**, and
possibly no network. You still have to hand the user something you can defend.

This file is about honesty under those constraints: what actually raises
confidence, what only looks like verification, and how to report the gap.

---

## 1. Classify the risk before choosing a technique

Not all code carries the same risk. Split the deliverable:

| Class | Examples | Can be checked without running? |
|---|---|---|
| **Data tables** | signature byte patterns, face/UV tables, offsets | yes, decisively |
| **Pure functions of input** | geometry transform, parsing, path resolution | yes, by a second implementation |
| **ABI / layout assumptions** | struct displacements, calling convention | partly, by disassembly |
| **Integration** | hook installs, thread timing, GL state | no - device only |
| **Visual result** | "does it look right" | no - device only |

Spend effort in that order. A wrong **table** is cheap to disprove and expensive to
ship; a wrong **visual** can only be settled on hardware, so say so.

---

## 2. Techniques that genuinely raise confidence

### 2.1 Byte-pattern uniqueness (decisive, cheap)

For anything located by signature, the question "will this resolve on the build the
user runs" is answerable offline with total certainty:

```
python scripts/verify_signatures.py <libminecraftpe.so> --sigs <Signatures.cpp>
python scripts/multi_verify.py <libminecraftpe.so> ...
```

Only `1 hit` is usable; `0` is a version mismatch; `>1` is ambiguous and must be
lengthened. Re-run this against **the exact build being deployed**, and prefer
several builds if the user has them (`version-porting.md`).

This is the highest-value check in the whole workflow and needs no device.

### 2.2 A second implementation in a language you *can* run

When a pure algorithm is too intricate to eyeball (geometry transforms, packing,
path resolution), write a second implementation in Python and:

1. validate **the Python one** against a ground truth you trust (an export from the
   authoring tool, a reference file, an invariant like area conservation);
2. then prove the C++ is the *same algorithm* - mechanically, not by memory:
   - compare **constants** by parsing the C++ source and the Python source and
     asserting they agree field by field;
   - compare **structural decisions** (transform order, which corners are swapped,
     thresholds, comparison direction) with assertions that read the C++ source;
3. make those checks fail loudly when they cannot find what they expect. A check
   that silently passes when the code moved is worse than no check.

The Python implementation is not decoration: it is the only executable oracle for
the C++ in this environment. Keep it in sync, and state in the docs that the C++
was verified *against* it rather than by execution.

### 2.3 Compile it, even if you cannot link or run it

The cross-compiler is available and is a real type checker:

```
clang++ --target=aarch64-none-linux-android<N> --sysroot=<ndk>/... \
        -std=c++20 -I<src> -fsyntax-only file.cpp
```

That catches type errors, bad overloads, missing includes, and template problems -
a large share of real defects. It does **not** catch logic errors, layout errors, or
anything runtime. Do not mistake "compiles" for "correct".

### 2.4 Read the engine's own instructions for ABI claims

Claims like "the argument is a float triple, not a block coordinate", "the field is
at displacement X", or "the call is unconditional" are settlement questions about
disassembly, not about running the code. `scripts/a64.py` answers them offline
(`func`, `callers`, `guards`, `disas`, `xref`).

This is stronger evidence than a successful run, because it explains *why*.

### 2.5 Self-checking builds and logs

When the device is the only oracle, make the mod **report its own state** on the
first run: which signatures resolved, how many hooks installed, how many models
loaded, the first few decisions. That converts "it does nothing" into a one-line
diagnosis. See `instrumentation-and-diagnostics.md`.

Design the checks so a *negative* result is visible: a filter that prints nothing
is indistinguishable from a hook that never fired.

---

## 3. Techniques that only look like verification

| Looks like | Why it is not |
|---|---|
| "It compiles" | says nothing about behaviour |
| A Python port that passes | validates the port, not the C++ - unless you also prove they match |
| Reading the diff carefully | finds what you were looking for, not what you missed |
| One successful device run of an earlier build | does not cover the changed path |
| A unit table filled in from memory | the rounding errors live in the digits you did not check |

The failure mode to avoid: a chain of individually weak checks that together feel
like one strong one.

---

## 4. Dead ends worth not repeating

Recorded so the next attempt does not burn the same hour.

* **Retargeting the NDK's libc++ to WebAssembly.** The NDK sysroot's libc++ is
  configured for Android: it fails on `_LIBCPP_HAS_NO_THREADS`, then on the locale
  headers (`mask`, `alpha`, `_LIBCPP_LOCALE__L_CTYPE`), then on
  `sys/user.h` ("Unsupported architecture"), and the Android C headers it does
  export are not viable for a wasm target. Freestanding source files that only need
  `<string>`/`<vector>`/`<cmath>` may still be portable, but STL-heavy translation
  units are not worth it.
* **Fetching a host compiler at runtime.** With no network, `pip install`-style
  solutions fail; check for an existing toolchain (MSVC, LLVM, mingw, WSL) before
  assuming there is none, and then stop - do not spend the budget on workarounds.
* **Assuming `adb` implies a device.** `adb devices` returning an empty list is a
  hard stop for runtime verification; check it early so plans do not depend on it.

---

## 5. Reporting the gap

The deliverable is not complete until the user knows what was and was not proven.
A short table is enough:

| Item | Status | How |
|---|---|---|
| signatures resolve uniquely on the target build | verified | offline scan |
| geometry/format algorithm | verified | second implementation + ground truth |
| C++ constants match the validated tables | verified | source-level assertion |
| binary exports the loader symbol, correct ABI | verified | ELF inspection |
| **renders correctly in game** | **not verified** | needs a device |

Then state the one sentence that matters: *what still needs the user's hands*. Users
accept "the maths is proven, the visual needs your device"; they do not accept
silence followed by a surprise.
