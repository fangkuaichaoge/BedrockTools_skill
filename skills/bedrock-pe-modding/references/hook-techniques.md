# Hook Techniques (from BedrockTools + preloader SDK)

## 1. Signatures: locating functions without symbols

`libminecraftpe.so` is a stripped arm64 ELF. BedrockTools stores a
**byte pattern of the function head** for every function it uses, and resolves
it at runtime by scanning `.text`.

### 1.1 Pattern format

`pl::memory::resolveSignature` accepts hex strings like
`"FF 03 01 D1 FD 7B 02 A9 ..."`; `?` (or `??`) wildcards one byte. The
dictionary lives in BedrockTools:

```
src/core/memory/Signatures.cpp
```

Example (start of `VersionString`):

```
FF 03 01 D1 FD 7B 02 A9 F4 4F 03 A9 FD 83 00 91 ...
```

### 1.2 Why function heads work as signatures

arm64 functions begin with a fixed prologue. Register allocation can change
between builds, so BedrockTools wildcards the **register-encoding byte**
(the low byte of each 4-byte instruction) and keeps the opcode bytes:

```
?? 3C 00 13   ; SXTH W8, W1  (register byte wildcarded)
?? 08 80 52   ; MOV W9, #0x40
```

Rules:
- 4 bytes per AArch64 instruction, space-separated.
- Keep the opcode bytes (high 3); wildcard the low register byte when it can
  vary.
- Prefer patterns >= 48 bytes (BedrockTools mostly uses 64, some 20-32).
- The pattern must be UNIQUE in the target `.so`; ambiguous patterns must be
  lengthened.

### 1.3 Runtime resolution

The preloader scans the `.text` section of the loaded library
(`pl/memory/Signature.cpp`). With imagebase 0, the in-memory address equals
the file offset, so an offline scan of the `.so` file gives the same result
as the runtime scan (run `scripts/verify_signatures.py`).

## 2. Inline hooks (detours)

### 2.1 Engine: GlossHook

The preloader bundles GlossHook (`pl/Gloss.h`, static lib `libGlossHook.a`):

- `GlossInit(true)` — must be called once before any hooking (true also
  initializes linker-hook capability).
- `GlossHook(addr, new_func, &old_func)` — writes a trampoline at the target
  head; `old_func` becomes a callable copy of the original.
- `GlossHookAddrByName(lib, offset, ...)` — hook by library offset (waits for
  the library to load).
- `GlossHookByName(lib, sym, ...)` / `GlossPltHook(lib, sym, ...)` — hook by
  symbol name / PLT.
- `GlossGotHook(got_addr, new_func, &old_func)` — rewrite a GOT entry.
- `GlossHookDisable/Enable/Delete`, `GlossHookGetOldFunc`, `GlossHookReplaceNewFunc`.
- Instruction helpers: `MakeArm64B`, `MakeArm64BL`, `MakeArm64NOP`,
  `MakeArm64RET`, `MakeArm64AbsoluteJump` (emits `LDR X18,#8; BR X18; dest`).

Inline hook mechanics on arm64:

1. Copy the first instructions of the target (enough to fit a jump) into
   private memory (the trampoline), fixing any PC-relative instructions
   (ADRP / literal loads).
2. Patch the function head with a jump to your detour (near: `B +-128MB`;
   far: `LDR X18,#8; BR X18; <detour addr>`).
3. Your detour calls the saved original pointer to run the untouched logic.

### 2.2 Preloader wrapper: pl::memory::hook

`pl::memory::hook(target, detour, &original, priority)` adds a **multi-mod
hook chain** on top of GlossHook:

- `HookPriority`: Highest=0, High=100, Normal=200, Low=300, Lowest=400
  (lower runs first).
- `unhook(target, detour)` removes one link and rebuilds the chain.
- RAII alternative: `pl::memory::HookHandle`.

