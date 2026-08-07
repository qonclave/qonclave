# Plan: unified per-track analysis API + hub-side pose detection

> **STATUS: IMPLEMENTED** — all five phases are built, tested, and verified
> end-to-end (edge on the UNO Q → hub on the Snapdragon X Elite; 1.39 ms
> NPU inference, live skeleton at `/user/tracks/<id>.jpg`). Deviations from
> this plan as written: the edge client file is `analysis_client.py`; the
> gitignore entries live in the root `.gitignore` (there is no
> `hub/.gitignore`); pose setup is wired into `setup_hub.ps1` directly (the
> face-ID chain goes through its own `setup/setup.ps1`); the shared
> `qnn_session.py` takes optional provider options and pose opts into
> `htp_performance_mode: burst`; `PoseBackend.status()` reports the
> session's *resolved* provider rather than mirroring
> `FaceIdentityBackend`'s load-time guess; and the dashboard's recognition
> feed was kept, fed by the face sub-result. See `qonclave_plan.md` §14 for
> the up-to-date scope record.

Roadmap item 3 from `docs/scratch.txt`. Targets branch `main` with the
uncommitted per-track recognition feature in the working tree.

## Context

The edge already resolves *who* each tracked person is: `PersonTracker` assigns
`track_id`, `track_crop.py` crops the person, `recognition_client.py` samples
that crop to the hub's `POST /recognize`, and `identity_map.py` applies a
"known is sticky" rule. Hub-side pose estimation tied to the same `track_id`,
feeding simple fall logic, has not started.

Rather than add a second endpoint with its own sampling loop, the edge should
send **one crop to one endpoint** and let the hub fan it out to every analyzer
that wants it. Today's sampler is face-specific (it stops entirely once a track
is `known`), which is wrong for pose: fall detection needs a continuous time
series for as long as a person is tracked.

The hub is already equipped for this. `hub/geniex-env` runs Python 3.12.10
ARM64 with `onnxruntime 1.28.0` + `onnxruntime_qnn 2.4.0` — the exact stack
HRNetPose w8a8 was benchmarked on at **1.45 ms / ~690 FPS** on the Hexagon NPU
(31× the CPU path). Critically, the edge's person box removes the need for a
hub-side person detector, which was the dominant cost (~1.2 s/frame) when this
was prototyped standalone.

**Decisions taken:** replace `/recognize` outright (no shim); pose runs
continuously at ~3–5 Hz per live track; the hub retains keypoints **and**
annotated frames; model files come from an AI Hub export only (no vendored
binaries), mirroring `face_id`; the HTP context binary is built as an explicit
setup step.

> Retention note: storing annotated frames means imagery persists on the hub,
> which the privacy cascade otherwise avoids. Built as requested, but gated —
> `QONCLAVE_TRACK_FRAMES_ENABLED` (default `1` for demo), a file cap, and a
> gitignored directory. Set to `0` for any non-demo deployment.

## Design

```
edge detection callback
  └─ PersonTracker → per-track boxes
     └─ TrackSampler.analyzers_due(track_id) → {"face","pose"} | {"pose"} | {}
        └─ one background thread per frame: decode frame ONCE, crop all due tracks
           └─ POST /track/analyze  (image + track_id + analyzers=face,pose)

hub
  └─ /track/analyze
     ├─ face → FaceIdentityBackend.identify()      (existing)
     ├─ pose → PoseBackend.estimate()              (new, HRNet w8a8 on NPU)
     ├─ TrackStore.record()                        (keypoint ring buffer)
     ├─ save annotated frame (gated)               (skeleton overlay)
     └─ delete the uploaded crop
```

Sampling cadence is **per analyzer, unioned**:

| analyzer | policy |
|---|---|
| `face` | sample until `status == "known"`, then never again (unchanged rule) |
| `pose` | every `POSE_SAMPLE_INTERVAL_SEC` (default `0.25` = 4 Hz) while the track is alive |

One crop serves whichever analyzers are due, so a known person costs one
request per pose tick instead of two. The existing one-in-flight-per-track cap
stays, which self-limits the real rate to hub round-trip time.

## Phase 1 — Hub pose backend

