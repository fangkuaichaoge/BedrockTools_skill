# Targeted Static Analysis of a Huge Stripped Binary

How to answer precise questions about a multi-hundred-megabyte stripped arm64
`libminecraftpe.so` in **seconds per step**, on the machine you already have —
instead of waiting on a full disassembler auto-analysis that never finishes.

Written from a real investigation that recovered the block-rendering mechanism
(face emitters, their callers, the visibility mask, the face ABI, the texture
path) from a several-hundred-megabyte stripped binary. Read this before starting
any "how does the engine do X" question on a big target.

---

## 0. If you are an agent: the rule that must not be skipped

Agents reliably reach for the heavyweight disassembler first, because it is the
tool that *sounds* right for "reverse engineer this binary". That instinct is
wrong here and it is expensive: the run does not finish, and the answer was two
call-graph edges away.

**Do this, in this order, and do not open a disassembler until step 5:**

1. State the question as an address-level query — "which functions call X",
   "what gates this call", "who references this string" — not "analyze the
   binary".
2. Run the offline helper. It answers each of those in seconds:
   `a64.py callers` / `cluster` / `guards` / `xref` / `func` / `disas`.
3. Only if the offline answer is insufficient, pick **one** address to
   decompile.
4. If any heavy tool is already running and has not answered within a minute or
   two, stop it and continue offline.
5. The disassembler is **optional**. If none is installed, the workflow still
   works end to end.

**Signals you have taken the wrong path:** you started a full/auto analysis; you
asked a tool for an "overview"; you are waiting on a progress bar; you do not
yet know the address you care about.

**Signals the offline tool is being used correctly:** every command names a
specific address; results come back in seconds; you are reading call sites and
bit tests rather than pseudocode.

If the helper reports an error, fix the argument — it prints a one-line reason
and a usage block, never a traceback, so a non-zero exit is not a reason to
switch tools.

---

## 1. Why not just run the disassembler

A full IDA/Ghidra auto-analysis of a game-sized library is the wrong tool for a
targeted question:

* It does not finish in a reasonable time and can consume many GB of database.
* The result is mostly irrelevant — you care about a handful of functions.
* You usually already know one address in the area (a signature you verified,
  or a function you found by string xref), and that is enough to walk the graph
  yourself.

**Use the heavyweight tool when you need its decompiler on one function, not to
"analyze the binary".** Everything below is read-only, offline, and needs only
`numpy` + `capstone`.

### If a disassembler MCP *is* available

Some setups (for example the `rea` MCP server with an IDA provider) can serve
targeted per-function queries. That is genuinely useful for **pseudocode** of a
function you have already located — but it still has to load and analyze the
whole binary before the first answer, so:

* Ask it about **specific functions by address**, never "analyze this binary".
* Prefer headless/attached registration that keeps the database open across
  queries; a cold start per query is unusable at this size.
* Treat the first query as a one-time cost and batch your questions.
* For structure recovery (callers, clusters, masks) the offline method below is
  faster and needs no provider at all. Locate with offline analysis, then use
  the decompiler only where pseudocode actually helps.

## 2. The two facts that make this cheap

**Fact 1: file offset == virtual address.** Bedrock Android links with image
base 0 and the first `PT_LOAD` is 1:1 (`p_offset == p_vaddr`). So an address you
recover from a raw byte scan is directly usable in a disassembler and in a hook,
with no rebasing. Verify once per file:

```python
# program headers: PT_LOAD entries where offset == vaddr
```

**Fact 2: direct calls are decodable in bulk.** An AArch64 `BL` is
`100101 imm26`, and its target is `pc + sign_extend(imm26, 26) * 4`. That means
*every* direct call in the binary can be decoded with integer arithmetic on a
`uint32` array — no disassembler, no symbols, no linear sweep. This single trick
is what turns "find the callers" from a minutes-long job into a sub-second one.

