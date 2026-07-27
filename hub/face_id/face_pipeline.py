"""
Face identification pipeline: MediaPipe (detection) + CavaFace (embedding)

Works on:
  - Linux x86_64          (CPU)
  - Windows x86_64        (CPU)
  - Windows ARM64 (WoS)   (CPU or NPU via QNNExecutionProvider)
  - macOS ARM64           (CPU)

NPU mode (Snapdragon X Elite):
  1. Export model on any machine with AI Hub account:
       qai-hub configure --api_token YOUR_TOKEN
       qai-hub-models export cavaface --target-runtime onnx --device "Snapdragon X Elite"
     This produces build/CavaFace.onnx in the current directory.

  2. Copy CavaFace.onnx to hub/face_id/models/CavaFace.onnx

  3. Run with --npu flag:
       python face_pipeline.py --npu identify --image unknown.jpg

Usage:
  python face_pipeline.py compare  image1.jpg image2.jpg
  python face_pipeline.py identify image.jpg --db ./known_faces/
  python face_pipeline.py benchmark image.jpg
  python face_pipeline.py --npu compare image1.jpg image2.jpg
"""

import argparse
import platform
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image

# ── Paths ─────────────────────────────────────────────────────────────────────

MODELS_DIR          = Path(__file__).parent / "models"
CAVAFACE_ONNX_PATH  = MODELS_DIR / "CavaFace.onnx"

MEDIAPIPE_MODEL_URL  = (
    "https://storage.googleapis.com/mediapipe-models/face_detector/"
    "blaze_face_short_range/float16/1/blaze_face_short_range.tflite"
)
MEDIAPIPE_MODEL_PATH = Path(__file__).parent / "face_detector.tflite"

THRESHOLD = 0.3   # cosine similarity threshold for same/different person

# ── CavaFace: CPU (qai-hub-models) ───────────────────────────────────────────

def _build_cavaface_cpu():
    from qai_hub_models.models.cavaface.model import CavaFace
    from qai_hub_models.models.cavaface.app import CavaFaceApp

    model = CavaFace.from_pretrained()
    return CavaFaceApp(model, input_height=112, input_width=112)


def _embed_cpu(app, face_img: Image.Image) -> np.ndarray:
    return app.predict_features(
        face_img.convert("RGB").resize((112, 112), Image.LANCZOS),
        use_flip=True,
    )


# ── CavaFace: NPU (onnxruntime-qnn + QNNExecutionProvider) ───────────────────

def _build_cavaface_npu():
    """Load CavaFace ONNX on Hexagon NPU via QNNExecutionProvider."""
    if not CAVAFACE_ONNX_PATH.exists():
        raise FileNotFoundError(
            f"NPU model not found: {CAVAFACE_ONNX_PATH}\n"
            "Export it first:\n"
            "  qai-hub configure --api_token YOUR_TOKEN\n"
            "  qai-hub-models export cavaface --target-runtime onnx "
            "--device \"Snapdragon X Elite\"\n"
            "Then copy the resulting CavaFace.onnx to hub/face_id/models/"
        )

    try:
        import onnxruntime as ort
    except ImportError:
        raise ImportError("onnxruntime-qnn not installed. Run: pip install onnxruntime-qnn")

    # QnnHtp.dll = Hexagon NPU backend (ships with QNN SDK / Qualcomm AI Stack)
    # Falls back to CPU if HTP not available
    providers = [
        ("QNNExecutionProvider", {"backend_path": "QnnHtp.dll"}),
        "CPUExecutionProvider",
    ]

    try:
        session = ort.InferenceSession(str(CAVAFACE_ONNX_PATH), providers=providers)
        active = session.get_providers()[0]
        print(f"  CavaFace running on: {active}")
    except Exception:
        # If QNN fails (e.g. QnnHtp.dll not on PATH), fall back to CPU ONNX
        print("  [!] QNNExecutionProvider unavailable, falling back to CPU ONNX")
        session = ort.InferenceSession(
            str(CAVAFACE_ONNX_PATH),
            providers=["CPUExecutionProvider"],
        )

    input_name = session.get_inputs()[0].name
    return session, input_name


