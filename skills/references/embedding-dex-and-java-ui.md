# Embedding a DEX and Hosting Custom Android Views from a Native Mod

How to put compiled Java/Kotlin classes **inside** a native mod `.so`, load them
at runtime without touching the filesystem, and drive them from C++ — including
the case where those classes host their own Android `View` tree (`WebView` for
HTML, or a `View`/`Canvas` surface) on top of the game Activity.

This is a mechanism reference. It says how the plumbing works and what breaks it.
It deliberately does **not** prescribe how the resulting panel should look —
that is a design decision for whoever builds the mod.

Read `levilauncher-native-mod-guide.md` first for the host contract (manifest,
lifecycle, `ModContext`). Everything below assumes you already have a
`JavaVM*` from `ModContext`.

---

## 1. Why this shape

A `preload-native` mod runs **inside the launcher/game process**, which already
has a live ART VM, an application `ClassLoader` chain and a real `Activity`.
That means:

* no `JNI_OnLoad` and no `JNI_GetCreatedJavaVMs` in the mod — the launcher's own
  `libpreloader.so` did that and handed the `JavaVM*` to `ModContext`;
* compiled Java can be loaded at runtime from bytes, so the mod stays a single
  `.so` (no side-car `.dex` file to lose during reinstall);
* the classes can reach launcher classes (their parent loader is the launcher's),
  and can attach `View`s to the live Activity decor tree.

The cost: everything is JNI, every call can throw, and the Activity can be
destroyed at any time. §5 and §6 are about those two hazards.

---

## 2. Build pipeline: Java → DEX → byte array → `.so` → ByteBuffer

### 2.1 Compile and dex

```bash
javac --release 8 -encoding UTF-8 -Xlint:-options \
      -classpath <SDK>/platforms/android-<API>/android.jar \
      -d build/classes $(find java -name '*.java')

d8 --release --min-api 26 \
   --lib <SDK>/platforms/android-<API>/android.jar \
   --output build/dex $(find build/classes -name '*.class')
# -> build/dex/<module>.dex
```

Notes that matter:

* `--min-api 26` because `InMemoryDexClassLoader` only exists from API 26. If you
  need to support older versions you need a different loading strategy (§8).
* `--release` is required: debug builds carry extra metadata and are needlessly
  large.
* `d8` lives in `<SDK>/build-tools/<ver>/`. On Windows invoke it through `cmd /c`
  — it is a `.bat` wrapper and will not run directly from every shell.
* `javac --release 8` produces class files d8 fully understands; newer targets
  work but add nothing here.

### 2.2 Turn the dex into a C array

Generate a header rather than shipping the `.dex` as a file. A ~10-line script
is enough; the point is that the committed tree contains only text:

```python
# scripts/gen_payloads.py  (sketch)
data = open("build/dex/<module>.dex", "rb").read()
assert data[:4] == b"dex\n", "not a dex file"

def arr(name, blob, ctype):
    body = "\n".join(
        "    " + ", ".join(f"0x{b:02x}" for b in blob[i:i+16]) + ","
        for i in range(0, len(blob), 16))
    return (f"inline constexpr {ctype} {name}[] = {{\n{body}\n}};\n")

hdr = ("// generated - do not edit\n#pragma once\n#include <cstddef>\n"
       "namespace payload {\n\n"
       + arr("dexBytes", data, "unsigned char")
       + f"\ninline constexpr std::size_t dexBytesSize = {len(data)};\n}}\n")
open("src/dex_payload.h", "w").write(hdr)
```

Then `#include "dex_payload.h"` from the C++ source. The array lands in
`.rodata` and is part of the `.so`; nothing is written to disk at runtime.

**Verify the embed in the built artifact**, because a wrong offset or a truncated
array fails at runtime with a confusing `ClassNotFoundException`:

```python
blob = open("lib<mod>.so", "rb").read()
off = blob.find(b"dex\n038")
size = struct.unpack_from("<I", blob, off + 32)[0]   # dex header file_size
assert off >= 0 and off + size <= len(blob)
```

The dex header's `file_size` field (offset 32) must match, and the whole range
must be inside the file.

### 2.3 What not to do

* Do not `mmap`/`fopen` a `.dex` you unpacked next to the mod — reinstall paths
  and scoped storage will lose it.
* Do not try to `dlopen` a dex; it is not a native library.
* `RegisterNatives` is optional: use it when the Java side must call back into C++ (the class-shape
  variant in §11 does exactly that). Otherwise keep the direction **native → Java** (§7).

---

## 3. Loading the DEX at runtime

The chain is fixed; each step is one JNI call and each can fail.

```
JavaVM* (from ModContext)
  → GetEnv / AttachCurrentThread
  → obtain a parent ClassLoader
  → NewDirectByteBuffer(dexBytes, dexBytesSize)
  → InMemoryDexClassLoader(ByteBuffer, parentLoader)
  → ClassLoader.loadClass("pkg.Class")
  → NewGlobalRef + cache jmethodIDs
```

### 3.1 Getting a parent `ClassLoader`

`FindClass` from a mod on a non-Java thread only searches the *system* loader,
which will not see launcher classes. Capture a real loader instead, in this
order of preference:

```cpp
// A) A launcher class that is guaranteed to exist; its loader is the app loader.
jclass state = env->FindClass("org/<launcher>/.../SomeLauncherClass");
// B) Generic fallback: the current Application's context.
jclass at  = env->FindClass("android/app/ActivityThread");
jmethodID cur = env->GetStaticMethodID(at, "currentApplication",
                                       "()Landroid/app/Application;");
jobject app = env->CallStaticObjectMethod(at, cur);
jclass ctx  = env->FindClass("android/content/Context");
jmethodID gcl = env->GetMethodID(ctx, "getClassLoader",
                                 "()Ljava/lang/ClassLoader;");
jobject parent = env->CallObjectMethod(app, gcl);
```

Both are `FindClass`-based, so this step must run on a thread whose `FindClass`
can see those classes — in practice the thread the launcher calls your lifecycle
on, or any thread after the Application is fully up. Try A, fall back to B, and
**treat "no parent loader" as a normal not-ready condition**, not an error: the
mod may be enabled before the Activity exists.

### 3.2 The dex itself

```cpp
jobject buf = env->NewDirectByteBuffer(
        const_cast<unsigned char *>(payload::dexBytes),
        static_cast<jlong>(payload::dexBytesSize));   // zero copy, read-only source
```

`NewDirectByteBuffer` over the `.rodata` array is the whole trick: the dex is
never copied, never written, and `InMemoryDexClassLoader` reads it in place.
A heap `jbyteArray` would also work but requires `ByteBuffer.wrap` through a
Java hop, and older ART variants are stricter about needing a **direct** buffer.

```cpp
jclass loaderCls = env->FindClass("dalvik/system/InMemoryDexClassLoader");
jmethodID ctor = env->GetMethodID(
        loaderCls, "<init>",
        "(Ljava/nio/ByteBuffer;Ljava/lang/ClassLoader;)V");
jobject dexLoader = env->NewObject(loaderCls, ctor, buf, parent);
```

### 3.3 Loading your class

```cpp
jclass clCls = env->FindClass("java/lang/ClassLoader");
jmethodID loadClass = env->GetMethodID(
        clCls, "loadClass", "(Ljava/lang/String;)Ljava/lang/Class;");
jstring name = env->NewStringUTF("pkg.mod.Bridge");
jobject loaded = env->CallObjectMethod(dexLoader, loadClass, name);
jclass bridge = static_cast<jclass>(env->NewGlobalRef(loaded));
```

The resulting loader is a **child** of the parent: your classes can see launcher
and framework classes, the launcher cannot see yours. That asymmetry is usually
what you want, and it is also why step 3.1 must succeed before your class can
`Class.forName` anything launcher-side.

### 3.4 Cache the method IDs

Resolve every entry point once, right after `loadClass`, and fail the whole
bridge if any is missing:

```cpp
mStart = env->GetStaticMethodID(bridge, "start", "()V");
mStop  = env->GetStaticMethodID(bridge, "stop",  "()V");
if (!mStart || !mStop) { /* release + report */ }
```

Per-call `GetStaticMethodID` is a lookup in the class's method table every time;
cache them and null them on unload.

### 3.5 Wrap every step

```cpp
bool checkExcept(JNIEnv *e, const char *step) {
    if (!e->ExceptionCheck()) return true;
    e->ExceptionClear();
    LOGE("java exception at: %s", step);   // name the step, or debugging is blind
    return false;
}
```

A pending exception makes **every** subsequent JNI call return null/undefined.
Clearing it and naming the failed step is the difference between a five-minute
fix and an afternoon.

### 3.6 Thread discipline

`JNIEnv*` is per-thread. Do not cache it. Attach on demand and detach when you
were the one who attached:

```cpp
struct ScopedEnv {
    JavaVM *vm; JNIEnv *e = nullptr; bool attached = false;
    explicit ScopedEnv(JavaVM *v) : vm(v) {
        if (!vm) return;
        jint rc = vm->GetEnv(reinterpret_cast<void **>(&e), JNI_VERSION_1_6);
        if (rc == JNI_OK) return;
        if (rc == JNI_EDETACHED)
            attached = (vm->AttachCurrentThread(&e, nullptr) == JNI_OK);
        if (!attached) e = nullptr;
    }
    ~ScopedEnv() { if (attached) vm->DetachCurrentThread(); }
    explicit operator bool() const { return e != nullptr; }
};
```

`PushLocalFrame(n)` / `PopLocalFrame(nullptr)` around a long exchange keeps the
local reference table from filling up when you resolve a dozen classes.

---

## 4. Driving Java from C++

### 4.1 Strings: use `byte[]`, not `NewStringUTF`

`NewStringUTF` takes **modified UTF-8**, which cannot represent 4-byte (astral)
sequences and silently corrupts them. If the payload is a document — HTML,
JSON, user text — pass raw bytes and decode Java-side:

```cpp
// C++ side
jbyteArray arr = env->NewByteArray(static_cast<jsize>(len));
env->SetByteArrayRegion(arr, 0, static_cast<jsize>(len),
                        reinterpret_cast<const jbyte *>(data));
env->CallStaticVoidMethod(cls, mid, arr);   // mid: "([B)V"
env->DeleteLocalRef(arr);

// Java side
public static void setHtml(byte[] utf8) {
    String s = new String(utf8, StandardCharsets.UTF_8);
}
```

Only use `NewStringUTF` for short ASCII identifiers (class names, keys, ids).

### 4.2 Call shape

```cpp
void callVoid(jmethodID id, const char *step) {
    if (!id || !mClass) return;
    ScopedEnv env(mVm);
    if (!env) return;
    env.e->CallStaticVoidMethod(mClass, id);
    checkExcept(env.e, step);
}
```

Balance every `NewByteArray`/`NewStringUTF`/`NewObject` with a
`DeleteLocalRef` unless it lives inside a `PushLocalFrame`. A global ref
(`NewGlobalRef`) is the only thing that survives past the frame — keep exactly
one for the class, and delete it in `unload()`.

---

## 5. Hosting a `View` tree on the game Activity

If your dex classes draw their own UI, they need an `Activity` and a place to
attach. Two independent problems: **finding** the Activity, and **surviving** it
being replaced.

### 5.1 Resolving the current Activity

Prefer a launcher class that already tracks it; fall back to reflection. Both
paths must be wrapped — a renamed launcher class must downgrade to the fallback,
not crash.

```java
private static Activity resolveActivity() {
    Activity a = fromLauncherState();
    return a != null ? a : fromActivityThread();
}
```

* **from a launcher state class**: `Class.forName(...)` → static accessor →
  check `!isFinishing() && !isDestroyed()`.
* **from the framework**: `ActivityThread.currentActivityThread()` →
  reflect the `mActivities` map → for each record, reflect `activity` → first
  live one.

Whatever you get, validity-check it every time you use it. Between two calls the
user can have left the game.

### 5.2 The attach-once design is wrong

Minecraft's Activity is destroyed and recreated (leaving a world, returning from
the background, configuration change). Every `View` you added lives in the old
decor tree and becomes unreachable. The overlay silently stops drawing and no
exception is thrown anywhere.

