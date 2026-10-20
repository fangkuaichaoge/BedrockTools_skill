# Using REA for Precise Analysis

[REA](https://github.com/morluto/rea) ("Reverse Engineer Anything") is an MCP
server + CLI that adapts an existing disassembler into a queryable provider. It
is genuinely useful for Bedrock work — **for the right questions**. This note
covers how to use it precisely, how to set it up, and where it is the wrong
tool.

Read `targeted-static-analysis.md` first: most structural questions are answered
faster offline. Use REA for what offline analysis cannot give you — **decompiled
pseudocode of one specific function**.

---

## 0. The mistake to avoid

The tempting sequence is: "I need to understand this binary" → install/launch a
disassembler → point it at the game library → wait. That sequence is wrong, and
on a game-sized library it does not converge.

The correct sequence is:

1. **Locate offline** — get the exact address you care about with `a64.py`
   (`callers`, `cluster`, `guards`, `xref`, `func`).
2. **Then** ask the provider about **that address only**:
   `rea function <so> 0xADDR`.
3. Never request an overview, never request "analysis of the binary", and never
   let a first query run without a specific address.

If a provider call has not returned in a minute or two, that is the signal to go
back to offline analysis rather than to keep waiting.

---

## 1. When REA helps, and when it does not

| Question | Use |
|---|---|
| "What does this function do?" (pseudocode) | **REA** |
| "Which functions call this one?" | offline (`a64.py callers`) |
| "Which small set of drivers calls this family?" | offline (`a64.py cluster`) |
| "What decision mask gates these calls?" | offline (`a64.py guards`) |
| "Which function contains this address?" | offline (`a64.py func`) |
| "Recover the ABI / argument sources at a call site" | offline (`a64.py disas`) |
| "Decompile this specific address to C" | **REA** |
| "Give me a typed API reconstruction with evidence" | **REA** |

The rule that matters: **REA must load and analyze the whole binary before its
first answer.** On a multi-hundred-megabyte game library that is a one-time
cost measured in many minutes, not seconds. So:

* Never ask REA to "analyze the binary" or give an overview. Ask it about a
  **specific function by address**.
* Locate addresses **offline** first, then spend REA's cost on the one function
  whose pseudocode you actually need.
* Keep the provider warm across queries (see §4) — a cold start per query makes
  this unusable at this size.

## 2. Setup, and the traps

REA needs Node.js (22.19+/24.11+/26+) and a native analysis engine: Hopper,
Ghidra, or IDA. The CLI alone is a small npm install; the engine is the real
work.

```bash
npm install --global rea-agents      # provides the `rea` command
rea doctor                            # shows what is ready and what is not
```

### Trap 1 — the packaged IDA provider is often too old

REA's headless mode needs the upstream provider's **database supervisor API**
(`idb_open` / `idb_list` / `idb_close`, and `--stdio --max-workers`). The
version on the package index can be an older, single-target build that only
accepts one `input_path` and serves SSE — REA will then fail with an opaque
`"Connection closed"` or `"operation is not declared by this provider"`.

Check the provider's CLI before trusting it:

```bash
idalib-mcp --help        # needs --stdio and --max-workers for REA headless mode
```

If those flags are missing, install the upstream **git main** rather than the
published package.

### Trap 2 — the MCP Python SDK major version

A disassembler MCP server commonly imports `mcp.server.fastmcp`. The MCP Python
SDK removed that name in 2.x (renamed to `mcp.server.mcpserver`). With 2.x
installed the server dies on import, and REA reports only `"Connection closed"`.

```bash
pip install "mcp<2"
```

Diagnose by running the provider's entry point directly — it prints the real
`ModuleNotFoundError`, which REA never shows you.

### Trap 3 — headless IDA needs its own Python binding

For IDA, install the `idapro` wheel that ships with the IDA installation, then
point it at the install:

```bash
pip install --no-deps <IDA-dir>/idalib/python/idapro-*.whl
python <IDA-dir>/idalib/python/py-activate-idalib.py
```

`idalib` is what makes **headless** analysis possible — no GUI, no license
dialog, no manual "wait for auto-analysis" step.

### Trap 4 — an offline/hosts-blocked machine

If the host blocks the provider's repository, route the installer through a
local proxy (`HTTP_PROXY`/`HTTPS_PROXY` for npm and pip, and
`git -c http.proxy=... -c https.proxy=...` for clones). Note that a hosts entry
pointing a hostname at `127.0.0.1` looks like a connection failure, not like a
block — check the hosts file before debugging DNS.

## 3. Registration

Write a small JSON file and point `REA_IDA_MCP_CONFIG` at it. Two shapes matter:

**Headless via stdio** (REA spawns the provider per session):

```json
{
  "command": "<python-dir>/Scripts/idalib-mcp.exe",
  "args": ["--stdio", "--max-workers", "1"],
  "mode": "headless",
  "env": { "IDADIR": "<IDA-dir>" }
}
```

**Headless via a supervisor URL** (one long-lived provider, many queries — much
better for a huge binary):

```json
{ "url": "http://127.0.0.1:8745/mcp", "mode": "headless" }
```

Start the supervisor yourself once:

```bash
idalib-mcp --host 127.0.0.1 --port 8745 --max-workers 1
```

Then select the provider:

```bash
export REA_IDA_MCP_CONFIG=/path/to/registration.json
export REA_ANALYSIS_PROVIDER=ida
```

For an **already-open GUI** instead, use the attached profile with the upstream
stdio proxy — but then the original binary must be the one open in the GUI, and
its hash must match.

## 4. Querying precisely

```bash
# pseudocode + disassembly + callees + refs for ONE function
rea function <path> <address-or-name> --json

# a bounded slice of code
rea decompile <path> <address> --json

# reconstruct a function's API boundary with evidence
rea inspect-native-api <path> <address> --json
```

Practical rules:

* **Pass an address**, not a guessed name. Names come from symbols that a
  stripped game library does not have.
* **Pipe to a file** — the JSON envelope is large (it embeds raw upstream
  results, a normalized result, and an explicit limitations list).
* **Read the `limitations` array.** It is honest and tells you which facets are
  *unknown* rather than *absent*. For the IDA adapter, complete function body
  ranges, typed references, basic blocks and direct callers are unavailable —
  so do not read an empty `callers` field as "nothing calls this".
* **Keep the supervisor alive.** With a warm supervisor, repeat queries against
  the same binary reuse the open database instead of re-analyzing.
* **Expect the first query to be slow** on a huge target, and let it finish.
  Interrupting leaves a half-built database that the next run starts over.

## 5. What the output gives you that offline analysis cannot

From a real run on a small arm64 ELF, REA returned a canonical Evidence bundle
containing:

* target identity: SHA-256 digest, format, architecture;
* `lookup_funcs` resolving the symbol to an address;
* full linear disassembly;
* **Hex-Rays pseudocode** with control flow reconstructed (it correctly
  recovered a function-local static's `__cxa_guard_acquire` / `__cxa_guard_release`
  initialisation pattern, which is tedious to read from raw assembly);
* callees, incoming/outgoing references, referenced strings and names;
* an explicit `confidence` (`observed`) and `authority` (`shipped-artifact`)
  plus the limitations list.

That pseudocode is the payoff: it turns "here are 32 instructions" into "this is
a thread-safe lazy singleton returning a registration table".

## 6. Verifying a setup

```bash
rea doctor          # node, host, and one line per engine registration
rea capabilities    # which operations this provider actually declares
```

`doctor` reporting `ida-registration: ok` means the *registration file* is
valid; it explicitly does not launch the engine, so availability and
compatibility are only proven by the first real query. Prove the pipeline on a
**small** binary first — a few-KB `.so` analyzes in seconds and exercises the
whole path (supervisor spawn, database creation, decompiler, Evidence
assembly). Only then point it at the game library.

## 7. Checklist

- [ ] `rea doctor` shows a healthy engine registration.
- [ ] Provider CLI accepts the flags REA's chosen mode needs.
- [ ] MCP Python SDK pinned `<2` if the provider imports `mcp.server.fastmcp`.
- [ ] Pipeline proven end-to-end on a small binary.
- [ ] Addresses located **offline** before spending a cold start.
- [ ] Supervisor kept alive for repeat queries on a huge target.
- [ ] `limitations` read; empty facets treated as unknown, not absent.
- [ ] Structural questions (callers, clusters, masks, boundaries) answered
      offline, not through the engine.
