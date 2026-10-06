# Analyzing libminecraftpe.so Directly (no game needed)

You can analyze the `.so` on disk without a device, emulator, or running
game. This covers: identify the file, read sections, list symbols, find
strings, and verify signatures — all read-only.

## 1. Identify the file

```bash
file libminecraftpe.so
readelf -h libminecraftpe.so
```

Expected for Bedrock Android: ELF64, little-endian, `e_machine = 0xb7`
(AArch64), usually stripped (`.symtab`/`.strtab` absent, but the dynamic
symbol table remains).

Python (standard library only):

```python
import struct
data = open("libminecraftpe.so", "rb").read()
assert data[:4] == b"\x7fELF"
print("class:", data[4], "endian:", data[5], "machine:", hex(struct.unpack_from("<H", data, 18)[0]))
```

## 2. Segments and sections

Program headers (PT_LOAD) define what is mapped into memory and its
permissions:

```bash
readelf -l libminecraftpe.so
```

Section headers (when present) give named regions. The important ones for
hooking:

| Section | What lives there | Use for |
|---|---|---|
| `.text` | executable code | signature scanning |
| `.rodata` | strings and constants | string xrefs, version strings |
| `.data.rel.ro` | vtable / relocations | `resolveVtableFunction` |
| `.got` / `.got.plt` | imported function pointers | GOT/PLT hooks |
| `.data` / `.bss` | globals | patch/read global state |
| `.eh_frame` / `.eh_frame_hdr` | unwinding | function boundary hints |

IDA-style section listing (example output, your values will differ):

```
.text       start 0x<...> size 0x<...>
.rodata     start 0x<...> size 0x<...>
```

With imagebase 0 (default in IDA), **vaddr == file offset**, so the address
reported by an offline scan is directly usable in IDA and at runtime.

## 3. Dynamic symbols

The dynamic symbol table is not stripped:

```bash
readelf --dyn-syms --wide libminecraftpe.so | head -50
```

Look for:

- `JNI_OnLoad` / `Java_*` exports (Android JNI surface),
- imports of `dlopen`/`dlsym`/`mprotect`/`pthread_*` (what the game uses),
- `memcpy`/`memset`/`strlen` imports (common GOT-hook targets),
- `__cxa_*` / `_ZTV*` symbols (vtable / RTTI anchors for
  `resolveVtableFunction`).

`scripts/elf_facts.py` dumps this automatically (counts + keyword-filtered
lists) into a JSON report.

## 4. Strings

```bash
strings -a -n 6 libminecraftpe.so | grep -iE "minecraft|version|protocol"
```

The version string in `.rodata` identifies the game build; this is the string
`VersionString` functions usually return. BedrockTools hooks that function
exactly for this reason.

## 5. Signature verification (the key workflow)

The point of direct analysis is validating BedrockTools' signature dictionary
against the binary you actually have:

```bash
python scripts/verify_signatures.py <libminecraftpe.so> \
    --sigs <path/to/BedrockTools/src/core/memory/Signatures.cpp> \
    --json sig_report.json
```

The script:

1. parses every `SignatureDefinition{SignatureId::X, "hex pattern"}`
   from `Signatures.cpp`;
2. reads the executable PT_LOAD segments of the `.so`;
3. scans for each pattern (wildcard-aware);
4. reports vaddr, file offset, match count, and UNIQUE/AMBIGUOUS/MISSING.

Example output:

```
UNIQUE    VersionString  vaddr=0x<...>  matches=1  bytes=192  fixed=148
UNIQUE    NormalTick     vaddr=0x<...>  matches=1  bytes=64   fixed=60
MISSING   SomeNewFunction
```

UNIQUE = safe to hook; AMBIGUOUS = lengthen the pattern; MISSING = the
dictionary and the binary do not match (different game version) — re-extract
that signature with a disassembler.

## 6. Cross-checking with a disassembler (optional)

If you want to confirm a hit is really the intended function, jump to the
reported vaddr in IDA/Ghidra and decompile. This is optional; the offline
scan already proves uniqueness.

## 7. Quick Python checklist (no external tools)

`scripts/elf_facts.py` performs all of the above (ELF header, segments,
sections, dynamic symbols, strings, version candidates) with the standard
library and writes `elf_facts.json`. Use it as the first step on any new
`.so` you receive.

## 8. Renderer / graphics forensics from the binary (and its limits)

Useful things that can be established **statically**, with the dynamic symbol
table plus the string table:

| Question | How to answer it |
|---|---|
| Which graphics API family does the target use? | Enumeration of the imported symbol names, grouped by prefix; a near-empty import list for the *draw* calls is itself a finding |
| Which calls are resolved by name at runtime? | The symbol names appear as plain strings in `.rodata` even though they are not imported |
| Is a "missing" import really unused? | Count the name in the string table before concluding it is unused - it may be resolved dynamically |
| Which entry points must be intercepted to see everything? | Union of (imported symbols) and (API-family names present as strings), including instanced/indirect/multi variants |
| Are shader texts embedded? | Search for the shader language's own tokens; embedded source is often NUL-delimited printable text, so a scan for an entry-point token followed by a NUL-bounded printable run recovers it |
| Is a shader binary blob present? | Look for the intermediate representation's magic; an IR blob also carries its symbol names as strings, which is enough to learn attribute/uniform vocabulary even without source |

Limits - stop static work and ask the device when:

* the shader sources are **generated at runtime** (only a small subset of
  built-in shaders is present verbatim, and the interesting passes are missing);
* the vocabulary you need is the *generated* one (the target renames everything
  during translation), so static names do not match what the API receives;
* a name appears only as a fragment (no surrounding structure to decode).

In those cases the productive move is one instrumentation build that prints the
vocabulary actually observed at the API boundary, instead of more binary
archaeology. Static analysis is for *locating* things; the device is for
*deciding* what they mean.

