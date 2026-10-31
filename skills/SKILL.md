---
name: bedrock-pe-modding
description: "Develop native mods and hooks for Minecraft Bedrock (PE) Android arm64 by following the exact techniques used in the open-source BedrockTools project. Use when the user wants to: hook, patch, or mod libminecraftpe.so; write a LeviLauncher / preloader native mod (.so); turn a desired feature into concrete game-function hooks; decide between inline hooks, vtable hooks, and direct byte patches; extract or verify ARM64 byte signatures; build an arm64-v8a Android .so with NDK + CMake or xmake; package a mod (manifest.json / .levipack); port a mod to a new Bedrock version; or understand Bedrock PE internals such as tick, render, FOV, packet, attack, screen, weather, time, skin, and UI hooks. Not for Java/Spigot/Forge mods or for non-Bedrock ELF targets without adaptation."
---

# Bedrock PE Native Modding (BedrockTools patterns)

This skill distills the mod/hook/SDK/build techniques of the open-source
[BedrockTools](https://github.com/QYCottage/BedrockTools) project into a
reusable workflow. Target binary: Minecraft Bedrock Android **arm64-v8a**
(`libminecraftpe.so`). Runtime: **LeviLauncher + preloader**; the preloader
SDK is open source at
[LiteLDev/preloader-android](https://github.com/LiteLDev/preloader-android).

Everything below is deliberately **machine-agnostic**: replace
`<libminecraftpe.so>`, `<NDK>`, `<your-mod-dir>` with real paths on the
machine you are on. The code examples follow BedrockTools' code exactly; do
not invent new abstractions.

## Workspace layout — one task, one folder

Every mod build or reverse-engineering investigation gets its **own new
directory**. Create it before writing the first file, and put every produced
artifact there: sources, build outputs, analysis reports, screenshots and the
packaged `.levipack`. Do not scatter files into the workspace root, into an
unrelated mod's folder, or into the `skills/` tree. Reuse an existing folder
only when continuing that same task.

## Non-negotiable rules (learned the hard way)

These are not style preferences; each one comes from a build that crashed or a "fix" that
silently did nothing.

1. **A fix needs evidence.** Acceptable evidence: a UNIQUE signature verification, caller
   disassembly showing the argument setup, a decompiled body, a runtime log line that shows the
   first wrong value, or an A/B test where only the suspected code changed. If you have none of
   these, you have a hypothesis: label it, change exactly one variable, and test. Never stack two
   speculative changes into one build -- you lose the ability to bisect.
2. **Never guess an ABI.** Recover the signature from the callers: which register holds what,
   whether the return comes back through the dedicated return register (sret), how large the
   out-struct is (stack frames, table strides, pointer arithmetic in the caller). A composite
   engine function whose out-struct you have not mapped must not be called from a mod; replicate
   the chain from verified primitives or find another entry point.
3. **Assume the render path is hostile.** No exceptions (the mod is built with unwind tables
   disabled, so a throwing call terminates the process), no file I/O on the render thread, no
   speculative graphics-state changes. If an artefact appears, first find how the vanilla engine
   draws the same element and call that code path.
4. **Every pointer read needs a guard.** A process-map snapshot goes stale; refresh it. Pointers
   may carry a tag in their top bits: normalize before comparing addresses. Guard whole
   `memcpy`/string windows, not just their first address. An unguarded read during a scan is a
   crash waiting for the wrong mapping.
5. **Fail open.** Hooks call the original first and never drop a call. A feature that cannot be
   computed must render nothing, not fall back to garbage memory.
6. **Ship in one-change steps with backups.** Before a risky change, copy the mod folder to
   `<mod> - backup-<version>-<what>-<timestamp>`; for a release,
   `<mod> - release-<version>-<timestamp>` with sources, analysis, dist and docs. Bump the
   manifest version every build and keep the version in the debug log banner.
7. **Record the failures.** For every version, write down what was tried and *why it was wrong*
   (see `references/lessons-learned.md`). The failure notes are what save the next session.
8. **A crash outranks a missing feature, always.** Nothing unverified may reach a hook or an
   engine call: prove a hook target is *code inside the game image* (module check plus a byte
   check), identify objects by a fixed vtable address point before using their slots or methods,
   and guard every dereference with a readable-map check whose snapshot can refresh. When in
   doubt, draw nothing. One change per build. Full checklist:
   `references/coexistence-and-crash-safety.md`.
9. **Work out *which layer* a conflict lives on before touching code.** Another mod can collide at
   the hook layer (chain manager - usually fine), the signature layer (its inline hook rewrites the
   prologue, so your patterns stop matching and your detour is silently never installed), the
   RTTI/vtable layer (an SDK helper returning 0 on a different launcher build), or the render
   queue. Decide the layer from a device log. Read `references/coexistence-and-crash-safety.md`
   before "fixing" a coexistence bug.

## Tool selection — read this before analyzing anything

A Bedrock `.so` is hundreds of megabytes. **Do not start a disassembler's
auto-analysis on it.** That is the single most common way this work goes wrong:
the analysis does not finish in a reasonable time, it produces a multi-gigabyte
database, and the answer you actually needed was two call-graph edges away.

**Answer every structural question offline first.** The helper does it in
seconds:

```bash
python scripts/a64.py func    <libminecraftpe.so> --addr 0x...     # which function is this in
python scripts/a64.py callers <libminecraftpe.so> --addrs 0x...    # who calls it
python scripts/a64.py cluster <libminecraftpe.so> --addrs 0x,...   # which drivers call this family
python scripts/a64.py guards  <libminecraftpe.so> --addrs 0x...    # what mask gates those calls
python scripts/a64.py xref    <libminecraftpe.so> --addrs 0x...    # who references this data
python scripts/a64.py disas   <libminecraftpe.so> --addr 0x...     # linear disassembly
```

| Question | Tool | Never |
|---|---|---|
| Which function contains this address? | `a64.py func` | disassembler |
| Who calls this? How many callers? | `a64.py callers` | disassembler |
| Which few drivers call this whole family? | `a64.py cluster` | disassembler |
| What bit-mask gates these calls? | `a64.py guards` | disassembler |
| Who references this string/vtable? | `a64.py xref` | disassembler |
| What are these instructions? | `a64.py disas` | disassembler |
| What does this ONE function do, in C? | a disassembler MCP, **by address** | full auto-analysis |
| Verify a signature is unique | `scripts/verify_signatures.py`, `scripts/sigscan.py` | disassembler |
| Which struct field moved between two builds? | `scripts/getter_census.py`, `scripts/ptr_vec_scan.py`, `scripts/disp_census.py` | eyeballing a header |
| Which vtable slot moved, or was a virtual inserted? | `scripts/vtable_slot_map.py` on `vtable_walk.py` dumps | guessing an index |

Rules that make this stick:

1. **Locate first, decompile second.** Never open a disassembler until you can
   name the exact address you want decompiled. Finding that address is offline
   work.
2. **Never ask a disassembler for an overview** of a game library, and never
   let it "analyze the binary". Ask it about one function by address.
3. **If a heavy tool is still running after a minute or two, stop waiting** and
   answer the question offline. Come back to the heavy tool only for the
   pseudocode of one function that still puzzles you.
4. **A disassembler is optional; the workflow must not depend on it.** If none
   is installed, offline analysis is still sufficient to locate, hook and
   verify.

Full method, including the two facts that make offline analysis cheap and the
pitfalls that waste an afternoon: `references/targeted-static-analysis.md`.
Using a disassembler MCP provider without fighting it:
`references/rea-and-disassembler-providers.md`.

## Architecture (understand before coding)

- **Load chain**: LeviLauncher (Java) -> `libpreloader.so` (C++ loader:
  scans `mods/`, validates `manifest.json`, `dlopen`s your `.so`) ->
  your mod -> `libminecraftpe.so` (loaded later by the game).
- **Lifecycle**: preloader looks up the exported symbol `PLGetModRegistration()`
  and calls `load/enable/disable/unload`. Register with the
  `PL_REGISTER_MOD(Type, instance)` macro (`pl/Mod.hpp`); `load()` is required and
  the rest are optional. The full host-side contract (manifest fields, config,
  Mod Menu, input, packaging) is summarised in
  `references/levilauncher-native-mod-guide.md`.
- **Timing**: the game library may not be loaded when `enable()` runs, so either
  probe with `dlopen("libminecraftpe.so", RTLD_NOW | RTLD_NOLOAD)` (do **not**
  hook `dlopen` in a Java host process) or install from a watcher that polls then
  lets the image settle — see `references/mod-lifecycle-and-crash-safety.md`.
  `GlossInit(true)` is **not** the mod's job any more: `pl::memory::hook()` calls it
  internally. Call it yourself only if you drive the raw `GlossHook` API directly.
- **Function location**: the game `.so` is stripped. BedrockTools stores
  **byte-signatures** (function-head machine code with `?` wildcards) and
  resolves them at runtime via `pl::memory::resolveSignatures`. When you need a
  function BedrockTools does not have, locate it **offline** (see "Tool
  selection" above) rather than by opening a disassembler.
- **Version sensitivity**: signatures, member offsets, and vtable slots
  change per game version. Porting = re-extract/re-verify (see
  `references/version-porting.md` for the mechanics and
  `references/porting-a-live-mod.md` for the end-to-end playbook: one `.so` for
  several builds, a disjoint signature as the build-profile gate, runtime offset
  selection, moving capture points, the diagnostic loop, and multi-build release
  discipline).

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
| Visual effect on existing geometry (outline / tint / glow / depth tricks) | none — the graphics-API boundary itself | shader-source injection + two-pass draw, no signatures (see `references/render-pipeline-hooks.md`) |
| Screen open/close detection | `ContainerScreenControllerOpen/Dtor`, `ChatScreenOpen/Dtor` | inline hook + event |
| Hit result / reach | `LevelGetHitResult`, `HitResultGetEntity` | resolve + call as function |
| Player name / skin | `ActorGetNameTag`, `ActorSetNameTag` + `offsets::Player::mName/mSkin` | inline hook / field access |
| Weather / biome | `WeatherTick`, `WeatherIsRaining`, `BiomeGetTemperature` | inline hook or field access via `offsets::Weather` |
| Packet send to server | `LoopbackPacketSenderSendToServer` | inline hook (inspect/modify before send) |
| Bone / model pose (position, rotation, scale) | bone lookup + its transform record | inline hook on the lookup, edit the returned record (see `references/signature-forensics.md` §9) |
| Replace a block's **model** (custom mesh for a block category) | `BlockTessellator::_tessellateFace{Up,Down,North,South,East,West}` + `Tessellator::vertexUV` | inline hooks: suppress the original's vertices, emit your own through the real vertex emitter (see `references/block-model-replacement.md`) |

**Before installing a single hook**, read
`references/mod-lifecycle-and-crash-safety.md`. The preloader enables mods
*before* the game library is mapped, so `enable()` must poll for the library and
then let it settle. Getting this wrong crashes the launcher, which then asks the
user to re-import the mod — with no usable stack trace.

### Step 2. Locate the function

Do this **offline** (see "Tool selection" above). In order of preference:

1. BedrockTools already ships a signature for it (`Signatures.cpp`) -> resolve
   it and you are done.
2. Search BedrockTools/community sources for the function name.
3. A string the function uses -> `a64.py xref` on the string's address (search
   for the **full NUL-terminated literal**, including any namespace prefix).
4. A sibling function you already know -> `a64.py cluster` to find the driver
   that calls the whole family, then `a64.py disas` around the calls.
5. `a64.py func` to label any address with its enclosing function.

### Step 3. Understand the function (decompile is the LAST resort)

Answer as much as possible offline first — the guard pattern and the argument
setup usually give away the mechanism without any pseudocode:

- **What decides this call?** `a64.py guards` reads the `tbz`/`tbnz` bit tests
  around a call site, which is how you recover a decision mask.
- **Where do the arguments come from?** `a64.py disas` backwards from the call:
  register moves, stack temporaries, and virtual-call results are all visible.
- **Which function is this inside?** `a64.py func`.

Only then, if the control flow is still unclear, open a disassembler **on that
one address** (`rea function <so> <addr>`, or IDA/Ghidra by hand) to read the
pseudocode. Confirm:

- what it returns and what it does (fields read/written, vtable slots called);
- whether the behavior is data-driven (patch a constant/field) or logic-driven
  (hook before/after);
- its calling convention (arm64: args in X0..., return in X0/W0/S0).

Decompilation is **optional**: the workflow must not depend on it, and it must
never be the first step.

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
  dlopen/EGL hooks, signature format rules, library-boundary hooks.
- `references/hook-engineering.md` — hook **judgement and safety**: picking the
  mechanism (call vs hook vs patch vs vtable), choosing the narrowest site, hook
  lifecycle and idempotence, fail-open rules (never drop a call, never let an
  exception escape, never recurse through your own detour), proving a hook is
  live, priorities/coexistence, real failure modes, observe-before-mutate
  rollout. Read it before designing any interception, and whenever a hook
  "works but does nothing" or crashes randomly.
- `references/mod-lifecycle-and-crash-safety.md` — **startup timing and crash
  safety**: why the preloader enables mods before the game library is mapped,
  why hooking `dlopen` to wait for it crashes the launcher, the poll-then-settle
  attach sequence that works, explicit hook-engine init, how to diagnose "the
  launcher dies and asks me to re-import", stale-config repair, and the rule
  that a preset must not pin a player-facing toggle. Read it before writing
  `load()`/`enable()`, and whenever the host process dies at startup.
- `references/mod-menu-registration.md` — the **Mod Menu registration contract** read out
  of the preloader source: `mod_id` must equal the mod folder name or be empty, the owner id
  only exists while the preloader is calling `load()`/`enable()` synchronously, duplicate ids
  are rejected, and every failure is silent. Includes the recipe that works (register from
  `load()`, declare the id explicitly only when registering before the lifecycle) and the
  checklist for "the module does not appear".
- `references/container-and-slot-reversing.md` — how to enumerate **container contents**:
  recovering a block actor's vtables, finding `getContainerSize` by scanning for a constant
  return, finding `getItem` by its array arithmetic, why the direct accessor must be used
  instead of the forwarding stub, and which numbers survive a game update.
- `references/item-names-and-language.md` — **localized item names** through
  `ItemStackBase::getName()` (non-virtual, `std::string` by value, sret in `x8`) and the
  player's language through the engine's localization object, including the CJK cross-check
  that turns a guessed offset into a verified one.
- `references/preview-panels-and-container-ui.md` — building a **custom HUD panel** that
  shows real container contents: capturing the UI render context, drawing item icons through
  the engine's item renderer, measuring text before laying it out, paginating a 54-slot
  container into columns, and the lifetime rules that stop such a mod from crashing when the
  player leaves a save.
- `references/container-preview-live-contents-and-icons.md` — an overlay that shows
  **live container contents with correct icons**: the two-instance problem (which actor holds
  fresh data and which one is drawable), record rules that keep the two apart, learning client
  item identity from the engine's own item renderer, the `ItemStack` layout (152 bytes, shared
  reference block at `+0x8`, description-id virtual at `0x2f0`), the private-reference-block
  recipe for drawing items the drawable instance never held, the texture-atlas dead end, and
  container screen open/close detection including the cross-container destructor crash.
- `references/version-checkpoints-and-change-notes.md` — **checkpoint discipline**: when to
  ask the user for a backup (a fix confirmed, a feature accepted, before a risky rework), the
  immutable backup layout, the change-note template that records what/why/evidence/known
  issues/rollback, and how to prove a rollback by hash.
- `references/signature-forensics.md` — finding a function and **proving it is
  usable**: what "usable" means (identity, uniqueness, ABI, lifetime, thread,
  re-entrancy, side effects), anchors for finding candidates, how to build a
  pattern that survives version bumps, the evidence ladder (shape -> structure ->
  decompiler -> runtime observation -> behavioural A/B), how to prove it is safe
  to call, version drift, and a ship checklist. **§10 covers the case where the
  binary names nothing**: the anchor ladder for unnamed functions (prototype
  strings used by asserts -> RTTI/vtable -> source-path strings -> distinctive
  literals -> call-graph position -> pseudocode), why eliminating a candidate is
  part of the job, the three ways a "function" can have no signature at all
  (inlined getter, non-virtual member, stale dictionary entry), and what to ask
  for when the answer really is "not derivable from this binary". Read it before
  hooking any function you have not personally observed at runtime.
- `references/so-analysis.md` — analyze the `.so` on disk without a device:
  ELF header, segments/sections, dynamic symbols, strings, signature
  verification, and the Python-only checklist.
- `references/targeted-static-analysis.md` — **answer precise questions about a
  multi-hundred-megabyte stripped binary in seconds**: why a full auto-analysis
  is the wrong tool, the two facts that make it cheap (file offset == vaddr, and
  bulk `BL` decoding with integer arithmetic), the anchor → family → callers →
  cluster → guards → argument-setup workflow, recovering function boundaries
  from `.eh_frame_hdr`, and the pitfalls (a call being unconditional does not
  mean the callee is unconditionally visible; re-derive conclusions per build).
  Read it before any "how does the engine do X" question on a big target.
- `references/rea-and-disassembler-providers.md` — using an MCP disassembler
  provider (REA) **precisely**: when it helps (pseudocode of one located
  function) and when offline analysis is faster, the setup traps (provider
  version too old for the supervisor API, MCP Python SDK 2.x renaming
  `fastmcp`, missing `idalib` binding, hosts-blocked downloads), registration
  shapes (stdio vs a long-lived supervisor URL), querying by address with a
  warm database, and treating the output's `limitations` as unknown-not-absent.
  Read it when a disassembler is available and you need pseudocode.
- `references/build-deploy.md` — NDK+CMake build, xmake build, manifest,
  .levipack packaging, deployment, logcat verification.
- `references/preloader-mod-implementation-notes.md` — field notes from a real
  Android port, limited to traps the general references do not cover: runtime
  image base for build-specific fallbacks (and the missing-FATAL symptom when it
  is skipped), vtable-slot prototype verification, nearby-actor enumeration ABI,
  overlay primitive/anchor specifics, locating the per-frame world renderer,
  optional engine hooks and stale-cache cleanup, client-side scan budgeting, and
  the crash-log nuance.
- `references/levilauncher-native-mod-guide.md` — the **host side** distilled from
  the official LeviLaunchroid docs (`docs.levimc.org/LeviLaunchroid`): mod
  directory layout and the manifest fields that silently break a mod (`entry` must
  equal the built `.so`, `overwrite_files`/`overwrite_folders` default to *not*
  replacing, `minecraft_versions` gating), the lifecycle contract (`load()`
  required, both `load()` and `load(ModContext&)` accepted), the `NativeMod`
  metadata/path/logger handle, SDK linking via a **pinned** FetchContent tag, typed
  config merge semantics, Mod Menu builders and draw commands, the Input API's
  *missing unregister*, memory API surface and wildcard syntax, packaging, and a
  ship checklist. §11 records docs-vs-headers drift and says which one to trust.
  Read it before starting any new preloader mod, so the plumbing does not get
  re-derived from scratch.
- `references/ida-workflow.md` — optional IDA / IDA Pro MCP guidance
  (generic; any IDA version). §8 is the productive mode: **drive IDA by
  address**, never let it auto-analyse a 300 MB+ library. Confirm which build
  the database holds before trusting an address, decompile only what the offline
  tools could not decide, pass tool params through a file (shells mangle inline
  JSON), know which question each call answers, and remember the decompiler
  yields behaviour — identity still comes from the anchors in
  `signature-forensics.md` §10.
- `references/version-porting.md` — supporting several game builds from one
  `.so`: why `--tight` patterns stop matching, house-style portability, the two
  independent porting tasks (signatures vs object layout), the per-build hit
  matrix, and making the check unskippable.
- `references/porting-a-live-mod.md` — the **end-to-end playbook for adding a new
  build to a mod that already ships**: the compatibility contract (one `.so`,
  legacy branch untouched), using the upstream release that declares its target
  build, fallbacks plus a disjoint signature as the build-profile gate, moving
  struct offsets/vtable slots with their evidence counts, capture points that move
  even when the old signature still resolves (new text virtual, object discovered
  from the engine's own queue), the pinned-`adrp` signature trap, differential
  old-build testing, the diagnostic-log decision tree, the release checklist and
  a pitfalls list. Read it before any "port this mod to version X" task.
- `references/hud-buttons-and-icon-art.md` — launcher HUD buttons from a native
  mod: what the bridge really validates, the missing active-PNG setter and the
  `ButtonInfo` workaround, toggle event semantics, embedding art as generated
  byte arrays, and drawing pixel-art icons with Pillow (sampled ink colours,
  alpha-mask recolouring, keycap compositing) with offline verification.
  The launcher's stock button art (keycap bases + glyph pairs, 512x512 PNG)
  ships in `assets/hud-button-templates/` as the style reference and
  compositing base.
- `references/render-pipeline-hooks.md` — distilled experience for **visual
  effects on a closed-source renderer**: choosing the hook layer, wrapping
  every API resolution path, defensive shader-source injection, self-reporting
  heuristics, GL state hygiene, the two-pass shell and the geometry rules that
  decide whether it looks right, capping additive brightness, rollback
  discipline and artifact verification. Read it before starting any
  outline / tint / glow / material-effect mod, and whenever a rendering mod
  "does nothing" or looks wrong.
- `references/block-model-replacement.md` — replacing a **block's model** with a
  custom mesh: why the per-face geometry emitters are the right hook layer, why
  the original must always run with only its vertices suppressed, why the
  engine's own culling must not be repeated, how diagonal quads vanish unless
  they are reachable from both of their axes (and the dedup id that stops the
  fix double-drawing), per-face base colour vs per-vertex ambient occlusion,
  atlas-tile UV clamping, batch dedup and the GUI/item icon it breaks, safe
  block-name lookup, the overlap-culling trap (a protrusion is not covered by a
  sparse shell), bake-verbatim discipline with area/UV assertions, and the
  diagnostic rule that a filter which does not filter destroys the evidence.
  Read it before any "change how block X looks" task.
- `references/verifying-unrun-cpp.md` — delivering code you **cannot execute** (no
  host toolchain, no device): classifying risk, byte-pattern uniqueness, a second
  implementation in a runnable language plus source-level assertions that the C++
  is the same algorithm, cross-compiler type checking, using disassembly to settle
  ABI claims, self-reporting builds, the techniques that only *look* like
  verification, recorded dead ends (retargeting NDK libc++ to WebAssembly), and how
  to report the remaining gap so the user knows what still needs their hands.
- `references/shader-source-injection.md` — the mechanics of patching GLSL as
  text: insertion anatomy, comment-stripped offset mapping, identifier
  discovery by normalised family, component-count correctness, interface/link
  safety (varyings vs uniforms), which stages cannot be patched, and how to
  host-test the transform. Read it before injecting anything into shaders.
- `references/instrumentation-and-diagnostics.md` — how to build a mod that
  explains its own failures: log design rules, canaries, instrumenting the
  API's silent errors, the observe-only instrumentation build, hot-path cost
  discipline, log delivery (platform log vs file), and what to ask the user
  for. Read it whenever a mod "does nothing" or a device log is needed.
- `references/feature-rollback.md` — rollback, removal and rename discipline:
  keeping a known-good state, delete-code-not-flags, proving a removal (source
  grep + shipped-binary strings + size), preserving the surviving path,
  renaming a shipped deliverable at every layer, and recording why a feature
  was removed.
- `references/coexistence-and-crash-safety.md` — **coexistence with another hooking mod and crash-safety invariants**: the three layers a conflict can live on, the decision tree for "my feature disappeared", the unverified-hook-target crash, the invariant checklist, deep-anchored signatures, ELF vaddr tables when the SDK RTTI resolver returns 0, and post-mortem discipline.
- `references/lessons-learned.md` — distilled experience: why forcing
  upstream gates fails, fixing at the drop site, reversing working
  references, and debugging process. Read it whenever a GUI item icon is
  missing, stretched, submitted-but-invisible, updated after `setItem`, or
  affected by container open/close lifecycle.

## Scripts

- `scripts/check_mod_signatures.py` — read the signatures out of a **mod source file** and
  verify each one resolves exactly once in **every** build you ship, reporting MISSING and
  AMBIGUOUS. Exit code gates a release.
- `scripts/verify_signatures.py` — scan a `.so` for all signatures parsed
  from BedrockTools `Signatures.cpp`; report UNIQUE/AMBIGUOUS/MISSING.
- `scripts/ida_decompile_targets.py` — optional IDAPython batch script to
  decompile resolved addresses and write a JSON report.
- `scripts/aarch64_enc.py` — encode AArch64 patch instructions (MOV/NOP/RET/BR/FMOV).
- `scripts/ida_mcp_client.py` — JSON-RPC client for the IDA Pro MCP service.
  Unwraps the server's JSON envelope (pseudocode / instructions / stdout) so a
  decompile prints code instead of escaped JSON. Pass params as `@file.json`:
  a shell rewrites quotes and braces on the way to a native program, so inline
  JSON arrives malformed. See `references/ida-workflow.md` §8.
- `scripts/package_levipack.py` — package a mod into `.levipack` (manifest +
  .so + resources) and verify the result.
- `scripts/elf_facts.py` — read-only ELF facts (header, segments, sections,
  dynamic symbols, version strings) for a fresh `.so`.
- `scripts/a64.py` — offline AArch64/ELF helper: `xref` (ADRP+ADD/LDR data
  references), `disas` (linear disassembly), `func` (enclosing function from
  `.eh_frame_hdr`), `callers` (direct BL callers), `cluster` (group a family's
  call sites into their driver functions), `guards` (the bit tests that follow
  each call, revealing a per-call decision mask), `ptr` (data pointers equal to
  an address). Answers "which function is this inside", "who calls it", "which
  few drivers call this whole family", and "what mask gates those calls" without
  a disassembler GUI. See `references/targeted-static-analysis.md`.
- `scripts/mksig.py` — generate a wildcard signature from a function head
  (house-style or `--tight`), print its match count, and emit a ready-to-paste
  C++ literal block with the separator placed correctly. See
  `references/signature-forensics.md` §9 for the traps it avoids.
- `scripts/sigscan.py` — scan a `.so` for named wildcard patterns
  (`UNIQUE`/`AMBIGUOUS`/`MISSING`), or search its strings with `--strings`.
  Use it to verify that a generated pattern is unique before you hook it.
- `scripts/multi_verify.py` — verify a whole signature set against **several
  builds at once** and print a hit-count matrix. The only acceptable result is
  1 in every column. This is the tool that answers "is my mod compatible with
  these versions?" — see `references/version-porting.md`.
- `scripts/offset_check.py` — read the struct displacements a function actually
  uses back out of the game's disassembly, per build. Signatures and object
  layout are independent porting tasks; this checks the second one, where a
  mismatch is silent rather than an error.
- `scripts/vtable_walk.py` — recover a class's vtables offline from RTTI:
  `RTTI name -> typeinfo -> vtable -> virtual function addresses`. Reads
  `.rela.dyn`, because on a PIE image the pointer slots are **zero in the file**
  and the value lives in the relocation addend — scanning raw qwords finds
  nothing and looks like "no RTTI". The slot count is itself a finding: a 2-slot
  vtable is a destructor pair, so every other member of that class is
  non-virtual and this tool cannot find it. See
  `references/signature-forensics.md` §10.
- `scripts/vtable_slot_map.py` — map one RTTI class's vtable slots between two
  builds by masking PC-relative immediates in each slot function's prologue and
  matching what remains. Shows unchanged slots, a uniform shift from an inserted
  virtual, and rewritten slots. Feed it dumps produced by `vtable_walk.py`.
- `scripts/getter_census.py` — find trivial field getters (`ldr x0,[x0,#imm]; ret`)
  and count their direct callers. The same accessor in two builds has a nearly
  identical caller count; the displacement it returns is where the field is, which
  proves moves like a base-class span growing by one alignment unit.
- `scripts/ptr_vec_scan.py` — count "load pointer field at candidate displacement,
  then load a Vec3/Vec2 from that pointer". Component pointers (state vector,
  rotation component) show their displacement moving by the count migrating.
- `scripts/disp_census.py` — encoded-displacement census over the executable
  segments, optionally comparing two builds side by side. Cheap way to form
  hypotheses about which displacement moved; prove with the tools above.

## Notes

- Compatibility: this skill uses the shared `SKILL.md` frontmatter convention
  (`name` + `description`) and is usable by Codex, Claude Code, Cursor, and
  other agents that support agent skills. Repo-level entry guides:
  `AGENTS.md` (Codex convention) and `CLAUDE.md` (Claude Code convention).
- Hook/patch only what the user explicitly authorized; keep to learning and
  personal use and respect the game's terms of service.
- Always keep a backup of the original `.so`; fail safely on version mismatch.
