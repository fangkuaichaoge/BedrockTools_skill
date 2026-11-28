# Native mod code patterns: structure, plumbing, guards

The four studied implementations are small because their code is shaped, not because they do
little. This reference collects the code-level patterns they share: how to lay out the project, how
to keep raw-memory access in one place, how to write a detour that cannot leak state, and which
C++ habits keep the hot path safe. It complements the technique references; nothing here names a
specific game build.

## 1. Project layout that stays testable

```
src/core/*.hpp        pure logic, no platform or engine headers, host-testable
src/native/*.hpp/.cpp the ONLY units that touch the game image (profile, at, fn, vfn, exec)
src/feature/*.hpp     feature logic on top of interfaces, no raw memory helpers
src/lifecycle.cpp     import/export, gate, hook table, wiring
tests/                host tests for core; SDK stubs for units that include platform headers
tools/                signature generation, binary verification, packaging, checks
```

Rules that pay off:

- A pure header (math, parsing, layout, state machine) can be compiled and tested on the host in
  milliseconds. Anything that needs the game image goes through an interface the test can fake.
- Exactly one translation unit owns the address table and the read/write helpers. Every other
  unit calls typed wrappers, so a wrong offset is one edit, not a search.
- Feature units never include platform or engine headers; they take small structs and callbacks.
  This is what lets a lifecycle unit be tested with stubbed SDK headers.

## 2. Lifecycle skeleton

```cpp
class Mod final {
public:
    static Mod &instance();
    bool load(const pl::mod::ModContext &ctx);   // prepare, gate, create dirs
    bool enable();                               // register UI, install hooks
    bool disable();                              // invalidate state, remove UI
    bool unload();                               // release, unhook
};
PL_REGISTER_MOD(Mod, Mod::instance())
```

- `load`: read config, capture only the values you are allowed to keep, run the build gate, and
  fail closed before touching anything. Do not install hooks here unless the host guarantees the
  image is mapped and settled.
- `enable`: publish UI, install hooks/patches, start workers. Every failure unwinds what was
  already done.
- `disable`: flip the active flag first, invalidate sessions, unregister UI, stop workers.
- `unload`: release handles; if the library is linked non-deletable, only clear a liveness flag.
- Wrap every callback body in `try/catch (...)`; an exception crossing the loader or the game is a
  crash, not an error.

## 3. The only raw-memory file

```cpp
inline std::uintptr_t base();                       // module base, resolved once
template <class T> T at(const void *p, std::size_t off);   // memcpy read, never a cast store
template <class F> F fn(Entry e);                   // typed function pointer from the profile
template <class F> F vfn(const void *obj, int slot);       // vtable slot, null-safe
inline bool in_code(std::uintptr_t address);        // inside an executable segment
inline bool valid_vec(Vec v);                       // finite and in world range
```

Patterns:

- The profile is a `constexpr` array of `{name, rva, pattern}`; `fn()` looks up by name, `vfn()`
  reads the vtable pointer then the slot and refuses anything outside executable code.
- `at<T>` copies bytes into a local; it never binds a reference to a guessed layout. Callers
  validate pointers and values before use.
- One `resolve()` runs at load: base + range check + full pattern compare, or nothing. There is no
  runtime scan and no "try the next candidate" path.

## 4. Hook table with rollback

```cpp
struct HookSpec {
    const char *name;
    void *target;
    void *detour;
    void **original;
    bool installed{};
};

bool install_all(std::span<HookSpec> hooks);   // stop at first failure
void remove_all(std::span<HookSpec> hooks);    // reverse order, idempotent
```

Detour shape:

```cpp
static Ret detour(Args... args) {
    Ret result{};
    if (original) result = original(args...);      // always first
    if (!g_active.load(std::memory_order_acquire)) return result;
    try {
        observe(args...);                          // read-only or append-only
    } catch (...) {
        report_once("observe");                    // no exception escapes
    }
    return result;
}
```

- Head-replacement patches are different: read the current bytes, compare with the expected
  original, apply, and record the handle for revert. Never write without the comparison.
- `remove_all` runs in reverse install order so a later hook never sees a half-removed chain.

## 5. RAII guards: restore in the destructor

```cpp
struct ColourGuard {
    void *a{}, *b{};
    Colours old_a{}, old_b{};
    explicit ColourGuard(void *a_, void *b_);      // snapshot both
    ~ColourGuard();                               // restore both, main then dark
};

struct MatrixGuard {
    void *owner{};
    void *top{};
    ~MatrixGuard();                               // pop through the guard
};

struct ScopedCurrent {
    T *previous;
    explicit ScopedCurrent(T *next) : previous(current) { current = next; }
    ~ScopedCurrent() { current = previous; }
};
```

- Guard objects live on the stack; early returns and exceptions still unwind them.
- Restore in reverse order of acquisition; never restore only the first of a pair.
- A guard that captured a null target must be a no-op, not a crash.