BedrockTools wrapper (`src/core/memory/Hooks.hpp`):

```cpp
using Handle = State*;
inline Handle install(void* target, void* detour, void** original) {
    if (!target || !detour) return nullptr;
    if (pl::memory::hook(target, detour, original) != 0) return nullptr;
    auto* state = new (std::nothrow) State{target, detour};
    if (state) return state;
    pl::memory::unhook(target, detour);
    return nullptr;
}
inline void remove(Handle hook) { pl::memory::unhook(hook->target, hook->detour); delete hook; }
```

### 2.3 Canonical hook shape

```cpp
static std::string (*versionOriginal)(void*) = nullptr;

static std::string versionDetour(void* self) {
    std::string version = versionOriginal ? versionOriginal(self) : std::string{};
    return "MyMod v1.0 " + version;
}

// install (usually in onInit / load):
uintptr_t addr = bedrocktools::memory::resolve(SignatureId::VersionString);
bedrocktools::hooks::install((void*)addr, (void*)versionDetour, (void**)&versionOriginal);
```

Notes:
- The detour calling convention must match the target (arm64: args X0...,
  return X0/W0/S0).
- Keep `orig` pointers as file-scope statics; never store them in locals.
- Check `resolve()` != 0 before hooking; skip safely on failure.

### 2.4 BedrockTools' hook set (`src/core/GameHooks.cpp`)

```cpp
using VersionStringFn = std::string(*)(void*);
using NormalTickFn    = void(*)(void*);
using AttackFn        = bool(*)(void*, void*, void*, void*);
using ClientInstanceUpdateFn = void*(*)(void*, bool);

hookSignature(SignatureId::VersionString,             versionDetour,        &versionOriginal);
hookSignature(SignatureId::NormalTick,                tickDetour,           &tickOriginal);
hookSignature(SignatureId::GameModeAttack,            gameModeAttackDetour, &gameModeAttackOriginal);
hookSignature(SignatureId::SurvivalModeAttack,        survivalModeAttackDetour, &survivalModeAttackOriginal);
hookSignature(SignatureId::ClientInstanceUpdate,      clientUpdateDetour,   &clientUpdateOriginal);
hookSignature(SignatureId::ContainerScreenControllerOpen, containerOpenDetour,  &containerOpenOriginal);
hookSignature(SignatureId::ContainerScreenControllerDtor, containerCloseDetour, &containerCloseOriginal);
hookSignature(SignatureId::ChatScreenOpen,            chatOpenDetour,       &chatOpenOriginal);
hookSignature(SignatureId::ChatScreenDtor,            chatCloseDetour,      &chatCloseOriginal);
hookEgl();   // eglSwapBuffers
```

`hookEgl()` is the template for hooking an external library export:

```cpp
auto egl = bedrocktools::hooks::openLibrary("libEGL.so");
const auto address = bedrocktools::hooks::symbol(egl, "eglSwapBuffers");
auto handle = bedrocktools::hooks::install((void*)address, (void*)swapBuffersDetour, (void**)&swapBuffersOriginal);
bedrocktools::hooks::closeLibrary(egl);
```

### 2.5 dlopen hook (waiting for the game library)

In `Runtime::load()`:

```cpp
void* minecraft = dlopen("libminecraftpe.so", RTLD_NOW | RTLD_NOLOAD); // already loaded?
if (!minecraft) {
    void* libdl = openLibrary("libdl.so");
    void* sym   = (void*)symbol(libdl, "dlopen");
    dlopenHook  = install(sym, (void*)dlopenDetour, (void**)&dlopenOriginal);
}
```

`dlopenDetour` checks `strstr(filename, "libminecraftpe.so")` and uses a
`thread_local bool resolvingFromDlopen` guard to avoid recursion.

## 3. Memory patching

### 3.1 Head replacement (Fullbright example)

Replace the first 12 bytes of the target with "return a constant":

