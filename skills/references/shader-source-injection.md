# Shader Source Injection (patching GLSL as text)

Distilled experience from injecting code into a closed-source renderer's shaders
at the moment they are submitted for compilation. Generic; no game-specific
identifiers.

Why this technique: it changes appearance without touching a single byte of the
target's geometry or game logic, and because it is a **pure string transform** it
can be unit-tested on the host with no device.

---

## 1. Where the insertion goes

* Declarations must sit at **global scope**, after the existing declarations and
  immediately before the entry point. Inserting before the stage's main function
  keeps the original declarations intact.
* Redefining an existing attribute is done with a **function-like macro** on its
  own name. A macro that references itself in its own replacement list is legal -
  it is not re-expanded - so `#define X (X + ...)` is safe.
* Scan a **comment-stripped copy** so that "declarations" inside comments or
  strings do not fool the matcher. Strip by replacing comment bodies with spaces
  while preserving newlines: the stripped copy then has **identical length and
  offsets**, so an insertion index computed on it is valid for the original.
* Keep one marker identifier in the injected text (for example the name of an
  injected uniform) and refuse to inject twice - idempotence matters because the
  same source can be submitted more than once.

## 2. Discovery, not hard-coding

Cross-compiled shaders rename and repunctuate everything. Therefore:

1. enumerate the stage's declarations;
2. pick the one whose **normalised** identifier belongs to the family you need
   (normalise on *both* sides: lower-case, strip separators - a pattern that
   differs from reality only by an underscore or a prefix silently matches
   nothing);
3. reject ambiguity (two candidates in the same family) instead of picking one;
4. reject an identifier that is reused as a local, a parameter or an output -
   a textual macro would corrupt those uses;
5. only then rewrite.

Every rejection is a reason string in the log. A rewrite that silently does not
happen is the most expensive kind of bug in this technique.

## 3. Interface and link safety

This is where a shader patch can break the target rather than just itself:

* **A varying/output you add must exist on both stages.** Adding a vertex-stage
  output whose fragment-stage input is missing (or vice versa) can fail linking.
  A link failure on a program you modified can be fatal in some renderers, so:
  prefer **uniforms** over new varyings, and if a per-fragment value is
  unavoidable, patch both stages in the same round and check the link status.
* **Verify the link status yourself** and log the driver's info text together
  with whether the program was modified.
* **Resolve injected uniform locations after linking**; a missing location means
  "this program is not eligible", not "call it anyway".
* **Keep the neutral value neutral.** An injected mode/selector uniform must be
  reset to its pass-through value immediately after the extra pass, and the
  original state restored - a leaked "effect on" value silently changes every
  later pass in the frame.

## 4. Component-count correctness

Handle every combination your discovery can return, and never silently
truncate:

* a `vec4` attribute may carry `w != 1`, so scale/extrude only `xyz` and
  preserve `w`;
* a `vec4` normal is not a `vec4` direction: use its `xyz`;
* build the replacement expression so that it has the same type as the
  attribute it replaces.

## 5. Which stages you cannot patch

Detect and skip, rather than half-apply:

* fragment stages with **multiple or array outputs** (deferred/MRT pipelines);
* stages whose colour output variable cannot be identified unambiguously;
* sources missing the attribute family the effect needs;
* sources matched by a user-provided exclusion list.

Skipping is a normal outcome: report it with the observed vocabulary so the
classifier can be extended later.

## 6. Robustness

* Wrap the whole transform so that an internal error can only result in
  "not patched". An exception escaping into a render/compile thread is a process
  abort, not a warning.
* Never let the transform throw on malformed input (unbalanced braces, truncated
  source): return "skipped" with a reason.
* Keep the transform free of GL calls so it stays host-testable, and keep a host
  test suite that covers: classic and modern syntax, renamed attributes,
  `vec3`/`vec4` combinations, comment-only decoys, identifier reuse, exclusion,
  idempotence, and the interface/link rules above.

## 7. Testing without a device

Because the transform is pure, the host tests are the primary correctness gate:
table-driven cases of source -> expected source (or expected skip reason). Device
testing then only has to confirm classification (which programs are seen and
which are eligible), which is a single log line per program.
