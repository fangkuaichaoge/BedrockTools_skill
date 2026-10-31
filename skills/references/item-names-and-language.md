# Localized item names and the player's language

Two separate problems that look like one:

1. **Item names** — the player-facing name of a stack, in the player's language, identical to
   what the inventory shows.
2. **Your own labels** — the handful of words your mod prints ("Chest", "Empty"), which should
   follow the same language.

Both were solved on device; the dead ends are recorded because they are easy to repeat.

---

## 1. Item names: let the engine answer

`ItemStackBase::getName()` returns `std::string` **by value** and is **non-virtual** (the class's
vtable has only a destructor pair). So:

* locate it by byte pattern (`scripts/mksig.py <lib> --addr <addr> --len ... --tight`), verify the
  pattern matches exactly once in the build, and
* declare it as a plain function taking `this` first:

```cpp
using ItemDisplayNameFn = std::string (*)(const ItemStackBase*);
ItemDisplayNameFn itemDisplayName = nullptr;   // resolved once at install

std::string nameOf(const ItemStackBase* stack) {
    if (!stack || !itemDisplayName) return {};
    return itemDisplayName(stack);             // sret pointer supplied by the compiler
}
```

On AArch64 a returned `std::string` is built through the **sret pointer in `x8`**; the compiler
supplies it automatically when the declared return type is `std::string`, which is why the
declaration above works even though the engine's symbol is a member function. Confirming this is
cheap: the function prologue starts by saving `x8` (`mov x19, x8`) before saving `x0` (`mov x20, x0`).

If the pattern is unavailable, render icons and counts without names. Falling back to raw
identifiers (`minecraft:diamond_pickaxe`) reads as a bug to players.

### Why not rebuild the name yourself

The name comes from the engine's translation tables (`item.<id>.name`, `tile.<id>.name`, plus
potions and custom names). Reproducing that path means re-implementing `buildDescriptionId`,
custom-name handling and the localization lookup, and staying correct across every item type.
One call does all of it.

## 2. The player's language: read it, do not guess

The engine keeps the selected language in its localization object. A working path, verified by
decompilation:

```
i18n  = I18n::getInstance()                    // Meyers singleton getter, pattern-resolved
holder = *(i18n + 0xa8)                        // shared_ptr<Holder>::ptr, set by setLanguage
loc    = *(holder + 0x08)                       // Localization*
code   = std::string at loc + 0x20              // "en_US", "zh_CN", ...
```

Cross-check available for free: the engine stores a `bool` at `i18n + 0x1e0` that is `0` exactly
for `ja_JP`, `ko_KR`, `zh_CN`, `zh_TW` and `1` otherwise. Requiring the decoded code to agree with
that flag turns "the offset is probably right" into a real check.

Reading the string requires the host layout, since the field belongs to the engine's STL, not
yours:

```cpp
// libc++ on Android: low bit of byte 0 marks the long form
if ((bytes[0] & 1) == 0) { length = bytes[0] >> 1; data = bytes + 1; }         // SSO
else { memcpy(&length, bytes + 8, 8); memcpy(&data, bytes + 16, 8); }          // heap
```

Validate before trusting: length bounded, characters restricted to `[A-Za-z0-9_-]`, and the CJK
flag agreement above. A rejected read costs a fallback to English, not a crash.

**`i18n + 0xa8` is written under a shared lock by the engine.** Reading it without that lock is a
benign race for a display-only feature (a torn read is rejected by the checks), and it avoids
copying an engine-owned `shared_ptr` across STL boundaries, which is the worse failure mode.
Say so in a comment so the next reader does not "fix" it into a crash.

### Alternative that avoids the language entirely

If the only strings you need are a few container names, ask the engine for the name of the thing
instead of translating it yourself: the block's own display name (or `Container::getName()` for a
container) is already localized. Store no language at all.

## 3. Your own labels

Keep a small table indexed by a `UiLanguage` enum, and select it from the decoded code:

```cpp
messageText(UiLanguage::SimplifiedChinese, Message::Chest)  // "箱子"
```

Practical rules:

* one table per message set, with **every** supported language present or absent — never half a
  row;
* unknown or unreadable language → English, and the table is written so that is a valid state;
* re-read the language every few hundred frames rather than once at install: players change it
  from the settings menu without restarting.

## 4. Checklist

1. Item names match the inventory exactly, in a non-English language.
2. Switching the game language changes your labels within a few seconds.
3. A chest with a renamed item shows the custom name.
4. Unknown language / unreadable localization → still renders, in English, without errors.

## Entity names are identity-based, not class-based

Item names come from the stack, entity names do not: many mobs share a base-class RTTI name, so a
name derived from the class name is wrong (and sometimes even translates, which is worse). Go
through the engine's identity data - the actor's definition identifier, then its canonical
identifier string - and let the game translate the key built from it. Try both the vanilla short
form and the namespaced form, treat a translation equal to the key as missing, and cache per actor.
If a single engine function appears to do the whole chain, check its callers first: its out
parameter may be a larger cache record and its options struct may have hidden state.

## Description id and client item identity

Two `Item` virtuals matter for overlays that draw items themselves:

- slot **`0x308`** is consulted first from the name function (custom-name check);
- slot **`0x2f0`** returns the **description id** through an sret `std::string` — for example
  `item.apple.name`; the name function then hands that string to the language table.

Reaching the item behind a stack: `stack + 0x8` is a pointer to a **shared** reference block and
`*(block)` is the `Item*`. Because that block is shared between stacks, never write into it. When a
temporary stack is needed, copy the 152-byte stack and point the copy at a private reference block.

The renderer (`ItemRenderer::renderGuiItemNew`) only draws stacks whose `Item` is client-side, so
identities have to be learned from stacks the game itself renders — hooking that renderer is the
reliable source.
