# AGENTS.md — AI working guide for this workspace

This workspace is a Minecraft Bedrock (PE) Android native-mod / hook research
environment. The methodology comes from the open-source **BedrockTools**
project. Your job is to apply that methodology to a local
`libminecraftpe.so`: locate functions, extract/verify signatures, write hooks
or patches, compile an arm64 `.so`, and verify it.

## 0. Rules

### Iron rules (apply to every task, in every session)

1. **When a decision is uncertain, ask the user first — before building.**
   State the choice, the trade-off and what you would do by default, and ask
   for their preference, any advice, and whether they have supplementary
   material (a reference mod, a decompile, a screenshot, a spec). One short
   question costs seconds; guessing costs a build, a device test and a
   rollback. This applies to scope ("should this also cover X?"), to semantics
   ("does □ mean slow, or cannot harvest?"), and to mechanism ("engine renderer
   or my own draw call?"). Do not ask about things the evidence already
   settles — read the evidence first, then ask what is genuinely open.
2. **Before hunting evidence, work out what the feature relates to.**
   Reverse engineering starts from the feature, not from a search: name the
   subsystem that owns the behavior (which screen, which renderer, which item
   or block class, which asset family), then find the engine function that
   already implements it and read *that*. Confirm the path exists — data path,
   render path, input path — before writing UI on top of it. A feature whose
   underlying path does not exist in this engine cannot be built by trying
   harder, and finding that out early is the whole point of this rule.
3. **When the user says they have a tool, skill or reference, use it.**
   If they name a prepared script, skill, dump, IDA session, reference project
   or document, that is the intended input: load and use it instead of
   re-deriving the same thing from scratch. Their prepared material outranks
   your own shortcut. If it turns out to be unusable, say so with the concrete
   reason and ask — do not silently substitute your own approach.

4. **A crash is worse than a missing feature, and nothing unverified may reach a hook.**
   Before any engine address is hooked or called, prove it is *code inside the game image* (module
   check from the linker's own program headers, plus a byte check against the signature). Identify
   objects by a fixed vtable address point before reading their slots or calling their methods — never by "the second argument should be ...". Guard every dereference with a readable-map check
   whose snapshot refreshes on a miss. When in doubt, do not draw and do not call: fail open.
   Change one variable per build; a two-change "fix" cannot be attributed. The full invariant
   checklist and the coexistence decision tree live in
   `skills/bedrock-pe-modding/references/coexistence-and-crash-safety.md` — read it before touching
   hook installation, pointer validation, or anything a second mod also touches.

### Working rules

- **Method source**: use `BedrockTools-main` (the BedrockTools source tree).
  Its code is the reference for every hook/patch/signature pattern. Other mod
  projects in the workspace may be used only as build-environment references,
  never as the methodology source for deliverables.
- For any hooking/modding/IDA/signature/build task, **read the skill first**:
  `skills/bedrock-pe-modding/SKILL.md` plus its `references/` and `scripts/`.
- Everything you produce must be **machine-agnostic and English-readable**:
  use placeholders like `<libminecraftpe.so>`, `<NDK>`, `<mod-dir>` instead of
  local absolute paths; do not bake in game-version numbers, file sizes, or
  build-specific addresses (they confuse readers on other machines).
- The target binary is usually `libminecraftpe.so` in the workspace root.
  Confirm its path with `rg --files` / `Get-ChildItem` rather than assuming.
- Cross-agent compatibility: `CLAUDE.md` mirrors this file so Claude Code and
  other agents that look for `CLAUDE.md` find the same guidance. The skill
  itself uses the shared `SKILL.md` frontmatter convention
  (`name` + `description`) understood by Codex, Claude Code, Cursor, etc.

## 1. Standard workflow (for every mod/hook task)

1. **Feature -> function**: ask which game function implements the desired
   behavior; check BedrockTools' `Signatures.cpp` dictionary first.
2. **Verify signatures**:

   ```
   python skills/bedrock-pe-modding/scripts/verify_signatures.py <libminecraftpe.so> \
   --sigs <BedrockTools .../src/core/memory/Signatures.cpp> --json sig_report.json
   ```

   Only UNIQUE is safe; AMBIGUOUS must be lengthened; MISSING means a version
   mismatch. A few successful checks are enough to proceed.
3. **Optional disassembler**: if a function is undocumented, use IDA/Ghidra
   (any install) to decompile it and confirm behavior; this is optional.
4. **Implement**: `PL_REGISTER_MOD` + `pl::memory::hook` (inline) or
   `pl::memory::writeBytes` (patch, verify original bytes first). Follow
   BedrockTools code shapes exactly.
5. **Build**: NDK + CMake (see
   `skills/bedrock-pe-modding/references/build-deploy.md`); the output must
   export `PLGetModRegistration`.
6. **Deploy & verify**: manifest.json + `.so` into the launcher's `mods/`
   directory; check logcat.

## 2. Tooling conventions

- Prefer `rg --files` for listing files.
- Use absolute paths in commands; some sandboxes deny `cd` to other drives.
- Running `python`, `cmake`, `ninja`, or a disassembler may require elevated
  permission in this environment; request it with a clear justification.
- Edit files with `apply_patch`; do not write files with `cat >`.
- Never copy huge analysis databases (e.g. multi-GB `.i64`) into deliverables;
  work on a disposable copy in a temp dir when needed.

## 3. Deliverables in this workspace

| Path | Content |
|---|---|
| `skills/bedrock-pe-modding/SKILL.md` | skill entry: architecture, API table, standard workflow |
| `skills/bedrock-pe-modding/references/workflow-playbook.md` | read first: the session loop, one track per request type (feature, bug, crash, version, coexistence, rollback, checkpoint), when to stop verifying and offer the user the package, user signals and responses, hand-over report format, release mechanics, anti-patterns |
| `skills/bedrock-pe-modding/references/runtime-build-resolution.md` | running on builds you do not have: build-gate diagnosis, vtables by RTTI, globals by decoded ADRP reference sites, functions by signature or slot, strict layout-profile probe (exactly one profile or nothing), cross-checks, holdout-build proof, generator/replay rules |
| `skills/bedrock-pe-modding/references/feature-workflow.md` | feature -> function -> feasibility -> hook decision |
| `skills/bedrock-pe-modding/references/hook-techniques.md` | full hook technique reference (inline, patches, vtable, GOT/PLT, library-boundary hooks) |
| `skills/bedrock-pe-modding/references/hook-engineering.md` | hook judgement and safety: mechanism choice, site choice, lifecycle, fail-open rules, proving liveness, coexistence, failure modes |
| `skills/bedrock-pe-modding/references/coexistence-and-crash-safety.md` | coexistence with another hooking mod + crash-safety invariants: the three layers a conflict can live on, the decision tree for "my feature disappeared", the unverified-hook-target crash, the invariant checklist, deep-anchored signatures, ELF vaddr tables when the SDK RTTI resolver returns 0 |
| `skills/bedrock-pe-modding/references/mod-lifecycle-and-crash-safety.md` | startup timing and crash safety: preloader load order, why not to hook `dlopen`, poll-then-settle attach, diagnosing "the launcher dies and asks me to re-import", stale-config repair |
| `skills/bedrock-pe-modding/references/signature-forensics.md` | finding a function and proving it is usable: anchors, pattern design, evidence ladder, ABI/thread/re-entrancy checks, ship checklist |
| `skills/bedrock-pe-modding/references/so-analysis.md` | direct `.so` analysis (ELF/sections/symbols/strings/signatures) |
| `skills/bedrock-pe-modding/references/build-deploy.md` | NDK+CMake build, manifest, .levipack, deploy |
| `skills/bedrock-pe-modding/references/preloader-mod-implementation-notes.md` | field notes from a real port: runtime base for build-specific fallbacks, vtable-slot verification, actor enumeration ABI, overlay specifics, per-frame render entry discovery, optional hooks/cache cleanup, scan budgeting, crash-log nuance |
| `skills/bedrock-pe-modding/references/levilauncher-native-mod-guide.md` | host-side contract distilled from the official LeviLaunchroid docs: mod layout and manifest fields (entry must equal the built `.so`, `overwrite_*` default to not replacing, `minecraft_versions` gating), lifecycle (`load()` required, both `load()` and `load(ModContext&)` accepted), `NativeMod` handle, pinned FetchContent SDK, typed config merge semantics, Mod Menu, the Input API's missing unregister, memory APIs, packaging, ship checklist, and docs-vs-headers drift |
| `skills/bedrock-pe-modding/references/ida-workflow.md` | optional IDA / IDA Pro MCP guide |
| `skills/bedrock-pe-modding/references/version-porting.md` | supporting several builds from one `.so`: signature portability (house style vs `--tight`), object-layout porting, per-build hit matrix, making the check unskippable |
| `skills/bedrock-pe-modding/references/porting-a-live-mod.md` | end-to-end playbook for adding a new build to a shipping mod: one `.so` with a runtime build profile, a disjoint signature as the build gate, moved offsets/vtable slots and their evidence tools, moving capture points (engine queue discovery), the pinned-`adrp` signature trap, differential old-build testing, the diagnostic-log decision tree, multi-build release discipline, pitfalls |
| `skills/bedrock-pe-modding/references/engine-ui-buttons-and-overlays.md` | on-screen buttons/panels drawn with Minecraft's own UI: layer choice (launcher button vs launcher draw commands vs engine UI), `MinecraftUIRenderContext` finish/draw-text hooks and fill/text/clip slot ABIs, slot verification by disassembly + signature cross-check, UI-unit geometry, container-screen gating (and the screens that override `open()`), touch hit-testing, fail-open checklist |
| `skills/bedrock-pe-modding/references/embedding-dex-and-java-ui.md` | compiled Java/Kotlin inside a native mod: javac → d8 → C-array pipeline, `InMemoryDexClassLoader` over a `NewDirectByteBuffer` of the `.rodata` image, parent `ClassLoader` acquisition, `jmethodID` caching, `ScopedEnv` attach/detach, documents as `byte[]` not `NewStringUTF`, attaching `View`/`WebView` to the Activity decor, the Activity-recreation watchdog, drag slop/gravity/clamp rules, the `onPageFinished` state-push gate, `View.onDraw` essentials, lifecycle, checklist, symptom → cause table |
| `skills/bedrock-pe-modding/references/container-item-moves-and-sorting.md` | moving items inside a container from a mod: measured select/moveSelectedTo verb semantics (one item per call), why raw moveItem and container-size accessors are unusable, screen-class gating so unknown layouts cannot split stacks, the single-target-layout + distance-metric sort planner, burst pacing that avoids server desync, per-kind conservation checks, capture-and-replay alternative |
| `skills/bedrock-pe-modding/references/hud-buttons-and-icon-art.md` | launcher HUD buttons: bridge validation rules, the ButtonInfo workaround for active-PNG icons, toggle event semantics, embedded byte-array art, Pillow icon pipeline with offline verification |
| `skills/bedrock-pe-modding/references/render-pipeline-hooks.md` | experience notes for visual effects on a closed-source renderer (hook layer choice, shader-source injection, GL state hygiene, shell geometry, brightness budgets, rollback, artifact verification) |
| `skills/bedrock-pe-modding/references/shader-source-injection.md` | patching GLSL as text: insertion, identifier discovery, component counts, interface/link safety, unpatchable stages, host tests |
| `skills/bedrock-pe-modding/references/instrumentation-and-diagnostics.md` | building self-diagnosing mods: log design, canaries, API error instrumentation, observe-only builds, hot-path cost, log delivery |
| `skills/bedrock-pe-modding/references/feature-rollback.md` | rollback/removal/rename discipline: known-good state, delete-not-disable, proving removal, preserving the surviving path |
| `skills/bedrock-pe-modding/references/block-model-replacement.md` | replacing a block's model at the per-face emitter layer: why the original must always run, diagonal-quad reachability, base colour vs per-vertex AO, atlas UV clamping, batch dedup, overlap-culling trap |
| `skills/bedrock-pe-modding/references/verifying-unrun-cpp.md` | delivering code you cannot execute: risk classification, byte-pattern uniqueness, a second implementation plus source-level assertions that the C++ is the same algorithm, cross-compiler type checking, settling ABI claims from disassembly, self-reporting builds, techniques that only look like verification, recorded dead ends, and how to report the remaining gap |
| `skills/bedrock-pe-modding/references/ui-actor-rendering.md` | a live entity model in a HUD panel: copy the engine UI actor renderers, preconditions, pose and fit, panel hole, the several-passes-per-frame pitfall (draw once per frame), wrong theories, debugging without logs |
| `skills/bedrock-pe-modding/references/lessons-learned.md` | distilled debugging experience (gates, drop sites, item rendering, rendering-effects pointer) |
| `skills/bedrock-pe-modding/references/field-notes-overlay-mod.md` | generic field notes from a look-at info overlay (WAILA/Jade style): feature → subsystem, anchors for stripped binaries, evidence ladder, server vs client data, the registry-key trap, hook chain, the coexistence case study (diagnostic counters, never-parsed fallback signature), HUD icons and a live mob preview, localization, parallel analysis, checklists |
| `skills/bedrock-pe-modding/scripts/` | verify_signatures, elf_facts, aarch64_enc, ida_mcp_client, package_levipack, gen_dex_payload, ida_decompile_targets, a64, mksig, sigscan, multi_verify, offset_check, vtable_walk, vtable_slot_map, getter_census, ptr_vec_scan, disp_census |
| `CLAUDE.md` | Claude Code / other-agent companion to this file |
| `HOOK_TUTORIAL.md` | user-facing full tutorial |
| `github-upload/bedrock-pe-modding/` | clean GitHub-ready copy of this repo package |

## 4. Ethics and safety

- Only hook/patch binaries and versions the user explicitly authorized; keep
  work to learning/personal use and respect the game's terms of service.
- Do not ship or perform unapproved network scraping or cracking actions.