Use a **main-thread watchdog** instead of building once:

```java
private static final Handler MAIN = new Handler(Looper.getMainLooper());
private static boolean ticking;

private static void startWatchdog() {
    if (ticking) return;
    ticking = true;
    attemptAttach();                       // immediate: game may already be up
    MAIN.postDelayed(new Runnable() {
        @Override public void run() {
            if (!wanted) { ticking = false; return; }
            attemptAttach();
            MAIN.postDelayed(this, 1000L);
        }
    }, 1000L);
}

private static void attemptAttach() {
    Activity a = resolveActivity();
    if (a == null) { status = "waiting for activity"; return; }
    boolean alive = a == lastActivity.get()
            && root != null && root.getParent() != null;
    if (alive) return;
    rebuild(a);                            // activity is new, or the tree died
}
```

`rebuild()` must `removeView` the old host and `destroy()` any `WebView` before
creating new ones, or you leak an ever-growing chain of dead view trees.

The 1 s cadence is a deliberate cost/benefit choice; one reflective call per
second is cheap, and a missed re-attach is a silently dead feature. Stop the
loop when the mod is disabled.

### 5.3 Attaching

```java
View decor = activity.getWindow().getDecorView();
if (!(decor instanceof FrameLayout)) { status = "decor is not a FrameLayout"; return; }
FrameLayout host = new FrameLayout(activity);
((FrameLayout) decor).addView(host, new FrameLayout.LayoutParams(
        ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
```

