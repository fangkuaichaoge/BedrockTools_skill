---
name: bedrock-pe-modding
description: "Develop native mods and hooks for Minecraft Bedrock (PE) Android arm64 by following the exact techniques used in the open-source BedrockTools project. Use when the user wants to: hook, patch, or mod libminecraftpe.so; write a LeviLauncher / preloader native mod (.so); turn a desired feature into concrete game-function hooks; decide between inline hooks, vtable hooks, and direct byte patches; extract or verify ARM64 byte signatures; build an arm64-v8a Android .so with NDK + CMake or xmake; package a mod (manifest.json / .levipack); port a mod to a new Bedrock version or make it run on unlisted builds (runtime address resolution); follow the end-to-end mod workflow (feature, bug, crash, version, release, hand-over to the user for testing); decide what to build and which route to take; draw your own 3D geometry in the world (panels, labels, icons); embed a class-shape dex plus a WebView page UI; pin one game build with a build-id + RVA signature profile; rewrite geometry at a vanilla draw callsite; drive vanilla container screens or a server-side open session; build a launcher-HUD settings UI; capture a session timeline or author cinematic camera paths; embed an ImGui editor; or understand Bedrock PE internals such as tick, render, FOV, packet, attack, screen, weather, time, skin, and UI hooks. Not for Java/Spigot/Forge mods or for non-Bedrock ELF targets without adaptation."
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

## Start here — the workflow

Read `references/workflow-playbook.md` at the start of every mod session. It classifies the
request (feature, bug, crash, version, coexistence, rollback, checkpoint), gives the track for
each, says when to stop verifying and hand the user a package, how to read the user's reactions,
and the report format. The rest of this skill is the detail behind those tracks.

Before designing the feature itself, read `references/mod-creative-thinking.md`: the ten-second
demo test, the route ladder (settings → observe → state → reuse a vanilla screen → custom UI →
world geometry → own renderer), reuse-before-build, coexistence, scope budgets, and the one-page
design to fill in before writing code.

For "the mod does not work on game version N": check the build gate first, then
`references/runtime-build-resolution.md` (supporting builds you do not have) or
`references/porting-a-live-mod.md` (adding a build you do have).

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
   engine call: every hook target must be proven to be *code inside the game image* (module check
   plus byte check; for a one-image mod, an exact build-id match of the mapped image is an
   acceptable substitute — see `references/runtime-build-resolution.md` §0b), every object must be
   identified by a fixed vtable address point before its slots or methods are used, and every
   dereference must follow a readable-map check whose snapshot can refresh. When in doubt, draw
   nothing. A "fix" that changes two things at once is not a fix —
   it costs the ability to attribute the result. Full checklist:
   `references/coexistence-and-crash-safety.md`.
9. **Work out *which layer* a conflict lives on before touching code.** Another mod can collide at
   the hook layer (chain manager — usually fine), the signature layer (its inline hook rewrites the
   prologue → your patterns stop matching → your detour is silently never installed), the
   RTTI/vtable layer (an SDK helper that returns 0 on a different launcher build), or the render
   queue. Decide the layer from a device log, then fix that layer. Read
   `references/coexistence-and-crash-safety.md` before "fixing" a coexistence bug.
