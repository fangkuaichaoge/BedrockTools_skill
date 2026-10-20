# Feature Rollback, Removal and Rename Discipline

Distilled experience from a session in which a feature had to be removed after
the user reported that it broke their visuals, and the deliverable was renamed
twice. Generic rules only.

---

## 1. Always keep a known-good state

* One versioned package per release, kept on disk. When the user says "roll it
  back", you should be able to produce the last good build immediately - not
  reconstruct it from memory.
* Keep the feature you are about to touch as a *separate* code path from the one
  that already works. Two effects that share a pipeline should share only the
  pipeline, never each other's state.
* New effects default to **off**. A broken new effect must not be able to break
  the previously verified one.

## 2. When the user asks to roll back

Do it, completely, in one build. Do not negotiate and do not leave the feature
"available but disabled". Then:

* say exactly what was removed;
* say what remains and confirm the remaining path is unchanged;
* state whether the user must reinstall, and whether settings are preserved.

While doing so, keep investigating in parallel if the evidence contradicts the
attribution - a feature that never actually executed cannot have caused the
visual damage, and saying so (with the log line that proves it) is more useful
than silently reverting the wrong thing. Report the real cause too, especially
when it is a parameter you introduced without a ceiling.

## 3. Remove code, not flags

Leaving the implementation in place behind a default-off flag is not removal:

* the hooks are still installed and still run;
* state is still touched (blend, cull, depth, bindings);
* the next reader cannot tell whether it is dead or dormant;
* a future default flip resurrects the bug.

Delete the code. Then delete its surface: menu entries, persisted keys, log
lines, docs.

## 4. Prove the removal

Deleting by line range is how you break a build or leave a dangling reference.
Use scripted, asserting edits instead:

1. locate each removal by an exact start/end marker; **fail loudly** if a marker
   is not found (that means the code moved and the edit would corrupt it);
2. never delete a block whose boundary you only guessed;
3. re-grep the sources for the feature's identifiers - expect zero;
4. scan the **shipped binary's strings** for the same identifiers - expect zero;
5. check the size movement: removing a feature should shrink the artifact, and
   the amount should be plausible for the code removed.

A rename leaves exactly the same kind of residue (stale strings in the binary,
stale paths in the manifest) and the strings scan catches it.

## 5. Preserve the surviving path

When you remove one effect from a file that also implements another:

* treat the surviving path as frozen: no incidental reformatting, no renamed
  variables, no "while I am here" changes;
* re-read the surviving code after the deletion to confirm structure and
  braces are intact (automated cuts are good at leaving a stray brace);
* re-verify the surviving effect on device in the same round.

## 6. Renaming a shipped deliverable

A rename is not a one-file edit. Apply it at every layer, then verify:

| Layer | Check |
|---|---|
| manifest (display name, entry file) | fields match the new identity |
| built library name / build target | artifact name matches the manifest entry |
| deployment folder and packaging script | copies the new file, not the old |
| derived paths (log file, config file) | new names, and the docs say so |
| docs | no stale name anywhere |
| shipped binary strings | zero occurrences of the old name |

Two user-facing consequences must be stated explicitly:

* a **renamed library will not overwrite the old one** - the user must delete
  the old mod folder, otherwise both load (the old one with the old settings);
* derived config/log paths move, which **resets user settings**.

## 7. Version numbers and "do I need to reinstall?"

Make the answer obvious:

* code change -> bump the version, tell the user to reinstall;
* docs-only change -> either keep the version and say "the library is
  byte-identical, no reinstall needed", or bump and say the same. Never ship two
  different artifacts under one version without saying so.

## 8. Write down why it was removed

Add a short, evidence-backed note to the project's technical notes: the
symptom, the log line or measurement that identified the cause, and the
conclusion ("this is not achievable at this layer"). Without it, the same dead
end gets re-attempted a month later - including by you.