Always type-check the decor view. Adding the host as a full-screen
`MATCH_PARENT` child and positioning children inside it means you never have to
recompute absolute screen coordinates, and `removeView(host)` tears down
everything at once.

### 5.4 Touch, drag and hit-testing

If you make anything draggable, three things are needed or it will feel broken:

**Slop.** Decide "tap vs drag" by accumulated distance, not by the first move:

```java
float dx = e.getRawX() - downX, dy = e.getRawY() - downY;
if (!dragging && Math.abs(dx) + Math.abs(dy) > slopPx) dragging = true;
```

Use `getRawX/getRawY` (screen space), not `getX/getY` (parent space) — otherwise
a drag inside a container that is itself moving gives the wrong delta.

**Gravity conversion.** If the view is positioned with `Gravity.CENTER`, its
`leftMargin/topMargin` are meaningless. On the first `ACTION_DOWN`, convert to
absolute coordinates from the measured position:

```java
if (lp.gravity != (Gravity.TOP | Gravity.START)) {
    lp.gravity = Gravity.TOP | Gravity.START;
    lp.leftMargin = view.getLeft();   // already measured
    lp.topMargin  = view.getTop();
    view.setLayoutParams(lp);
}
```

**Clamp and re-clamp.** Clamp the drag to the parent bounds, and re-clamp on
layout change — a remembered position can be out of bounds after a rotation or
an Activity rebuild:

