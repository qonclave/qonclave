# Track detected persons across frames (centroid tracker)

## Context

The Uno Q edge app (`edge/arduino_uno_q_00/qonclave-detect-objects-on-camera/`) runs person detection per-frame via the `VideoObjectDetection` brick. Each frame's callback (`send_detections_to_ui` in `python/main.py`) receives a fresh, independent dict of detections — e.g. `{"person": [{"confidence": 0.92, "bounding_box_xyxy": (x1, y1, x2, y2)}, ...]}` — with no notion of "this is the same person as last frame." That's confirmed by reading the brick's source (`app_bricks/video_objectdetection/__init__.py:262-290`): it builds a fresh `detections` dict from `bounding_boxes` every WebSocket message, no track IDs.

To eventually rotate the camera toward a person, we first need to track a person's bounding box across frames — assign a persistent ID, and derive a movement direction from the box's centroid history. This step only adds that tracking (ID + direction), computed in the backend; no camera actuation, and no UI changes (per your direction: person-only tracking, backend-only for now).

## Approach

Add a small, dependency-free centroid tracker (no numpy/scipy needed — just tuples/math), matching Arduino's existing "no heavy deps" pattern in this app (`requirements.txt` only has `requests`, `python-dotenv`, `paho-mqtt`).

### New file: `python/person_tracker.py`

`PersonTracker` class:
- `update(person_detections: list[dict]) -> list[dict]`: takes this frame's `detections.get("person", [])` (each item has `confidence` and `bounding_box_xyxy`), returns the same detections enriched with `track_id`, `centroid`, `direction`, `dx`, `dy`, `frames_tracked`.
- Internal `_Track`: `id`, `centroid`, `bbox`, `disappeared` (frames since last matched), `history` (a `deque(maxlen=N)` of past centroids for direction smoothing), `frames_tracked`.
- Matching: greedy nearest-centroid matching between existing tracks and this frame's detections (Euclidean distance on bbox centroids), capped by `max_distance`. This is the standard lightweight approach (à la `dlib`/`imutils` centroid tracker) — appropriate here since we only ever expect a handful of people in frame, so greedy is as good as Hungarian and much simpler.
- Unmatched existing tracks: `disappeared += 1`; deregistered once `disappeared > max_disappeared`.
- Unmatched detections: registered as new tracks with a fresh incrementing ID.
- Direction: compare oldest vs newest centroid in `history`; if movement magnitude is below `min_movement_px`, label `"stationary"`; otherwise bucket the angle into 8 compass directions (`left`, `right`, `up`, `down`, and diagonals) via `atan2`. Chosen because pan (left/right) and tilt (up/down) are exactly the two axes the eventual camera-rotation logic will need — this labels both axes plus diagonals now so that logic is a straightforward consumer later.
- Tunables via constructor args, wired to env vars in `main.py` (mirrors existing `PERSON_CONFIDENCE_THRESHOLD` etc. pattern):
  - `max_disappeared` (default 10) — frames to keep a track alive with no match before dropping it.
  - `max_distance` (default 150 px) — max centroid jump to still count as the same person.
  - `direction_history` (default 5) — frames of centroid history used to smooth direction.
  - `min_movement_px` (default 10) — minimum net movement over the smoothing window before calling it "stationary".

### Wire into `python/main.py`

- Import `PersonTracker`, instantiate once near the other module-level singletons (next to `detection_stream`), reading the four tunables from env vars (documented below).
- Inside `send_detections_to_ui`, after `detections` dict is built (but without touching the existing icon/UI/hub logic), add:
  ```python
  person_tracks = person_tracker.update(detections.get("person", []))
  if person_tracks:
      log.debug(f"Person tracks: {[{'id': t['track_id'], 'direction': t['direction']} for t in person_tracks]}")
  ```
- No changes to the `ui.send_message("detection", ...)` loop or `maybe_notify_hub` — those stay exactly as-is (backend-only, no UI/hub payload changes per your answer). `person_tracks` is available as a plain local for now; hooking it into the hub payload or a UI message is future work once rotation logic exists.

