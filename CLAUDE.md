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

- **Method source**: use `BedrockTools-main` (the BedrockTools source tree).
  Its code is the reference for every hook/patch/signature pattern. Other mod
  projects in the workspace may be used only as build-environment references,
  never as the methodology source for deliverables.
- For any hooking/modding/IDA/signature/build task, **read the skill first**:
  `skills/bedrock-pe-modding/SKILL.md` plus its `references/` and `scripts/`.
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
| `skills/bedrock-pe-modding/references/hook-techniques.md` | full hook technique reference |
| `skills/bedrock-pe-modding/references/so-analysis.md` | direct `.so` analysis (ELF/sections/symbols/strings/signatures) |
| `skills/bedrock-pe-modding/references/build-deploy.md` | NDK+CMake build, manifest, .levipack, deploy |
| `skills/bedrock-pe-modding/references/ida-workflow.md` | optional IDA / IDA Pro MCP guide |
| `skills/bedrock-pe-modding/references/version-porting.md` | porting checklist |
| `skills/bedrock-pe-modding/scripts/` | verify_signatures, elf_facts, aarch64_enc, ida_mcp_client, package_levipack, ida_decompile_targets |
| `HOOK_TUTORIAL.md` | user-facing full tutorial |
| `AGENTS.md` / `CLAUDE.md` | per-agent entry guides (Codex / Claude Code & others) |
| `github-upload/bedrock-pe-modding/` | clean GitHub-ready copy of this repo package |

## 4. Ethics and safety

- Only hook/patch binaries and versions the user explicitly authorized; keep
  work to learning/personal use and respect the game's terms of service.
- Do not ship or perform unapproved network scraping or cracking actions.
