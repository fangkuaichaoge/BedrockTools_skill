# Optional IDA / Disassembler Workflow

Reverse engineering with a disassembler is **optional** in this skill. The
primary workflow relies on BedrockTools' existing signature table and the
offline verifier. Use IDA (any modern version) or Ghidra only when you need
to:

- locate a function BedrockTools does not ship a signature for,
- confirm that a resolved signature really is the intended function,
- read member offsets (`this + 0xNNN`) or vtable slots for a new game version,
- extract a fresh signature for a new version.

All paths below are placeholders (`<ida-install>`, `<libminecraftpe.so>`);
substitute your own.

## 1. Loading the binary

```
<ida-install>/ida64.exe <libminecraftpe.so>
```

The library is loaded with imagebase 0, so **vaddr == file offset**, which
matches the output of `scripts/verify_signatures.py`.

Useful operations:

| Goal | Action |
|---|---|
| Jump to an address | `G` then enter e.g. `0x<address>` |
| Decompile a function | put the cursor in it, press `F5` (Hex-Rays) |
| Find references | `X` on a function / string |
| Browse strings | `Shift+F12`, double-click, then `X` |
| Search raw bytes | `Alt+B`, enter `FF 03 01 D1 ...` |

## 2. Locating a function from a string

1. Open the strings window and search for a relevant message/constant
   (e.g. a version string, an error message, a packet name).
2. Double-click it, press `X` and jump to the referencing function.
3. Press `F5` and confirm the logic matches the expected behavior.
4. Note the function head address.

## 3. Extracting a signature (BedrockTools style)

1. Put the cursor at the function head (first instruction of the prologue).
2. Copy the bytes of the first 12-16 instructions.
3. Format as `XX XX XX XX` space-separated, 4 bytes per instruction.
4. Wildcard the low (register-encoding) byte of each instruction where
   register allocation may vary; keep opcode bytes.
5. Anchor on distinctive immediates / instruction combinations.
6. Verify uniqueness with `scripts/verify_signatures.py`; lengthen if
   AMBIGUOUS.

Example from a real Bedrock function:

```asm
10056c48  SXTH  W8, W1
10056c4c  MOV   W9, #0x40
10056c50  MOV   W1, #0x40
10056c54  SUB   W8, W8, W0,SXTH
10056c58  ORR   X0, X9, X8,LSL#32
10056c5c  RET
```

Signature (register bytes wildcarded):

```
?? 3C 00 13 ?? 08 80 52 ?? 08 80 52 ?? A1 20 4B ?? 81 08 AA C0 03 5F D6
```

## 4. Reading member offsets and vtable slots

In the Hex-Rays view, structure accesses appear as:

```c
*(_DWORD *)(this + 0x424) = color;   // LevelRendererPlayer::mFogColorRed = 0x424
```

Indirect calls through the vtable appear as:

```c
(*(void (**)(__int64, ...))(*(_QWORD *)this + 8LL * 6))(this, ...);  // slot 6
```

Record these into BedrockTools' `include/bedrocktools/sdk/offsets/*.hpp`
namespaces (`VTable`, `ClientInstance`, `Actor`, ...).

## 5. Batch decompilation without a GUI

`scripts/ida_decompile_targets.py` is an IDAPython script: it reads
`[{"id":"NormalTick","addr":174207268}, ...]`, decompiles each address with
Hex-Rays, and writes a JSON report (output paths via environment variables
`IDA_TARGETS_JSON` / `IDA_REPORT_JSON`; defaults to the script's directory).

Run:

```bash
<ida-install>/idat64.exe -A -S"<abs path>/ida_decompile_targets.py" -Lida.log <db-or-so>
```

The script only reads the database and exits without saving.

## 6. IDA Pro MCP (optional, for AI-driven analysis)

IDA Pro MCP exposes IDA through the Model Context Protocol so an AI agent can
decompile, disassemble, list strings, follow xrefs, etc.

Install the plugin and server:

```bash
python -m ida_pro_mcp --install
python -m ida_pro_mcp --config
```

Then:

1. Open IDA and load the database / binary.
2. Start the MCP plugin inside IDA (it serves on a local HTTP endpoint such
   as `http://127.0.0.1:13337/mcp`).
3. Configure the MCP client:

```json
{ "mcpServers": { "ida-pro-mcp": { "type": "http", "url": "http://127.0.0.1:13337/mcp" } } }
```

Typical tools: `decompile` (addr), `disasm` (addr, count), `strings`,
`xrefs`, `segs`, `get_name`/`set_name`.

## 7. Verifying a decompiled target against the signature table

Full loop:

```
resolve signature (verify_signatures.py) -> address
        -> optional decompile at that address
        -> confirm logic matches the expected function
        -> implement hook/patch
```

This closes the loop between "the signature hits" and "the function really is
what we think it is".
