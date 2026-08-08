# Porting to a New Game Version

Every game update can change function addresses, member offsets, and vtable
slots in `libminecraftpe.so`. Byte signatures (machine-code patterns) are more
stable but must always be re-verified.

## Porting checklist

1. **Get the new `.so`**: extract `lib/arm64-v8a/libminecraftpe.so` from the
   new APK.
2. **Identify the version**: scan `.rodata` for the version string (e.g.
   `<game-version>`), or hook `VersionString` and read its return value.
3. **Re-verify all signatures**:

   ```bash
   python scripts/verify_signatures.py <new.so> \
       --sigs <BedrockTools .../src/core/memory/Signatures.cpp> \
       --json new_report.json
   ```

   - UNIQUE -> signature still valid.
   - AMBIGUOUS -> lengthen / add fixed anchor bytes.
   - MISSING -> re-extract the signature for that function from the new
     binary (optional disassembler, see `references/ida-workflow.md`).
4. **Re-verify offsets**: struct layouts change. Decompile the corresponding
   functions and re-read `this + 0xNNN` accesses; update
   `include/bedrocktools/sdk/offsets/*.hpp`.
5. **Re-verify vtable slots**: check `vtable[idx]` indirect calls in the new
   decompilation.
6. **Check hardening changes**: newer builds may add PAC/BTI prologues
   (`BTI C`, `PACIBSP`) or change the function head; inline-hook trampolines
   then need to handle return-address signing, or the signature's first 4
   bytes will MISSING.
7. **Rebuild, deploy, test**: rebuild the `.so`, deploy with the manifest,
   and confirm every hook/patch via logcat.
8. **Record the result**: keep a per-version signature report (e.g.
   `signatures-<version>.json`) and document offset changes.

## Experience rules

- Wildcarding register-encoding bytes is more stable than wildcarding
  immediates.
- Avoid baking BL call offsets into signatures (high ambiguity).
- Patches must verify original bytes before writing; on mismatch the mod
  should skip safely, not corrupt memory.
- Constant patches (e.g. 64 -> 999) usually survive updates; struct offsets
  and vtable slots are the most volatile.
