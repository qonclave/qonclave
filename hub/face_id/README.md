# Face Identification — MediaPipe + CavaFace

Identifies faces against a database of known people.
Works on Windows x86, Windows ARM64 (WoS), Linux, macOS — no GPU required.

## Setup

### Windows
```powershell
.\run.ps1
```

### Linux / macOS
```bash
chmod +x run.sh
./run.sh
```

> **Note:** `opencv-python-headless` is installed first to prevent mediapipe
> from pulling in `opencv-contrib-python` (full GUI build, ~90MB, not needed).

## Add known faces

Drop one clear face photo per person into `known_faces/`, named as the person:

```
known_faces/
  alice.jpg
  bob.jpg
  charlie.jpg
```

One photo per person is enough. Use a clear, front-facing photo.

## Usage

### Compare two images
```bash
# Linux/macOS
./run.sh compare photo1.jpg photo2.jpg

# Windows
.\run.ps1 compare -Image1 photo1.jpg -Image2 photo2.jpg
```

### Identify a person
```bash
# Linux/macOS
./run.sh identify unknown.jpg

# Windows
.\run.ps1 identify -Image unknown.jpg
```

### Benchmark speed
```bash
# Linux/macOS
./run.sh benchmark photo.jpg

# Windows
.\run.ps1 benchmark -Image photo.jpg
```

## How it works

```
Input image
    ↓
MediaPipe FaceDetector   — finds face bounding box (~10ms CPU / ~1ms NPU)
    ↓
Crop + resize to 112×112
    ↓
CavaFace                 — generates 512-dim face embedding (~250ms CPU / ~4ms NPU)
    ↓
Cosine similarity        — compare against known_faces/ embeddings
    ↓
Name or "Unknown"
```

## Dependencies

| Package | Purpose |
|---------|---------|
| `mediapipe` | Face detection (NPU-ready via TFLite) |
| `qai-hub-models[cavaface]` | CavaFace face embedding model |
| `pillow` | Image loading |
| `numpy` | Embedding comparison |

## Similarity threshold

Default threshold is `0.3` (30%). Tune in `face_pipeline.py`:
- Lower → more permissive (fewer "Unknown" results)
- Higher → more strict (fewer false positives)