```python
words = np.frombuffer(data, dtype="<u4")
is_bl = (words & 0xFC000000) == 0x94000000
imm26 = (words & 0x03FFFFFF).astype(np.int64)
imm26 = np.where(imm26 >= (1 << 25), imm26 - (1 << 26), imm26)
target = np.arange(words.size) * 4 + imm26 * 4
callers_of_X = np.nonzero(is_bl & (target == X))[0] * 4
```

## 3. The workflow

### Step 1 — anchor

Get **one** address inside the area of interest. Good anchors, best first:

1. A signature you already verified as UNIQUE (see `signature-forensics.md`).
2. A string xref: find the message in `.rodata`, then the `ADRP`+`ADD` that
   references it.
3. An imported/exported symbol from the dynamic symbol table.

Never start by scanning for "something that looks like a block renderer".

### Step 2 — find the family

If the area has several sibling functions (one per cube face, one per packet
type, one per UI state), locate them all the same way. Sibling families are the
best structure in a stripped binary: they have near-identical prologues and are
called from the same small set of places.

### Step 3 — callers, then cluster the callers

```bash
a64.py callers <so> --addrs 0x...          # who calls this
a64.py cluster <so> --addrs 0x...,0x...,   # group their call sites
```

`cluster` sorts all call sites of the whole family and groups those within a
window (~0x900 by default). Each cluster is a **driver**: a function that calls
several members of the family in sequence. On the real target this reduced
74–101 call sites per face down to **12 drivers**, and the two largest were the
per-block geometry drivers. It also reports the enclosing function of each
cluster, giving you the driver's entry point for free.

**Why this matters:** you do not need to understand every caller. The driver is
where the *decision* is made; the other callers are usually special cases.

### Step 4 — read the decisions, not the instructions

Once you have a driver, look at what sits **immediately after each family call**:

```bash
a64.py guards <so> --addrs 0x...
```

On the real target every face call was followed by one or two bit tests:

```asm
bl    #<FaceDown>          ; called unconditionally
tbz   w27, #1, <skip>      ; guard A
tbnz  w8,  #1, <skip>      ; guard B
```

That pattern *is* the design:

* The `bl` is **unconditional** — the family member runs for every invocation,
  visible or not. The guard decides whether surrounding bookkeeping repeats.
* The bit index identifies the family member in a **decision mask**: bit 1 =
  Down, 2 = Up, 3 = North, 4 = South, 5 = West, and East via bit 0 in a second
  register.
* Two different registers (`w8`/`w9`, `w27`) hold related masks.

A recovery like "bit N of register R means face F" is exactly the kind of fact
that is invisible in decompiled pseudocode and obvious in the raw guard pattern.

### Step 5 — read the argument setup

Disassemble backwards from a call site to recover the ABI and the argument
sources:

```bash
a64.py disas <so> --addr 0x... --count 40
```

Look for `mov x0..x4, <reg>` immediately before the call. In the real case this
confirmed the five-argument face ABI and revealed that:

* the position argument is a **stack temporary** built by `scvtf` from integer
  coordinates (so it is a world-space float triple, not a pointer to a block
  position struct);
* the texture argument is the **result of a virtual call** (`ldr x8,[xN];
  ldr x8,[x8,#0x40]; blr x8`) — i.e. per-face textures come from a vtable slot,
  with a selector register.

## 4. Recovering function boundaries without symbols

`.eh_frame_hdr` still carries the unwind table on a stripped binary, so it
gives a complete, sorted list of function ranges. Parse it once and you can ask
"which function contains this address" for any address:

```bash
a64.py func <so> --addr 0x...
```

On the real target this yielded several hundred thousand function records. This
is how you label a call site with its enclosing function without any disassembler
database, and it is what makes the `cluster` output actionable.

Encoding notes: the table is `DW_EH_PE` encoded; `0x03` = `udata4` absolute,
`0x1B` = `sdata4` pcrel. Handle both, and remember the header's own encoding
bytes tell you which.