10. **Count before you change.** When static reasoning has produced two "plausible fixes" that did
    not help, stop guessing. Ship one observe-only build that writes a small status file
    (detour call counters, a counter per early-return gate, each hook's install result plus the
    target's *live* head bytes, the loaded-library list) and have the user run it with and
    without the other mod. The diff of the two files names the broken layer directly.
11. **A fallback that has never run is broken until proven otherwise.** Exercise every fallback by
    simulating the failure it exists for (overwrite 4/16/20/24/64 head bytes, then re-run the
    exact check), and verify offline with a *replica of the shipped parser/matcher*, not a
    lookalike. Multi-line byte-pattern literals need an explicit separator at every join, or the
    compiler glues two tokens into one malformed token and the whole pattern fails to parse.
12. **A key scheme that works for some keys and not others is the wrong key scheme.** Registries
    are often keyed by a registration id, not a name hash. Read the static initializer, build
    any runtime lookup table from the engine's own registry, and trust the table only when it
    reproduces one value known independently. Check every name you will look up against the
    binary's string table before the first build.
13. **Know which copy and which side you are reading.** In a local world the client and the
    integrated server each own an object copy. Before writing UI, read what the class sends to
    the client (its update-packet method). Show only data that is really there and hide the line
    otherwise; never show a default as if it were real.
14. **"Doesn't work on version N" is a gate question before it is a code question.** If the mod
    whitelists build-ids, an unknown build installs nothing on purpose. Check that first. To
    support builds you do not have, resolve addresses at start (RTTI vtables, decoded reference
    sites, signatures) and take the layout from the one known build whose code matches strictly.
    If zero or two profiles match, install nothing. Prove it on a holdout build that is not in the
    table. See `references/runtime-build-resolution.md`. A build-id gate must read the linker note
    from the **mapped** image and compare the whole descriptor (§0b); a gate that never passes
    looks exactly like a broken mod, so log the rejection with the expected id.
15. **Offer the package when the remaining gap is device-only.** Once the build compiles, the
    package hash matches, and every new address has evidence, ask whether the user wants to test
    it now instead of starting a long verification detour. If the user asks "what are you doing?",
    drop the tangent. If they decline to send an artifact, do not ask again until a test fails.
    See `references/workflow-playbook.md` §9.

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
`references/ida-workflow.md` §9.

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
  hook the loader in a Java host process) or install from a watcher that polls
  then lets the image settle. Inside the game process a guarded, post-return
  loader gate is a valid **fallback** when the image is genuinely not mapped yet;
  its conditions are in `references/mod-lifecycle-and-crash-safety.md` §2b.
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
| Biome-driven environment overhaul (fog, sky, precipitation, ambient particles, wind audio) | the frame's fog-setup and sky producers, the weather emitter + particle-spawn entry, the world render pass; audio is **not** an engine hook | compose the engine's values, draw your own geometry through its tessellator, suppress vanilla with a scoped marker, synthesise audio (see `references/weather-and-ambience-effects.md`) |
| Screen open/close detection | `ContainerScreenControllerOpen/Dtor`, `ChatScreenOpen/Dtor` | inline hook + event |
| Custom on-screen button / panel gated by game state | `MinecraftUIRenderContext` finish (slot 31) + container screen open/dtor | hook finish, draw with the context's own primitives (see `references/engine-ui-buttons-and-overlays.md`) |
| Real Android widgets (text input, browser, a Java SDK, `Canvas` drawing) | **none — no engine address is involved** | embed a dex in the `.so`, load it with `InMemoryDexClassLoader`, attach `View`s to the Activity decor (see `references/embedding-dex-and-java-ui.md`) |
| Your own **3D geometry in the world** (panels, labels, icons, quad-based models) | none — hook the frame's world-render entry and borrow its state | capture the context, borrow engine materials by name, drive the tessellator behind RAII guards, rasterize panels, engine-font text, cell-probe occlusion (see `references/in-world-3d-rendering.md`) |
| A **Java object / JS bridge / real HTML UI** built without Java logic | none — no engine address is involved | class-shape dex + `RegisterNatives` + `InMemoryDexClassLoader`, embedded page over a `WebView`, page-initiated pull protocol (see `references/embedding-dex-and-java-ui.md` §11) |
| Hit result / reach | `LevelGetHitResult`, `HitResultGetEntity` | resolve + call as function |
| Player name / skin | `ActorGetNameTag`, `ActorSetNameTag` + `offsets::Player::mName/mSkin` | inline hook / field access |
| Weather / biome | `WeatherTick`, `WeatherIsRaining`, `BiomeGetTemperature` | inline hook or field access via `offsets::Weather` |
| Packet send to server | `LoopbackPacketSenderSendToServer` | inline hook (inspect/modify before send) |
| Bone / model pose (position, rotation, scale) | bone lookup + its transform record | inline hook on the lookup, edit the returned record (see `references/signature-forensics.md` §9) |
| Replace a block's **model** (custom mesh for a block category) | `BlockTessellator::_tessellateFace{Up,Down,North,South,East,West}` + `Tessellator::vertexUV` | inline hooks: suppress the original's vertices, emit your own through the real vertex emitter (see `references/block-model-replacement.md`) |
| Change geometry the engine **already draws** (a map, sign, frame face, item) | none new — intercept the vanilla call site | return-address callsite key, emit/swallow vertices, swap one argument object, sret shim for large returns (see `references/in-place-render-interception.md`) |
| Reuse a **vanilla screen** through the engine's own open path | the engine's own use/interact verbs and its open dispatch | nearby-block path, session-scoped validity handling, detached-actor identity substitution, host-tick session liveness (see `references/vanilla-screens-and-server-sessions.md`) |
| HUD labels / settings UI with **no engine UI hook** | none — the host's draw-command list | one settings table driving defaults/schema/clamp, worker thread for IO, draw commands as data (see `references/launcher-hud-data-driven-ui.md`) |
| Supporting **one exact game build** for engine internals | the image's build id + RVA + pattern table | generate/verify the profile, all-or-nothing gate, vtable identity checks, fail open (see `references/build-locked-abi-profiles.md`) |
| One mod with **many small features** (independent toggles, shared HUD service) | the UI context's finish/text slots as one shared service | module base class + registry, derived enable state, one UI render-pass subscription, identity-bound delayed actions, named slot/offset headers (see `references/module-framework-and-shared-services.md`) |
| Integrate with the **host's overlay/menu surface** | the host's own Java classes via JNI | cached host API handles, checked JNI + environment RAII, embedded Runnable task dex posted to the UI thread, atomic task queue, host UI-mode checks (see `references/launcher-java-overlay-bridge.md`) |
| **Capture a session timeline** and reconstruct it deterministically (seek, frame export) | the packet path, chunk delivery and the tick | ordered action stream + context snapshot, reserved synthetic identity, per-kind field filtering, private reconstruction world, phase-machine replay, deterministic frame clock (see `references/session-timeline-capture.md`) |
| **Cinematic camera paths** / a camera editor | the frame's camera objects (renderer / component / level) | keyframes with per-segment interpolation, shortest-arc sampling, narrowest application point, FOV observe/restore, pure-tick sampling for export (see `references/cinematic-camera-paths.md`) |
| A **complex in-game editor** (timeline, panels, tables) | none — your own GL pass over the composed frame | vendored ImGui + one GL backend, embedded font/assets, explicit input capture vs pass-through, throttled frame loop (see `references/imgui-native-overlay.md`) |

