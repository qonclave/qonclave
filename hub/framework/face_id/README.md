# Face Identification — MediaPipe + CavaFace

Identifies faces against a database of known people.
Works on Windows x86, Windows ARM64 (WoS / Snapdragon X), Linux, macOS.

---

## Setup

### Step 1 — Run setup script (once)

**Using this from the Qonclave hub? You don't run anything here.**
`hub/setup_hub.ps1` already calls this script for you, passing its own
`geniex-env` interpreter — which is what you want, because `hub/server.py`
imports `face_id.identity.FaceIdentityBackend` in-process (see
`hub/README.md`), so face-ID's dependencies have to live in the environment
that actually runs the hub server, not this machine's system Python:

```powershell
.\hub\setup_hub.ps1 -AiHubToken YOUR_TOKEN
```

See the root `README.md` for its `-SkipFaceId` / job-ID flags. Everything
below is for running face_id **standalone**, with no hub involved.

**Windows (x86 or ARM64), standalone:**
```powershell
cd hub\framework\face_id\setup
.\setup.ps1
```
This installs into whatever `python` resolves to on PATH. To target a specific
interpreter instead, pass it explicitly:
```powershell
.\setup.ps1 -PythonPath C:\path\to\python.exe
```

On ARM64 (Snapdragon X) this automatically exports both AI models for NPU — it
will prompt for your Qualcomm AI Hub token (free at https://workbench.aihub.qualcomm.com,
then Account → Settings → API Token), or pass it with `-Token YOUR_TOKEN`.
Takes ~10 minutes on first run.

On x86 it installs CPU dependencies only.

**Linux / macOS:**
```bash
cd hub/framework/face_id/setup
chmod +x setup.sh && ./setup.sh
```

#### Reusing an existing NPU export (faster re-setup)

Compiling `MediaPipeFace` and `CavaFace` submits jobs to Qualcomm AI Hub's
cloud and can take several minutes each (model upload, compile, and — unless
skipped — profiling/inference on a real device queue). A compiled job's
result stays downloadable by job ID indefinitely (until AI Hub garbage-collects
it), so re-running setup on the same machine/account doesn't need to
recompile from scratch:

```powershell
cd hub\framework\face_id\setup
.\setup_npu.ps1 -Token YOUR_TOKEN `
  -MediaPipeFaceJobId jpeyev475 `
  -CavaFaceJobId jg9dj44q5
```

This skips installing the full `qai-hub-models`/torch dependency stack (only
the lightweight `qai_hub` client is needed to look up and download an
existing job) and skips the compile/profile/inference cloud wait entirely —
it just downloads the already-compiled `.onnx` directly. You can pass either
flag alone to reuse one model while exporting the other fresh.

**Passing both flags is what actually skips the torch install.** `setup.ps1`
needs `qai-hub-models` for two things: exporting models, and the CPU embedder.
With both jobs reused, neither applies on ARM64 — NPU inference only ever
touches `onnxruntime-qnn` — so Step 3 skips it entirely. With one flag or
none, the exporter is still required and the full stack is installed.

The trade-off is the CPU embedder fallback. `identity.py` picks its mode from
whether `CavaFace.onnx` exists; without `qai_hub_models` installed there is no
PyTorch path to fall back to, so a missing `CavaFace.onnx` means face-ID
reports unavailable rather than running slowly. Re-run `setup.ps1` without the
job-ID flags to get that safety net back.

The job IDs above are **this repo's own verified-working exports** (`jpeyev475`
= MediaPipeFace face detector, compiled with `--include-detector-postprocessing`
so the graph itself does anchor-decoding — see `face_pipeline.py`'s `_detect_npu`;
`jg9dj44q5` = CavaFace). They only resolve for the AI Hub account/token that
created them — a different developer's token can't look them up. If you get a
"not found" error, they've likely been garbage-collected or belong to a
different account; just omit the job ID flags to export fresh.

### Step 2 — Add known faces

Drop one clear front-facing photo per person into `known_faces/`, named as the person:
```
known_faces/
  mahesh_babu.jpg    (already included)
  pawan_kalyan.jpg   (already included)
  your_name.jpg      (add your own)
```
Supported formats: `.jpg` `.jpeg` `.png` `.webp`

**Or enroll from the hub dashboard** — the security app's dashboard has an
"Enroll a known face" card (name + photo). It posts to `POST /user/known_faces`,
which calls `FaceIdentityBackend.enroll(name, image_path)`: the name is
slugified to the same `first_last` filename convention, the photo is validated
to contain a detectable face (when the model is loaded), saved into
`known_faces/`, and the embeddings cache is invalidated so the **next inference
recognizes the new person automatically** — no restart. Re-enrolling a name
replaces that person's existing photo. `GET /user/known_faces` returns the
current roster.

---

## Running

All modes are run directly via Python:

### Identify a person
```bash
# CPU (any machine)
python face_pipeline.py identify unknown.jpg

# NPU (Snapdragon X, after setup.ps1)
python face_pipeline.py --npu identify unknown.jpg
```

### Compare two photos (same person?)
```bash
python face_pipeline.py compare photo1.jpg photo2.jpg
python face_pipeline.py --npu compare photo1.jpg photo2.jpg
```

### Benchmark speed
```bash
python face_pipeline.py benchmark photo.jpg
python face_pipeline.py --npu benchmark photo.jpg --runs 20
```

---

## How it works

