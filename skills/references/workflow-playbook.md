# Workflow Playbook: from request to a package the user tests

Read this first in every mod session. It condenses the rest of the skill into the loop that
actually ships: classify the request, follow its track, hand over a testable package, read the
user's reaction, and checkpoint. Each step points to the reference that has the detail.

---

## 0. The loop

```
request → classify → (ask only what evidence cannot settle) → evidence → one change
       → build (version bump) → verify offline → package → hand over → user tests
       → confirmed? → checkpoint (backup + docs) → next request
       → failed?    → read the result as a decision tree → evidence → one change → ...
```

Two rules apply to every turn:

* **One variable per build.** A build with two speculative changes cannot be attributed.
* **A crash is worse than a missing feature.** Every path that is not proven installs nothing.

---

## 1. Classify the request

| The user says | Track | Start with |
|---|---|---|
| "add X", "make it show Y" | **Feature** (§2) | `feature-workflow.md`, `signature-forensics.md` |
| "how did mod X do Y", "I want that technique" | **Technique** (§2b) | `in-world-3d-rendering.md`, `embedding-dex-and-java-ui.md` §11 |
| "X is wrong", "shows ✗ where it should be ✓" | **Bug** (§3) | the data path that produces X |
| "it crashes", "the launcher asks to re-import" | **Crash** (§4) | `mod-lifecycle-and-crash-safety.md`, `coexistence-and-crash-safety.md` |
| "doesn't work on version N", "support N" | **Version** (§5) | `runtime-build-resolution.md` §0 |
| "breaks with mod Z installed" | **Coexistence** (§6) | `coexistence-and-crash-safety.md` |
| "remove X", "go back to how it was" | **Rollback** (§7) | `feature-rollback.md` |
| "perfect", "no problems", "back it up" | **Checkpoint** (§8) | `feature-rollback.md` §9 |

When one message carries two tracks, do them in this order: crash, then version, then bug, then
feature.

---

## 2. Feature track

1. Name the subsystem that owns the behaviour (which screen, renderer, block or item class).
   If the feature is **your own 3D geometry in the world**, start from `in-world-3d-rendering.md`;
   if it needs a **Java object, a JS bridge or an HTML page**, start from
   `embedding-dex-and-java-ui.md` §11. Neither kind has an engine function to hook in the usual
   sense — the first hooks the frame's world-render entry, the second has no engine address at all.
2. Find the engine function that already does it, and confirm the data path exists. Check which
   copy you are reading (client or integrated server) before building UI on top of it.
3. Ask about semantics the evidence cannot settle (what a mark means, which targets, icon or text).
   Do not ask what the binary answers.
4. Signature: unique on **every** supported build. Vtable slots: verified against the live table.
5. Implement behind fail-open guards. Build, package, hand over.

## 2b. Technique track ("learn how a shipped mod does it")

1. Locate the mechanism in the artifact, not in its description: packaging shape → symbol/string
   scan → targeted decompile of one function at a time (`so-analysis.md`,
   `targeted-static-analysis.md`, `ida-workflow.md`).
2. Separate **engine API** from **mod code**, and label every claim verified / inferred / unknown.
3. Re-implement the idea behind your own guards and fail-open paths; never copy identifiers,
   addresses or constants from the artifact.
4. The two mechanism families this skill documents in depth have dedicated notes: world-space
   geometry (`in-world-3d-rendering.md`) and the Java-object/page half
   (`embedding-dex-and-java-ui.md` §11).

## 3. Bug track

1. Reproduce from the user's description: which target, which world, which other mods.
2. Find the **first wrong value** on the data path, not the place where it shows.
3. If two static fixes have not helped, stop guessing and ship one observe-only build (counters,
   install results, live head bytes) instead (`instrumentation-and-diagnostics.md`).
4. Fix at the drop site. Delete superseded code instead of disabling it.

## 4. Crash track

1. Revert to the last confirmed package first, so the user can keep playing.
2. Classify: at start (lifecycle, load order), on an action (an unverified call or ABI), or random
   (an unguarded read, a stale map snapshot, render-thread exceptions).
3. Check every hook target against the invariant list in `coexistence-and-crash-safety.md`.
4. The fix must make the failing path *not run*, not "run more carefully".

## 5. Version track (new build, or "doesn't show on N")

1. **Check the build gate first.** If the mod whitelists build-ids, an unknown build installs
   nothing on purpose. That explains "nothing shows" without any change in the game.
2. Decide the contract with the user if it is unclear: listed builds only, or any build whose
   layout matches a listed one.