```java
host.addOnLayoutChangeListener((v,l,t,r,b,ol,ot,or,ob) -> reClamp());
```

### 5.5 The `WebView`

```java
WebView web = new WebView(activity);
WebSettings s = web.getSettings();
s.setJavaScriptEnabled(true);
web.addJavascriptInterface(new Bridge(), "yourname");   // exposed as window.yourname
web.setWebViewClient(new WebViewClient() {
    @Override public void onPageFinished(WebView v, String url) { onLoaded(); }
});
web.loadDataWithBaseURL("file:///android_asset/", html, "text/html", "utf-8", null);
```

* Content comes from a `String` (your embedded payload), so `loadDataWithBaseURL`
  with a dummy base and `"utf-8"` is right. Do **not** use `loadData` — it needs
  URL-encoding and mangles non-ASCII.
* Keep `setAllowFileAccess(false)` / `setAllowContentAccess(false)` unless you
  genuinely need local files; the base URL above is only there to satisfy
  relative resolution.
* `addJavascriptInterface` methods run on a background thread — marshal to the
  main thread with `Handler.post` before touching any `View`.
* `removeJavascriptInterface(name)` + `destroy()` when tearing down.

**Timing gotcha that looks like a state bug:** `evaluateJavascript` executes
against the *loaded* document. Pushing state right after `loadDataWithBaseURL`
lands in the previous (or empty) document, so a page that replaces a placeholder
from a JS callback keeps showing it forever. Gate the push on `onPageFinished`:

```java
private static boolean pageReady;
private static String lastLine;

private static void pushStatus() {
    if (web == null) return;
    lastLine = computeLine();
    if (!pageReady) return;            // onPageFinished will replay it
    web.evaluateJavascript("window.onNative && window.onNative('" + esc(lastLine) + "')", null);
}

private static void onLoaded() {
    pageReady = true;
    pushStatus();
    MAIN.postDelayed(DexBridge::pushStatus, 250L);  // page script may attach later
}
```

Give the page a polling fallback too (an `info()` method on the JS interface),
so a missed push degrades to a second-late update instead of a frozen label.

### 5.6 A `View` with `onDraw` as an alternative surface

A `View` subclass that draws in `onDraw` needs no browser and no HTML. The
technical essentials only:

```java
public final class Surface extends View {
    private final Paint fill = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint stroke = new Paint(Paint.ANTI_ALIAS_FLAG);

    public Surface(Context c) {
        super(c);
        stroke.setStyle(Paint.Style.STROKE);   // same Paint object, different modes
        setWillNotDraw(false);                 // required for a plain View subclass
    }

    @Override protected void onDraw(Canvas c) {
        super.onDraw(c);
        float w = getWidth(), h = getHeight();
        if (w <= 0 || h <= 0) return;          // not measured yet
        // ... primitives ...
    }
}
```

Rules that keep it correct:

* **Density.** Convert every dimension with
  `TypedValue.applyDimension(COMPLEX_UNIT_DIP, v, res.getDisplayMetrics())`
  once, in the constructor. Raw pixels look wrong on every other device.
* **Animation.** `invalidate()` for a one-shot redraw; `postInvalidateOnAnimation()`
  from a timer/threshold for a frame loop. **Stop the loop when the view is
  hidden** (`setVisibility(GONE)`), otherwise it keeps invalidating behind
  whatever is on top:
  ```java
  public void setAnimating(boolean on) {
      if (animating == on) return;
      animating = on;
      if (on) postInvalidateOnAnimation();
  }
  ```
  and drive the per-frame values from `SystemClock.uptimeMillis()`, never from a
  counter that assumes a fixed frame rate.
* **Text.** Set `Paint.setTextSize` in density-scaled units and re-set the
  `Typeface` if you mix normal/bold; `drawText`'s `y` is the **baseline**, not the
  top of the glyph.
* **Overflow.** A row-based layout (`y` accumulated per row) is easy to reason
  about and easy to audit; check the total against the view height before you
  ship, because a 2 dp overflow is invisible in code and visible on device.
* **Touch.** Same rules as §5.4: raw coordinates, slop, clamp.
* **`onSaveInstanceState` is not involved** — this is an overlay, not a screen.
  Position/state that must survive belongs in your own config, not the view.

Both surfaces can coexist in one panel: put the `WebView` and the `View` in the
same `FrameLayout` and toggle `VISIBLE`/`GONE`, remembering to stop the `View`'s
frame loop when it is hidden. This is useful when one surface is good at text
and layout while the other needs per-frame drawing.

---

## 6. Lifecycle

```cpp
bool load(ModContext &ctx) {
    mBridge.setVm(ctx.javaVm());          // cheap, must not touch Java yet
    return true;
}
bool enable(ModContext &) {
    if (!mBridge.ensureReady()) {         // may be too early: not an error
        LOGW("java bridge not ready");
        return true;                      // fail open; never block the launcher
    }
    mBridge.start();
    return true;
}
bool disable(ModContext &) { mBridge.stop(); return true; }
bool unload(ModContext &) {
    mBridge.stop();
    mBridge.release();                    // DeleteGlobalRef + null every id
    return true;
}
```

* `load()` runs before the game image is necessarily ready — store the `JavaVM*`,
  do not call into Java.
* `enable()` may still be too early for the Activity. The watchdog in §5.2 is
  what recovers; returning `true` here is correct.
* `unload()` must drop the global ref and null the cached IDs. A dangling
  `jclass` after the loader is gone is a use-after-free.
* Never let a C++ exception cross a lifecycle boundary.

---

## 7. Direction of calls

Keep it **native → Java**:

```
C++  --CallStatic*Method-->  dex class  --new View/WebView-->  Activity
```

A dex loaded through `InMemoryDexClassLoader` does not need to call back into
C++ for this pattern. If you do need Java → native, `RegisterNatives` from the
mod's `JNI_OnLoad` (or `Java_...` exports) works, but it adds a second ABI to
keep in sync and is unnecessary for a UI/loading feature.

---

## 8. Alternatives and when they apply

| approach | need | tradeoff |
|---|---|---|
| `InMemoryDexClassLoader` over a direct buffer | API 26+ | zero-copy, single-file mod; nothing on disk |
| `PathClassLoader` / `DexClassLoader` on a file | any | needs a writable path + the file to survive reinstall |
| `ByteBuffer.wrap(byte[])` | API 26+ | one extra Java hop; some ART variants are strict about direct buffers |
| no dex at all — launcher draw commands | see `engine-ui-buttons-and-overlays.md` | no Java UI, but no JNI and no dex to embed |
| `AAssetManager` for assets | asset must be in the APK | not available to a mod's own files |

If a panel can be built with the launcher's own HUD/draw-command API, prefer it:
it is far less machinery. Reach for the dex route when you need real Android
widgets (`EditText`, `WebView`), a Java networking/audio SDK, or per-frame
`Canvas` drawing.

