r"""
Face identification pipeline: MediaPipe (detection) + CavaFace (embedding)

Works on:
  - Linux x86_64          (CPU)
  - Windows x86_64        (CPU)
  - Windows ARM64 (WoS)   (CPU or full NPU via QNNExecutionProvider)
  - macOS ARM64           (CPU)

NPU mode (Snapdragon X Elite -- ~5ms total vs ~265ms CPU):
  Run setup.ps1 once on the Snapdragon X machine.
  Then run with --npu:
    python face_pipeline.py --npu identify unknown.jpg

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

MODELS_DIR               = Path(__file__).parent / "models"
CAVAFACE_ONNX_PATH       = MODELS_DIR / "CavaFace.onnx"
CAVAFACE_DATA_PATH       = MODELS_DIR / "CavaFace.data"
MEDIAPIPE_NPU_ONNX_PATH  = MODELS_DIR / "MediaPipeFace.onnx"

MEDIAPIPE_MODEL_URL  = (
    "https://storage.googleapis.com/mediapipe-models/face_detector/"
    "blaze_face_short_range/float16/1/blaze_face_short_range.tflite"
)
MEDIAPIPE_MODEL_PATH = MODELS_DIR / "face_detector.tflite"

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

def _qnn_session(onnx_path: Path, label: str):
    """Create an InferenceSession on the Hexagon NPU via onnxruntime-qnn.

    onnxruntime's QNN support is a dynamically-registered "plugin" execution
    provider (added in the 1.20+ device-based EP API): the provider library
    must be registered by path, then bound to the actual NPU OrtEpDevice via
    SessionOptions.add_provider_for_devices — passing "QNNExecutionProvider"
    as a plain string to InferenceSession(providers=...) silently no-ops and
    falls back to CPU on this onnxruntime version.
    """
    import onnxruntime as ort

    try:
        import onnxruntime_qnn as qnn

        try:
            ort.register_execution_provider_library(qnn.get_ep_name(), qnn.get_library_path())
        except Exception:
            pass  # already registered from a previous _qnn_session() call

        npu_devices = [
            d for d in ort.get_ep_devices()
            if d.ep_name == qnn.get_ep_name() and d.device.type == ort.OrtHardwareDeviceType.NPU
        ]
        if not npu_devices:
            raise RuntimeError("no QNN NPU device found")

        so = ort.SessionOptions()
        so.add_provider_for_devices(npu_devices, {"backend_path": qnn.get_qnn_htp_path()})
        session = ort.InferenceSession(str(onnx_path), sess_options=so)
        print(f"  {label} running on: {session.get_providers()[0]}")
        return session
    except Exception as e:
        print(f"  [!] QNNExecutionProvider unavailable for {label} ({e}), falling back to CPU ONNX")
        return ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])


def _build_cavaface_npu():
    """Load CavaFace ONNX on Hexagon NPU via QNNExecutionProvider."""
    if not CAVAFACE_ONNX_PATH.exists():
        raise FileNotFoundError(
            f"NPU model not found: {CAVAFACE_ONNX_PATH}\n"
            "Export it first:\n"
            "  qai-hub configure --api_token YOUR_TOKEN\n"
            "  qai-hub-models export cavaface --target-runtime onnx "
            "--device \"Snapdragon X Elite\"\n"
            "Then copy the resulting CavaFace.onnx to hub/framework/face_id/models/"
        )

    session = _qnn_session(CAVAFACE_ONNX_PATH, "CavaFace")
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


# ── MediaPipe face detector ───────────────────────────────────────────────────

def ensure_detector_model():
    """Fetch the BlazeFace TFLite detector if absent. Public so the setup
    scripts can pre-fetch it without restating the URL (see setup/)."""
    if not MEDIAPIPE_MODEL_PATH.exists():
        print("Downloading MediaPipe face detector (~228KB)...")
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(MEDIAPIPE_MODEL_URL, MEDIAPIPE_MODEL_PATH)


def _build_detector_cpu():
    import mediapipe as mp
    from mediapipe.tasks.python.vision import FaceDetector, FaceDetectorOptions
    from mediapipe.tasks.python.core.base_options import BaseOptions

    ensure_detector_model()
    options = FaceDetectorOptions(
        base_options=BaseOptions(model_asset_path=str(MEDIAPIPE_MODEL_PATH)),
        min_detection_confidence=0.4,
    )
    return ("mediapipe", FaceDetector.create_from_options(options))


def _build_detector_npu():
    """Load MediaPipe Face Detector ONNX on Hexagon NPU via QNNExecutionProvider."""
    if not MEDIAPIPE_NPU_ONNX_PATH.exists():
        raise FileNotFoundError(
            f"NPU detector not found: {MEDIAPIPE_NPU_ONNX_PATH}\n"
            "Run setup/setup_npu.ps1 to export both models."
        )

    session = _qnn_session(MEDIAPIPE_NPU_ONNX_PATH, "MediaPipeFace")
    input_name = session.get_inputs()[0].name
    input_shape = session.get_inputs()[0].shape  # e.g. [1, 3, 128, 128]
    return ("onnx", session, input_name, input_shape)


def _build_detector(use_npu: bool):
    if use_npu and MEDIAPIPE_NPU_ONNX_PATH.exists():
        return _build_detector_npu()
    return _build_detector_cpu()


def detect_and_crop_face(detector, pil_img: Image.Image, padding: float = 0.3) -> "Image.Image | None":
    if detector[0] == "onnx":
        return _detect_npu(detector, pil_img, padding)
    return _detect_mediapipe(detector[1], pil_img, padding)


def _detect_mediapipe(mp_detector, pil_img: Image.Image, padding: float) -> "Image.Image | None":
    import mediapipe as mp

    rgb      = pil_img.convert("RGB")
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.array(rgb))
    result   = mp_detector.detect(mp_image)

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


def _detect_npu(detector_tuple, pil_img: Image.Image, padding: float) -> "Image.Image | None":
    """Run MediaPipe BlazeFace ONNX on NPU, return cropped face."""
    _, session, input_name, input_shape = detector_tuple
    h_in, w_in = input_shape[2], input_shape[3]  # e.g. 128x128

    rgb = pil_img.convert("RGB")
    w, h = rgb.size

    # Preprocess: resize to model input, normalize [0,1]
    resized = rgb.resize((w_in, h_in), Image.LANCZOS)
    arr = np.array(resized, dtype=np.float32) / 255.0
    inp = arr.transpose(2, 0, 1)[np.newaxis]  # [1, 3, H, W]

    outputs = session.run(None, {input_name: inp})
    # Exported with --include-detector-postprocessing: outputs are already
    # decoded + sigmoid-scored (no raw-anchor math needed here).
    #   boxes:  [1, N, 16] pixel coords relative to the (w_in, h_in) resize,
    #           first 4 values per anchor are (x_min, y_min, x_max, y_max)
    #   scores: [1, N] sigmoid confidence per anchor
    boxes, scores = outputs[0], outputs[1]
    scores = scores.squeeze()

    best_idx = int(np.argmax(scores))
    if scores[best_idx] < 0.4:
        return None

    box = boxes[0][best_idx][:4]
    sx, sy = w / w_in, h / h_in  # scale from model-input space back to original image
    bw = (box[2] - box[0]) * sx
    bh = (box[3] - box[1]) * sy
    x1 = max(0, int(box[0] * sx - bw * padding))
    y1 = max(0, int(box[1] * sy - bh * padding))
    x2 = min(w, int(box[2] * sx + bw * padding))
    y2 = min(h, int(box[3] * sy + bh * padding))

    if x2 <= x1 or y2 <= y1:
        return None
    return rgb.crop((x1, y1, x2, y2)).resize((112, 112), Image.LANCZOS)

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


def _cache_path(db_dir: Path, use_npu: bool) -> Path:
    suffix = "npu" if use_npu else "cpu"
    return db_dir / f".embeddings_{suffix}.npy"


def _load_db(detector, model, db_dir: Path, use_npu: bool) -> dict:
    """Load embeddings from cache if up to date, else recompute and save."""
    exts      = {".jpg", ".jpeg", ".png", ".webp"}
    img_paths = sorted(p for p in db_dir.rglob("*") if p.suffix.lower() in exts)
    cache     = _cache_path(db_dir, use_npu)

    # Check if cache is still valid: exists and newer than all images
    if cache.exists():
        cache_mtime = cache.stat().st_mtime
        if all(p.stat().st_mtime <= cache_mtime for p in img_paths):
            data = np.load(str(cache), allow_pickle=True).item()
            print(f"  Loaded {len(data)} embeddings from cache (instant)")
            return data

    # Recompute
    print(f"  Computing embeddings for {len(img_paths)} known face(s)...")
    known = {}
    for img_path in img_paths:
        emb = get_embedding(detector, model, str(img_path), use_npu)
        if emb is not None:
            known[img_path.stem] = emb
            print(f"    enrolled: {img_path.stem}")

    if known:
        np.save(str(cache), known)
        print(f"  Saved to cache: {cache.name}")

    return known


def mode_identify(detector, model, unknown_path: str, db_dir: str, use_npu: bool):
    db = Path(db_dir)

    print(f"\nLoading face database from: {db}")
    known = _load_db(detector, model, db, use_npu)

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
    det_mode = "NPU ONNX" if (use_npu and MEDIAPIPE_NPU_ONNX_PATH.exists()) else "CPU (MediaPipe)"
    emb_mode = "NPU ONNX" if use_npu else "CPU (PyTorch)"
    print(f"Detector : {det_mode}")
    print(f"Embedder : {emb_mode}")
    print("Loading models...")

    t0       = time.time()
    detector = _build_detector(use_npu)
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