def _embed_npu(session_tuple, face_img: Image.Image) -> np.ndarray:
    session, input_name = session_tuple
    face = face_img.convert("RGB").resize((112, 112), Image.LANCZOS)
    arr  = np.array(face, dtype=np.float32) / 255.0
    inp  = arr.transpose(2, 0, 1)[np.newaxis]  # [1, 3, 112, 112]

    emb      = session.run(None, {input_name: inp})[0].squeeze()
    # Flip augmentation for better accuracy
    emb_flip = session.run(None, {input_name: inp[:, :, :, ::-1].copy()})[0].squeeze()
    emb = (emb + emb_flip) / 2
    norm = np.linalg.norm(emb) + 1e-9
    return emb / norm


# ── MediaPipe face detector (CPU on all platforms) ───────────────────────────

def _ensure_mp_model():
    if not MEDIAPIPE_MODEL_PATH.exists():
        print("Downloading MediaPipe face detector (~228KB)...")
        urllib.request.urlretrieve(MEDIAPIPE_MODEL_URL, MEDIAPIPE_MODEL_PATH)


def _build_detector():
    import mediapipe as mp
    from mediapipe.tasks.python.vision import FaceDetector, FaceDetectorOptions
    from mediapipe.tasks.python.core.base_options import BaseOptions

    _ensure_mp_model()
    # Note: MediaPipe on Windows has no QNN/GPU delegate — CPU only
    options = FaceDetectorOptions(
        base_options=BaseOptions(model_asset_path=str(MEDIAPIPE_MODEL_PATH)),
        min_detection_confidence=0.4,
    )
    return FaceDetector.create_from_options(options)


def detect_and_crop_face(detector, pil_img: Image.Image, padding: float = 0.3) -> "Image.Image | None":
    import mediapipe as mp

    rgb      = pil_img.convert("RGB")
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.array(rgb))
    result   = detector.detect(mp_image)

    if not result.detections:
        return None

    det   = max(result.detections, key=lambda d: d.categories[0].score)
    bb    = det.bounding_box
    w, h  = rgb.size
    pad_x = int(bb.width * padding)
    pad_y = int(bb.height * padding)
    x1    = max(0, bb.origin_x - pad_x)
    y1    = max(0, bb.origin_y - pad_y)
    x2    = min(w, bb.origin_x + bb.width + pad_x)
    y2    = min(h, bb.origin_y + bb.height + pad_y)

    return rgb.crop((x1, y1, x2, y2)).resize((112, 112), Image.LANCZOS)


# ── Unified embedding call ────────────────────────────────────────────────────

def get_embedding(detector, model, image_path: str, use_npu: bool) -> "np.ndarray | None":
    img  = Image.open(image_path)
    face = detect_and_crop_face(detector, img)
    if face is None:
        print(f"  [!] No face detected in {image_path}")
        return None
    return _embed_npu(model, face) if use_npu else _embed_cpu(model, face)


# ── Modes ─────────────────────────────────────────────────────────────────────

def mode_compare(detector, model, image1: str, image2: str, use_npu: bool):
    print(f"\nComparing:\n  A: {image1}\n  B: {image2}\n")
    t0 = time.time()
    e1 = get_embedding(detector, model, image1, use_npu)
    e2 = get_embedding(detector, model, image2, use_npu)
    elapsed = (time.time() - t0) * 1000

    if e1 is None or e2 is None:
        print("Cannot compare - face not detected.")
        return

    sim   = float(np.dot(e1, e2))
    label = "SAME PERSON" if sim > THRESHOLD else "different person"
    print(f"Similarity : {sim * 100:.1f}%")
    print(f"Result     : {label}  (threshold={THRESHOLD*100:.0f}%)")
    print(f"Time       : {elapsed:.0f}ms")