New package `hub/framework/pose/`, mirroring `hub/framework/face_id/` exactly.

**Runtime: `onnxruntime-qnn`, not GenieX.** GenieX (`framework/vlm.py`,
`framework/llm.py`) is a *generative* runtime — tokenizer, chat template,
`generate()`, KV cache. HRNetPose is a CNN returning a heatmap tensor, so it
takes the path the repo already uses for face ID: ONNX Runtime with the QNN
execution provider on the Hexagon NPU. **Pose follows the `face_id` precedent,
not the `vlm` precedent** — same package, same session builder, same CPU
fallback, same AI Hub provisioning convention.

**`pose_pipeline.py`** — low-level inference. Port the preprocessing and
heatmap decode from the standalone prototype, but **reuse the repo's existing
session builder** rather than writing a second one:
- Session creation: reuse `face_pipeline._qnn_session(onnx_path, label)`. It
  already registers the plugin EP via the package's own helpers
  (`qnn.get_ep_name()` / `qnn.get_library_path()` / `qnn.get_qnn_htp_path()`),
  binds the NPU `OrtEpDevice` through `add_provider_for_devices`, and falls back
  to `CPUExecutionProvider` on any failure. Its docstring already documents the
  trap: passing `"QNNExecutionProvider"` as a plain string to
  `InferenceSession(providers=...)` **silently no-ops and runs on CPU**.
  - Preferred refactor: lift `_qnn_session()` into a shared
    `hub/framework/qnn_session.py` imported by both `face_id/face_pipeline.py`
    and `pose/pose_pipeline.py`, so there is one QNN entry point in the repo.
    Keep a thin alias in `face_pipeline` so its own CLI keeps working.
- Prefer `models/hrnet_pose_ctx.onnx` (context binary) when present: session
  init drops **6.0 s → 0.30 s**. Fall back to the raw QDQ model.
- Preprocess: aspect-preserving letterbox of the crop to 192×256, then the quantization from `metadata.json` (`scale=0.003917243331670761`, `zp=0`).
- Decode: per-channel argmax over the 17×64×48 heatmap + quarter-offset sub-pixel refinement, dequantized with `scale=0.0037365437019616365`, `zp=10`. Return keypoints in **crop-relative** coordinates plus the letterbox transform, so callers can map back.
- CLI self-test (`python hub/framework/pose/pose_pipeline.py benchmark <img>`), matching `face_pipeline.py`'s convention.

**`pose.py`** — `PoseBackend`, contract-identical to `FaceIdentityBackend`:
`__init__` cheap and import-free, `is_available()`, `warmup()`, `status()`,
`estimate(image_path) -> dict`, a `threading.Lock` around inference, and
**never raises** — returns `{"available": False, "error": ...}` on any
non-ARM64 host or missing model. Result shape:

```python
{"available": bool, "status": "ok"|"no_pose"|"unavailable",
 "keypoints": [[x, y, score], ...17] | None,   # crop-relative pixels
 "mean_score": float|None, "latency_s": float|None, "error": str|None}
```

**`overlay.py`** — `draw_pose_overlay(crop_jpeg, keypoints, label) -> bytes`,
using the COCO-17 skeleton edge list with a left-cool / right-warm colour scheme.

**`models/README.txt`** — copy the structure of
`hub/framework/face_id/models/README.txt`: list `hrnet_pose.onnx` (0.9 MB),
`hrnet_pose.data` (109 MB), `metadata.json`, and the generated
`hrnet_pose_ctx.onnx` (29 MB), plus the manual export steps as a fallback. All
gitignored — **nothing here is committed and nothing is vendored in**.

**`setup/setup_pose.ps1`** — models come from an AI Hub export only, exactly as
`face_id/setup/setup_npu.ps1` does for CavaFace. `hrnet_pose` is already in the
installed catalog (`hub/geniex-env/.../qai_hub_models/models/hrnet_pose/` with
its own `export.py`), so this mirrors the existing script rather than inventing
a flow:

