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
    "blaze_face_full_range/float16/1/blaze_face_full_range.tflite"
)
MEDIAPIPE_MODEL_PATH = MODELS_DIR / "blaze_face_full_range.tflite"

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

try:
    from ..qnn_session import qnn_session as _shared_qnn_session
except ImportError:  # run directly as a script (python face_pipeline.py ...)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from qnn_session import qnn_session as _shared_qnn_session


def _qnn_session(onnx_path: Path, label: str):
    """Thin alias kept for this module's own callers/CLI. The real
    implementation — including why passing "QNNExecutionProvider" as a plain
    string to InferenceSession(providers=...) silently no-ops and falls back
    to CPU — lives in the shared hub/framework/qnn_session.py, used by both
    face ID and pose."""
    return _shared_qnn_session(onnx_path, label)


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


_FULLRANGE_INPUT_SIZE = 192  # this model's fixed input resolution
_FULLRANGE_NUM_CELLS = 48    # 192 / stride(4): single-layer anchor grid


def _fullrange_anchors() -> np.ndarray:
    """Anchor centers for Google's BlazeFace "full_range" (sparse) detector:
    a single stride-4 layer over the 192x192 input, one fixed-size anchor per
    48x48 cell (2304 total) -- mirrors MediaPipe's
    face_detection_full_range_sparse.pbtxt SsdAnchorsCalculator config
    (num_layers=1, strides=[4], aspect_ratios=[1.0], fixed_anchor_size=true).
    """
    centers = [
        ((col + 0.5) / _FULLRANGE_NUM_CELLS, (row + 0.5) / _FULLRANGE_NUM_CELLS)
        for row in range(_FULLRANGE_NUM_CELLS)
        for col in range(_FULLRANGE_NUM_CELLS)
    ]
    return np.array(centers, dtype=np.float32)  # [2304, 2], normalized (x, y)


_FULLRANGE_ANCHORS = _fullrange_anchors()


def _letterbox(rgb: Image.Image, size: int):
    """Resize preserving aspect ratio onto a black size x size canvas,
    centered -- matches the preprocessing the reference MediaPipe Tasks CPU
    detector applies internally (a plain squash-resize measurably hurts this
    model's accuracy, confirmed empirically). Returns (canvas, scale,
    pad_left, pad_top) so detections can be mapped back to original-image
    pixel coordinates."""
    w, h = rgb.size
    scale = size / max(w, h)
    nw, nh = max(1, round(w * scale)), max(1, round(h * scale))
    resized = rgb.resize((nw, nh), Image.LANCZOS)
    canvas = Image.new("RGB", (size, size), (0, 0, 0))
    pad_left, pad_top = (size - nw) // 2, (size - nh) // 2
    canvas.paste(resized, (pad_left, pad_top))
    return canvas, scale, pad_left, pad_top


def _build_detector_npu():
    """Load the full_range BlazeFace detector on Hexagon NPU via
    QNNExecutionProvider.

    This ONNX model is NOT from qai_hub_models' catalog export (that uses a
    different, less accurate "back model" checkpoint bundled with the
    mediapipe_face package -- confirmed empirically to miss/under-score
    turned or distant faces that Google's actual full_range weights catch
    cleanly). It's a from-scratch conversion of Google's official
    blaze_face_full_range.tflite to ONNX (tflite2onnx), then compiled for
    this device via Qualcomm AI Hub. See setup/README for provenance and how
    to reproduce.
    """
    if not MEDIAPIPE_NPU_ONNX_PATH.exists():
        raise FileNotFoundError(
            f"NPU detector not found: {MEDIAPIPE_NPU_ONNX_PATH}\n"
            "Run setup/setup_npu.ps1 to export both models."
        )

    session = _qnn_session(MEDIAPIPE_NPU_ONNX_PATH, "MediaPipeFace")
    input_name = session.get_inputs()[0].name
    return ("onnx", session, input_name)


def _build_detector(use_npu: bool):
    if use_npu and MEDIAPIPE_NPU_ONNX_PATH.exists():
        return _build_detector_npu()
    return _build_detector_cpu()


