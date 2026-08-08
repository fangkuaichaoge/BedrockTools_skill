---
name: bedrock-pe-modding
description: "Develop native mods and hooks for Minecraft Bedrock (PE) Android arm64 by following the exact techniques used in the open-source BedrockTools project. Use when the user wants to: hook, patch, or mod libminecraftpe.so; write a LeviLauncher / preloader native mod (.so); turn a desired feature into concrete game-function hooks; decide between inline hooks, vtable hooks, and direct byte patches; extract or verify ARM64 byte signatures; build an arm64-v8a Android .so with NDK + CMake or xmake; package a mod (manifest.json / .levipack); port a mod to a new Bedrock version; or understand Bedrock PE internals such as tick, render, FOV, packet, attack, screen, weather, time, skin, and UI hooks. Not for Java/Spigot/Forge mods or for non-Bedrock ELF targets without adaptation."
---

# Bedrock PE Native Modding (BedrockTools patterns)

This skill distills the mod/hook/SDK/build techniques of the open-source
[BedrockTools](https://github.com/RadiantByte/BedrockTools) project into a
reusable workflow. Target binary: Minecraft Bedrock Android **arm64-v8a**
(`libminecraftpe.so`). Runtime: **LeviLauncher + preloader**; the preloader
SDK is open source at
[LiteLDev/preloader-android](https://github.com/LiteLDev/preloader-android).

Everything below is deliberately **machine-agnostic**: replace
`<libminecraftpe.so>`, `<NDK>`, `<your-mod-dir>` with real paths on the
machine you are on. The code examples follow BedrockTools' code exactly; do
not invent new abstractions.

## Architecture (understand before coding)

- **Load chain**: LeviLauncher (Java) -> `libpreloader.so` (C++ loader:
  scans `mods/`, validates `manifest.json`, `dlopen`s your `.so`) ->
  your mod -> `libminecraftpe.so` (loaded later by the game).
- **Lifecycle**: preloader looks up the exported symbol `PLGetModRegistration()`
  and calls `load/enable/disable/unload`. Register with the
  `PL_REGISTER_MOD(Type, instance)` macro (`pl/Mod.hpp`).
- **Timing**: call `GlossInit(true)` first in `load()`. The game library may
  not be loaded yet, so either hook `dlopen` (BedrockTools' approach in
  `src/core/Runtime.cpp`) or probe with
  `dlopen("libminecraftpe.so", RTLD_NOW | RTLD_NOLOAD)`.
- **Function location**: the game `.so` is stripped. BedrockTools stores
  **byte-signatures** (function-head machine code with `?` wildcards) and
  resolves them at runtime via `pl::memory::resolveSignatures`.
- **Version sensitivity**: signatures, member offsets, and vtable slots
  change per game version. Porting = re-extract/re-verify (see
  `references/version-porting.md`).

## Core APIs (from the preloader SDK and BedrockTools)

| Purpose | API | Header |
|---|---|---|
| Register a mod | `PL_REGISTER_MOD(Type, inst)` | `pl/Mod.hpp` |
| Inline hook with chain | `pl::memory::hook(target, detour, &orig, priority)` / `unhook` / `HookHandle` | `pl/memory/Hook.hpp` |
| Low-level hook engine | `GlossInit(true)`, `GlossHook(addr, new, &old)`, `GlossGotHook`, `GlossPltHook`, `GlossHookByName/AddrByName` | `pl/Gloss.h` |
| Resolve signatures | `pl::memory::resolveSignature(pattern, "libminecraftpe.so")` | `pl/memory/Signature.hpp` |
| Patch memory | `pl::memory::writeBytes/readBytes/revertPatch` | `pl/memory/Patch.hpp` |
| Resolve vtable slot by RTTI | `pl::memory::resolveVtableFunction(typeInfoName, slot, module)` | `pl/memory/Vtable.hpp` |
| Logging | `pl::log::Logger::getOrCreate(name)` | `pl/Logger.hpp` |
| Input callback | `pl::input::registerMouseCallback(...)` | `pl/Input.hpp` |
| Launcher menu | `pl::modmenu::ModuleBuilder / ButtonBuilder` | `pl/ModMenu.hpp` |
| Runtime ABI for other mods | `BedrockTools_GetApi(1)` export, `api::find()` | `include/bedrocktools/Api.hpp` |

BedrockTools thin wrappers you should reuse as-is:
- `src/core/memory/Hooks.hpp` — `hooks::install(target, detour, &original)`
  (wraps `pl::memory::hook`, records a handle; `hooks::remove(handle)`).
- `src/core/GameHooks.cpp` — canonical example of 9 signature hooks + 1 EGL
  hook and the event dispatch pattern.
- `include/bedrocktools/sdk/Memory.hpp` — `sdk::field<T>(obj, off)`,
  `sdk::virtualCall<Ret>(inst, slot, ...)`, `sdk::patchMemory(addr, data, size)`
  (mprotect RWX + memcpy + `__builtin___clear_cache` + restore RX).
- `include/bedrocktools/sdk/Functions.hpp` — `sdk::function<Fn>(SignatureId)`
  turns a resolved signature address into a typed function pointer.
- `src/core/memory/Signatures.cpp` — the signature dictionary
  (`SignatureId` + hex pattern for ~80 game functions).

## Workflow: from user feature to working hook

Follow this order strictly. It mirrors how BedrockTools is built: feature ->
game function -> location -> feasibility -> hook choice -> verify -> build.

### Step 1. Understand the feature and name the game function

Before touching the binary, answer: *which game function implements the
behavior the user wants to change?* BedrockTools maps features to functions:

| Desired feature (BedrockTools module) | Game function (SignatureId) | Hook strategy |
|---|---|---|
| Zoom / FOV change | `GetFov` | inline hook, modify return |
| Low-sensitivity while zoomed | `LocalPlayerApplyTurnDelta` | inline hook, scale input |
| Hide hand while zoomed | `BaseOptionRegistryGetHideItemInHand` | inline hook, force true |
| Fullbright / remove darkness | `Fullbright` | head-replace patch (12 bytes, return max light) |
| Time changer | `Time` / `SetTime` | inline hook, override return/arg |
| Attack cancel / hit detection | `GameModeAttack` / `SurvivalModeAttack` | inline hook + cancellable event |
| Local player tick logic | `NormalTick` | inline hook + event |
| Frame / render-loop logic | `eglSwapBuffers` (libEGL.so) | inline hook on exported symbol |
| Screen open/close detection | `ContainerScreenControllerOpen/Dtor`, `ChatScreenOpen/Dtor` | inline hook + event |
| Hit result / reach | `LevelGetHitResult`, `HitResultGetEntity` | resolve + call as function |
| Player name / skin | `ActorGetNameTag`, `ActorSetNameTag` + `offsets::Player::mName/mSkin` | inline hook / field access |
| Weather / biome | `WeatherTick`, `WeatherIsRaining`, `BiomeGetTemperature` | inline hook or field access via `offsets::Weather` |
| Packet send to server | `LoopbackPacketSenderSendToServer` | inline hook (inspect/modify before send) |

### Step 2. Locate the function

Preferred, no reverse-engineering required:

1. BedrockTools already ships a signature for it (`Signatures.cpp`) -> resolve
   it and you are done.
2. If not: search BedrockTools/community sources for the function name; use a
   disassembler only as a helper (optional, see `references/ida-workflow.md`).

### Step 3. Decompile / analyze (optional but recommended for new functions)

If the function is not already documented, use IDA / Ghidra (any install) to
decompile the function at the resolved address and confirm:

- What it returns and what it does (does it read/write fields? call vtable slots?).
- Whether the behavior is data-driven (patch a constant / field) or
  logic-driven (need a hook before/after).
- Its signature / calling convention (arm64: args in X0..., return in X0/W0/S0).

Decompilation is **optional**: the workflow must not depend on it.

### Step 4. Decide feasibility, then choose the hook technique

Decision table (strict BedrockTools usage):

| Situation | Technique | BedrockTools example |
|---|---|---|
| Need logic before/after the call, modify args or return | inline hook via `pl::memory::hook` / `hooks::install` | `versionDetour`, `tickDetour`, `gameModeAttackDetour` |
| Function should just return a constant / do nothing | head-replace patch (copy original first, then `RET`) | Fullbright 12-byte patch |
| Change a constant, a branch, or force a path | byte patch with original-byte verification | `MOV #0x40 -> #0x3E7`, NOP a branch, `MOV W0,#1; RET` |
| Override a virtual method | vtable slot via `sdk::virtualCall` / `resolveVtableFunction` | `offsets::VTable` constants |
| Hook a library import (e.g. EGL) | symbol + `hooks::install` on `dlsym` result | `hookEgl()` |
| Wait for the game library | `dlopen` hook | `dlopenDetour` in Runtime.cpp |

Safety rules (BedrockTools practice, non-negotiable):

1. Always `readBytes` the current bytes before patching.
2. If current == target patch -> already patched, skip.
3. If current == expected original -> apply patch.
4. Otherwise -> version mismatch: skip and log; never write garbage.

### Step 5. Verify signatures on the actual `.so`

```bash
python scripts/verify_signatures.py <libminecraftpe.so> \
    --sigs <BedrockTools .../src/core/memory/Signatures.cpp> \
    --json sig_report.json
```

Only `UNIQUE` matches are safe. `AMBIGUOUS` -> lengthen the pattern;
`MISSING` -> wrong version, re-extract with a disassembler.

### Step 6. Implement the mod (BedrockTools code patterns)

Templates in `references/hook-techniques.md` and the root `HOOK_TUTORIAL.md`. The two
canonical shapes:

Inline hook:

```cpp
static std::string (*versionOriginal)(void*) = nullptr;
static std::string versionDetour(void* self) {
    std::string v = versionOriginal ? versionOriginal(self) : std::string{};
    return v + " | MyMod v1.0";
}
uintptr_t addr = pl::memory::resolveSignature(pattern, "libminecraftpe.so");
pl::memory::hook((void*)addr, (void*)versionDetour, (void**)&versionOriginal);
```

Safe byte patch:

```cpp
std::array<uint8_t,4> expected{...}, replacement{...};
auto cur = pl::memory::readBytes(addr, 4);
if (cur == replacement) return;               // already patched
if (cur == expected) pl::memory::writeBytes(addr, replacement, "mypatch");
// else: version mismatch -> log and skip
```

### Step 7. Build the `.so` and deploy

See `references/build-deploy.md` for the generic NDK + CMake commands
(toolchain via `$ANDROID_NDK_HOME`, ABI `arm64-v8a`, `-DANDROID_PLATFORM=android-24`).
Output must export `PLGetModRegistration`; ship it next to a valid
`manifest.json` into the launcher's `mods/` directory.

## Events (BedrockTools' typed event system)

`src/core/GameHooks.cpp` converts game callbacks into typed events; modules
subscribe via `bus().subscribe<Event>(cb, priority)`. Third-party mods can
subscribe through the runtime ABI with `RuntimeListener<Event>`.

| Event | Source hook | Use case |
|---|---|---|
| `FrameEvent` | `eglSwapBuffers` | every frame |
| `LocalPlayerTickEvent` | `NormalTick` | player tick |
| `ClientInstanceUpdateEvent` | `ClientInstanceUpdate` | get ClientInstance |
| `AttackEvent` (cancellable) | `GameModeAttack`/`SurvivalModeAttack` | attack logic |
| `ScreenStateEvent` | screen Open/Dtor hooks | UI state |
| `MouseInputEvent` | `pl::input` | mouse |

## References (load on demand)

- `HOOK_TUTORIAL.md` (workspace root) — full user-facing tutorial: feature ->
  game function -> locate -> verify -> hook -> compile -> package -> deploy.
- `references/feature-workflow.md` — feature -> function -> feasibility ->
  hook-decision guide with worked examples.
- `references/hook-techniques.md` — full hook technique reference: inline
  hooks, chains, head replacement, NOP/branch patches, vtable, GOT/PLT,
  dlopen/EGL hooks, signature format rules.
- `references/so-analysis.md` — analyze the `.so` on disk without a device:
  ELF header, segments/sections, dynamic symbols, strings, signature
  verification, and the Python-only checklist.
- `references/build-deploy.md` — NDK+CMake build, xmake build, manifest,
  .levipack packaging, deployment, logcat verification.
- `references/ida-workflow.md` — optional IDA / IDA Pro MCP guidance
  (generic; any IDA version).
- `references/version-porting.md` — porting checklist for new game versions.

## Scripts

- `scripts/verify_signatures.py` — scan a `.so` for all signatures parsed
  from BedrockTools `Signatures.cpp`; report UNIQUE/AMBIGUOUS/MISSING.
- `scripts/ida_decompile_targets.py` — optional IDAPython batch script to
  decompile resolved addresses and write a JSON report.
- `scripts/aarch64_enc.py` — encode AArch64 patch instructions (MOV/NOP/RET/BR/FMOV).
- `scripts/ida_mcp_client.py` — JSON-RPC client for the IDA Pro MCP service.
- `scripts/package_levipack.py` — package a mod into `.levipack` (manifest +
  .so + resources) and verify the result.
- `scripts/elf_facts.py` — read-only ELF facts (header, segments, sections,
  dynamic symbols, version strings) for a fresh `.so`.

## Notes

- Hook/patch only what the user explicitly authorized; keep to learning and
  personal use and respect the game's terms of service.
- Always keep a backup of the original `.so`; fail safely on version mismatch.