```
Input image
    |
    v
Face Detection (MediaPipe)
    CPU : BlazeFace TFLite        ~15ms
    NPU : MediaPipeFace.onnx      ~0.7ms  (QNNExecutionProvider)
    |
    v
Crop + resize to 112x112
    |
    v
Face Embedding (CavaFace)
    CPU : PyTorch weights         ~250ms
    NPU : CavaFace.onnx + .data   ~4.3ms  (QNNExecutionProvider)
    |
    v
Cosine similarity vs known_faces/ embeddings
    |
    v
"Identified as: mahesh_babu (45.2%)"  or  "Unknown person"
```

**Multiple faces per frame** — the detector returns every face it finds, not
just the most prominent one. The CLI `identify` mode prints a per-face verdict
(bbox, best match, known/unknown) for each. In-process, `identity.py` exposes
two entry points:

- `identify(image_path)` — single best face, flat dict (unchanged).
- `identify_all(image_path)` — one result per detected face:
  `{"face_count": N, "faces": [{"name", "confidence", "identified", "bbox",
  "scores", "detector_score"}, ...]}`, highest detector-confidence first.

So a two-person frame yields e.g. `mahesh_babu` + `unknown` together. On CPU
(MediaPipe) the detector already de-duplicates; on NPU (raw BlazeFace anchors)
`face_pipeline.py` applies non-max suppression so each real face counts once.
The security app (`apps/security/policy.py`) uses `identify_all` and summarizes
all faces into `identity_status` (e.g. `"2 faces: mahesh_babu (98%), unknown"`).

**Known face embeddings are cached** after first run — subsequent calls load
from `known_faces/.embeddings_cpu.npy` (or `_npu.npy`) instantly. Cache
auto-rebuilds when you add or replace photos.

---

## Speed

| Step              | CPU      | NPU     |
|-------------------|----------|---------|
| Face detection    | ~15ms    | ~0.7ms  |
| Face embedding    | ~250ms   | ~4.3ms  |
| DB lookup (cache) | <1ms     | <1ms    |
| **Total**         | **~265ms** | **~5ms** |

---

## File layout

```
hub/framework/face_id/
  face_pipeline.py            main script / CLI
  identity.py                 FaceIdentityBackend, imported by hub/server.py
  setup/
    setup.ps1                 Windows one-time setup
    setup.sh                  Linux/macOS one-time setup
    setup_npu.ps1             called by setup.ps1 on ARM64 (NPU export)
    constraints.txt           pip constraints: forces opencv-headless
  tools/
    build_opencv_arm64.ps1    build opencv from source for ARM64 if needed
  known_faces/
    mahesh_babu.jpg
    pawan_kalyan.jpg
    .embeddings_cpu.npy       auto-generated cache, gitignored
    .embeddings_npu.npy       auto-generated cache, gitignored
  models/                     all model files, gitignored
    face_detector.tflite      CPU detector. Pre-fetched by setup on x86; on
                              ARM64 fetched on demand, only if the NPU
                              detector is unavailable
    CavaFace.onnx             NPU, populated by setup.ps1 on ARM64
    CavaFace.data             (~250MB)
    MediaPipeFace.onnx
  wheels/
    opencv_python_headless-*-win_arm64.whl   pre-built ARM64 wheel
```

---

## Dependencies

| Package | Purpose |
|---------|---------|
| `opencv-python-headless` | cv2 (required by qai-hub-models internals) |
| `mediapipe` | Face detection on CPU |
| `qai-hub-models[cavaface]` | CavaFace embedding model |
| `onnxruntime-qnn` | NPU inference on Snapdragon X (ARM64 only) |
| `pillow` | Image loading |
| `numpy` | Embedding comparison |

---

## Tuning

**Similarity threshold** (default 0.3 = 30%):
```python
# face_pipeline.py line ~50
THRESHOLD = 0.3
```
- Lower → fewer "Unknown" results (more permissive)
- Higher → fewer false positives (more strict)

**Force cache rebuild** (after adding/replacing known faces):
```bash
del known_faces\.embeddings_cpu.npy    # Windows
rm known_faces/.embeddings_cpu.npy     # Linux/macOS
```

---

## Troubleshooting

**"No face detected"**
→ Use a clear, front-facing photo with good lighting.

**"Unknown person" for a known person**
→ Lower threshold to `THRESHOLD = 0.2`, or add more reference photos.

**NPU not activating**
→ Run `setup.ps1` on the Snapdragon X machine to export models.
→ Verify `models/CavaFace.onnx` and `models/MediaPipeFace.onnx` exist.
→ Note: onnxruntime's QNN support is a dynamically-registered "plugin"
execution provider — passing `"QNNExecutionProvider"` as a plain string to
`InferenceSession(providers=...)` silently falls back to CPU on current
onnxruntime versions. `face_pipeline.py`'s `_qnn_session()` handles the real
registration sequence (`register_execution_provider_library` +
`SessionOptions.add_provider_for_devices` against the actual NPU `OrtEpDevice`).
If you're debugging this yourself, check `session.get_providers()[0]` —
if it prints `CPUExecutionProvider`, the NPU device wasn't found/bound.

**opencv install fails on ARM64**
→ `setup.ps1` installs opencv from the pre-built wheel in `wheels/`. If that
wheel is missing or doesn't match your Python version, run
`tools\build_opencv_arm64.ps1` to build one from source (~30-50 min, needs the
VS 2022 ARM64 toolchain — the script installs it), copy the resulting `.whl`
into `wheels/`, then re-run `setup\setup.ps1`.