DETECT_MIN_SCORE = 0.4  # min detector confidence to accept a face


def _crop_box(rgb: Image.Image, x1: int, y1: int, x2: int, y2: int,
              padding: float) -> "Image.Image | None":
    """Pad a detected box, clamp to image bounds, crop and resize to 112x112."""
    w, h = rgb.size
    pad_x = int((x2 - x1) * padding)
    pad_y = int((y2 - y1) * padding)
    cx1 = max(0, x1 - pad_x)
    cy1 = max(0, y1 - pad_y)
    cx2 = min(w, x2 + pad_x)
    cy2 = min(h, y2 + pad_y)
    if cx2 <= cx1 or cy2 <= cy1:
        return None
    return rgb.crop((cx1, cy1, cx2, cy2)).resize((112, 112), Image.LANCZOS)


def _iou(a, b) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = iw * ih
    if inter == 0:
        return 0.0
    area_a = max(0, ax2 - ax1) * max(0, ay2 - ay1)
    area_b = max(0, bx2 - bx1) * max(0, by2 - by1)
    return inter / float(area_a + area_b - inter + 1e-9)


def _nms(boxes: list, scores: list, iou_thresh: float = 0.3) -> list:
    """Greedy non-max suppression; returns kept indices, highest score first."""
    order = sorted(range(len(scores)), key=lambda i: -scores[i])
    kept: list[int] = []
    for i in order:
        if all(_iou(boxes[i], boxes[j]) <= iou_thresh for j in kept):
            kept.append(i)
    return kept


def detect_faces(detector, pil_img: Image.Image, padding: float = 0.3,
                 min_score: float = DETECT_MIN_SCORE) -> list:
    """Detect every face in the image, highest-confidence first.

    Returns a list of {"face": 112x112 RGB PIL crop, "bbox": (x1,y1,x2,y2) in
    original-image pixels, "score": float}. Empty list if no face is found.
    """
    if detector[0] == "onnx":
        return _detect_faces_npu(detector, pil_img, padding, min_score)
    return _detect_faces_mediapipe(detector[1], pil_img, padding, min_score)


def detect_and_crop_face(detector, pil_img: Image.Image,
                         padding: float = 0.3) -> "Image.Image | None":
    """Backward-compatible single-face helper: the highest-confidence crop."""
    faces = detect_faces(detector, pil_img, padding)
    return faces[0]["face"] if faces else None


def _detect_faces_mediapipe(mp_detector, pil_img: Image.Image, padding: float,
                            min_score: float) -> list:
    import mediapipe as mp

    rgb      = pil_img.convert("RGB")
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.array(rgb))
    result   = mp_detector.detect(mp_image)

    # MediaPipe already applies non-max suppression, so its detections are
    # one-per-face; we just filter by score and crop each.
    faces = []
    for det in result.detections:
        score = float(det.categories[0].score)
        if score < min_score:
            continue
        bb = det.bounding_box
        x1, y1 = bb.origin_x, bb.origin_y
        x2, y2 = bb.origin_x + bb.width, bb.origin_y + bb.height
        crop = _crop_box(rgb, x1, y1, x2, y2, padding)
        if crop is not None:
            faces.append({"face": crop, "bbox": (x1, y1, x2, y2), "score": score})
    faces.sort(key=lambda f: -f["score"])
    return faces


