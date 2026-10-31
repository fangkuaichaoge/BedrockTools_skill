# CLAUDE.md — AI working guide (Claude Code and other agents)

This file is the Claude Code / generic-agent companion to `AGENTS.md` (the
Codex convention). The instructions are identical; read `AGENTS.md` as the
canonical copy when the two ever diverge.

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
   ("does ✗ mean slow, or cannot harvest?"), and to mechanism ("engine renderer
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
   objects by a fixed vtable address point before reading their slots or calling their methods -
   never by "the second argument should be ...". Guard every dereference with a readable-map check
   whose snapshot refreshes on a miss. When in doubt, do not draw and do not call: fail open.
   Change one variable per build; a two-change "fix" cannot be attributed. The full invariant
   checklist and the coexistence decision tree live in
   `skills/references/coexistence-and-crash-safety.md` - read it before touching hook
   installation, pointer validation, or anything a second mod also touches.

### Working rules

- **Method source**: use `BedrockTools-main` (the BedrockTools source tree).
  Its code is the reference for every hook/patch/signature pattern. Other mod
  projects in the workspace may be used only as build-environment references,
  never as the methodology source for deliverables.
- For any hooking/modding/IDA/signature/build task, **read the skill first**:
  `skills/SKILL.md` plus its `references/` and `scripts/`.
  The skill uses the shared `SKILL.md` frontmatter convention
  (`name` + `description`), which Codex, Claude Code, Cursor, and other
  agents can all discover.
- Everything you produce must be **machine-agnostic and English-readable**:
  use placeholders like `<libminecraftpe.so>`, `<NDK>`, `<mod-dir>` instead of
  local absolute paths; do not bake in game-version numbers, file sizes, or
  build-specific addresses (they confuse readers on other machines).
- The target binary is usually `libminecraftpe.so` in the workspace root.
  Confirm its path with `rg --files` / `Get-ChildItem` rather than assuming.

## 1. Standard workflow (for every mod/hook task)

1. **Feature -> function**: ask which game function implements the desired
   behavior; check BedrockTools' `Signatures.cpp` dictionary first.
2. **Verify signatures**:

   ```bash
   python skills/scripts/verify_signatures.py <libminecraftpe.so> \
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
   `skills/references/build-deploy.md`); the output must
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
| `skills/SKILL.md` | skill entry: architecture, API table, standard workflow |
| `skills/references/feature-workflow.md` | feature -> function -> feasibility -> hook decision |
| `skills/references/hook-techniques.md` | full hook technique reference |
| `skills/references/hook-engineering.md` | hook judgement and safety: mechanism choice, site choice, lifecycle, fail-open rules, proving liveness, coexistence, failure modes |
| `skills/references/coexistence-and-crash-safety.md` | coexistence with another hooking mod + crash-safety invariants: the three layers a conflict can live on, the decision tree for "my feature disappeared", the unverified-hook-target crash, the invariant checklist, deep-anchored signatures, ELF vaddr tables when the SDK RTTI resolver returns 0 |
| `skills/references/mod-lifecycle-and-crash-safety.md` | startup timing and crash safety: preloader load order, why not to hook `dlopen`, poll-then-settle attach, diagnosing "the launcher dies and asks me to re-import", stale-config repair |
| `skills/references/signature-forensics.md` | finding a function and proving it is usable: anchors, pattern design, evidence ladder, ABI/thread/re-entrancy checks, ship checklist |
| `skills/references/so-analysis.md` | direct `.so` analysis (ELF/sections/symbols/strings/signatures) |
| `skills/references/build-deploy.md` | NDK+CMake build, manifest, .levipack, deploy |
| `skills/references/preloader-mod-implementation-notes.md` | field notes from a real port: runtime base for build-specific fallbacks, vtable-slot verification, actor enumeration ABI, overlay specifics, per-frame render entry discovery, optional hooks/cache cleanup, scan budgeting, crash-log nuance |
| `skills/references/levilauncher-native-mod-guide.md` | host-side contract distilled from the official LeviLaunchroid docs: mod layout and manifest fields (entry must equal the built `.so`, `overwrite_*` default to not replacing, `minecraft_versions` gating), lifecycle (`load()` required, both `load()` and `load(ModContext&)` accepted), `NativeMod` handle, pinned FetchContent SDK, typed config merge semantics, Mod Menu, the Input API's missing unregister, memory APIs, packaging, ship checklist, and docs-vs-headers drift |
| `skills/references/ida-workflow.md` | optional IDA / IDA Pro MCP guide |
| `skills/references/version-porting.md` | supporting several builds from one `.so`: signature portability (house style vs `--tight`), object-layout porting, per-build hit matrix, making the check unskippable |
| `skills/references/porting-a-live-mod.md` | end-to-end playbook for adding a new build to a shipping mod: one `.so` with a runtime build profile, a disjoint signature as the build gate, moved offsets/vtable slots and their evidence tools, moving capture points (engine queue discovery), the pinned-`adrp` signature trap, differential old-build testing, the diagnostic-log decision tree, multi-build release discipline, pitfalls |
| `skills/references/hud-buttons-and-icon-art.md` | launcher HUD buttons: bridge validation rules, the ButtonInfo workaround for active-PNG icons, toggle event semantics, embedded byte-array art, Pillow icon pipeline with offline verification |
| `skills/references/render-pipeline-hooks.md` | experience notes for visual effects on a closed-source renderer (hook layer choice, shader-source injection, GL state hygiene, shell geometry, brightness budgets, rollback, artifact verification) |
| `skills/references/shader-source-injection.md` | patching GLSL as text: insertion, identifier discovery, component counts, interface/link safety, unpatchable stages, host tests |
| `skills/references/instrumentation-and-diagnostics.md` | building self-diagnosing mods: log design, canaries, API error instrumentation, observe-only builds, hot-path cost, log delivery |
| `skills/references/feature-rollback.md` | rollback/removal/rename discipline: known-good state, delete-not-disable, proving removal, preserving the surviving path |
| `skills/references/block-model-replacement.md` | replacing a block's model at the per-face emitter layer: why the original must always run, diagonal-quad reachability, base colour vs per-vertex AO, atlas UV clamping, batch dedup, overlap-culling trap |
| `skills/references/verifying-unrun-cpp.md` | delivering code you cannot execute: risk classification, byte-pattern uniqueness, a second implementation plus source-level assertions that the C++ is the same algorithm, cross-compiler type checking, settling ABI claims from disassembly, self-reporting builds, techniques that only look like verification, recorded dead ends, and how to report the remaining gap |
| `skills/references/lessons-learned.md` | distilled debugging experience (gates, drop sites, item rendering, rendering-effects pointer) |
| `skills/scripts/` | verify_signatures, elf_facts, aarch64_enc, ida_mcp_client, package_levipack, ida_decompile_targets, a64, mksig, sigscan, multi_verify, offset_check, vtable_walk, vtable_slot_map, getter_census, ptr_vec_scan, disp_census |
| `HOOK_TUTORIAL.md` | user-facing full tutorial |
| `.github/workflows/ci.yml` | CI: compiles every script, checks SKILL.md frontmatter, smoke-tests two CLIs |

## 4. Ethics and safety

- Only hook/patch binaries and versions the user explicitly authorized; keep
  work to learning/personal use and respect the game's terms of service.
- Do not ship or perform unapproved network scraping or cracking actions.
