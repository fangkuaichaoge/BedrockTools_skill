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
  `PL_REGISTER_MOD(Type, instance)` macro (`pl/Mod.hpp`).
- **Timing**: call `GlossInit(true)` first in `load()`. The game library may
  not be loaded yet, so either hook `dlopen` (BedrockTools' approach in
  `src/core/Runtime.cpp`) or probe with
  `dlopen("libminecraftpe.so", RTLD_NOW | RTLD_NOLOAD)`.
- **Function location**: the game `.so` is stripped. BedrockTools stores
  **byte-signatures** (function-head machine code with `?` wildcards) and
  resolves them at runtime via `pl::memory::resolveSignatures`. When you need a
  function BedrockTools does not have, locate it **offline** (see "Tool
  selection" above) rather than by opening a disassembler.
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
- `references/signature-forensics.md` — finding a function and **proving it is
  usable**: what "usable" means (identity, uniqueness, ABI, lifetime, thread,
  re-entrancy, side effects), anchors for finding candidates, how to build a
  pattern that survives version bumps, the evidence ladder (shape -> structure ->
  decompiler -> runtime observation -> behavioural A/B), how to prove it is safe
  to call, version drift, and a ship checklist. Read it before hooking any
  function you have not personally observed at runtime.
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
- `references/ida-workflow.md` — optional IDA / IDA Pro MCP guidance
  (generic; any IDA version).
- `references/version-porting.md` — supporting several game builds from one
  `.so`: why `--tight` patterns stop matching, house-style portability, the two
  independent porting tasks (signatures vs object layout), the per-build hit
  matrix, and making the check unskippable.
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
- `references/lessons-learned.md` — distilled experience: why forcing
  upstream gates fails, fixing at the drop site, reversing working
  references, and debugging process. Read it whenever a GUI item icon is
  missing, stretched, submitted-but-invisible, updated after `setItem`, or
  affected by container open/close lifecycle.

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

## Notes

- Compatibility: this skill uses the shared `SKILL.md` frontmatter convention
  (`name` + `description`) and is usable by Codex, Claude Code, Cursor, and
  other agents that support agent skills. Repo-level entry guides:
  `AGENTS.md` (Codex convention) and `CLAUDE.md` (Claude Code convention).
- Hook/patch only what the user explicitly authorized; keep to learning and
  personal use and respect the game's terms of service.
- Always keep a backup of the original `.so`; fail safely on version mismatch.
