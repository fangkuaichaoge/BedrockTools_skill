# Cinematic camera paths

A camera track turns a recorded timeline into a watchable cut: keyframes at ticks, selectable
interpolation per segment, and a camera override applied every frame. This is the camera side of a
replay or cinematic feature, and it is also reusable for cutscenes and screenshots.

## Keyframe model

```cpp
struct CameraKeyframe {
    std::uint32_t tick;      // where on the timeline
    Vec3 position;
    float yaw, pitch, roll;
    float fov;               // per-key
    std::int32_t dimension;  // do not interpolate across dimensions
    enum class Mode { Linear, Smooth, Ease, Hold, Hermite, CubicBezier } mode;
    Vec2 bezier1{0.42f, 0.0f}, bezier2{0.58f, 1.0f};  // for CubicBezier
};
```

- Keys are sorted by tick; upsert by tick, update may move a key and needs a re-sort.
- `Hold` freezes the pose until the next key — useful for readable establishing shots.
- Duplicate positions with two interpolation modes make a "slow down into a pause" segment.
- Roll is part of the pose, not a post-process; a camera path without roll looks locked-off.

## Sampling

```
sample(tick) -> pose:
  find the key pair [a,b] with a.tick <= tick < b.tick and a.dimension == currentDimension
  t = (tick - a.tick) / (b.tick - a.tick)
  u = curve(a.mode, t)         // linear, smoothstep, ease-in/out, hold, hermite, bezier
  pos = lerp(a.pos, b.pos, u)
  yaw = lerpAngle(a.yaw, b.yaw, u)   // shortest arc, not raw numbers
  pitch, roll, fov = lerp(...)
```

- Interpolate angles on the shortest arc; raw degrees across the wrap produce a spin.
- Hermite needs tangents (Catmull-Rom from neighbours); bezier maps `t` through the control points
  on the x-axis before evaluating y.
- Dimension is a hard boundary: cut, do not interpolate through a dimension change.
- If there is no pair (before the first key, after the last, or between different dimensions),
  return the nearest key or nothing; the caller decides whether to keep the free camera.

## Applying the camera

Try the narrowest application point that produces the shot, and always restore:

1. renderer-level camera override (the frame's camera object);
2. the camera component of the render entity that owns it;
3. the level renderer camera used by the world pass.

- Observe the native FOV first, then override with the keyframe value; restore the observed value
  when the session ends.
- Keep an editor camera separate from the sampled path: a free-fly camera for placing keys, and a
  preview mode that samples the path. Switching between them must not leak a half-applied camera.
- Apply once per render frame, before the frame's render entry; a second application in the same
  frame is a bug (the later one wins and the first is wasted).
- Block manual input while a cinematic owns the camera; tell the player which mode is active.

## Storage and editing

- The path belongs to the timeline, not to the mod: store it beside the replay/session file and
  load it lazily when the camera editor opens.
- Save on edit through the same temp+rename discipline as any other state; keep the editor's
  selected key out of the file.
- Markers: render small geometry at each key for the editor, hidden during playback/export.

## Determinism for export

- Sampling must be a pure function of tick (and dimension); no wall clock, no frame counter.
- Export renders at fixed ticks: sample, apply, render, encode. The same file must produce the same
  frames.
- Interpolation at integer ticks equals a keyframe exactly; assert it in a host test.

## Pitfalls

- Angle wrap, roll not normalised, FOV not restored: the three visual bugs users notice first.
- Interpolating across dimensions produces a camera flying through unloaded space.
- Applying the camera after the world pass has no effect; applying twice fights itself.
- Editor state leaking into playback (selected key, free-camera pose) changes the shot.
- Perspective-only FOV override breaks when the player changes the native FOV mid-session; track
  the observed value.

## Checklist

1. Keys sorted by tick, dimension boundary respected, roll included.
2. Sampling pure in tick; one interpolation mode per segment; angle shortest-arc.
3. Camera applied at the narrowest level, once per render frame, before the pass.
4. Native FOV observed and restored; cinematic mode owns input.
5. Editor camera separate from sampled camera; mode switch is atomic.
6. Path saved beside the timeline with atomic writes; markers editor-only.
7. Host test: integer-tick sampling equals the keyframe; angle wrap round-trips.
8. Export samples pure ticks; repeated export is identical.