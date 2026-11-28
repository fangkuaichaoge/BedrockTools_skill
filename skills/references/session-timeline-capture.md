# Session timeline capture and deterministic reconstruction

A session can be represented without the live world: an ordered **action stream** plus an initial
context. Timing arrives as tick boundaries, world data as chunk snapshots, user activity as packets
and movement batches, ambience as small environment deltas. Reconstruction feeds the same stream to a
private simulated world. This reference is about the container format and the determinism rules, not
about one feature built on top.

## 1. Container design

Start with a versioned, bounds-checked binary container:

- a four-byte signature and a format version in the header; refuse newer versions, migrate older
  ones explicitly;
- the file is a sequence of actions; each action is `{kind, tick, payload}` with kind and tick as
  varints and the payload as a length-prefixed blob;
- a context region is delimited by begin/end markers and its length is patched at the end, so a
  partially written file is detectable;
- primitives: unsigned varint, zigzag signed varint (32 and 64 bit), little-endian raw integers and
  floats, length-prefixed strings;
- every read is bounds-checked before use; the loader enforces a global size cap (a couple of
  gigabytes is plenty for one session), monotonic ticks and a payload-size check.

Write atomically: temporary file in the same directory, patch the region length, flush, rename; unlink
the temporary file on abort.

## 2. What the stream carries

| stream content | why it is there |
|---|---|
| tick boundary | timing; one boundary action per simulated tick |
| context snapshot | dimension, height range, player position/rotation, world time, weather levels and targets |
| chunk columns and sub-chunks | rebuild terrain without a live server |
| configuration and session packets | reproduce the exchange; kind + payload is enough |
| entity movement batches, one per tick | id, position, pitch/yaw/head/body, on-ground, gliding (with a known flag) |
| environment deltas | time/weather changes only when they change |

Do not carry what can be recomputed or what is constant. Do not serialize raw memory structures;
serialize the fields the reconstruction will feed back.

## 3. Identity and filtering

- Give the captured player a **reserved synthetic identity** that cannot collide with live actors:
  a reserved unique id and a runtime id in a range the client never assigns.
- Filter packets by identity with a per-kind field table: for each packet kind, the offsets and
  decoders of the fields that may reference the captured player. That is O(1) per packet and much
  cheaper than parsing everything.
- Cache configuration packets by kind; they are applied once when the reconstruction world starts.
- If capture starts mid-session, take the context immediately and mark the start tick.

## 4. Reconstruction phases

Reconstruction is a phase machine, because a world and a context must exist before the stream can run:

```
idle -> starting world -> loading context -> resetting context -> running
                                                    ^              |  paused
                                                    |              v
                                                 stopping <- finished / failed
```

- Context reset is phased: remove actors, unload/replace chunks, wait for sub-chunks to arrive, apply
  sub-chunks, apply state (time/weather), create the local player.
- One tick at a time: dispatch that tick's packets in order, apply the movement batch, apply the
  environment delta when present.
- Seek by rewinding to the context and fast-forwarding, or by keeping periodic checkpoints.
- Run in a **private world** created through the game's own world-creation path; never touch the
  user's save, never attach the session to a server.

## 5. Deterministic rendering clock

- Separate the simulation tick from the **render tick**: `renderTick = tick + partial`, where partial
  is the fraction of time to the next tick.
- Interpolate entity poses between adjacent captured poses; snap on teleports and large gaps instead
  of sliding.
- For frame export, drive render ticks from a frame clock (a token per queued frame, completion
  reported by the renderer), never from wall time. Queue one frame, render it, encode it, repeat.
- Frame encoding uses the platform media encoder on the rendered surface; keep it optional and link
  the media NDK only when that path exists.

## 6. Validation and failure

- On load: signature, version, action kinds, payload sizes, monotonic ticks, duration.
- On start: if the world never becomes ready, fail with a named status; if a packet cannot be
  dispatched, stop rather than run a corrupt world.
- Log the path, tick count and format version once; keep per-tick logs behind a diagnostic flag.

## 7. Pitfalls

- Carrying everything: stream volume explodes; batch, filter and delta.
- Identity collisions corrupt the reconstruction; reserved ids must be documented.
- Seek without checkpoints is slow; decide the trade-off up front.
- Input and camera ownership during reconstruction: block manual input, define who owns the camera,
  and restore the player's camera when it stops.
- Writing the played save, or attaching the session to a server, silently corrupts a world; keep it
  local and private.
- Hooks on the packet path are hot; filtering must be allocation-free and version-checked.

## 8. Checklist

1. Versioned container with signature, bounds-checked decoder, size cap, monotonic ticks.
2. Atomic writing with a patched context length; abort unlinks the temporary file.
3. The initial context holds everything needed to rebuild the world without a live server.
4. Chunks, packets, movement batches and environment deltas are carried with identity filtering.
5. Reconstruction is a phase machine over a private world; the reset order is fixed.
6. Seek and export share the rewind path; export uses a deterministic frame clock.
7. The render tick interpolates poses and snaps on gaps.
8. Load validation and named failures; corrupt streams are never run.
9. Input and camera ownership are defined; both are restored on stop.
10. The user's save and any remote server are never touched.