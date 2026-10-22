# bedrock-pe-modding

**Minecraft Bedrock (PE) native modding skill + hooking tutorial**, distilled
from the open-source [BedrockTools](https://github.com/QYCottage/BedrockTools)
project. It teaches how to hook and patch `libminecraftpe.so` (Android
arm64-v8a) for [LeviLauncher](https://github.com/LiteLDev/LeviLaunchroid) /
[preloader-android](https://github.com/LiteLDev/preloader-android), from
"user wants feature X" all the way to a compiled `.so` deployed and verified
on a device.

The package is **machine-agnostic and English-first**: no hardcoded local
paths, game versions, or file sizes. Every path in the docs is a placeholder
(`<libminecraftpe.so>`, `<NDK>`, `<mod-dir>`). A disassembler (IDA/Ghidra) is
optional; the core workflow runs on the binary itself.

---

## Requirements

| Need | For |
|---|---|
| Python 3.10+ | every script (stdlib only, except the three below) |
| `numpy` + `capstone` | `a64.py`, `mksig.py`, `offset_check.py` — pip installable, no network needed at run time |
| Android NDK (r28+) + CMake 3.22+ | building a mod `.so` — see `skills/references/build-deploy.md` |
| `adb` | deploying and reading `logcat` |

Everything else — signature verification, ELF inspection, vtable recovery, hit
matrices, packaging — runs offline on the `.so` alone.

---

## File structure

```
bedrock-pe-modding/
├── README.md                        # this file
├── AGENTS.md                        # working guide for AI agents (Codex convention)
├── CLAUDE.md                        # identical guidance for Claude Code / others
├── HOOK_TUTORIAL.md                 # user-facing tutorial: feature -> compiled .so
├── LICENSE                          # GPL-3.0
├── NOTICE                           # upstream credits (BedrockTools, preloader-android)
├── .gitignore                       # ignores build output, IDA databases, generated reports
├── .github/
│   └── workflows/ci.yml             # syntax + frontmatter + CLI smoke test
└── skills/                          # the installable skill directory
    ├── SKILL.md                     # entry point: architecture, API table, workflow
    ├── references/                  # 20 deep-dive notes, loaded on demand
    ├── scripts/                     # 12 offline tools
    └── assets/
        └── hud-button-templates/    # 14 PNG button templates (normal + pressed)
```

Two layers, deliberately separate:

* **the repository root** is what a human or agent reads first (`AGENTS.md`,
  `CLAUDE.md`, `HOOK_TUTORIAL.md`, `README.md`);
* **`skills/`** is a self-contained skill that can be copied into an agent's
  skills directory as-is. `SKILL.md` links its own material with paths relative
  to that directory (`references/…`, `scripts/…`), so the folder works wherever
  it is installed — the copy just has to keep the name your agent expects.

### `skills/references/` — 20 notes

| File | Answers |
|---|---|
| `feature-workflow.md` | "I have a user request — how do I get from it to a hook decision?" |
| `hook-techniques.md` | "Which mechanism: inline hook, byte patch, vtable, GOT/PLT, library boundary?" |
| `hook-engineering.md` | "Is this hook site safe, and how do I prove it is alive?" |
| `mod-lifecycle-and-crash-safety.md` | "Why does the launcher die on world load / ask me to re-import?" |
| `signature-forensics.md` | "How do I find a function and **prove** it is the right one?" (§10: when nothing names it) |
| `targeted-static-analysis.md` | "How do I answer precise questions about a 300 MB stripped binary in seconds?" |
| `so-analysis.md` | "What can I learn from the `.so` on disk with no device?" |
| `version-porting.md` | "How do I support several game builds from one `.so`?" |
| `build-deploy.md` | "How do I build an arm64 `.so`, package it, and verify it via logcat?" |
| `levilauncher-native-mod-guide.md` | "What does the host side contract look like?" (manifest, lifecycle, config, Mod Menu, input) |
| `ida-workflow.md` | "How do I drive IDA by address (incl. IDA Pro MCP) without wasting an afternoon?" |
| `rea-and-disassembler-providers.md` | "Which disassembler back-ends exist and how do I drive them?" |
| `render-pipeline-hooks.md` | "How do I add a visual effect on a closed-source renderer?" |
| `shader-source-injection.md` | "How do I patch GLSL as text without breaking the link?" |
| `block-model-replacement.md` | "How do I replace a block's model at the per-face tessellation layer?" |
| `hud-buttons-and-icon-art.md` | "How do I add launcher HUD buttons and draw pixel-art icons?" |
| `instrumentation-and-diagnostics.md` | "How do I make a mod that diagnoses itself?" |
| `feature-rollback.md` | "How do I roll back, remove, or rename a shipped feature?" |
| `verifying-unrun-cpp.md` | "How do I defend C++ I cannot compile or run here?" |
| `lessons-learned.md` | distilled debugging experience across the above |

### `skills/scripts/` — 12 tools

| Script | Question it answers | Deps |
|---|---|---|
| `verify_signatures.py` | does this signature dictionary resolve in **my** `.so`, and is each entry `UNIQUE` / `AMBIGUOUS` / `MISSING`? | stdlib |
| `sigscan.py` | scan a `.so` for named wildcard patterns, or grep its strings | stdlib |
| `mksig.py` | generate a wildcard signature from a function head (house style or `--tight`) | capstone |
| `multi_verify.py` | the same signature set across **several builds** — a hit-count matrix | stdlib |
| `a64.py` | `func` / `callers` / `cluster` / `guards` / `xref` / `disas` / `ptr` on an AArch64 ELF | numpy, capstone |
| `vtable_walk.py` | recover a class's vtables offline (`RTTI -> typeinfo -> vtable -> slots`) | stdlib |
| `offset_check.py` | read back the struct displacements a function really uses, per build | numpy, capstone |
| `elf_facts.py` | header / segments / sections / dynsym / version strings of a `.so` | stdlib |
| `aarch64_enc.py` | encode AArch64 patch instructions (MOV / NOP / RET / BR / FMOV) | stdlib |
| `package_levipack.py` | package a mod into `.levipack` and re-read it to verify the result | stdlib |
| `ida_mcp_client.py` | talk to IDA Pro MCP from a shell (unwraps the JSON envelope) | stdlib |
| `ida_decompile_targets.py` | batch-decompile a list of addresses from inside IDA | IDAPython |

`elf_facts.json` (a generated ELF dump) is **not** committed — it is
`.gitignore`d and reproducible with `elf_facts.py`.

---

## Quick start

### As a skill

Copy the contents of `skills/` into a directory named after the skill inside
your agent's skills directory (the directory must end up with `SKILL.md` at its
root):

```bash
# Codex
mkdir -p ~/.codex/skills/bedrock-pe-modding
cp -r skills/. ~/.codex/skills/bedrock-pe-modding/

# Claude Code
mkdir -p ~/.claude/skills/bedrock-pe-modding
cp -r skills/. ~/.claude/skills/bedrock-pe-modding/
```

On Windows (PowerShell):

```powershell
Copy-Item -Recurse skills\* "$env:USERPROFILE\.codex\skills\bedrock-pe-modding\"
```

The skill triggers on modding / hooking / signature / build tasks. `SKILL.md`
uses the shared `name` + `description` frontmatter convention, so it is
discoverable by Codex, Claude Code, Cursor and other agents that support agent
skills.

### As documentation

Read `HOOK_TUTORIAL.md` end to end, or jump straight in:

* the thinking: `references/feature-workflow.md`
* the mechanics: `references/hook-techniques.md`
* when a mod "does nothing" or looks wrong: `hook-engineering.md`,
  `signature-forensics.md`, `instrumentation-and-diagnostics.md`,
  `render-pipeline-hooks.md`

---

## The workflow in one line

```
feature -> game function -> locate (BedrockTools signature dictionary first)
-> analyze (optional disassembler) -> feasibility -> hook choice
(inline hook | vtable hook | byte patch) -> verify signature UNIQUE
-> implement with BedrockTools patterns -> build arm64 .so -> deploy -> logcat
```

---

## Verification

The signature verifier scans any `libminecraftpe.so` for the BedrockTools
dictionary and reports each pattern as `UNIQUE` / `AMBIGUOUS` / `MISSING`. Only
`UNIQUE` matches are safe to hook. Always run it against **your** binary —
signatures are version-sensitive:

```bash
python skills/scripts/verify_signatures.py \
    <libminecraftpe.so> --sigs <BedrockTools>/src/core/memory/Signatures.cpp
```

CI (`.github/workflows/ci.yml`) keeps the package honest on every push: it
byte-compiles every script, checks `SKILL.md`'s frontmatter, and smoke-tests
two CLIs.

---

## Adding new material

The tree above is the contract. When you learn something new, put it in the
right place and update the table that points at it:

| What you learned | Where it goes |
|---|---|
| a new way to locate or prove a function | `references/signature-forensics.md` |
| a hook/mechanism trap | `references/hook-engineering.md` or `hook-techniques.md` |
| a lifecycle / crash cause | `references/mod-lifecycle-and-crash-safety.md` |
| a host-side (launcher / SDK) contract change | `references/levilauncher-native-mod-guide.md` |
| a portable check you keep re-writing | `scripts/` (stdlib if possible) + a row in the script table |
| the same dead end for the second time | `references/lessons-learned.md` |

Then update: the file table in this README, `SKILL.md`'s reference list, and the
deliverables table in `AGENTS.md` + `CLAUDE.md`. A doc nobody links to is a doc
nobody reads.

---

## Related projects

- [BedrockTools](https://github.com/QYCottage/BedrockTools) — the method
  source (GPL-3.0).
- [preloader-android](https://github.com/LiteLDev/preloader-android) — the
  mod SDK (hook / signature / patch / mod-menu APIs).
- [LeviLaunchroid](https://github.com/LiteLDev/LeviLaunchroid) — the launcher,
  and the source of the host-side documentation summarised in
  `references/levilauncher-native-mod-guide.md`
  ([official docs](https://docs.levimc.org/LeviLaunchroid/)).

## License & credits

GPL-3.0 (see `LICENSE`). The methodology and code patterns are distilled from
BedrockTools (GPL-3.0) and the preloader-android SDK; see `NOTICE`.

Use responsibly: hook/patch only binaries you are authorized to modify, keep
work to learning/personal use, and respect the game's terms of service.
