# Building and Deploying a Mod .so

Generic, machine-agnostic instructions. Replace `<NDK>`, `<cmake>`, `<ninja>`,
`<mod-dir>` with the actual paths on your machine.

## 1. Required toolchain

- Android NDK (r28/r29 work; r28c is what BedrockTools documents)
- CMake >= 3.22 with Ninja (or xmake for the BedrockTools-style build)
- Python 3 (for packaging scripts)
- The preloader SDK source (`preloader-android`) available locally or fetched
  by the build (BedrockTools pulls it as an xmake package; the CMake template
  below uses a vendored copy)

Typical environment variables:

```bash
export ANDROID_NDK_HOME=/path/to/android-ndk-r29
export ANDROID_SDK_ROOT=/path/to/android-sdk
```

## 2. Minimal project layout

```
<mod-dir>/
├── CMakeLists.txt
├── manifest.json
├── vendor/preloader-android/        # copy of the preloader SDK
└── src/main.cpp
```

### CMakeLists.txt (pattern used by LeviLauncher mods; same shape as the
BedrockTools ecosystem)

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

### manifest.json (the preloader validates this; a missing/invalid file means
the mod is silently not loaded)

```json
{
  "type": "preload-native",
  "name": "MyMod",
  "author": "you",
  "version": "1.0.0",
  "entry": "libMyMod.so"
}
```

## 3. Configure and build

```bash
cmake -S . -B build -G Ninja \
  "-DCMAKE_TOOLCHAIN_FILE=$ANDROID_NDK_HOME/build/cmake/android.toolchain.cmake" \
  "-DCMAKE_MAKE_PROGRAM=$(which ninja)" \
  -DANDROID_ABI=arm64-v8a \
  -DANDROID_PLATFORM=android-24 \
  -DCMAKE_BUILD_TYPE=Release

cmake --build build -j 8
```

Output: `build/libMyMod.so` (arm64-v8a).

PowerShell equivalent:

```powershell
& $cmake -S . -B build -G Ninja `
  "-DCMAKE_TOOLCHAIN_FILE=$ndk\build\cmake\android.toolchain.cmake" `
  "-DCMAKE_MAKE_PROGRAM=$ninja" `
  -DANDROID_ABI=arm64-v8a -DANDROID_PLATFORM=android-24 -DCMAKE_BUILD_TYPE=Release
& $cmake --build build -j 8
```

## 4. Validate the artifact (required)

Check with any ELF tool (`readelf -h`, `file`, or a small Python script):

```python
import struct
data = open("build/libMyMod.so", "rb").read()
assert data[:4] == b"\x7fELF"
print("machine:", hex(struct.unpack_from("<H", data, 18)[0]))  # 0xb7 = AArch64
```

Also confirm the dynamic export table contains `PLGetModRegistration`
(`readelf --dyn-syms --wide build/libMyMod.so | grep PLGetModRegistration`).
The preloader finds the mod entry through `dlsym(handle, "PLGetModRegistration")`;
without it the mod does nothing.

## 5. xmake build (BedrockTools' official setup)

BedrockTools uses xmake; its `xmake.lua` pulls the `preloader` package and
runs `scripts/package_levipack.py` after build:

```bash
xmake f -y -p android -a arm64-v8a -m release --ndk=/path/to/android-ndk-r28c
xmake -y
```

Notes:
- `add_requires("preloader")` fetches preloader-android from GitHub; for
  offline builds, cache the package first.
- Release flags in BedrockTools' `xmake.lua`: `-fPIC -Oz -ffunction-sections
  -fdata-sections -flto -fno-rtti -fvisibility=hidden`, linker
  `-Wl,--gc-sections -Wl,--icf=all -Wl,--hash-style=gnu`.

## 5b. Link and export discipline for a shipped mod

Flags worth copying from a project that ships one `.so` against a closed binary:

- **Export only the host's lifecycle symbols.** A version script whose `global:` lists exactly
  the entries the host looks up, and `local: *;` for everything else, so the binary cannot leak
  symbols into the process.
- **Hide the rest at compile time**: `-fvisibility=hidden`, and `-fno-rtti` where nothing needs
  it. Exception support is a separate decision — a detour that throws aborts the process, so
  keep throwing and catching out of hook paths regardless.
- **`-Wl,--no-undefined`**: a missing dependency then fails the link instead of the device load.
- **16 KB page alignment** (`-Wl,-z,max-page-size=16384`) for current Android, plus
  `--build-id=sha1` so the artifact is identifiable in a crash report.
- **Strip release builds**, but keep the unstripped artifact for symbolication; a debug variant
  with symbols is what lets a device crash be attributed.

## 6. Packaging

Two options:

**Folder layout (simplest):**

```
mods/
└── MyMod/
    ├── manifest.json
    └── libMyMod.so
```

**`.levipack` zip** (what BedrockTools generates):

```
manifest.json
libMyMod.so
icon.png            (optional)
resources/...       (optional)
```

BedrockTools' `scripts/package_levipack.py` reads Name/Author/Description/
Version from `include/bedrocktools/Version.hpp`, builds the manifest, zips,
then re-reads the zip to verify entries, manifest content, and file sizes.

### Rules that keep a package honest

* **Stamp the version into the binary at build time**, and have the packager refuse to package
  when the binary does not contain the manifest version. One substring search catches the most
  common release mistake: the manifest moved on, the `.so` did not.
* **Write to a temporary archive, reopen and validate it, then replace the output.** A truncated
  zip is silently unusable, and a partially written file is worse than a failed build.
* **Set the overwrite flags deliberately.** Most hosts default to *not* replacing existing
  files; list only the entries that really must replace on upgrade. Leaving them empty means an
  update can silently keep old assets.

## 7. Deployment

Copy the mod folder (manifest + .so) into the launcher's `mods/` directory and
start the game through LeviLauncher.

## 8. Runtime verification (logcat)

```bash
adb logcat -s Preloader:* MyMod:* GlossHook:* -v time
```

Expected:

```
Preloader: loaded mod MyMod
Preloader: enable MyMod
MyMod:     VersionString resolved at 0x<...>
MyMod:     hook installed
```

Then confirm the in-game effect (e.g. the version string is rewritten, or the
patched behavior works).
