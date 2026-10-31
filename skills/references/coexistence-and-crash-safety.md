# Coexistence with another hooking mod, and crash-safety invariants

Two hard-won lessons are collected here, because they cost the most device-test rounds of any
work in this workspace:

* a feature that "disappears when another mod is installed" is usually **not** a hook conflict;
* a feature that "works, then crashes" is usually an **address or pointer that was never
  verified** before it was used.

Everything below is distilled from a real case: a HUD-panel mod (`HighlightInfo`) coexisting with a
third-party map mod (`LeviMap`) on the same game build. Both mods used the same preloader
`pl::memory::hook` API, hooked the same engine functions, and the panel silently stopped drawing.
Then two "fixes" in a row crashed the process before the real cause was nailed down.

---

## 1. Three independent layers where two mods can collide

Check them in this order. Do not assume the first one.

### 1.1 The hook layer — usually *not* the problem

The preloader's `hook()` is a **chain manager**, not a raw patch. In `src/pl/memory/Hook.cpp` it
keeps one `HookData` per target, with a `std::set<HookElement>` ordered by `HookPriority`; only the
first install creates a `GlossHook`, and every add/remove calls `rebuildChain()`, which rewires each
detour's `originalFunc` to the next detour (last one to the real `origin`). Consequences:

* two mods hooking one target is **supported by design**; both detours run;
* the order is priority, then insertion order; there is no "later hook wins";
* a detour that returns without calling its `originalFunc` silently cuts the chain for everyone
  after it — if you ever suspect this, prove it by logging your detour's call count, not by guessing.

If your detour **is** being called, the hook layer is fine and the bug is elsewhere.

### 1.2 The signature layer — the most common real cause

Another mod's inline hook rewrites the first bytes of the same function (a `ldr x16, #8; br x16;
.quad trampoline` style patch is 16 bytes; some libraries use 24). Any signature whose constrained
bytes fall inside that region stops matching, so `resolveSignature()` returns **0** and your detour
is silently never installed. Symptom: feature gone, no crash, no error, no log line.

Fix: keep the head-anchored pattern for the normal case, and add a **deep-anchored fallback** (see
§4) that is resolved only when the primary returns 0.

### 1.3 The RTTI / vtable layer — SDK helpers are not contract

An SDK helper that resolves a vtable entry by RTTI name (`resolveVtableFunction(...)`) may work on
one launcher build and return 0 on the next, because the implementation depends on the runtime
hook library being able to find the module's sections. A mod that puts its font capture, draw
context and identifier lookup behind such a helper loses **all** of them at once on a runtime
change, while signatures keep working — which makes it look like a completely different bug.

Rule: any SDK helper on the critical path must have its result **logged once per class**, and must
have a fallback that does not depend on it (see §5).

### 1.4 The render-queue layer

Two mods queueing engine UI in the same frame can reorder or drop batches of different textures.
Draw in texture-group passes and call the engine flush between groups (this is also what the
vanilla UI does); never assume one flush preserves submission order across textures.

---

## 2. Decision tree: "my feature disappeared after installing mod X"

```
feature gone, no crash
├─ is your detour called at all?  (log a call counter, not a timing guess)
│  ├─ yes → the hook layer is fine → check what YOUR detour does with the arguments (see §3)
│  └─ no  → the detour was never installed
│           ├─ log every resolveSignature() result and every hook() return code
│           ├─ pattern returns 0 while the same pattern resolves when mod X is absent
│           │  → signature layer (§1.2): add the deep-anchored fallback
│           └─ pattern returns 0 in every configuration
│              → the runtime/SDK helper is broken or the build changed (§1.3, §5)
└─ feature gone, WITH crash
   → §3: an unverified address or pointer is being used