```cpp
uint8_t patch[12] = {
    0x40, 0x8F, 0xA8, 0x52,   // MOV W0, #0x7F4
    0x00, 0x00, 0x27, 0x1E,   // FMOV S0, W0
    0xC0, 0x03, 0x5F, 0xD6    // RET
};
memcpy(m_originalBytes, m_patchTarget, 12);   // backup first
bedrocktools::sdk::patchMemory(m_patchTarget, patch, 12);
```

`sdk::patchMemory` (`include/bedrocktools/sdk/Memory.hpp`):

```cpp
mprotect(pageStart, size, PROT_READ|PROT_WRITE|PROT_EXEC);  // temporary RWX
memcpy(address, data, size);
__builtin___clear_cache(address, address+size);             // flush icache (required on arm64)
mprotect(pageStart, size, PROT_READ|PROT_EXEC);             // restore RX
```

The preloader equivalent is `pl::memory::writeBytes(addr, bytes, name)` /
`readBytes` / `revertPatch(name)`, which also records original bytes for
rollback.

### 3.2 Constant rewriting

`MOV W9, #0x40` (64) -> `MOV W9, #0x3E7` (999):

```cpp
// original {0x09,0x08,0x80,0x52} -> patch {0xE9,0x7C,0x80,0x52}
out[0] = 0xE0 | (current[0] & 0x1F);   // keep register number
memcpy(out+1, patch+1, 3);             // new immediate encoding
```

Rule: verify current bytes == expected original first; skip if already
patched; skip + log on mismatch (version safety).

### 3.3 NOP / branch rewriting

| Goal | Original | Patch |
|---|---|---|
| Skip a branch | `B.EQ ...` / `TBZ W8,#0,...` | `NOP` (`1F 20 03 D5`) |
| Force a path | `CMP X9,#...; B.NE ...` | unconditional `B +0x70` |
| Force a boolean | function head | `MOV W0,#1` (`20 00 80 52`) + `RET` (`C0 03 5F D6`) |

## 4. Vtable / virtual calls

### 4.1 Calling a virtual method

```cpp
template <class Return, class... Args>
Return virtualCall(void* instance, std::size_t index, Args&&... args) {
    auto table = *reinterpret_cast<void***>(instance);   // vtable pointer at object start
    auto fn = reinterpret_cast<Return(*)(void*, Args...)>(table[index]);
    return fn(instance, std::forward<Args>(args)...);
}
```

BedrockTools vtable slot constants live in
`include/bedrocktools/sdk/offsets/Core.hpp`:

```cpp
namespace VTable {
inline constexpr std::size_t ClientInstance_getRegion = 31;
inline constexpr std::size_t ClientInstanceGetMinecraftGame = 83;
inline constexpr std::size_t MinecraftUIRenderContextDrawText = 6;
}
```

### 4.2 Resolving a vtable slot from RTTI type name

`pl::memory::resolveVtableFunction("9CameraAPI", slot, "libminecraftpe.so")`:

1. Find the type-name string in `.rodata`.
2. Find the `typeinfo` object referencing it in `.data.rel.ro`.
3. Find the primary vtable (offset-to-top == 0) and read the slot.

## 5. Event bus (BedrockTools' event-driven design)

`include/bedrocktools/events/EventBus.hpp`:

- `subscribe<Event>(cb, priority)` / `subscribeRaw(EventType, cb, priority)`;
  `unsubscribe(id)`.
- `publish(event)` snapshots listeners, then calls them; events deriving from
  `Cancellable` can be `event.cancel()`.
- Events are structs with `static constexpr EventType type`.

Third-party mods subscribe through the runtime ABI without compile-time
dependency:

```cpp
#include <bedrocktools/BedrockTools.hpp>
bedrocktools::events::RuntimeListener<bedrocktools::events::LocalPlayerTickEvent> listener(
    [](auto& e) { if (e.player) { auto p = e.player->position(); } });
```