**Before touching engine internals, pin the build.** Generate a profile: the image's build id plus
one `{name, address, byte pattern}` entry per engine function or slot, verified by an independent
checker. A mod that guesses addresses between builds is the most common way an in-world feature
silently stops working; see `references/build-locked-abi-profiles.md`.

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
| Wait for the game library | poll with `RTLD_NOLOAD` (default); a guarded post-return loader gate only inside the game process | `dlopenDetour` fallback, conditions in `references/mod-lifecycle-and-crash-safety.md` §2b |

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

- `references/workflow-playbook.md` — **read first**: the session loop (classify → evidence →
  one change → build → package → hand over → checkpoint), one track per request type (feature,
  bug, crash, version, coexistence, rollback, checkpoint), when to stop verifying and offer the
  user the package, a table of user signals and what to do about each, the hand-over report
  format, release mechanics, and anti-patterns from real sessions.
- `references/runtime-build-resolution.md` — **running on game builds you do not have**: telling a
  build-gate no-op from a code change, scannable vs non-scannable facts, vtables by RTTI (exactly
  one address point), globals by strictly decoded `ADRP`+`ADD/LDR` reference sites (two that must
  agree), functions by signature or vtable slot, choosing the layout profile by a strict
  displacement-preserving code probe (exactly one profile or nothing), cross-checks, proving it on
  a holdout build, generator and replay rules, and what to tell the user.

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

- `references/container-preview-live-contents-and-icons.md` — an overlay that shows
  **live container contents with correct icons**: the two-instance problem (which actor holds
  fresh data and which one is drawable), record rules that keep the two apart, learning client
  item identity from the engine's own item renderer, the `ItemStack` layout (152 bytes, shared
  reference block at `+0x8`, description-id virtual at `0x2f0`), the private-reference-block
  recipe for drawing items the drawable instance never held, the texture-atlas dead end, and
  container screen open/close detection including the cross-container destructor crash.

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