```

Two cheap rules that make this tree answerable from a single device log:

1. Log **resolution results** (per pattern, per class) and **hook return codes** — not just
   "installed ok".
2. Log a **throttled call heartbeat** for every detour (first 3 calls, then once every few
   seconds). "Installed but never called" and "called every frame" are different bugs and the
   heartbeat distinguishes them without a debugger.

---

## 3. The crash you must never ship: an unverified address

Real incident: the `ScreenView::render` detour's second argument was assumed to be the render
context. With the other mod installed, that argument can be junk (a competing detour on the same
chain may forward only `x0`). Reading its first word as a "vtable" produced an address **outside**
the game module, and handing that to `pl::memory::hook()` patched memory that was not code — instant
process death with no log line after it.

Invariants that make this impossible:

| Invariant | How | Cost when missing |
|---|---|---|
| Every hook target must be inside the game image | `dladdr(target).dli_fbase == moduleBase` before calling `hook()` | patch arbitrary memory → crash |
| Never derive a hook target from an unidentified object | identify objects by a **fixed vtable address point** (`vtable(obj) == base + vaddr`), never by argument position | same |
| A hook target must be *code*, not data | the module check plus a byte check (§4) | same |
| Dereference only readable memory | cached `/proc/self/maps` ranges, **refreshed on a miss**, pointers untagged | stale snapshot or a freed object reads as garbage → crash |
| Screen controllers and their destructor slots | object readable, vtable readable, and each slot must pass the module check before hooking | hooking a data word as a function → crash on close |
| Engine calls on the critical path | only functions whose address was verified by bytes | calling the wrong function → undefined behaviour or crash |
| Failure mode | fail open: skip the frame, no drawing, no crash | a crash loses the session and the log |

Two meta-rules:

* **A crash is a bigger bug than a missing feature.** When in doubt, do not draw.
* **A "fix" that changes two things at once is not a fix.** Change one variable per build, or you
  cannot attribute the result (this workspace learned it the hard way: two speculative changes in
  one build cost an extra device round-trip and left the root cause ambiguous).

---

## 4. Deep-anchored signatures for functions that another mod hooks

`resolveSignature` returns `candidateOffset = anchorOffset - pattern.anchorIndex`, i.e. the address
of the pattern's **first byte**. Therefore a pattern that starts with `K` wildcard bytes still
resolves the identical function **head** — the wildcards cost nothing.

Design:

1. Wildcard the first `K` bytes, with `K` ≥ any plausible prologue patch (16–32 bytes is enough for
   every common arm64 inline-hook library; 128 is comfortably safe).
2. Anchor the constrained bytes **past** the patch: take 24–32 literal bytes from the function body
   at head + `K` (or the tail of the existing pattern if it reaches that far).
3. Verify offline on both a clean image and on images with simulated head patches of 16 / 24 / 32 /
   48 / 96 bytes: the pattern must stay **UNIQUE** and must resolve to the exact head.
4. Keep the original pattern as the primary (it is more portable across builds) and the deep one as
   the fallback when the primary returns 0.

Literal body bytes are safe as pattern data **if the image's `.text` has no dynamic relocations** —
verify that once per build: parse `.rela.dyn` and check that no `r_offset` falls inside `.text`.
(If the image does relocate text, keep the preloader's wildcarded-immediate style instead.)

---

## 5. Resolving engine entry points when the SDK resolver fails

Symptom: the RTTI helper returns 0 for **every** class (log all of them; if it is only one class,
it is a class-name/slot problem, not a runtime problem).

Fallback that has been in production in this workspace:

1. **Module base from the linker**, not from a signature: walk the program headers
   (`dl_iterate_phdr`), find the object whose name contains the game library name, take
   `dlpi_addr`. This must happen before anything else, so no other resolution is required to get it.
2. **Address table from the ELF**: read the section headers and `.rela.dyn`; for a class, find the
   typeinfo name string, then the pointer to it (the typeinfo object), then the vtable whose `[-1]`
   slot is that typeinfo and whose `[-2]` (offset-to-top) is zero. The **address point** is
   `vtable + 16` — that is exactly the value stored in objects, so it also serves as the identity
   test `vtable(obj) == moduleBase + addressPointVaddr`. Slot `n` is the word at
   `addressPoint + 8n`.
3. **Verify by bytes**: an address-table entry is only used when the bytes at
   `moduleBase + vaddr` match the signature's constrained bytes — prologue first, then the
   deep-anchored pattern's body bytes (which survive another mod's head patch). Otherwise fall back
   to the scanner.
4. **Portability fallback**: resolve all patterns in a **single pass** (Aho–Corasick over the
   patterns' anchors, exact same anchor rule as the preloader) instead of one full scan per
   pattern. This also removes a nasty failure mode: with ~30 patterns the per-pattern API needs
   ~20 s, so the hooks end up being installed while the menu is already drawing through the very
   functions being patched.

A byte-verified address table makes hook installation instant and removes the scan from the normal
path, which is itself a crash-prevention measure (no patching while the engine is executing the
patched code).

---

## 6. Post-mortem discipline that made this tractable

1. **Device log first, and make each line able to falsify a hypothesis.** "no font captured every
   frame" and "detour called 7000 times" together rule out the hook layer in one look.
2. **One change per build.** Instrument → hypothesise → one change → test → next hypothesis.
3. **Keep the tested binary.** Publish the version the user confirmed; ship the cleaned build only
   after re-testing, and keep the tested package next to it in `dist/`.
4. **Diagnostics never ship.** Remove the log/diagnostic modules before the release build, but keep
   the *safety* code that came out of them (module check, identity checks, readable-map checks).
5. **Write the post-mortem while it is fresh**, including the hypotheses that were wrong and the
   evidence that killed them. The next session starts from the doc, not from the beginning.