Note on invocation frequency: the brick only calls `on_detect_all` when at least one object of any class is detected that frame (`video_objectdetection/__init__.py:288`, `if len(detections) > 0`). So `person_tracker.update([])` (which decays a currently-tracked person) only runs on frames where *some* object was detected but not a person — a fully empty frame with zero detections doesn't invoke the callback at all, so a track can go briefly stale without decaying. This is an existing constraint of the brick's callback contract, not something to work around now.

### Env vars (add to `.env.example` and document in `README.md`, same table style as `PERSON_CONFIDENCE_THRESHOLD`)

| Var | Default | Meaning |
|-----|---------|---------|
| `PERSON_TRACK_MAX_DISAPPEARED` | `10` | Frames a person can go unmatched before their track is dropped |
| `PERSON_TRACK_MAX_DISTANCE_PX` | `150` | Max centroid movement (px) between frames to still count as the same person |
| `PERSON_TRACK_DIRECTION_HISTORY` | `5` | Frames of centroid history used to smooth the direction estimate |
| `PERSON_TRACK_MIN_MOVEMENT_PX` | `10` | Minimum net movement (px) over the smoothing window before direction is "stationary" |

### Tests: `test_person_tracker.py` (top-level, alongside `test_edge_mqtt_e2e.py`)

Follows this app's existing convention for standalone test scripts (see `test_edge_mqtt_e2e.py`, which is a plain script with `assert` + `print`, no pytest dependency added — this app's `requirements.txt` has no test framework). Written as plain `assert`-based `test_*()` functions (pytest-discoverable if pytest happens to be present, but also runnable directly via `python test_person_tracker.py`) covering:
- A single person tracked across several frames keeps the same `track_id`.
- Two people crossing paths get distinct, stable IDs (greedy matching doesn't swap them under normal separation).
- A person who moves right/left/up/down gets the matching direction label; one who barely moves is `"stationary"`.
- A track is dropped after `max_disappeared` empty updates, and a new detection afterward gets a new ID (not the old one).
- A detection farther than `max_distance` from any existing track registers as a new track rather than merging.

## Verification

- Run `python test_person_tracker.py` (or `pytest test_person_tracker.py` if available) — all assertions pass.
- Manual smoke test: run the app with `CAMERA_SOURCE=file` (bundled `media/walking_front_view.mp4`, no hardware needed) and confirm `qonclave.edge` logs show `Person tracks: [...]` with a stable `id` and a `direction` field while a person is in frame across multiple log lines.

---

# Show tracked person's position on the LED matrix

## Context

The Uno Q edge app (`edge/arduino_uno_q_00/qonclave-detect-objects-on-camera/`) already tracks each detected person across frames via `PersonTracker` (`python/person_tracker.py`), producing a `centroid` (pixel coords in the camera frame) and a `direction` per track, but this is currently backend-only — it's just logged (`main.py`'s `send_detections_to_ui`). The user's next step, ahead of eventual camera rotation, is to make this tracking visible on the Uno Q's physical 12x8 LED matrix: light up roughly where the person is in frame.

Per your answers: while a person is actively tracked, the position display **replaces** the normal object-icon display (falls back to existing icon/clear behavior when no person is tracked), and direction is conveyed by **dot position only** (no separate arrow/edge overlay) — a lit pixel that moves around the grid as the person moves is sufficient to imply direction over time, satisfying "display where the person is and its direction" literally and simply.

No microcontroller change is needed: `sketch/sketch.ino`'s existing `set_custom_led_array` Bridge handler already accepts any 96-char '0'/'1' bitstring (12 cols × 8 rows, row-major) and renders it verbatim — this is a pure Python-side addition that reuses that existing mechanism.

## Approach

### 1. New helper: `python/led_display.py`

A small pure function module (no state), mirroring the dependency-free style of `person_tracker.py`:

```python
def person_position_bitmap(centroid: tuple[float, float], frame_width: int, frame_height: int) -> list[list[int]]:
    """Map a centroid in frame pixel-space to a lit dot on a 12x8 grid."""
```

- Scales `centroid[0]` (x, 0..frame_width) to a column `0..11` and `centroid[1]` (y, 0..frame_height) to a row `0..7`, clamped to grid bounds.
- Lights a single cell by default; to keep the moving dot visible/legible (a single LED is easy to miss), light a small 2x2 block centered on the mapped cell when it's not on the grid edge (still just "dot position", not a separate direction glyph) — clamped so it never runs off the 12x8 bounds.
- Returns an 8x12 `list[list[int]]`, matching the shape convention already used in `main.py` for `icon_cache` bitmaps (`[[0]*12 for _ in range(8)]`), so it composes with the existing `bitstring = "".join(...)` flattening line already used for icons.