1. **Export** — `qai-hub-models export hrnet_pose --target-runtime onnx --device $Device`,
   with the same `-Token` prompt-or-pass handling. Add `-HrnetPoseJobId jXXXXXXXX`
   to reuse a completed compile job and skip the recompile, matching
   `-CavaFaceJobId` / `-MediaPipeFaceJobId`. Reuse `setup_npu.ps1`'s
   `Download-From-Job` helper and its "export CLI didn't finish cleanly but a
   job was scheduled" recovery path.
2. **Rename** — the job output is `job_<id>_optimized_onnx/model.onnx` +
   `model.data`. As with CavaFace, the `.onnx` references its `.data` sidecar by
   literal filename, so a plain rename breaks the link — rewrite it via
   `onnx.save_model`, the way `setup_npu.ps1` already does.
3. **Compile the context binary** — explicit step, run after the export on the
   target host. **Gotcha to encode:** `ort.ModelCompiler` defaults to
   `ORT_DISABLE_ALL`, which fails with a NHWC layout-transform error
   (`Conv_token_61 ... com.ms.internal.nhwc ... not selected by that EP`). Must
   pass `graph_optimization_level=ort.GraphOptimizationLevel.ORT_ENABLE_ALL`.
   Takes ~4.6 s. SDK/HTP-specific — regenerate on the target, never commit; a
   stale binary degrades to a slower start, not a failure.

Wire the whole thing into `hub/setup_hub.ps1` next to the face-ID call, with a
`-SkipPose` switch and the same non-fatal failure handling (a pose-setup failure
warns; the hub still runs and reports `pose: unavailable`).

Add `hub/.gitignore` entries for `framework/pose/models/*` and the track-frames
directory.

## Phase 2 — Replace `/recognize` with `/track/analyze`

**`hub/framework/server.py`**
- Delete the `/recognize` route.
- Add `POST /track/analyze`. Reuse the existing `track_id` parsing (form →
  query → JSON) and `transport.save_incoming_image()`. Parse `analyzers` as a
  comma-separated list, defaulting to `face,pose`.
- Run only the requested analyzers; each contributes an independent sub-object
  so one being unavailable never fails the other.
- Delete the uploaded crop in a `finally`, exactly as `/recognize` does today.
- Response:

```json
{"track_id": 4,
 "face": {"identity": "Priya", "confidence": 0.93, "status": "known"},
 "pose": {"status": "ok", "keypoints": [[0,0,0]], "mean_score": 0.71},
 "latency_ms": {"face": 88.0, "pose": 1.5}}
```

- `face` keeps the exact status vocabulary the edge already parses:
  `known` | `unknown` | `no_face` | `unavailable`. `pose` uses
  `ok` | `no_pose` | `unavailable`.
- Extend `create_app(...)` with a `pose=None` parameter and add `pose` to
  `/health` alongside `vlm` / `llm` / `mqtt` / `face_id` / `sms`.

**`hub/server.py`** — construct `PoseBackend()`, pass it to `create_app`, add it
to the `QONCLAVE_WARMUP` block and the startup log banner (replace the
`POST /recognize` line with `POST /track/analyze`).

## Phase 3 — Retention + dashboard surface

**`hub/framework/track_store.py`** (new) — mirrors `framework/events.py`'s
module-level, lock-guarded, ring-buffer style:
- `record(track_id, face_result, pose_result, frame_name)` — appends
  `{ts, keypoints, mean_score, identity, status}` to a per-track
  `deque(maxlen=QONCLAVE_TRACK_HISTORY_MAX)` (default 150 ≈ 40 s at 4 Hz).
- `history(track_id)`, `snapshot()`, `prune(active_ids)`, `latest_frame(track_id)`.
- This buffer is what fall logic will read later; nothing consumes it yet
  beyond the dashboard.

