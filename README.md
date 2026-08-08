# bedrock-pe-modding

**Minecraft Bedrock (PE) native modding skill + hooking tutorial**, distilled
from the open-source [BedrockTools](https://github.com/RadiantByte/BedrockTools)
project. It teaches how to hook and patch `libminecraftpe.so` (Android
arm64-v8a) for [LeviLauncher](https://github.com/LiteLDev/LeviLaunchroid) /
[preloader-android](https://github.com/LiteLDev/preloader-android), from
"user wants feature X" all the way to a compiled `.so` deployed and verified
on a device.

The package is **machine-agnostic and English-first**: no hardcoded local
paths, game versions, or file sizes. Every path in the docs is a placeholder
(`<libminecraftpe.so>`, `<NDK>`, `<mod-dir>`). A disassembler (IDA/Ghidra) is
optional; the core workflow runs on the binary itself.

## What's inside

```
├── AGENTS.md                      # guide for AI agents working in a mod repo
├── HOOK_TUTORIAL.md               # full user-facing tutorial (feature -> .so)
└── skills/bedrock-pe-modding/     # installable Codex skill
    ├── SKILL.md                   # entry: architecture, APIs, workflow
    ├── references/
    │   ├── feature-workflow.md    # feature -> function -> feasibility -> hook choice
    │   ├── hook-techniques.md     # signatures, inline hooks, patches, vtable, GOT/PLT
    │   ├── so-analysis.md         # analyzing libminecraftpe.so directly (no game needed)
    │   ├── build-deploy.md        # NDK+CMake build, manifest, .levipack, logcat
    │   ├── ida-workflow.md        # optional IDA / IDA Pro MCP guidance
    │   └── version-porting.md     # porting checklist for new game versions
    ├── scripts/
    │   ├── verify_signatures.py   # verify BedrockTools signatures on any .so
    │   ├── elf_facts.py           # read-only ELF facts (std-lib only)
    │   ├── aarch64_enc.py         # encode AArch64 patch instructions
    │   ├── ida_mcp_client.py      # JSON-RPC client for IDA Pro MCP
    │   ├── package_levipack.py    # package + verify .levipack
    │   └── ida_decompile_targets.py  # IDAPython batch decompiler
```

## Quick start

### As a Codex skill

Copy `skills/bedrock-pe-modding` into your Codex skills directory
(`~/.codex/skills/`), then ask Codex to hook or patch `libminecraftpe.so`.
The skill triggers on modding/hooking/signature/build tasks.

### As documentation

Read `HOOK_TUTORIAL.md` end to end, or jump into
`skills/bedrock-pe-modding/references/feature-workflow.md` for the thinking
process and `references/hook-techniques.md` for the mechanics.

## The workflow in one line

```
feature -> game function -> locate (BedrockTools signature dictionary first)
-> analyze (optional disassembler) -> feasibility -> hook choice
(inline hook | vtable hook | byte patch) -> verify signature UNIQUE
-> implement with BedrockTools patterns -> build arm64 .so -> deploy -> logcat
```

## Verification

The signature verifier (`scripts/verify_signatures.py`) scans any
`libminecraftpe.so` for the BedrockTools signature dictionary and reports
each pattern as `UNIQUE` / `AMBIGUOUS` / `MISSING`. Only `UNIQUE` matches are
safe to hook. Always run it against **your** binary — signatures are
version-sensitive.

## Related projects

- [BedrockTools](https://github.com/RadiantByte/BedrockTools) — the method
  source (GPL-3.0).
- [preloader-android](https://github.com/LiteLDev/preloader-android) — the
  mod SDK (hook / signature / patch / mod-menu APIs).
- [LeviLaunchroid](https://github.com/LiteLDev/LeviLaunchroid) — the launcher.

## License & credits

GPL-3.0 (see `LICENSE`). The methodology and code patterns are distilled from
BedrockTools (GPL-3.0) and the preloader-android SDK; see `NOTICE`.

Use responsibly: hook/patch only binaries you are authorized to modify, keep
work to learning/personal use, and respect the game's terms of service.
