# ImGui overlays in a native mod

Engine UI primitives are good for small HUD elements; they are painful for a timeline, a table of
tracks, or a keyframe inspector. A vendored immediate-mode GUI (Dear ImGui plus a GL backend)
gets you a desktop-class editor inside the game in days, at the cost of owning your own GL pass and
input routing. This reference is the integration shape and its traps.

## When to choose it

- The UI is an **editor**: timeline, tracks, tables, property grids, popups, multi-panel layout.
- The UI is **modal** and short-lived; the player opens it, works, closes it.
- You do not need the UI to look like a game screen or to scale with the engine's UI units.

Do not choose it for a single label or a button row; the engine UI layer or the launcher HUD is
cheaper and safer there.

## Bundling

- Vendor ImGui's core sources plus exactly one backend (OpenGL 3 is the usual) into the mod; pin
  the version in the build file so upstream changes cannot break the frame loop.
- One global context; initialize lazily on the first frame where a GL context is current, not in
  `load()`.
- Configure display size and scale from the real surface size and display density; do not rely on
  ImGui's default font size on a phone.
- `LoadIniSettingsFromMemory` with an embedded or empty ini, and never write `imgui.ini` next to the
  game by accident; if you persist layout, store it in your own data directory.
- Compile with exceptions enabled if the host SDK uses them; keep RTTI off only if ImGui and the
  backend do not need it.

## Frame integration

- Draw from **one** per-frame point (the swap hook or a render-pass hook), after the game's frame is
  composed and before the buffer is presented.
- Wrap the draw with the same state discipline as any own-GL draw: program, buffer, framebuffer,
  blend/depth/cull/scissor, vertex-attribute pointers. ImGui's backend changes many of them.
- Call `NewFrame` → build panels → `Render` → backend render exactly once per frame; a second call
  in the same frame corrupts the backend's buffers.
- When the overlay is hidden, skip the whole block: no `NewFrame`, no render, no input capture.

## Fonts and assets

- Embed the TTF as a byte array (or ship it as a resource file if the host allows) and add it at a
  size derived from density; if the scale changes, rebuild the atlas once.
- Keep the default font as a fallback so a missing custom font never leaves text invisible.
- An emblem or loading background can be a large embedded byte array; keep it in its own
  translation unit so it does not bloat the hot object, and decode/upload it once.
- If the project also ships resource or behavior packs, package the font and packs through the
  packaging script rather than copying files by hand.

## Input

- Translate the host's touch/mouse/key events into the GUI's IO: position (converted to pixels),
  buttons, wheel, key codes, text input.
- Respect `WantCaptureMouse` / `WantCaptureKeyboard`: when the overlay wants the event, consume it;
  otherwise forward to the game untouched.
- A modal editor should own input while open; make that explicit in one place instead of scattering
  per-widget checks.
- Text fields need the soft keyboard on Android; map show/hide to focus changes and never leave the
  keyboard up after the overlay closes.
- Handle the back button / escape: close the topmost popup, then the overlay.

## Editor architecture

- Keep the **model** (selection, timeline, keyframes, tracks) separate from the panels. Panels read
  a snapshot and emit commands; the model mutates in one place. This mirrors the module/settings
  separation and makes undo possible.
- Do not rebuild large tables from engine state every frame: snapshot at a fixed rate (for example
  a few times per second) and render from the snapshot.
- Throttle the overlay when idle: if nothing changed and nothing is hovered, you still need a frame
  for animations, but avoid re-scanning the timeline or the filesystem.

## Limits and pitfalls

- This is **your** GL pass: state restoration, no exceptions on the render thread, no file IO while
  drawing.
- The overlay does not integrate with the engine UI layer: it will not sit under engine tooltips or
  follow the engine's UI scale. Accept that, or use engine primitives instead.
- Input capture conflicts are the most common bug: the game receives a click the GUI also handled,
  or a drag is lost on the first move. Decide capture per frame, not per widget.
- Activity recreation (Android) invalidates the surface: rebuild the backend/context when the
  surface changes, and never cache the old one.
- Large embedded assets inflate the binary; keep them out of the module that runs per frame and
  measure the final `.so` size.
- Accessibility, IME and clipboard are weaker than native widgets; say so in the readme if the
  editor depends on them.

## Checklist

1. ImGui + one backend pinned and vendored; context created lazily with a live GL context.
2. One draw point per frame, full GL state save/restore, nothing else in the block.
3. Hidden overlay draws nothing and captures nothing.
4. Fonts derived from density with a safe fallback; assets loaded once.
5. Input translated, capture decided per frame, soft keyboard lifecycle handled.
6. Model separate from panels; commands, snapshot rate, no per-frame rescans.
7. Surface recreation rebuilds the backend; no cached surface state.
8. Resources/fonts/packs go through the packaging script; binary size measured.