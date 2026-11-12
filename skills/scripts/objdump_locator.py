"""Portable llvm-objdump discovery for the disassembly-based helper scripts.

Resolution order, first hit wins:

  1. $LLVM_OBJDUMP              -- explicit override, always respected
  2. PATH                       -- 'llvm-objdump' / 'llvm-objdump.exe'
  3. $ANDROID_NDK_HOME, $ANDROID_NDK_ROOT, $NDK_HOME
  4. $ANDROID_SDK_ROOT/ndk/*, $ANDROID_HOME/ndk/*   (newest version first)
  5. the NDK bundled with a CMake install, if any is on PATH

Nothing here is machine-specific: every candidate comes from the environment.
Import `find_objdump()`; it raises SystemExit with a message that names each
variable the caller can set.

    from objdump_locator import find_objdump
    OBJDUMP = find_objdump()

A module-level `OBJDUMP_CANDIDATES` list is also exported for scripts that
prefer to probe lazily on first use.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

_EXE = "llvm-objdump.exe" if os.name == "nt" else "llvm-objdump"
_ENV_NAMES = ("LLVM_OBJDUMP", "ANDROID_NDK_HOME", "ANDROID_NDK_ROOT", "NDK_HOME",
              "ANDROID_SDK_ROOT", "ANDROID_HOME")


def _ndk_bin_dirs() -> list[Path]:
    """Every plausible <ndk>/toolchains/llvm/prebuilt/<host>/bin under the SDK."""
    host_tags = ["windows-x86_64", "linux-x86_64", "darwin-x86_64"]
    out: list[Path] = []
    for var in ("ANDROID_NDK_HOME", "ANDROID_NDK_ROOT", "NDK_HOME"):
        root = os.environ.get(var)
        if root:
            for tag in host_tags:
                out.append(Path(root) / "toolchains" / "llvm" / "prebuilt" / tag / "bin")
    for var in ("ANDROID_SDK_ROOT", "ANDROID_HOME"):
        sdk = os.environ.get(var)
        if not sdk:
            continue
        ndk_root = Path(sdk) / "ndk"
        if not ndk_root.is_dir():
            continue
        # newest NDK first
        versions = sorted((p for p in ndk_root.iterdir() if p.is_dir()),
                          key=lambda p: p.name, reverse=True)
        for ver in versions:
            for tag in host_tags:
                out.append(ver / "toolchains" / "llvm" / "prebuilt" / tag / "bin")
    return out


def candidates() -> list[Path]:
    """Candidate paths in resolution order; existing ones are not filtered out."""
    out: list[Path] = []
    explicit = os.environ.get("LLVM_OBJDUMP")
    if explicit:
        out.append(Path(explicit))
    out.extend(Path(p) for p in _ndk_bin_dirs())
    return out


# Backwards-compatible name for scripts written against the older helper shape.
OBJDUMP_CANDIDATES = candidates()


def find_objdump() -> str:
    """Return a usable llvm-objdump path, or exit with actionable guidance."""
    explicit = os.environ.get("LLVM_OBJDUMP")
    if explicit:
        path = Path(explicit)
        if path.is_file():
            return str(path)
        raise SystemExit(
            f"LLVM_OBJDUMP is set to {explicit} but that file does not exist")

    found = shutil.which("llvm-objdump") or shutil.which("llvm-objdump.exe")
    if found:
        return found

    for candidate in _ndk_bin_dirs():
        exe = candidate / _EXE
        if exe.is_file():
            return str(exe)

    raise SystemExit(
        "llvm-objdump not found.\n"
        "Set one of:\n"
        "  LLVM_OBJDUMP=/path/to/llvm-objdump        (most direct)\n"
        "  ANDROID_NDK_HOME=/path/to/android-ndk     (SDK-provided NDK)\n"
        "  ANDROID_SDK_ROOT=/path/to/Android/Sdk     (NDK under <sdk>/ndk/*)\n"
        "or put llvm-objdump on PATH.")


if __name__ == "__main__":
    print(find_objdump())
