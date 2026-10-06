# AGENTS.md — AI working guide for this workspace

This workspace is a Minecraft Bedrock (PE) Android native-mod / hook research
environment. The methodology comes from the open-source **BedrockTools**
project. Your job is to apply that methodology to a local
`libminecraftpe.so`: locate functions, extract/verify signatures, write hooks
or patches, compile an arm64 `.so`, and verify it.

## 0. Rules

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

   ```bash
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
| `skills/bedrock-pe-modding/references/feature-workflow.md` | feature -> function -> feasibility -> hook decision |
| `skills/bedrock-pe-modding/references/hook-techniques.md` | full hook technique reference (inline, patches, vtable, GOT/PLT, library-boundary hooks) |
| `skills/bedrock-pe-modding/references/hook-engineering.md` | hook judgement and safety: mechanism choice, site choice, lifecycle, fail-open rules, proving liveness, coexistence, failure modes |
| `skills/bedrock-pe-modding/references/signature-forensics.md` | finding a function and proving it is usable: anchors, pattern design, evidence ladder, ABI/thread/re-entrancy checks, ship checklist |
| `skills/bedrock-pe-modding/references/so-analysis.md` | direct `.so` analysis (ELF/sections/symbols/strings/signatures) |
| `skills/bedrock-pe-modding/references/build-deploy.md` | NDK+CMake build, manifest, .levipack, deploy |
| `skills/bedrock-pe-modding/references/ida-workflow.md` | optional IDA / IDA Pro MCP guide |
| `skills/bedrock-pe-modding/references/version-porting.md` | porting checklist |
| `skills/bedrock-pe-modding/references/render-pipeline-hooks.md` | experience notes for visual effects on a closed-source renderer (hook layer choice, shader-source injection, GL state hygiene, shell geometry, brightness budgets, rollback, artifact verification) |
| `skills/bedrock-pe-modding/references/shader-source-injection.md` | patching GLSL as text: insertion, identifier discovery, component counts, interface/link safety, unpatchable stages, host tests |
| `skills/bedrock-pe-modding/references/instrumentation-and-diagnostics.md` | building self-diagnosing mods: log design, canaries, API error instrumentation, observe-only builds, hot-path cost, log delivery |
| `skills/bedrock-pe-modding/references/feature-rollback.md` | rollback/removal/rename discipline: known-good state, delete-not-disable, proving removal, preserving the surviving path |
| `skills/bedrock-pe-modding/references/lessons-learned.md` | distilled debugging experience (gates, drop sites, item rendering, rendering-effects pointer) |
| `skills/bedrock-pe-modding/scripts/` | verify_signatures, elf_facts, aarch64_enc, ida_mcp_client, package_levipack, ida_decompile_targets |
| `CLAUDE.md` | Claude Code / other-agent companion to this file |
| `HOOK_TUTORIAL.md` | user-facing full tutorial |
| `github-upload/bedrock-pe-modding/` | clean GitHub-ready copy of this repo package |

## 4. Ethics and safety

- Only hook/patch binaries and versions the user explicitly authorized; keep
  work to learning/personal use and respect the game's terms of service.
- Do not ship or perform unapproved network scraping or cracking actions.