---

## 9. Checklist

Before shipping:

- [ ] `javac --release 8` + `d8 --release --min-api 26`, and the dex header's
      `file_size` matches the array length.
- [ ] The built `.so` really contains the dex (`find(b"dex\n038")` + bounds
      check) and exports the lifecycle symbol.
- [ ] One `NewGlobalRef` for the class, deleted in `unload()`; every cached
      `jmethodID` nulled there too.
- [ ] Every JNI step is followed by `ExceptionCheck` with a **named** step.
- [ ] Document bytes go over `byte[]`, not `NewStringUTF`.
- [ ] `JNIEnv*` never cached; attach/detach paired.
- [ ] Activity resolved on every use and validity-checked; the overlay rebuilds
      on `rebuild()` rather than assuming the first attach survives.
- [ ] Old host `removeView`'d and `WebView` `destroy()`'d before rebuilding.
- [ ] `WebView` JS callbacks marshal to the main thread; a `View` frame loop
      stops when hidden.
- [ ] State pushes are gated on `onPageFinished`, with a polling fallback.
- [ ] Drag: raw coordinates, slop threshold, clamped, re-clamped on layout change.
- [ ] `load()` touches no Java; `enable()` returning `true` while the bridge is
      not ready is deliberate, not a bug.
- [ ] Release build has no `-g`, and the mod still loads after a reinstall
      (`overwrite_files` covers the `.so` and any shipped page).

---

## 10. Symptom → cause

| symptom | cause |
|---|---|
| mod loads, nothing appears, no exception | bridge built before the Activity existed and never retried (no watchdog) |
| overlay appears then dies after changing world | Activity recreated; view tree is dead (§5.2) |
| `ClassNotFoundException` for your own class | dex truncated/wrong offset, or `loadClass` name mismatched with the package |
| `ClassNotFoundException`/`NoClassDefFoundError` for a launcher class | parent loader step failed; fell back to the system loader which cannot see it |
| `NoSuchMethodError` | descriptor mismatch — check the exact `(…)V` string |
| Chinese/emoji text comes out as mojibake | `NewStringUTF` used for a document (§4.1) |
| page shows its placeholder forever | status pushed before `onPageFinished` (§5.5) |
| panel cannot be dragged | `Gravity.CENTER` margins never converted (§5.4) |
| panel drags wrong when the container moves | `getX/getY` instead of `getRaw/getRaw` |
| panel lands off-screen after rotation | no re-clamp on layout change |
| CPU stays high with the panel closed | `View` frame loop not stopped when hidden |
| second toggle does nothing | global ref/jmethodIDs dropped but `ensureReady` still thinks it is ready |
| crash in `unload` | `DeleteGlobalRef` on a stale `jclass`, or a call after the loader went away |

---

## 11. Class-shape variant: a Java API with no Java logic

The pipeline above compiles Java bodies. When the Java side only needs to *exist* — a JS bridge
object, a `Runnable` to hop to the UI thread — a **class-shape dex** is smaller and safer:

- one `public final class` extending Object, implementing the interface the host needs
  (`Runnable`);
- the bridge annotation on the **single method** JS may call, with runtime visibility, on the
  method (not the class);
- a constructor whose body is `super()` + `return` — two instructions, four code units;
- every method `public native` with no code; the C++ side binds them with `RegisterNatives`.

Loading is the same chain (direct buffer → in-memory loader → `loadClass` → global refs), plus:

- a `RegisterNatives` table `{name, signature, function pointer}` per method; check the return
  value, and log the mapping so a signature mismatch is visible;
- the same instance is posted to the UI thread (`runOnUiThread(instance)`) **and** registered as
  the JS interface (`addJavascriptInterface(instance, name)`) — one object serves both directions;
- integrity gates: the dex header `file_size` equals the embedded length, checksum and signature
  fields validate; refuse to load otherwise.

The page protocol for this variant is **page-initiated**: the page calls the bridge for `current`
state as its last load step, so there is no page-finished push and no ready race. Requests are
newline-delimited tuples; replies are JSON. Bind the session to an epoch and the originating bridge
instance, and refuse commands from a stale epoch or a different instance. Load the page with a
non-null placeholder origin (a fake `https://…invalid/` URL) rather than a null/`about:blank` base.

If the class loader or the page path fails, keep a **native fallback editor** so the feature degrades
instead of disappearing.