- `references/build-deploy.md` — NDK+CMake build, xmake build, manifest,
  .levipack packaging, deployment, logcat verification.

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
- `references/engine-ui-buttons-and-overlays.md` — **on-screen buttons and overlays drawn with
  Minecraft's own UI**: choosing between the launcher HUD button, launcher draw commands and the
  engine UI layer; the `MinecraftUIRenderContext` finish/draw-text hooks (slots 31/6) and the
  fill/text/clip primitives (slots 17/6/8/2/28) with their AArch64 ABIs; proving each slot by
  disassembly and cross-checking against byte signatures; UI-unit geometry and screen-size
  anchoring; container-screen gating (shared `ScreenController::open` slot 7 + destructor slot 0,
  and the screens that override `open()`); touch hit-testing (view pixels → UI units); and the
  fail-open installation checklist. Read it before writing any custom control or panel. §5b/§5c
  also cover giving the button a **vanilla texture face** (`getTexture`/`drawImage`/`flushImages`,
  slots 33/9/11) and placing it clear of the container's close button.
- `references/container-item-moves-and-sorting.md` — **acting on** container contents: the measured
  semantics of the controller's select (slot 58) and `moveSelectedTo` (slot 56) verbs (one item per
  call, verified from on-device step logs), why slot 71 and the container size accessor are
  unusable, refusing screens whose slot layout differs (furnace/campfire split stacks), the
  single-target-layout + distance-metric planner that converges instead of oscillating, burst
  pacing that does not desync the local server, per-kind conservation checks, and the
  capture-and-replay alternative.
- `references/hud-buttons-and-icon-art.md` — launcher HUD buttons from a native
  mod: what the bridge really validates, the missing active-PNG setter and the
  `ButtonInfo` workaround, toggle event semantics, embedding art as generated
  byte arrays, and drawing pixel-art icons with Pillow (sampled ink colours,
  alpha-mask recolouring, keycap compositing) with offline verification.
  The launcher's stock button art (keycap bases + glyph pairs, 512x512 PNG)
  ships in `assets/hud-button-templates/` as the style reference and
  compositing base.
- `references/embedding-dex-and-java-ui.md` — **compiled Java/Kotlin inside a native mod**: the
  javac→d8→C-array pipeline (`--min-api 26`, header `file_size` must match, verify the embed with
  `find(b"dex\n038")`), loading it with `InMemoryDexClassLoader` over a `NewDirectByteBuffer` of
  the `.rodata` array, obtaining a parent `ClassLoader` (launcher class → `ActivityThread` →
  `Context.getClassLoader`), caching `jmethodID`s, `ScopedEnv` attach/detach, why documents must
  cross JNI as `byte[]` and not `NewStringUTF`, attaching a `View`/`WebView` tree to the Activity
  decor, the **attach-once-is-wrong** watchdog that survives Activity recreation, touch
  drag/slop/gravity-conversion/clamp rules, the `onPageFinished` gate that a state push needs
  (and the polling fallback), `View.onDraw` essentials (density scaling, stopping the frame loop
  when hidden, baseline text, overflow audit), the lifecycle and fail-open rules, the alternatives
  table, a ship checklist and a symptom→cause table. `scripts/gen_dex_payload.py` performs the
  embed and rejects truncated/non-dex input. Read it before any mod that needs real Android
  widgets, a Java SDK, or `Canvas` drawing.
- `references/render-pipeline-hooks.md` — distilled experience for **visual
  effects on a closed-source renderer**: choosing the hook layer, wrapping
  every API resolution path, defensive shader-source injection, self-reporting
  heuristics, GL state hygiene, the two-pass shell and the geometry rules that
  decide whether it looks right, capping additive brightness, rollback
  discipline and artifact verification. Read it before starting any
  outline / tint / glow / material-effect mod, and whenever a rendering mod
  "does nothing" or looks wrong.