**A `pcrel` table is relative to the start of the table, not to each field.**
With `table_enc = 0x3b` (sdata4, pcrel), each entry's start and end are
`field_value + table_start`. Decoding them relative to their own position
silently yields ranges that cover only part of `.text`, and then every lookup
reports "no function" — including addresses you know are code. Sanity-check the
decoded set: the first range should start at `.text`, and a function you already
know (a verified signature) must resolve.

## 5. Decode helpers worth having

| What | Encoding | Why |
|---|---|---|
| `BL` / `B` | `100101 imm26` / `000101 imm26` | every direct call, in bulk |
| `TBZ`/`TBNZ` | `0011011 0 b5:5 imm14 Rt` | the decision masks |
| `ADRP` + `ADD` | page + 12-bit imm | data xrefs (strings, vtables) |
| `ADRP` + `LDR` | page + scaled 12-bit imm | vtable / GOT slot access |

For data references, only pair an `ADRP` with the **immediately following**
`ADD`/`LDR` that uses its destination register — otherwise you produce false
xrefs from unrelated register reuse.

## 6. Pitfalls that cost real time

* **A driver is not necessarily the caller you first find.** The biggest cluster
  was not the one holding the visibility mask; both had to be read.
* **"Called unconditionally" is not "always visible".** The guard after a call
  usually gates the *surrounding* work, not the call itself. Do not infer
  visibility semantics from the presence of the call alone — read the guard and
  the mask.
* **An earlier conclusion must be re-derived when the mechanism changes.** A
  design rule that was correct for one layer ("the engine culls before calling
  you, so never re-cull") can be wrong for the same layer on a different build.
  Re-read the guards; do not carry the conclusion forward as folklore.
* **Cluster windows need tuning.** Too small and one driver splits into several
  clusters; too large and unrelated functions merge. 0x900 worked for a driver
  that emits six faces; check the output and adjust.
* **Do not let a slow tool block the investigation.** If the heavyweight path is
  still running after minutes, answer the question offline first; keep the
  heavyweight run for pseudocode of the one function that still puzzles you.
* **Search for the full NUL-terminated literal, not a substring.** Engine
  identifiers are namespaced (`minecraft:geometry.leaves`). Searching for
  `geometry.leaves` finds the substring and returns an address *inside* the
  string; xrefs to that address are zero, and it looks like the string is
  unreferenced. Verify the byte after your match is `\0`.
* **Zero xrefs usually means "referenced from a data table", not "unused".**
  Registry entries are built as `{hash, std::string}` in static initialisers, so
  the name is looked up by a 64-bit hash and never by a direct code reference to
  the literal. Search for the hash instead, or find the initialiser by its
  structure (the `mov`/`movk` hash build followed by `adrp`+`add` to the string).
* **An empty result from a structure scan is a result.** "0 direct callers" for
  a function that is obviously called means it is reached through a vtable or
  callback — which is itself the answer to "how is this dispatched".

## 7. Checklist

- [ ] Confirmed `p_offset == p_vaddr` for the first `PT_LOAD` (addresses are raw).
- [ ] Anchored on a verified signature, string xref, or symbol — not on a guess.
- [ ] Found the whole sibling family before looking for callers.
- [ ] Clustered call sites into drivers and labelled each with its function.
- [ ] Read the guard pattern after each call; recovered register + bit per member.
- [ ] Read the argument setup backwards from a call; recorded the ABI and where
      each argument comes from (register, stack temp, virtual-call result).
- [ ] Parsed `.eh_frame_hdr` for function ranges, and sanity-checked the decoded
      set against a known function.
- [ ] Searched full NUL-terminated literals, including namespace prefixes.
- [ ] Wrote down which conclusions are *observed* and which are *inferred*.
- [ ] Never started a full auto-analysis; every heavy query named one address.