3. If you have the library: add it as a build (`porting-a-live-mod.md` §3a), in this order:
   1. add its build-id to the table and a column to every per-build value;
   2. regenerate the cross-build signatures (plus `*Deep`, plus `*Alt` for rebuilt functions);
   3. port addresses (RTTI for vtables, reference sites for data, signatures for functions);
   4. port the layout (getter census, displacement census, slot map) and confirm meaning;
   5. run the matrix verifier on **all** builds until it is clean;
   6. build, package, and have the user test the new build **and** one old build.
4. If you do not: runtime resolution (`runtime-build-resolution.md`). Prove it on a holdout build
   you have and that is not in the table.
5. If the user declines to send the library, design around it. Ask for it only after a device test
   on that build has failed.

## 6. Coexistence track

Decide the layer from evidence (hook chain, signature, RTTI/vtable, render queue), then fix that
layer. Prologue-independent (deep) signatures and baked vtable fallbacks are the usual fixes.

## 7. Rollback track

Go back to a confirmed state by hash, remove the feature completely, prove the removal (strings,
symbols, call sites), and keep the surviving paths byte-identical.

## 8. Checkpoint track

When the user confirms a result ("perfect", "no problems on N", "back it up"):

1. Back up the mod folder: `<mod> - backup-<version>-<what>-<yyyyMMdd-HHmm>`. Include sources,
   analysis scripts, `dist/` and docs. Leave out build intermediates.
2. Prove the backup: the hash of the `.so` inside the backed-up package equals the built one.
3. Update the change log, the status file and the compatibility notes.
4. Only then start the next request.

Never mark a goal as done before the user has confirmed it on the device.

---

## 9. Handing over a package

### 9.1 When to stop verifying and hand over

Offline verification has diminishing returns. Hand the package over when all of these hold:

* the build compiles with the real cross toolchain, and the package hash matches the build;
* every hooked address on the new path has evidence (signature, slot cross-check, holdout result);
* the remaining gap is something only the device can answer (the C++ path never ran on the host,
  the exact build is unavailable).

If more offline proof would take a long detour (installing a toolchain, writing a host harness),
say what it would add, and **ask whether the user wants the package now and will test it
themselves**. Many users prefer that. A crash on a fail-open design is unlikely, and a test costs
them a minute.

### 9.2 Signals to read, and what they mean

| The user says | Meaning | Do |
|---|---|---|
| "what are you doing?" | you are on a tangent they do not see the value of | answer in one or two sentences, drop the tangent, return to the main path |
| "just look at how X was built" | stop researching, use the existing setup | use the project's existing build, not a new one |
| "just build it, I'll test it myself" | they want the package now | stop verifying, build, package, report the remaining gap in one line |
| "do you need me to send ...?" / declines to send | the artifact is unavailable | do not ask again until a test fails |
| "no problem", "perfect" | a confirmed state | checkpoint (§8) |
| short, impatient replies | too much text or too many questions | shorter reports, fewer questions, more doing |
| a long message with screenshots | they want the detail addressed | answer each point |

Ask proactively at these points, in one short question with your default:

* before a long verification detour: "Do you want the package now, or should I verify X first?
  (default: package now)";
* before a decision the evidence cannot settle (scope, semantics, icon vs text);
* after a confirmed result: whether to back up, if they did not say.

### 9.3 The hand-over report

Write it in the user's language, short, in this order:

1. **Package**: path and version. Which builds it covers.
2. **What changed**, in one or two sentences, with the mechanism.
3. **Evidence**: what was verified and how (holdout build → profile, values equal, hashes).
4. **The gap**: what was not verified and why (for example "the C++ path did not run on the host").
5. **What to test** and what each outcome means: works → done; nothing shows → send X; crash →
   roll back to `<previous package>` and send the crash log.

No preamble, no closing summary.

---

## 10. Build and release mechanics

* Bump the manifest version on **every** build, including test builds.
* Build with the project's own toolchain (`build-deploy.md`). Do not hunt for alternative host
  compilers. If the platform SDK is missing, the host compiler cannot help anyway.
* Package, then check that the `.so` in the package is byte-identical to the build output and that
  it exports the registration entry point.
* Keep the previous confirmed package next to the new one for rollback.
* Change-log entry: what, why, evidence, known gaps.

---

## 11. Anti-patterns seen in real sessions

* Reverse engineering a "changed function" when the build gate explained the symptom.
* Spending a long stretch on a host compiler or harness the user never asked for.
* Re-asking for a library the user already declined to send.
* Shipping two changes in one test build.
* Reporting "verified" when only the generator ran, not the generated table.
* Marking a task done before the device confirmed it.
* Long reports to a user who replies in five words.
