# Feature -> Function -> Feasibility -> Hook Decision

The core thinking workflow, modeled exactly on how BedrockTools is built.
Use this before writing any code.

## The pipeline

```
User wants feature F
        |
        v
1. Name the game function(s) that implement the behavior F touches
        |
        v
2. Locate the function
   (a) BedrockTools Signatures.cpp already has it  -> resolve
   (b) community knowledge / prior art
   (c) optional disassembler (IDA/Ghidra): string xref -> function
        |
        v
3. Analyze (optional but recommended for unknown functions):
   decompile, read what it returns / writes / calls
        |
        v
4. Feasibility check: can F be expressed as
   (a) change return value?           -> inline hook or head-replace
   (b) change argument/input?         -> inline hook (modify before orig)
   (c) change a constant/branch?      -> byte patch
   (d) change object state?           -> field offset write / call
   (e) change virtual dispatch?       -> vtable hook
   (f) change when it runs?           -> dlopen / EGL / tick hooks + events
        |
        v
5. Verify signature is UNIQUE on the target .so
        |
        v
6. Implement with BedrockTools patterns, build, deploy, test
```

## Worked example 1: "Zoom" (BedrockTools `src/modules/visual/zoom.cpp`)

1. Feature: smooth zoom like OptiFine.
2. Functions: camera FOV is produced by `GetFov`; sensitivity by
   `LocalPlayerApplyTurnDelta`; hiding the held item by
   `BaseOptionRegistryGetHideItemInHand`.
3. Analysis: `GetFov` returns `float`; the module lerps the value toward a
   target while zooming, and returns the custom FOV from the detour.
4. Feasibility: return-value modification -> inline hook, call original first
   to get the base FOV, then return the animated value.
5. Verification: `verify_signatures.py` reports the three signatures UNIQUE.
6. Implementation (exact BedrockTools shape):

```cpp
static float (*_getFov_orig)(void*, float, int) = nullptr;
static float _getFov_zoom_hook(void* _this, float a, int enableVariableFOV) {
    float originalFov = _getFov_orig ? _getFov_orig(_this, a, enableVariableFOV) : 0.0f;
    if (g_zoomMod && g_zoomMod->isZoomActive()) {
        g_zoomMod->m_currentFov =
            std::lerp(g_zoomMod->m_currentFov, g_zoomMod->m_targetZoomFov, g_zoomMod->m_animSpeed);
        return g_zoomMod->m_currentFov;
    }
    return originalFov;
}
// onInit:
uintptr_t addr = bedrocktools::memory::resolve(SignatureId::GetFov);
bedrocktools::hooks::install((void*)addr, (void*)_getFov_zoom_hook, (void**)&_getFov_orig);
```

## Worked example 2: "Fullbright" (BedrockTools `src/modules/visual/fullbright.cpp`)

1. Feature: no darkness, max light level.
2. Function: the light-level computation reached via `SignatureId::Fullbright`.
3. Analysis: the function computes a brightness value; we do not need its
   logic at all.
4. Feasibility: return a constant -> head-replace patch (12 bytes).
5. Implementation:

```cpp
uint8_t patch[12] = {
    0x40, 0x8F, 0xA8, 0x52,   // MOV W0, #large
    0x00, 0x00, 0x27, 0x1E,   // FMOV S0, W0
    0xC0, 0x03, 0x5F, 0xD6    // RET
};
memcpy(m_originalBytes, m_patchTarget, sizeof(patch));   // backup first
bedrocktools::sdk::patchMemory(m_patchTarget, patch, sizeof(patch));
```

## Worked example 3: "Attack cancel" (BedrockTools `GameHooks.cpp`)

1. Feature: cancel or observe attacks (module `AutoGG`, reach counter, etc.).
2. Functions: `GameModeAttack` / `SurvivalModeAttack` (signature ids).
3. Analysis: both are `bool (*)(void* mode, void* target, void*, void*)`; the
   return value indicates whether the attack happened.