## 6. Field offsets

Read object state at fixed offsets (`sdk::field<T>` or direct pointer math):

```cpp
std::string* name = (std::string*)((uintptr_t)player + offsets::Player::mName);   // 2824
void* skin = *(void**)((uintptr_t)player + offsets::Player::mSkin);               // 2552
```

BedrockTools organizes offsets by class in
`include/bedrocktools/sdk/offsets/*.hpp` (World, Render, Network, UI, Skin,
Core). Offsets are version-specific; obtain them by decompiling the
corresponding function and reading `this + 0xNNN` accesses (see
`references/ida-workflow.md`).

## 7. Runtime ABI for other mods (BedrockTools_GetApi)

`include/bedrocktools/Api.hpp` defines ABI v1:

```cpp
struct ApiV1 {
    uint32_t abiVersion;
    uint32_t structSize;
    uintptr_t (*resolveSignature)(uint16_t id);
    sdk::ClientInstance* (*clientInstance)();
    uint64_t (*subscribe)(EventType, EventPriority, EventCallback, void*);
    void (*unsubscribe)(uint64_t);
};
```

Consumers `dlopen("libBedrockTools.so", RTLD_NOLOAD)` +
`dlsym("BedrockTools_GetApi")` to reuse BedrockTools' signature resolution
and event stream.

## 8. Common pitfalls

- Missing `GlossInit(true)` -> hooks silently fail.
- Resolving before `libminecraftpe.so` is loaded -> returns 0; use the dlopen
  hook or `RTLD_NOLOAD` probe.
- Patching without verifying original bytes -> corrupts memory on version
  mismatch. Always read-then-verify-then-write.
- Forgetting `__builtin___clear_cache` after writes on arm64.
- `mprotect` must be page-aligned: `pageStart = addr & ~(pagesize-1)`.
- Multiple mods hooking the same function: use `pl::memory::hook` priorities,
  not raw GlossHook calls that overwrite each other.

## 9. Library-boundary hooks (detouring a platform library)

When the effect you want is expressible in terms of a **platform API** (graphics,
audio, input, file I/O) rather than game data, detour that library instead of
resolving game functions. You gain version tolerance (no signatures) and lose
access to engine-level objects - accept that trade knowingly.

Practical requirements:

1. **Cover every resolution path.** A symbol can arrive via a static import
   (branch through the loader stub), a lazy `dlsym`-style lookup, or the API's
   own "get proc address" query, which may hand out a *driver* address different
   from the exported stub. Detour the exported body **and** wrap the
   proc-address dispatcher so dynamically fetched pointers also come back as
   your detour. Then re-scan the libraries the target actually resolved against
   (the one your own module links is not necessarily the one the target uses).
2. **Install deferred, idempotently.** The library may not be loaded when your
   mod enables. Use a short watcher that retries, processes each library once,
   and never double-patches an address. Track "fully processed" **per library**:
   a mod that itself links a sibling library will otherwise report success from
   its own dependency while the target's real library is still unpatched.
3. **Never drop a call.** If a detour is reached before its original pointer was
   captured, resolve it lazily and, failing that, execute the resolved export
   rather than returning early. Interception layers must fail open.
4. **Prove arrival.** Log the first real call of one entry point that must run
   constantly (a canary). "Hooks installed" is not evidence; arrival is.
5. **Beware entry-point families.** The interesting calls may not be the
   statically imported ones (instanced/indirect variants are often resolved by
   name at runtime). Enumerate the API family from the binary's imports *and*
   its string table, and hook the union - see `so-analysis.md` §8.
6. **Restore state you touch.** If the API has cached state (bindings, masks,
   modes), save and restore exactly, because the target assumes it is unchanged.

For what to do with those hooks once installed (shader-text injection, extra
passes, geometry caveats, brightness budgets), see
`render-pipeline-hooks.md` and `shader-source-injection.md`.