- `references/weather-and-ambience-effects.md` — **biome-driven environment effects**
  (fog, sky, precipitation, ambient particles, wind audio): one validated world snapshot
  per tick, biome-family classification with neighbourhood and temporal blending, composing
  the engine's fog/sky/rain instead of replacing them, suppressing vanilla particles with a
  thread-local scope marker, drawing world-space particles through the engine's own
  tessellator/material (verification, tint save/restore, tick-to-frame interpolation),
  synthesising ambience audio with no assets (atomic gains, slew, lifecycle hazards), a
  single settings table feeding persistence + menu + hot-reloaded rules, the thread/lock
  architecture, the build-id gate and packaging discipline, an anti-pattern table and a
  ship checklist. Read it before any weather/ambient-fx mod, and when one is invisible,
  doubled, or stutters.
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
- `references/coexistence-and-crash-safety.md` — **coexistence with another hooking
  mod and crash-safety invariants**: the three layers a conflict can live on (hook chain,
  signature, RTTI/vtable), the decision tree for "my feature disappeared after installing mod X",
  the unverified-hook-target crash and how to make it impossible, the invariant checklist, deep-
  anchored signatures for functions another mod hooks, resolving engine entry points from the ELF
  when the SDK resolver returns 0, and the post-mortem discipline. Read it before any change that
  touches hook installation, pointer validation or another mod's territory.
- `references/field-notes-overlay-mod.md` — **end-to-end field notes from a look-at info
  overlay (WAILA/Jade style)**, written generically with no function names or addresses: feature
  → owning subsystem, anchors for locating functions in a stripped binary (RTTI, NBT keys,
  localization prefixes, the engine's own consumer, static initializers), the evidence ladder,
  safe data reads (server vs client copy, update packets, the registry-key trap, ECS and synched
  data), hook-chain behavior, the full coexistence case study (four blind fixes vs one diagnostic
  build, the never-parsed fallback signature), HUD icons and a live 3D mob in a panel, sizing,
  localization, parallel analysis with subagents, build discipline and checklists. Read it before
  building any HUD info feature or debugging "it does not show with mod X".
- `references/ui-actor-rendering.md` — **rendering a live entity model in a HUD panel**:
  copying the engine's own UI actor renderers (HUD paper doll, inventory horse) call for call,
  the preconditions that crash or hang when missed (world settled, HUD on top, actor
  components), pose and fit, putting the model inside the panel (nine-slice with a hole), the
  pitfall that the overlay hook runs several times per frame (draw the model once per frame),
  the wrong theories and why they should have been rejected sooner, debugging without logs.
  Read it before drawing any 3D model from a HUD hook.
- `references/lessons-learned.md` — distilled experience: why forcing
  upstream gates fails, fixing at the drop site, reversing working
  references, and debugging process. Read it whenever a GUI item icon is
  missing, stretched, submitted-but-invisible, updated after `setItem`, or
  affected by container open/close lifecycle.
- `references/in-world-3d-rendering.md` — **how to draw your own 3D geometry in the game
  world**: capture the frame's world-render entry, borrow the engine's materials by name, guard
  matrix and shader-colour state with RAII, submit through the engine tessellator in
  camera-relative coordinates, rasterize panels into bounded quad grids with tiny pattern tables
  and greedy merging, draw text with the engine font (one matrix push per block, shadow as a
  second pass), probe cells for occlusion instead of raycasting geometry, and keep a per-frame
  budget. Ends with a performance checklist and a symptom → cause table. Read it before adding
  any in-world geometry, label, icon or panel.

- `references/build-locked-abi-profiles.md` — **one mod, one game build**: the build-id gate, the
  `{name, address, pattern}` table generated with PC-relative immediates masked and a single-hit
  assertion, the independent verifier that proves identity through relocations and typeinfo,
  all-or-nothing installation, vtable identity checks, multi-build profiles, and the pitfalls of
  hand-edited tables. Read it before hooking or calling anything inside the game image.
- `references/in-place-render-interception.md` — **change the geometry the engine already draws**:
  callsite-keyed interception by return address, emit-first/swallow-siblings, swapping one
  argument object under object identity, structure-return assembly shims, relief geometry from
  image data (luminance, normalisation, side faces), scope state, and the traps (drifting call
  sites, GPU lifetime, swallowing vertices for the wrong caller).
- `references/vanilla-screens-and-server-sessions.md` — **reusing vanilla screens**: the nearby-block
  route through the engine's own use/interact verbs, reconstructing the open intent through the
  engine's own dispatch, session-scoped validity handling, detached-actor identity substitution with
  an ownership token, host-tick session liveness, solo detection, request queues, and pitfalls
  (solo-only releases, per-tick allowances, missing host-side models).
- `references/launcher-hud-data-driven-ui.md` — **HUD UI without engine hooks**: draw commands as
  data (alignment sentinel, shadow as a second command, dedupe, rate cap, empty list clears), font
  registration and validation, one settings table driving defaults/file/schema/clamp, the
  tick/worker split with a try-lock tick, deferred world init, the localization key ladder, and
  the lock/re-entrancy traps.
- `references/native-mod-code-patterns.md` — **code-level patterns for a native mod**: the
  testable project layout (one raw-memory unit, pure core, feature units), the lifecycle skeleton,
  the address/pointer helper set (`base` / `at` / `fn` / `vfn` / `in_code`), a hook table with
  all-or-nothing rollback, the detour shape (original first, gate after, nothing throws), RAII
  guards for matrix / colour / current-object scope, vtable identity checks and the fail-open
  shape, threading idioms (`try_to_lock` tick, worker condition variable, join before unhook), the
  one-settings-table pattern, strict protocol parsing and UTF-16↔UTF-8 handling, the C++ habits
  worth keeping, and the host tests that catch real mod bugs.
- `references/mod-creative-thinking.md` — **the design thinking before the code**: where ideas come
  from (friction, absence, recontext) and the one-sentence test; the ten-second demo; the route
  ladder from settings up to an own renderer and why to take the lowest rung; reuse-before-build
  and the worse-clone test; feasibility as a path question (data / action / render / persistence);
  coexistence as a design choice; scope budgets; persistence identity and the invisible-failure
  plan; the one-page design template; the iterate-in-visible-increments loop; presentation and
  localization; anti-patterns. Read it before the first line of code.
- `references/module-framework-and-shared-services.md` — **hosting many small features in one mod**:
  the module base class (metadata, derived `enabled = master && keybind`, transition-only
  enable/disable), the registry (owned modules, raw view, allocation-free id lookup, one registration
  unit), the UI render-pass **subscription service** (one finish hook, a passive text hook capturing
  the current font per thread, fixed callback array, re-entrancy guard), identity-bound delayed
  actions, frame state, and named per-domain slot/offset headers.
- `references/launcher-java-overlay-bridge.md` — **integrating with the host's overlay surface**:
  resolve the host's classes through the app class loader and cache every method/field id (all
  checked), a checked JNI wrapper with a private failure type and environment RAII, an embedded
  `Runnable` task dex bound with `RegisterNatives` and posted through `runOnUiThread`, a
  single-queue atomic post gate, host UI-mode checks (editor/menu/pause), position persistence, and
  the drift/activity/ref pitfalls.