def _detect_faces_npu(detector_tuple, pil_img: Image.Image, padding: float,
                      min_score: float) -> list:
    """Run the full_range BlazeFace ONNX on NPU, return all faces (post-NMS).

    Unlike the old qai_hub_models export (which baked anchor-decoding into
    the graph via --include-detector-postprocessing), this model's outputs
    are raw per-anchor regressor/classifier tensors -- BlazeFace's standard
    anchor-decode + sigmoid + NMS is done here by hand, matching Google's
    face_detection_full_range_sparse.pbtxt anchor config.
    """
    _, session, input_name = detector_tuple
    rgb = pil_img.convert("RGB")

    canvas, scale, pad_left, pad_top = _letterbox(rgb, _FULLRANGE_INPUT_SIZE)
    arr = np.array(canvas, dtype=np.float32) / 255.0
    inp = arr.transpose(2, 0, 1)[np.newaxis]  # [1, 3, 192, 192]

    regressor, classifier = session.run(None, {input_name: inp})
    regressor = regressor[0]  # [2304, 16]: dx, dy, dw, dh, ...landmarks (unused)
    scores = 1.0 / (1.0 + np.exp(-classifier[0, :, 0]))  # [2304] sigmoid

    # Unlike MediaPipe Tasks (single argmax per face), the raw anchor grid
    # fires many overlapping boxes per face — collect all above threshold,
    # then NMS so each real face is represented once.
    size = _FULLRANGE_INPUT_SIZE
    cand_boxes, cand_scores = [], []
    for i in np.where(scores >= min_score)[0]:
        ax, ay = _FULLRANGE_ANCHORS[i]
        dx, dy, dw, dh = regressor[i, :4]
        # fixed_anchor_size -> anchor w/h = 1.0 (normalized); scale = input size
        cx, cy = ax + dx / size, ay + dy / size
        bw, bh = dw / size, dh / size
        # normalized letterbox-canvas coords -> canvas pixels -> original image pixels
        x1 = ((cx - bw / 2) * size - pad_left) / scale
        y1 = ((cy - bh / 2) * size - pad_top) / scale
        x2 = ((cx + bw / 2) * size - pad_left) / scale
        y2 = ((cy + bh / 2) * size - pad_top) / scale
        cand_boxes.append((x1, y1, x2, y2))
        cand_scores.append(float(scores[i]))

    faces = []
    for i in _nms(cand_boxes, cand_scores):
        bx1, by1, bx2, by2 = cand_boxes[i]
        crop = _crop_box(rgb, int(bx1), int(by1), int(bx2), int(by2), padding)
        if crop is not None:
            faces.append({
                "face": crop,
                "bbox": (int(bx1), int(by1), int(bx2), int(by2)),
                "score": cand_scores[i],
            })
    return faces


def get_embedding(detector, model, image_path: str, use_npu: bool) -> "np.ndarray | None":
    """Embed the single highest-confidence face (compare/benchmark modes)."""
    img  = Image.open(image_path)
    face = detect_and_crop_face(detector, img)
    if face is None:
        print(f"  [!] No face detected in {image_path}")
        return None
    return _embed_npu(model, face) if use_npu else _embed_cpu(model, face)


def get_embeddings(detector, model, image_path: str, use_npu: bool) -> list:
    """Embed every detected face. Returns a list of
    {"embedding": np.ndarray, "bbox": tuple, "score": float}, highest-confidence
    first; empty list if no face is found."""
    img   = Image.open(image_path)
    faces = detect_faces(detector, img)
    if not faces:
        print(f"  [!] No face detected in {image_path}")
        return []
    out = []
    for f in faces:
        emb = _embed_npu(model, f["face"]) if use_npu else _embed_cpu(model, f["face"])
        out.append({"embedding": emb, "bbox": f["bbox"], "score": f["score"]})
    return out


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
    faces = get_embeddings(detector, model, unknown_path, use_npu)
    if not faces:
        return

    print(f"\nDetected {len(faces)} face(s):")
    for i, f in enumerate(faces, 1):
        scores    = {name: float(np.dot(emb, f["embedding"])) for name, emb in known.items()}
        best_name = max(scores, key=scores.get)
        best_score = scores[best_name]

        print(f"\n[Face {i}] bbox={f['bbox']} detector_score={f['score']*100:.1f}%")
        print("  --- Scores ---")
        for name, score in sorted(scores.items(), key=lambda x: -x[1]):
            marker = " <- best match" if name == best_name else ""
            print(f"    {name:30s}  {score*100:.1f}%{marker}")

        if best_score > THRESHOLD:
            print(f"  => Identified as : {best_name}  ({best_score*100:.1f}%)")
        else:
            print(f"  => Unknown person  (best '{best_name}' only {best_score*100:.1f}%)")


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