4. Feasibility: publish an event first; if a subscriber cancels, return false
   without calling the original.
5. Implementation:

```cpp
bool dispatchAttack(AttackKind kind, AttackFn original, void* gm, void* target, void* a2, void* a3) {
    AttackEvent event{kind, gm, reinterpret_cast<sdk::Actor*>(target), a2, a3};
    bus().publish(event);
    if (event.cancelled()) return false;
    return original ? original(gm, target, a2, a3) : false;
}
bool gameModeAttackDetour(void* gm, void* target, void* a2, void* a3) {
    return dispatchAttack(AttackKind::GameMode, gameModeAttackOriginal, gm, target, a2, a3);
}
```

## Worked example 4: "Find an undocumented parser (skins.json)"

Scenario: the BedrockTools signature dictionary has no entry for the code
that reads a skin pack's `skins.json`. Locate and understand it using nothing
but the binary (plus an optional disassembler).

1. Feature: understand how skin packs load / where `skins.json` is parsed.
2. Function guess: some function that reads the file `skins.json` and parses
   JSON entries such as `localization_name`, `texture`, `geometry`, `cape`,
   and `animations`.
3. Locate (no signature available -> string-xref route):
   - Search strings for `skins.json` and related messages.
   - The error message
     `"Invalid entry in skins.json - Every entry under 'skins' must be a json object ..."`
     is a unique anchor: follow its xref (a data reference).
   - All references land inside one function `sub_<addr>` -> that function is
     the parser.
4. Analyze (decompile):
   - Opens `"skins.json"` through a pack virtual call; returns 0 on failure.
   - `Json::Reader::parse` (strict JsonCpp) parses the whole file; the root
     must be an object.
   - Iterates the `"skins"` array: every entry must be an object (otherwise
     the error above is logged and the entry is skipped), then reads
     `localization_name`, `texture`, `geometry`, `cape`, and `animations`,
     strips directory prefixes from texture/cape paths, falls back to
     `geometry.humanoid.custom` when geometry is missing or default, validates
     that texture/cape assets exist, and appends a skin entry to a list.
5. Feasibility / hook choice:
   - Keep every entry's custom geometry: patch the
     `geometry.humanoid.custom` fallback branch (constant/condition patch).
   - Enable cape parsing: patch the flag/condition that skips the
     `["cape"]` read.
   - Accept trailing commas in the strict parser: NOP the JsonCpp
     object-end rejection branch.
   - Observe parse failures: inline-hook the parser and read the error path.
6. Verify: the parser is confirmed by its own string xrefs; every patch point
   is validated by reading original bytes before writing (version-safe).

## Decision shortcuts

- Function returns a value you want to change -> inline hook, call orig first,
  return modified.
- Function takes an argument you want to change -> inline hook, modify arg,
  call orig.
- Function has a constant/branch that limits behavior -> byte patch with
  original-byte verification.
- Function is a virtual method on an object you already hold -> vtable call or
  vtable slot hook.
- Feature is per-frame or per-tick -> reuse `FrameEvent`/`LocalPlayerTickEvent`
  instead of hooking another function.
- Feature needs an object only available later (ClientInstance, local player)
  -> hook `ClientInstanceUpdate`/`NormalTick` and cache the pointer
  (BedrockTools `gamehooks::clientInstance()`).

## Feasibility rules of thumb (from BedrockTools experience)

- Prefer the smallest change: a 4-byte patch beats a hook; a hook beats
  reimplementing the function.
- Never patch without verifying the original bytes; a wrong-version patch is
  worse than no patch.
- If a signature is AMBIGUOUS, do not guess: lengthen it or find a better
  anchor instruction.
- If the target function is huge and complex, consider hooking one of its
  callees (a smaller helper) instead.
- Error/log strings are excellent anchors for finding undocumented parsers:
  search the message text, follow its xref, and decompile the referencing
  function.