## 6. Identity checks and fail-open shape

```cpp
if (!client || vtable_of(client) != base() + kClientVtableRva) { report_once("client"); return; }
if (!in_code(target)) return;
if (!std::isfinite(v.x) || std::abs(v.x) > kWorldLimit) return;
```

- Identify an object by its vtable **address point** first; only then read fields or call slots.
- Re-derive scene pointers each frame/sample; do not cache an engine pointer across frames.
- Keep one deduplicated error line per failure (`lastFailure` compare) so logs stay readable.
- On any doubt: return the original result and draw nothing. A missing feature is recoverable.

## 7. Threading idioms

- Flags the hot path reads: `std::atomic<bool>` with acquire/release; data: one mutex.
- Tick side never waits for IO: `std::unique_lock lock(mutex, std::try_to_lock)` and skip the frame
  when the lock is busy.
- Worker side: condition variable for "work available", timer for polling, IO outside the lock,
  results moved in under a short lock.
- Register process-wide callbacks once (`std::once_flag`); the host often has no unregister, so
  gate behaviour on your own flag instead.
- Shutdown order: stop and join workers **before** removing hooks; flush final state.
- Snapshot shared state under the lock, release, then call host/engine APIs — never the reverse.

## 8. One settings table

```cpp
struct SettingDef {
    const char *key;
    const char *title;
    SettingType type;
    const char *default_value;
    float min, max;
};
constexpr SettingDef kSettings[] = { /* one row per setting */ };
```

- `defaults()` derives the persisted map from the table (buttons excluded).
- `schema()` emits the host's control tree from the same rows; add a setting in one place.
- `apply_values()` parses, clamps to `[min,max]`, writes the canonical string back, and falls back
  to the default on invalid input.
- Persistence: `"key" "value"` lines, unknown keys dropped, missing keys defaulted,
  save through temp file + rename.
- Migration: a marker file forces a one-time default change without touching other values.

## 9. Protocol and text

- Tuple protocol: one action word, then strictly parsed fields, then the payload text as the rest.
- Parse numbers with `from_chars`/classic locale and reject trailing characters; colours as six hex
  digits plus forced alpha; ids as unsigned decimal; booleans as `"1"`/`"0"` (accept `true`/`false`).
- Replies are hand-built JSON with an escape helper (`"`, `\`, control chars) and a fixed error
  shape; never string-concatenate unescaped user text.
- JNI strings are modified UTF-8: they cannot carry supplementary characters. Move documents as
  byte arrays, or convert UTF-16 ↔ UTF-8 yourself with length bounds.

## 10. C++ habits worth keeping

- `std::string_view` in, owning type out; `[[nodiscard]]` on lookups; `std::optional` for
  "not available" instead of sentinel pointers.
- `std::exchange` for one-shot actions; `try_emplace` for caches; `std::clamp` for every user
  value; `std::array` + `constexpr` for tables.
- `static_assert` on every structure shared with the engine (size, alignment, offsets) — it is the
  cheapest ABI check in the project.
- Designated initialisers for command/config structs, so a new field cannot silently shift others.
- Export discipline: `-fvisibility=hidden` plus a version script that keeps exactly the host entry
  point global; link with `--no-undefined`, and use `-z nodelete` when a game thread may still be
  inside a detour at unload.
- Hot path: no allocation, no file IO, no locks held across engine calls; reserve vectors once and
  reuse them.

## 11. Tests that catch real mod bugs

- Host-test the pure core: exact vertex counts for known inputs, winding, byte counts, layout
  round-trips, migrations, protocol parse errors, clamp behaviour, edge cases (NaN, empty,
  out-of-range) as first-class tests.
- Stub the SDK headers for host builds of lifecycle/worker units; single test binary per concern.
- Assert contracts rather than implementation: "unique hit at the expected address", "exactly N
  vertices", "packed archive equals the build output".
- Package self-audit: build the archive, reopen it, compare entries, hashes and the manifest.
- State explicitly what was not tested (device, GPU lifetime, WebView) in the release notes.

## 12. Checklist

1. One raw-memory unit; every other unit is testable on the host.
2. Profile resolved once; gate fails closed; no runtime scanning.
3. Hook table installs all-or-nothing and removes in reverse order.
4. Detours call the original first, never throw, and gate on an atomic flag.
5. Guards restore matrix/colour/current state in destructors, in reverse order.
6. Every object read is vtable-identified; every call target is exec-range-checked.
7. Failure is one deduplicated log line; the feature draws/does nothing.
8. Settings come from one table; persistence is temp+rename; values are clamped.
9. Tick never blocks on IO; workers joined before unhook.
10. Protocol input is strictly parsed; output is escaped; JNI text moves as bytes.
11. `static_assert` the layouts you share with the engine; export only the host entry.
12. Host tests assert exact counts and contracts; packaging verifies itself.