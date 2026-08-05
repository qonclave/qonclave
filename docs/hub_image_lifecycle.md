# Where images live on the hub (debugging map)

Four independent places an image can end up, depending on which endpoint
touched it and which env switches are on. Check this before assuming a
missing/wrong image is a bug — it might just be a disabled switch.

## 1. `hub/uploads/` — raw frames from `/edge/event` and `/user/reason`

- Written by `transport.save_incoming_image()` (`hub/framework/transport.py:53`).
- Filename: `<UTC timestamp>-<8 hex>.<ext>`, e.g. `20260805T203112Z-a1b2c3d4.jpg`.
- **Kept forever** — nothing prunes this directory. It's gitignored
  (`hub/uploads/` in `.gitignore`) but not size-capped; on a long-running
  demo it grows without bound.
- Every `/edge/event` frame also gets a JSON sidecar next to it:
  `<frame>.json` (`transport.save_result_sidecar()`,
  `hub/framework/transport.py:97`) — the full verdict/reasoning/latency
  record for that exact frame. **Start here when debugging a bad
  verification**: the sidecar has the VLM's reasoning text and confidence
  for that specific image.
- Served at `GET /user/frames/<name>` (both the `.jpg` and its `.json`).
- `/user/latest.jpg` and the dashboard always point at the most recent one
  (`events.latest_frame_name()`).

## 2. `hub/track_frames/` — per-track annotated pose frames

- One file per **track_id**, not per frame: `track_<id>.jpg`, overwritten
  every time (`_publish_track_frame()`, `hub/framework/server.py:95`).
- Only written if `QONCLAVE_TRACK_FRAMES_ENABLED=1` (**default on**). Set
  to `0` for any non-demo run — it's the one place raw camera imagery
  persists to disk outside `uploads/`.
- Capped at `QONCLAVE_TRACK_FRAMES_MAX` (default 50) files total, oldest
  by mtime pruned — see the known non-atomic-pruning caveat in
  `docs/hub_code_assessment.md`.
- Served at `GET /user/tracks/<id>.jpg`.
- Gitignored (`hub/track_frames/`).

## 3. In-memory only (never touches disk)

- **Live pose stream**: `track_store._frame_bytes` (`hub/framework/track_store.py:46`)
  — latest annotated JPEG per track, held in RAM. Works even with
  `QONCLAVE_TRACK_FRAMES_ENABLED=0` because it's gated by the *separate*
  `QONCLAVE_TRACK_STREAM_ENABLED` switch (default on). View live at
  `GET /user/tracks/<id>/stream.mjpg` (drop into an `<img src>`, or just
  open the URL in a browser).
- **Face-recognition activity feed**: `recognize_activity.py`'s ring buffer
  (`maxlen=30`) — the crop that was actually fed to the face matcher, kept
  around only so `/user/recognize_activity/<id>.jpg` can show it. Debug a
  bad face match here, not in `uploads/`, since `/track/analyze` crops are
  deleted from disk right after inference (see #4).

## 4. `/track/analyze` crops — deleted immediately

- The crop uploaded to `/track/analyze` is saved to `uploads/` transiently,
  then **always deleted** in a `finally:` block right after face/pose
  inference (`hub/framework/server.py:432-435`), regardless of success or
  failure. It never accumulates in `uploads/`.
- If you need to see what was actually sent for a given `/track/analyze`
  call, you must catch it via #2 (track_frames, if pose ran and retention
  is on) or #3 (recognize_activity, if face ran) — there's no path to the
  original crop after the request returns.

## Quick reference

| Want to see... | Look at |
|---|---|
| The exact frame + VLM verdict for a `/edge/event` call | `hub/uploads/<frame>.jpg` + `<frame>.json`, or `GET /user/frames/<name>` |
| Most recent frame overall | `GET /user/latest.jpg` |
| Live skeleton video for one track | `GET /user/tracks/<id>/stream.mjpg` |
| Latest still frame for one track | `GET /user/tracks/<id>.jpg` (only if `QONCLAVE_TRACK_FRAMES_ENABLED=1`) |
| The crop a face match was run against | `GET /user/recognize_activity/<id>.jpg` (id from `GET /user/recognize_activity`) |
| A `/track/analyze` crop after the fact | Not retrievable — it's deleted before the response returns |
| Enrolled reference photos | `hub/framework/face_id/known_faces/<slug>.<ext>` |

## Env switches that change what persists

| Var | Default | Effect |
|---|---|---|
| `QONCLAVE_TRACK_STREAM_ENABLED` | `1` | In-memory live MJPEG stream (#3). No disk. |
| `QONCLAVE_TRACK_FRAMES_ENABLED` | `1` | Also write `track_frames/track_<id>.jpg` (#2). Set `0` to stop persisting imagery. |
| `QONCLAVE_TRACK_FRAMES_MAX` | `50` | Cap on files in `track_frames/`. |

`hub/uploads/` has no on/off switch or cap — every `/edge/event` and
`/user/reason` call writes there unconditionally.
