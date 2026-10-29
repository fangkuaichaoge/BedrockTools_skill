# Version checkpoints and change notes

A rule for every mod project in this workspace: **keep exactly one checkpoint for the version that
works, and nothing else.** Iteration is fast and destructive, so the working state must be
recoverable; but checkpointing every attempted build turns the workspace into a warehouse of dead
folders that hides which version is actually usable.

## When to create one

Only when the build is **known to work**:

- the user confirms the fix or feature on their device ("works now", "no crash", "perfect", or any
  equivalent confirmation);
- or a release package is produced and the user says they will ship it.

Do **not** create a checkpoint for an intermediate build, a failed attempt, a "maybe this fixes it"
package, or a version the user has not confirmed. Iterate freely between checkpoints; while testing,
only the package in `dist/` is needed.

If a risky rework is about to start, the checkpoint you need is still the working version you are
about to leave - if no working checkpoint exists yet, create one then, not once per attempt.

## One working checkpoint per project

When a new version becomes the working one, it replaces the old working checkpoint:

1. create the new folder;
2. verify it (see below);
3. delete the superseded working checkpoint.

That keeps exactly one recoverable good state per project and no intermediate clutter. Never keep two
"working" checkpoints for the same project "just in case"; if a rollback target is genuinely needed
for a different game build (for example a 1.26.5x package next to a 1.26.60 package), say so in the
folder name and in the change note.

## Naming and layout

A new folder next to the project:

```
<project> - backup-<version>-<short-tag>-<yyyyMMdd-HHmm>/
```

- Do not rename an old checkpoint when a version number changes.
- Do not edit files inside an existing checkpoint; if something is wrong, create the corrected folder
  and delete the old one.
- Include: source tree, manifest/build files, docs, the built package (`dist/*.levipack`), and the
  build directory when it is small enough to be useful.

## The note inside the folder

Every checkpoint contains a change note named `CHANGELOG.md`, and it must record:

- checkpoint time, version, target game build, package name and size, library size and hash;
- what changed, and **why** (the evidence: log lines, disassembly facts, crash addresses, user
  reports);
- how it was verified (offline checks and what the user confirmed on device);
- known issues and how to roll back.

Short, factual, English, no marketing. The "why" section is what makes the checkpoint useful months
later.

## Verification before declaring it done

- Build output and the packaged library are byte-identical (compare hashes).
- Signature/offset checks the project ships (if any) pass against the target library.
- The user has confirmed the build on device, or the note explicitly says device confirmation is
  pending.
- When the checkpoint replaces an older one, delete the old folder only after the new one verifies.