def mode_identify(detector, model, unknown_path: str, db_dir: str, use_npu: bool):
    db   = Path(db_dir)
    exts = {".jpg", ".jpeg", ".png", ".webp"}

    print(f"\nBuilding face database from: {db}")
    known = {}
    for img_path in sorted(db.rglob("*")):
        if img_path.suffix.lower() not in exts:
            continue
        emb = get_embedding(detector, model, str(img_path), use_npu)
        if emb is not None:
            known[img_path.stem] = emb
            print(f"  enrolled: {img_path.stem}")

    if not known:
        print("No faces enrolled - check db path and image files.")
        return

    print(f"\nIdentifying: {unknown_path}")
    unknown_emb = get_embedding(detector, model, unknown_path, use_npu)
    if unknown_emb is None:
        return

    scores    = {name: float(np.dot(emb, unknown_emb)) for name, emb in known.items()}
    best_name = max(scores, key=scores.get)
    best_score = scores[best_name]

    print("\n--- Scores ---")
    for name, score in sorted(scores.items(), key=lambda x: -x[1]):
        marker = " <- best match" if name == best_name else ""
        print(f"  {name:30s}  {score*100:.1f}%{marker}")

    print()
    if best_score > THRESHOLD:
        print(f"Identified as : {best_name}  ({best_score*100:.1f}%)")
    else:
        print(f"Unknown person  (best '{best_name}' only {best_score*100:.1f}%)")


def mode_benchmark(detector, model, image_path: str, runs: int, use_npu: bool):
    img  = Image.open(image_path)
    face = detect_and_crop_face(detector, img)
    if face is None:
        print("No face detected.")
        return

    embed_fn = (lambda f: _embed_npu(model, f)) if use_npu else (lambda f: _embed_cpu(model, f))
    embed_fn(face)  # warmup

    times_det, times_emb = [], []
    for _ in range(runs):
        t0 = time.time()
        detect_and_crop_face(detector, img)
        times_det.append((time.time() - t0) * 1000)

        t0 = time.time()
        embed_fn(face)
        times_emb.append((time.time() - t0) * 1000)

    total = [d + e for d, e in zip(times_det, times_emb)]
    print(f"\n--- Benchmark ({runs} runs) ---")
    print(f"{'Step':<22} {'Avg':>8} {'Min':>8} {'Max':>8}")
    print(f"{'Face detection':<22} {np.mean(times_det):>7.1f}ms {np.min(times_det):>7.1f}ms {np.max(times_det):>7.1f}ms")
    print(f"{'CavaFace embed':<22} {np.mean(times_emb):>7.1f}ms {np.min(times_emb):>7.1f}ms {np.max(times_emb):>7.1f}ms")
    print(f"{'Total':<22} {np.mean(total):>7.1f}ms {np.min(total):>7.1f}ms {np.max(total):>7.1f}ms")
    print(f"\nEstimated FPS : {1000/np.mean(total):.1f}")
    print(f"Mode          : {'NPU' if use_npu else 'CPU'}")
    print(f"Platform      : {platform.system()} {platform.machine()}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Face identification: MediaPipe + CavaFace")
    parser.add_argument("--npu", action="store_true",
                        help="Use NPU (requires CavaFace.onnx in models/ folder)")
    sub = parser.add_subparsers(dest="mode")

    p = sub.add_parser("compare",   help="Compare two face images")
    p.add_argument("image1")
    p.add_argument("image2")

    p = sub.add_parser("identify",  help="Identify face against a database folder")
    p.add_argument("image")
    p.add_argument("--db", default="./known_faces/")

    p = sub.add_parser("benchmark", help="Benchmark end-to-end latency")
    p.add_argument("image")
    p.add_argument("--runs", type=int, default=20)

    # Shorthand flags
    parser.add_argument("--image1")
    parser.add_argument("--image2")
    parser.add_argument("--identify")
    parser.add_argument("--db", default="./known_faces/")

    args = parser.parse_args()

    use_npu = args.npu
    mode    = platform.system() + " " + platform.machine()
    print(f"Platform : {mode}  Python {sys.version.split()[0]}")
    print(f"Mode     : {'NPU (QNNExecutionProvider)' if use_npu else 'CPU (PyTorch)'}")
    print("Loading models...")

    t0       = time.time()
    detector = _build_detector()
    model    = _build_cavaface_npu() if use_npu else _build_cavaface_cpu()
    print(f"Ready in {time.time()-t0:.1f}s\n")

    if args.mode == "compare" or (args.image1 and args.image2):
        mode_compare(detector, model, args.image1, args.image2, use_npu)
    elif args.mode == "identify" or args.identify:
        mode_identify(detector, model, args.identify or args.image, args.db, use_npu)
    elif args.mode == "benchmark":
        mode_benchmark(detector, model, args.image, args.runs, use_npu)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
