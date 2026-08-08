# Minecraft Bedrock (PE) `libminecraftpe.so` Hooking Tutorial

> Method source: the open-source **BedrockTools** project (a native mod for
> Minecraft Bedrock on Android). This tutorial teaches the full pipeline:
> feature -> game function -> locate -> analyze -> decide hook strategy ->
> implement -> compile to `.so` -> package -> deploy -> verify.
>
> Everything is machine-agnostic: replace `<libminecraftpe.so>`, `<NDK>`,
> `<mod-dir>` with your real paths. A disassembler (IDA/Ghidra) is **optional**
> — the workflow works without it because BedrockTools already ships a
> signature dictionary for ~80 game functions.

---

## Contents

1. [Background and terms](#1-background-and-terms)
2. [The ecosystem: LeviLauncher -> preloader -> mod](#2-the-ecosystem)
3. [The thinking workflow: feature -> function -> hook](#3-the-thinking-workflow)
4. [Finding the game function](#4-finding-the-game-function)
5. [Signatures: how BedrockTools locates functions](#5-signatures)
6. [Verifying signatures offline (UNIQUE / AMBIGUOUS / MISSING)](#6-verifying-signatures)
7. [Hook strategies](#7-hook-strategies)
8. [Writing the mod: full code templates](#8-writing-the-mod)
9. [Compiling to .so (NDK + CMake)](#9-compiling-to-so)
10. [Packaging and deployment](#10-packaging-and-deployment)
11. [Runtime verification (logcat)](#11-runtime-verification)
12. [Porting to a new game version](#12-porting-to-a-new-version)
13. [Common pitfalls](#13-common-pitfalls)
14. [Appendix: BedrockTools file map](#14-appendix)

---

## 1. Background and terms

| Term | Meaning |
|---|---|
| `libminecraftpe.so` | The core native library of Minecraft Bedrock Android. Nearly all game logic lives here: tick, render, network, UI, weather, time, skin... |
| arm64-v8a | 64-bit ARM ABI, the only target covered here. |
| stripped | Compiled without symbol table; disassembly shows names like `sub_10056C48`. |
| signature | A byte pattern of a function head used to uniquely locate a function in the binary (symbol-free addressing). |
| inline hook | Rewrite the function head to jump to your detour; call the saved original for unchanged behavior. |
| patch | Directly change bytes: constants, branches, or the whole small function. |
| vtable | C++ virtual dispatch table; `obj->virtualFn()` compiles to `vtable[slot](obj, ...)`. |
| preloader | LeviLauncher's C++ loader (`libpreloader.so`): loads mods, provides hook/signature/patch APIs. |
| LeviLauncher | Android Bedrock launcher that loads native mods (LeviLaunchroid ecosystem). |
| `.levipack` | LeviLauncher mod package; a zip containing `manifest.json`, the `.so`, and resources. |

## 2. The ecosystem

### 2.1 Load chain

```
LeviLauncher (Java)
        |
        v
libpreloader.so          scans mods/, validates manifest.json, dlopen(mod .so)
        |
        v
libMyMod.so              exports PLGetModRegistration() -> load/enable/disable/unload
        |
        v
libminecraftpe.so        game loads later; mod hooks dlopen to wait for it
```

### 2.2 Mod lifecycle

`PL_REGISTER_MOD(MyMod, instance)` generates:

```cpp
extern "C" PL_EXPORT pl::mod::ModRegistration* PLGetModRegistration() {
    static auto registration = pl::mod::detail::makeRegistration(instance);
    return &registration;   // {instance, load, enable, disable, unload}
}
```

Your `.so` **must export `PLGetModRegistration`** or the preloader ignores it.

### 2.3 Timing: the mod loads before the game

BedrockTools solves this by hooking `dlopen` (see `src/core/Runtime.cpp`):

```cpp
void* minecraft = dlopen("libminecraftpe.so", RTLD_NOW | RTLD_NOLOAD); // loaded yet?
if (!minecraft) {
    void* libdl = openLibrary("libdl.so");
    void* sym   = (void*)symbol(libdl, "dlopen");
    dlopenHook  = install(sym, (void*)dlopenDetour, (void**)&dlopenOriginal);
}
```

```cpp
void* dlopenDetour(const char* filename, int flags) {
    void* handle = dlopenOriginal ? dlopenOriginal(filename, flags) : nullptr;
    if (handle && filename && strstr(filename, "libminecraftpe.so") && !resolvingFromDlopen) {
        Runtime::get().minecraftLoaded();   // now resolve signatures + install hooks
    }
    return handle;
}
```

`resolvingFromDlopen` is a `thread_local bool` guard preventing recursion.

## 3. The thinking workflow

Before touching code, think in this order (this is how BedrockTools is built):

```
1. Feature:        what behavior does the user want to change?
2. Game function:  which function implements that behavior?
3. Locate:         does BedrockTools already ship a signature for it?
4. Analyze:        what does it return / write / call? (decompile optional)
5. Feasibility:    can we implement it by changing return / arg / constant /
                   object state / virtual dispatch / timing?
6. Hook choice:    inline hook | vtable hook | direct patch
7. Verify:         signature is UNIQUE on the target .so
8. Build & test:   compile, deploy, logcat
```

Feature-to-function mapping (BedrockTools examples):

| Feature | Game function (SignatureId) | Strategy |
|---|---|---|
| Zoom / FOV | `GetFov` | inline hook, modify return |
| Low sensitivity while zoomed | `LocalPlayerApplyTurnDelta` | inline hook, scale input |
| Hide hand while zoomed | `BaseOptionRegistryGetHideItemInHand` | inline hook, force true |
| Fullbright | `Fullbright` | head-replace patch (return max light) |
| Time changer | `Time` / `SetTime` | inline hook, override return/arg |
| Cancel attack | `GameModeAttack` / `SurvivalModeAttack` | inline hook + cancellable event |
| Player tick logic | `NormalTick` | inline hook + event |
| Per-frame logic | `eglSwapBuffers` (libEGL.so) | hook exported symbol |
| Screen open/close | `ContainerScreenControllerOpen/Dtor`, `ChatScreenOpen/Dtor` | inline hook + event |
| Reach / hit result | `LevelGetHitResult`, `HitResultGetEntity` | resolve + call |
| Player name / skin | `ActorGetNameTag`, `ActorSetNameTag` + field offsets | hook / field access |
| Weather / biome | `WeatherTick`, `WeatherIsRaining`, `BiomeGetTemperature` | hook / field access |
| Send packet to server | `LoopbackPacketSenderSendToServer` | inline hook |

## 4. Finding the game function

1. **Use BedrockTools' dictionary first**: `src/core/memory/Signatures.cpp`
   already maps ~80 functions (tick, FOV, weather, time, render, UI, packets,
   attack, actor...). Resolve by id and you are done.
2. **Community knowledge**: search for the function name in BedrockTools,
   modding wikis, or prior mod sources.
3. **Optional disassembler**: if you must find a new function, use IDA/Ghidra
   (any install): find a related string (`Shift+F12`), follow its xref (`X`),
   decompile (`F5`). See `skills/bedrock-pe-modding/references/ida-workflow.md`.

## 5. Signatures

### 5.1 Format

`pl::memory::resolveSignature` takes hex strings:

```
FF 03 01 D1 FD 7B 02 A9 F4 4F 03 A9 FD 83 00 91 ...
```

- 4 bytes per AArch64 instruction, space-separated.
- `?` wildcards one byte.
- Register-encoding bytes (usually the low byte of each instruction) are
  wildcarded; opcode bytes are kept.
- Keep signatures >= 48 bytes when possible (BedrockTools mostly 64).

Example (6-instruction function):

```asm
SXTH  W8, W1
MOV   W9, #0x40
MOV   W1, #0x40
SUB   W8, W8, W0,SXTH
ORR   X0, X9, X8,LSL#32
RET
```

becomes:

```
?? 3C 00 13 ?? 08 80 52 ?? 08 80 52 ?? A1 20 4B ?? 81 08 AA C0 03 5F D6
```

### 5.2 Extracting a signature with a disassembler (optional)

1. Jump to the function head.
2. Copy the first 12-16 instructions' bytes.
3. Wildcard register bytes as above.
4. Search `Alt+B` to check uniqueness, then verify offline (next section).

## 6. Verifying signatures

`skills/bedrock-pe-modding/scripts/verify_signatures.py` parses every
signature from BedrockTools' `Signatures.cpp` and scans the `.so` executable
bytes:

```bash
python scripts/verify_signatures.py <libminecraftpe.so> \
    --sigs <path/to/BedrockTools/src/core/memory/Signatures.cpp> \
    --json sig_report.json
```

Verdicts:

| Verdict | Meaning | Action |
|---|---|---|
| UNIQUE | exactly one match | safe |
| AMBIGUOUS | multiple matches | lengthen / add fixed anchor bytes |
| MISSING | no match | wrong version; re-extract with a disassembler |

Example output line:

```
UNIQUE    VersionString  vaddr=0x<...>  matches=1  bytes=192  fixed=148
```

Run this against your binary. When the game version matches the dictionary's
target version, entries resolve uniquely; in a sample run every signature
reported UNIQUE (0 ambiguous, 0 missing).

## 7. Hook strategies

### 7.1 Inline hook (most common)

Use when you need logic before/after the call, or to modify arguments/return.

Mechanics:
1. Copy the target's head instructions into a trampoline (fix PC-relative
   addressing).
2. Patch the head with a jump to your detour (`LDR X18,#8; BR X18; dest` for
   far jumps).
3. Detour calls the saved original to run untouched logic.

BedrockTools shape:

```cpp
static float (*_getFov_orig)(void*, float, int) = nullptr;

static float _getFov_zoom_hook(void* _this, float a, int enableVariableFOV) {
    float originalFov = _getFov_orig ? _getFov_orig(_this, a, enableVariableFOV) : 0.0f;
    if (g_zoomMod && g_zoomMod->isZoomActive()) {
        return g_zoomMod->m_currentFov;      // modify the return value
    }
    return originalFov;
}

uintptr_t addr = bedrocktools::memory::resolve(SignatureId::GetFov);
bedrocktools::hooks::install((void*)addr, (void*)_getFov_zoom_hook, (void**)&_getFov_orig);
```

### 7.2 Head replacement (function becomes a constant)

```cpp
uint8_t patch[12] = {
    0x40, 0x8F, 0xA8, 0x52,   // MOV W0, #0x7F4
    0x00, 0x00, 0x27, 0x1E,   // FMOV S0, W0
    0xC0, 0x03, 0x5F, 0xD6    // RET
};
memcpy(m_originalBytes, m_patchTarget, 12);   // backup first!
bedrocktools::sdk::patchMemory(m_patchTarget, patch, 12);
```

### 7.3 Constant / branch patches

```cpp
std::array<uint8_t,4> expected{0x09,0x08,0x80,0x52};  // MOV W9,#0x40
std::array<uint8_t,4> replacement{0xE9,0x7C,0x80,0x52}; // MOV W9,#0x3E7 (999)
auto cur = pl::memory::readBytes(addr, 4);
if (cur == replacement) return;          // already patched
if (cur != expected) { log("version mismatch"); return; }  // safe fail
pl::memory::writeBytes(addr, replacement, "my_patch");
```

Common patches: `NOP` (`1F 20 03 D5`) a skip branch; unconditional `B`
to force a path; `MOV W0,#1; RET` to force true.

### 7.4 Vtable hooks

```cpp
template <class Return, class... Args>
Return virtualCall(void* instance, std::size_t index, Args&&... args) {
    auto table = *reinterpret_cast<void***>(instance);
    auto fn = reinterpret_cast<Return(*)(void*, Args...)>(table[index]);
    return fn(instance, std::forward<Args>(args)...);
}
```

Slot constants live in `include/bedrocktools/sdk/offsets/Core.hpp`
(`ClientInstance_getRegion = 31`, `ClientInstanceGetMinecraftGame = 83`, ...).
To find a slot by RTTI name:
`pl::memory::resolveVtableFunction("9CameraAPI", slot, "libminecraftpe.so")`.

### 7.5 Hook a library import (e.g. EGL for per-frame)

```cpp
auto egl = bedrocktools::hooks::openLibrary("libEGL.so");
auto address = bedrocktools::hooks::symbol(egl, "eglSwapBuffers");
bedrocktools::hooks::install((void*)address, (void*)swapBuffersDetour, (void**)&swapBuffersOriginal);
bedrocktools::hooks::closeLibrary(egl);
```

## 8. Writing the mod

### 8.1 Project layout

```
<mod-dir>/
├── CMakeLists.txt
├── manifest.json
├── vendor/preloader-android/      # preloader SDK
└── src/main.cpp
```

### 8.2 main.cpp (hook example: append to the version string)

```cpp
#include <string>
#include "pl/Gloss.h"
#include "pl/Mod.hpp"
#include "pl/Logger.hpp"
#include "pl/memory/Signature.hpp"
#include "pl/memory/Hook.hpp"

namespace {
using VersionStringFn = std::string(*)(void*);
VersionStringFn versionOriginal = nullptr;

std::string versionDetour(void* self) {
    std::string version = versionOriginal ? versionOriginal(self) : std::string{};
    return version + " | MyMod v1.0.0";
}
}  // namespace

class MyMod {
public:
    bool load() {
        GlossInit(true);
        // Pattern from BedrockTools Signatures.cpp, SignatureId::VersionString
        const char* pattern =
            "FF 03 01 D1 FD 7B 02 A9 F4 4F 03 A9 FD 83 00 91 54 D0 3B D5 F3 03 08 AA "
            "88 16 40 F9 A8 83 1F F8 E8 03 00 91 ? ? ? ? ? ? ? ? 42 DC 28 91 E0 03 00 91 "
            "E1 03 1F AA 23 00 80 52 ? ? ? ? 08 08 40 F9 00 00 C0 3D 1F 00 00 F9 1F FC 00 A9";
        uintptr_t addr = pl::memory::resolveSignature(pattern, "libminecraftpe.so");
        auto& logger = pl::log::Logger::getOrCreate("MyMod");
        if (!addr) {
            logger.error("VersionString signature not found - version mismatch");
            return false;
        }
        logger.info("VersionString resolved at {:#x}", addr);
        if (pl::memory::hook((void*)addr, (void*)versionDetour, (void**)&versionOriginal) != 0) {
            logger.error("hook failed");
            return false;
        }
        return true;
    }
};

static MyMod mod;
PL_REGISTER_MOD(MyMod, mod)
```

### 8.3 main.cpp (patch example)

```cpp
#include <array>
#include "pl/Gloss.h"
#include "pl/Mod.hpp"
#include "pl/Logger.hpp"
#include "pl/memory/Patch.hpp"
#include "pl/memory/Signature.hpp"

class MyMod {
public:
    bool load() {
        GlossInit(true);
        const char* pattern = "FD 7B BF A9 FD 03 00 91 08 00 40 F9 A1 06 80 52 ..."; // example
        uintptr_t addr = pl::memory::resolveSignature(pattern, "libminecraftpe.so");
        if (!addr) return false;

        std::array<uint8_t, 4> expected{0x09, 0x08, 0x80, 0x52};
        std::array<uint8_t, 4> replacement{0xE9, 0x7C, 0x80, 0x52};
        auto cur = pl::memory::readBytes(addr, 4);
        if (cur.size() != 4) return false;
        if (cur == replacement) return true;                       // already patched
        if (cur != expected) {
            pl::log::Logger::getOrCreate("MyMod").error("version mismatch at {:#x}", addr);
            return false;                                          // safe fail
        }
        return pl::memory::writeBytes(addr, replacement, "my_patch");
    }
};

static MyMod mod;
PL_REGISTER_MOD(MyMod, mod)
```

## 9. Compiling to .so

### 9.1 CMakeLists.txt

```cmake
cmake_minimum_required(VERSION 3.22)
project(MyMod LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -O2 -fvisibility=hidden -ffunction-sections -fdata-sections -w")
set(CMAKE_SHARED_LINKER_FLAGS "${CMAKE_SHARED_LINKER_FLAGS} -Wl,--gc-sections,--strip-all -s")

set(VENDOR_DIR "${CMAKE_CURRENT_SOURCE_DIR}/vendor")
add_subdirectory(${VENDOR_DIR}/preloader-android)

add_library(MyMod SHARED src/main.cpp)
target_link_libraries(MyMod PRIVATE preloader log)
target_include_directories(MyMod PRIVATE ${VENDOR_DIR}/preloader-android/src)
```

### 9.2 Build (Linux/macOS)

```bash
export ANDROID_NDK_HOME=/path/to/android-ndk-r29
cmake -S . -B build -G Ninja \
  "-DCMAKE_TOOLCHAIN_FILE=$ANDROID_NDK_HOME/build/cmake/android.toolchain.cmake" \
  "-DCMAKE_MAKE_PROGRAM=$(which ninja)" \
  -DANDROID_ABI=arm64-v8a \
  -DANDROID_PLATFORM=android-24 \
  -DCMAKE_BUILD_TYPE=Release
cmake --build build -j 8
```

### 9.3 Build (Windows PowerShell)

```powershell
$ndk = "C:\path\to\android-ndk-r29"              # adjust
$cmake = "C:\path\to\cmake\bin\cmake.exe"         # adjust
$ninja = "C:\path\to\ninja.exe"                   # adjust

& $cmake -S . -B build -G Ninja `
  "-DCMAKE_TOOLCHAIN_FILE=$ndk\build\cmake\android.toolchain.cmake" `
  "-DCMAKE_MAKE_PROGRAM=$ninja" `
  -DANDROID_ABI=arm64-v8a -DANDROID_PLATFORM=android-24 -DCMAKE_BUILD_TYPE=Release
& $cmake --build build -j 8
```

Output: `build/libMyMod.so`.

### 9.4 Validate the artifact (required)

```bash
file build/libMyMod.so                              # ARM aarch64
readelf --dyn-syms --wide build/libMyMod.so | grep PLGetModRegistration
```

Machine code must be `0xb7` (AArch64); the dynamic symbols must include
`PLGetModRegistration`.

### 9.5 xmake (BedrockTools' own build)

```bash
xmake f -y -p android -a arm64-v8a -m release --ndk=/path/to/android-ndk-r28c
xmake -y
```

`after_build` in BedrockTools' `xmake.lua` automatically packages the
`.levipack`.

## 10. Packaging and deployment

### 10.1 Folder deployment

```
mods/
└── MyMod/
    ├── manifest.json
    └── libMyMod.so
```

`manifest.json`:

```json
{
  "type": "preload-native",
  "name": "MyMod",
  "author": "you",
  "version": "1.0.0",
  "entry": "libMyMod.so"
}
```

The preloader validates this file; a missing/invalid manifest is the most
common reason "nothing happened".

### 10.2 .levipack

A zip with at least `manifest.json` and `libMyMod.so`. BedrockTools'
`scripts/package_levipack.py` automates this and verifies the result.

## 11. Runtime verification

```bash
adb logcat -s Preloader:* MyMod:* GlossHook:* -v time
```

Success looks like:

```
Preloader: loaded mod MyMod
Preloader: enable MyMod
MyMod:     VersionString resolved at 0x<...>
MyMod:     hook installed
```

Then confirm the in-game effect.

## 12. Porting to a new version

1. Extract the new `libminecraftpe.so` from the new APK.
2. Re-run the verifier; UNIQUE signatures stay, AMBIGUOUS get lengthened,
   MISSING get re-extracted (optional disassembler).
3. Re-check member offsets and vtable slots with a disassembler.
4. Check for PAC/BTI changes in function heads.
5. Rebuild, redeploy, re-verify via logcat.

See `skills/bedrock-pe-modding/references/version-porting.md` for the full
checklist.

## 13. Common pitfalls

1. Missing `GlossInit(true)` -> hooks silently fail.
2. Resolving before the game library loads -> 0; use the dlopen hook.
3. Ambiguous or short signatures -> hooking the wrong function and crashing.
4. Patching without verifying original bytes -> memory corruption on mismatch.
5. Forgetting `__builtin___clear_cache` after writes (arm64).
6. `mprotect` not page-aligned.
7. Bad `manifest.json` -> preloader refuses to load.
8. `PLGetModRegistration` stripped by `--gc-sections` -> keep it exported.
9. Multiple mods hooking the same function -> use `pl::memory::hook`
   priorities instead of raw GlossHook.

## 14. Appendix

BedrockTools file map:

```
BedrockTools-main/
├── xmake.lua                                  # build config (preloader dep, arm64 flags)
├── src/main.cpp                               # PL_REGISTER_MOD entry
├── src/core/
│   ├── Runtime.cpp                            # dlopen probe, signature resolve, install flow
│   ├── GameHooks.cpp                          # 9 signature hooks + EGL hook (event sources)
│   ├── Api.cpp                                # BedrockTools_GetApi (third-party mod ABI)
│   └── memory/
│       ├── Hooks.hpp                          # hooks::install/remove thin wrapper
│       └── Signatures.cpp                     # the signature dictionary (~80 functions)
├── include/bedrocktools/
│   ├── sdk/Memory.hpp                         # patchMemory / field / virtualCall
│   ├── sdk/Functions.hpp                      # signature address -> typed function pointer
│   ├── sdk/offsets/*.hpp                      # member offsets + vtable slots
│   ├── events/*.hpp                           # typed event system
│   └── Api.hpp                                # ApiV1 ABI definition
└── scripts/package_levipack.py                # .levipack packaging + verification
```

Companion skill: `skills/bedrock-pe-modding/SKILL.md` and its references.
