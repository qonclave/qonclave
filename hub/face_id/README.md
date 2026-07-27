# Face Identification — MediaPipe + CavaFace

Identifies faces against a database of known people.
Works on Windows x86, Windows ARM64 (WoS / Snapdragon X), Linux, macOS.

---

## Setup

### Step 1 — Run setup script (once)

**Windows (x86 or ARM64):**
```powershell
cd hub\face_id
.\setup.ps1
```
On ARM64 (Snapdragon X) this automatically exports both AI models for NPU — it
will prompt for your Qualcomm AI Hub token (free at https://workbench.aihub.qualcomm.com,
then Account → Settings → API Token). Takes ~10 minutes on first run.

On x86 it installs CPU dependencies only.

**Linux / macOS:**
```bash
cd hub/face_id
chmod +x setup.sh && ./setup.sh
```

### Step 2 — Add known faces

Drop one clear front-facing photo per person into `known_faces/`, named as the person:
```
known_faces/
  mahesh_babu.jpg    (already included)
  pawan_kalyan.jpg   (already included)
  your_name.jpg      (add your own)
```
Supported formats: `.jpg` `.jpeg` `.png` `.webp`

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
hub/face_id/
  face_pipeline.py            main script
  setup.ps1                   Windows one-time setup
  setup.sh                    Linux/macOS one-time setup
  setup_npu.ps1               called by setup.ps1 on ARM64 (NPU export)
  build_opencv_arm64.ps1      build opencv from source for ARM64 if needed
  constraints.txt             forces opencv-headless (no ARM64 issue)
  face_detector.tflite        auto-downloaded by setup script
  known_faces/
    mahesh_babu.jpg
    pawan_kalyan.jpg
    .embeddings_cpu.npy       auto-generated cache, gitignored
    .embeddings_npu.npy       auto-generated cache, gitignored
  models/                     populated by setup.ps1 on ARM64
    CavaFace.onnx
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

**opencv install fails on ARM64**
→ Run `build_opencv_arm64.ps1` to build from source, then re-run `setup.ps1`.