**Annotated frames** — when `QONCLAVE_TRACK_FRAMES_ENABLED=1`, write
`track_<id>.jpg` into `hub/track_frames/` (one file per track, overwritten —
same convention as the edge's `save_crop_locally`). Cap total files at
`QONCLAVE_TRACK_FRAMES_MAX` (default 50) and prune on write.

**New routes in `framework/server.py`:**
- `GET /user/tracks` — `{track_id: {identity, status, latest_pose, history_len}}`
- `GET /user/tracks/<int:track_id>.jpg` — latest annotated frame

## Phase 4 — Edge sampler rework

**`python/analysis_client.py`** (replaces `recognition_client.py`) — keep the
`should_sample` / `claim` / `release` / `forget` structure and its careful
claim-before-crop-work ordering, but make the policy multi-analyzer:

```python
def analyzers_due(self, track_id, is_known, now=None) -> set[str]
def claim(self, track_id, analyzers, now=None) -> None      # per-analyzer stamps
def send_claimed(self, track_id, crop_jpeg, analyzers, on_result) -> None
```

`_last_sent_at` becomes `dict[track_id, dict[analyzer, float]]`. `_in_flight`
stays per-track (one request carries all due analyzers). The synthetic error
result on failure keeps the same shape so callers still need no error path.

**`python/track_crop.py`** — two changes.

*1. Return the person box's position inside the crop.* The crop sent today is
the full person box, but it is framed for **face** detection: `padding=0.25` on
left/right/bottom and `padding_top=0.8` above the head, so a face at the box's
top edge isn't clipped. That is correct for MediaPipe and should not change.
It is wrong for pose:

- crop height = `box_h × (1 + 0.8 + 0.25)` = **2.05 × box_h**
- the person fills only **~49% of the crop**, offset into its lower half
- letterboxed into 192×256, the subject lands at roughly **half** the model's
  usable input height

Top-down pose models expect a tight box (~1.25× expansion, subject filling the
frame), so this costs keypoint accuracy for no reason. Rather than send a second
crop, `crop_person` returns the unpadded box's rect **relative to the crop**:

```python
def crop_person(...) -> "tuple[bytes, tuple[int,int,int,int]] | None"
    # ... existing padding/clamping ...
    person_box_in_crop = (int(x1) - ix1, int(y1) - iy1,
                          int(x2) - ix1, int(y2) - iy1)
    return encoded.tobytes(), person_box_in_crop
```

`main.py` sends it as a `person_box` form field on `/track/analyze`.
`PoseBackend` expands that rect by the standard 1.25 and letterboxes **it**
(not the whole crop) to 192×256; face ID keeps using the full crop untouched.
One crop, one request, both analyzers correctly framed. If `person_box` is
absent the hub falls back to the whole crop, so the field stays optional.

*2. Per-analyzer rejection thresholds.* `min_size_px=40` and
`min_visible_ratio=0.85` are also face-tuned. A 40 px person upscaled to a
256 px input is a 6× stretch — enough to answer "is there a face", useless for
limb positions. And `min_visible_ratio` is actively harmful for fall detection:
a person who has fallen near the frame edge is exactly the event of interest,
and 0.85 silently drops them. Split the gate so a crop is sent if **either**
analyzer would accept it, and let the hub skip whichever analyzer the crop is
too poor for:

| | `min_size_px` | `min_visible_ratio` |
|---|---|---|
| face (unchanged) | 40 | 0.85 |
| pose | ~100 (box height) | ~0.5 |

*3. Decode once.* Add `crop_persons(frame_jpeg, boxes) -> dict` that decodes the
frame **once** and slices every due track from it. At 4 Hz across N tracks the
current one-`cv2.imdecode`-per-track-per-sample cost becomes significant on the
UNO Q. Keep `crop_person` as a single-box wrapper so existing tests hold —
though its return type changes, so `test_track_crop.py` needs updating either way.

**`python/main.py`**
- Swap the per-track thread spawn for **one** background thread per detection
  callback that decodes once, crops all due tracks, and posts them.
- Handle the new response shape: merge `result["face"]` into `identity_map`
  (unchanged sticky rule), keep `result["pose"]` in a small per-track latest-pose
  dict, and push both to the Web UI (`identity_map` message stays; add
  `pose_status`).
- Keep `_live_track_ids` staleness filtering exactly as-is.
- New env vars: `POSE_SAMPLE_INTERVAL_SEC=0.25`, `TRACK_ANALYZERS=face,pose`.

**`python/track_overlay.py`** — currently orphaned (written, no importer, no
`/track-preview` route). Leave it untouched; the skeleton overlay lives on the
hub where the keypoints are. Flag it for a separate decision.

## Phase 5 — Docs and config

- `edge/.../.env.example` — replace the `RECOGNITION_*` block with the
  `TRACK_ANALYZERS` / `POSE_SAMPLE_INTERVAL_SEC` set.
- `edge/.../README.md` — update the recognition section for the unified endpoint.
- `hub/README.md` — replace the `/recognize` row in the endpoint table with
  `/track/analyze`, `/user/tracks`, `/user/tracks/<id>.jpg`; document the pose
  backend and its env vars; extend the architecture mermaid diagram with
  `PoseBackend` and `TrackStore`.
- `docs/scratch.txt` — mark roadmap item 3's pipeline half as done.

## Tests

| File | Status |
|---|---|
| `hub/tests/test_recognize_endpoint.py` | **Rewrite** → `test_track_analyze_endpoint.py`. Keep the stub-backend approach and the crop-is-deleted assertion; add a stub `PoseBackend`, per-analyzer selection, and partial-availability cases. |
| `hub/tests/test_pose_backend.py` | New — `status()` / `estimate()` contract on a host with no model present (must report unavailable, never raise). |
| `hub/tests/test_track_store.py` | New — ring-buffer cap, prune, snapshot shape. |
| `edge/.../test_recognition_client.py` | **Rewrite** → `test_analysis_client.py`. Port all 8 existing cases; add: pose stays due after face goes known; one in-flight blocks both; per-analyzer interval independence. |
| `edge/.../test_track_crop.py` | Extend for `crop_persons` multi-box. |

All tests keep the repo's dependency-free `run_all()` + `if __name__ ==
"__main__"` convention — no pytest requirement.

## Verification

1. **Unit** (any machine, no models):
   `python hub/tests/test_track_analyze_endpoint.py`,
   `test_pose_backend.py`, `test_track_store.py`,
   `python edge/.../test_analysis_client.py`, `test_track_crop.py`.
2. **Pose on real hardware** (Snapdragon hub):
   `python hub/framework/pose/pose_pipeline.py benchmark <crop.jpg>` — expect
   ~1.4 ms/inference and ~0.3 s session init once the context model is built.
   Confirm `/health` reports `pose.available: true, mode: npu`.
3. **Endpoint**:
   `curl -F "image=@crop.jpg" -F "track_id=4" -F "analyzers=face,pose" http://localhost:8000/track/analyze`
   → both sub-objects populated; confirm the upload is gone from `hub/uploads/`.
4. **End to end**: start the hub, run the edge app with `CAMERA_SOURCE=file`
   against a clip with a single walking person whose scale changes across the
   clip (good for exercising the crop-rejection thresholds). Watch hub logs for
   ~4 Hz `/track/analyze` per track, check `GET /user/tracks` returns a growing
   history, and open `/user/tracks/1.jpg` for the skeleton.
5. **Degradation**: run the hub on x86 or with the model absent — pose must
   report `unavailable` while face and the rest of the hub keep working.

## Risks

- **Edge CPU at 4 Hz.** The single-decode change in Phase 4 is the mitigation;
  if the UNO Q still struggles, raise `POSE_SAMPLE_INTERVAL_SEC` first (fewer
  samples per second) — the hub side has enormous headroom (1.45 ms/inference).
- **Context-binary portability.** `hrnet_pose_ctx.onnx` is tied to the QAIRT
  build and HTP arch that produced it. Regenerate via `setup_pose.ps1` on each
  host; the raw-model fallback keeps things working if it's stale.
- **Silent CPU fallback.** `_qnn_session()` catches every QNN failure and drops
  to `CPUExecutionProvider`, so a missing model or unregistered EP looks like
  "working, just slow" (~45 ms vs ~1.4 ms). `PoseBackend.status()` must report
  the resolved mode (`npu`/`cpu`) the way `FaceIdentityBackend.status()` does,
  and the benchmark step in Verification is what confirms it.
- **AI Hub token is now a hard prerequisite.** With no vendored model files,
  nothing pose-related runs until an export completes. `-SkipPose` and the
  `pose: unavailable` degradation path are what keep the hub usable meanwhile.
- **No caller compatibility.** Replacing `/recognize` outright means the edge
  and the hub must be deployed together; there is no transition window.