- `references/session-timeline-capture.md` — **capturing a session as an ordered stream and
  reconstructing it**: the versioned container (signature, varints, length-prefixed blobs,
  bounds-checked decoder, size cap, patched context length, atomic writing), what the stream carries
  (tick, context, chunks, packets, movement batches, environment deltas), reserved synthetic identity
  and per-kind field filtering, the reconstruction phase machine over a private world, the
  deterministic frame clock, validation rules and pitfalls.
- `references/cinematic-camera-paths.md` — **camera tracks**: keyframe fields, per-segment
  interpolation (linear/smooth/ease/hold/hermite/bezier), shortest-arc angle interpolation,
  dimension boundaries, sampling as a pure function of tick, applying the camera at the narrowest
  level once per frame, observing/restoring native FOV, editor camera vs preview, markers, and the
  determinism rules for export.
- `references/imgui-native-overlay.md` — **an immediate-mode editor inside the mod**: vendoring the
  GUI and one GL backend, lazy context creation, one draw point per frame with full state restore,
  density-derived fonts with a fallback, embedded assets, per-frame input capture, soft-keyboard
  lifecycle, model/panels separation with a snapshot rate, surface recreation, and packaging
  resources and fonts.

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
- `scripts/gen_dex_payload.py` — turn a `.dex` into a C++ header
  (`inline constexpr unsigned char[]` + size) so a native mod can embed it in
  `.rodata` and hand it to `NewDirectByteBuffer` at runtime. Validates the dex
  magic and that the header's `file_size` matches the real length, which is the
  check that catches a truncated embed before it becomes a confusing
  `ClassNotFoundException`. See `references/embedding-dex-and-java-ui.md`.
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