### 2. Wire into `python/main.py`

- Import `person_position_bitmap` from `led_display`.
- Frame dimensions: use `camera.resolution` (the `(width, height)` tuple already held by the `camera` object constructed at module load — confirmed accurate at runtime since `V4LCamera`/`IPCamera` update it to the actual negotiated resolution, and it's the same coordinate space `bounding_box_xyxy` is reported in, per `draw_bounding_boxes` in `app_utils/image/image.py` treating box coords as raw pixels).
- In `send_detections_to_ui`, after computing `person_tracks = person_tracker.update(...)`:
  - If `person_tracks` is non-empty, pick one track to display (the first / most-established — e.g. `max(person_tracks, key=lambda t: t["frames_tracked"])`, so a briefly-flickering new detection doesn't steal the display from an established track), compute its bitmap via `person_position_bitmap(track["centroid"], *camera.resolution)`, flatten to a bitstring the same way icons already are, and `Bridge.call("set_custom_led_array", bitstring)` — **replacing** the icon-based `Bridge.call` for this frame (the existing `get_or_trigger_icon`/icon `Bridge.call` block in `send_detections_to_ui` only runs in the `else` branch, i.e. when no person is being displayed).
  - If `person_tracks` is empty, fall through to the existing icon/clear logic unchanged (already there).
  - No changes to `ui.send_message(...)` calls, hub forwarding, or the icon cache — this only changes what gets pushed to the LED matrix while a person is tracked.

This keeps the change localized: `send_detections_to_ui` gains one branch, no new Bridge method, no `sketch.ino` change, no new env vars needed (grid mapping is a pure geometric scale, nothing to tune yet).

### 3. Tests: extend `test_person_tracker.py`'s sibling pattern with a new `test_led_display.py`

Same plain-`assert` style as `test_person_tracker.py`/`test_edge_mqtt_e2e.py`:
- A centroid at the exact center of the frame maps to a cell near the center of the 12x8 grid.
- A centroid at each corner (0,0), (frame_width,0), (0,frame_height), (frame_width,frame_height) maps to a cell at/near the corresponding grid corner, clamped in-bounds (no index errors).
- Output shape is always 8 rows × 12 columns of 0/1 ints.

## Verification

- Run `python test_led_display.py` — all assertions pass.
- Run `python test_person_tracker.py` — still passes (untouched).
- `python -m py_compile python/main.py python/led_display.py` — syntax check.
- Manual smoke test: run the app with `CAMERA_SOURCE=file` (bundled `media/walking_front_view.mp4`, no hardware) and confirm via added debug logging (or just code reading) that `person_position_bitmap` is invoked with sane in-bounds coordinates while a person is in frame; on actual Uno Q hardware, confirm the lit dot visibly moves on the physical matrix as a person walks across the camera's view, and confirm it reverts to the normal object icon within one frame of the person leaving view.

---

# LED matrix: outer-ring position + center emotion, with front/rear row inversion

## Context

The Uno Q edge app (`edge/arduino_uno_q_00/qonclave-detect-objects-on-camera/`) previously showed a tracked person's position anywhere on the 12x8 LED matrix as a freely-placed 2x2 dot (`python/led_display.py`'s `person_position_bitmap`, wired into `python/main.py`'s `send_detections_to_ui`). Two changes were needed:

1. The position indicator should be constrained to the matrix's **outer ring** only (row 0, row 7, and columns 0/11 of rows 1-6). The freed-up **inner 6x10 region** is reserved for a **person emotion** indicator — eventually LLM-generated (mirroring the existing `CloudLLM`/hub `/edge/icon` pattern already used for object icons), but for now a hardcoded **smiley** placeholder.
2. The camera in use is a 360° dual-lens rig that stacks **rear camera on top, front camera on bottom** into one frame. The previous linear y→row mapping was backwards for this rig: a person in the frame's bottom half (front camera) should light up the **top** rows of the matrix, and a person in the top half (rear camera) should light up the **bottom** rows — i.e. the vertical mapping needed to be invertible, gated to only this camera setup.

The ring position uses a **continuous ray-cast** projection (direction from frame-center projected outward onto the ring, so movement around the whole ring stays smooth); the center smiley shows **only while a person is actively tracked** (same replace-the-icon behavior as before); and the front/rear inversion is applied as an **explicit step in `main.py`**, keeping `led_display.py` camera-agnostic.

No `sketch.ino` change was needed — still just a 96-char bitstring through the existing `set_custom_led_array` Bridge call.

## Approach

### 1. `python/led_display.py` — ring-constrained position + center smiley

- `person_position_bitmap(centroid, frame_width, frame_height)` reworked to project onto the ring instead of scaling freely:
  - Computes `nx, ny` = centroid position relative to frame center, normalized to `[-1, 1]` on each axis, clamped so out-of-frame centroids don't break the projection.
  - Dead-center (`nx == ny == 0`) defaults to a fixed direction (straight up) rather than dividing by zero.
  - Ray-casts onto the unit square's border (`scale = 1 / max(abs(nx), abs(ny))`), which pins at least one axis to exactly ±1 — once mapped to grid coordinates, this lands exactly on the ring by construction, no separate clamping/snapping needed.
  - Lights a 2-cell segment along the ring edge the point landed on (same row for top/bottom edges, same column for left/right edges) so it stays visible without spilling into the interior.
- New `SMILEY_BITMAP` — hardcoded 6x10 constant, embedded at offset `(1, 1)` in the 8x12 grid so it never touches the ring.
- New `emotion_bitmap(name: str = "smiley")` — returns a full 8x12 bitmap with `SMILEY_BITMAP` placed at the interior offset. Takes a `name` param even though only `"smiley"` exists today, so swapping in an LLM-generated bitmap later is a drop-in replacement for the function body, not a call-site change.
- New `person_display_bitmap(centroid, frame_width, frame_height, emotion="smiley")` — the composed entry point `main.py` calls: `person_position_bitmap(...)` OR'd cell-by-cell with `emotion_bitmap(emotion)`. Safe to OR unconditionally since the two never overlap by construction (ring vs. strict interior).

### 2. `python/main.py` — front/rear inversion + swap to the composed bitmap

- New env var `CAMERA_DUAL_LENS_STACKED` (default `false`) — only this specific 360° rig needs the row inversion; a plain USB/IP camera must not get it.
- In `send_detections_to_ui`'s `if person_tracks:` branch: if `CAMERA_DUAL_LENS_STACKED`, flip `cy = frame_h - cy` (rear/top-half → bottom rows, front/bottom-half → top rows) before calling `person_display_bitmap((cx, cy), frame_w, frame_h)`. Rest of the branch (bitstring flatten, `Bridge.call`, `ui.send_message("led_status", ...)`) unchanged.
- Import `person_display_bitmap` instead of `person_position_bitmap` from `led_display`.

### 3. Tests: `test_led_display.py` rewritten

Replaced the old free-placement corner/center assertions with ring-focused ones:
- `person_position_bitmap` never lights a strictly-interior cell for any input centroid, including out-of-frame ones.
- A centroid straight above/below/left/right of center lands on the corresponding ring edge (row 0, row `GRID_ROWS-1`, col 0, col `GRID_COLS-1`).
- Dead-center centroid doesn't raise and still lands on the ring.
- `person_display_bitmap` includes both a lit ring cell and the smiley's cells simultaneously (composition didn't drop either).
- Output shape stays 8x12 of 0/1 ints in all cases.

### 4. `README.md`

Updated the "LED Matrix Person Position Display" section: outer-ring-only positioning, center smiley placeholder (noting the eventual LLM-driven emotion path), and documented `CAMERA_DUAL_LENS_STACKED` in the Camera Source table.

## Verification

- `python test_led_display.py` — all 9 assertions pass.
- `python test_person_tracker.py` — still passes (untouched, 7 tests).
- `python -m py_compile python/main.py python/led_display.py` — syntax check, clean.
- Manual smoke test with `CAMERA_SOURCE=file`: confirm the position dot only ever appears on the matrix border while a person is tracked, the smiley is visible in the center at the same time, and toggling `CAMERA_DUAL_LENS_STACKED` flips whether a person in the frame's top vs. bottom half lights the top vs. bottom LED rows.
