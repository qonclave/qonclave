"""
Face identification pipeline: MediaPipe (detection) + CavaFace (embedding)

Works on:
  - Linux x86_64
  - Windows x86_64
  - Windows ARM64 (WoS / Snapdragon X)
  - macOS ARM64

Dependencies installed by run.sh / run.ps1 (no manual pip needed).

Usage:
  python face_pipeline.py --image1 a.jpg --image2 b.jpg
  python face_pipeline.py --identify unknown.jpg --db ./known_faces/
  python face_pipeline.py benchmark photo.jpg --runs 20
"""

import argparse
import platform
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image

# ── CavaFace (via qai-hub-models) ─────────────────────────────────────────────

def _build_cavaface():
    from qai_hub_models.models.cavaface.model import CavaFace
    from qai_hub_models.models.cavaface.app import CavaFaceApp

    model = CavaFace.from_pretrained()
    return CavaFaceApp(model, input_height=112, input_width=112)


def _get_embedding(app, face_img: Image.Image) -> np.ndarray:
    return app.predict_features(face_img.convert("RGB").resize((112, 112), Image.LANCZOS), use_flip=True)


# ── MediaPipe face detector ───────────────────────────────────────────────────

MEDIAPIPE_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_detector/"
    "blaze_face_short_range/float16/1/blaze_face_short_range.tflite"
)
MEDIAPIPE_MODEL_PATH = Path(__file__).parent / "face_detector.tflite"


def _ensure_mp_model():
    if not MEDIAPIPE_MODEL_PATH.exists():
        print("Downloading MediaPipe face detector (~228KB)...")
        urllib.request.urlretrieve(MEDIAPIPE_MODEL_URL, MEDIAPIPE_MODEL_PATH)


def _build_detector():
    import mediapipe as mp
    from mediapipe.tasks.python.vision import FaceDetector, FaceDetectorOptions
    from mediapipe.tasks.python.core.base_options import BaseOptions

    _ensure_mp_model()
    options = FaceDetectorOptions(
        base_options=BaseOptions(model_asset_path=str(MEDIAPIPE_MODEL_PATH)),
        min_detection_confidence=0.4,
    )
    return FaceDetector.create_from_options(options)


def detect_and_crop_face(detector, pil_img: Image.Image, padding: float = 0.3) -> "Image.Image | None":
    import mediapipe as mp

    rgb = pil_img.convert("RGB")
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.array(rgb))
    result = detector.detect(mp_image)

    if not result.detections:
        return None

    det = max(result.detections, key=lambda d: d.categories[0].score)
    bb = det.bounding_box
    w, h = rgb.size

    pad_x = int(bb.width * padding)
    pad_y = int(bb.height * padding)
    x1 = max(0, bb.origin_x - pad_x)
    y1 = max(0, bb.origin_y - pad_y)
    x2 = min(w, bb.origin_x + bb.width + pad_x)
    y2 = min(h, bb.origin_y + bb.height + pad_y)

    return rgb.crop((x1, y1, x2, y2)).resize((112, 112), Image.LANCZOS)


# ── Full pipeline ─────────────────────────────────────────────────────────────

THRESHOLD = 0.3


def get_embedding(detector, app, image_path: str) -> "np.ndarray | None":
    img = Image.open(image_path)
    face = detect_and_crop_face(detector, img)
    if face is None:
        print(f"  [!] No face detected in {image_path}")
        return None
    return _get_embedding(app, face)


# ── Modes ─────────────────────────────────────────────────────────────────────

def mode_compare(detector, app, image1: str, image2: str):
    print(f"\nComparing:\n  A: {image1}\n  B: {image2}\n")
    t0 = time.time()
    e1 = get_embedding(detector, app, image1)
    e2 = get_embedding(detector, app, image2)
    elapsed = (time.time() - t0) * 1000

    if e1 is None or e2 is None:
        print("Cannot compare - face not detected in one or both images.")
        return

    sim = float(np.dot(e1, e2))
    label = "SAME PERSON" if sim > THRESHOLD else "different person"
    print(f"Similarity : {sim * 100:.1f}%")
    print(f"Result     : {label}  (threshold={THRESHOLD*100:.0f}%)")
    print(f"Time       : {elapsed:.0f}ms")


def mode_identify(detector, app, unknown_path: str, db_dir: str):
    db = Path(db_dir)
    exts = {".jpg", ".jpeg", ".png", ".webp"}

    print(f"\nBuilding face database from: {db}")
    known = {}
    for img_path in sorted(db.rglob("*")):
        if img_path.suffix.lower() not in exts:
            continue
        emb = get_embedding(detector, app, str(img_path))
        if emb is not None:
            known[img_path.stem] = emb
            print(f"  enrolled: {img_path.stem}")

    if not known:
        print("No faces enrolled - check db path and image files.")
        return

    print(f"\nIdentifying: {unknown_path}")
    unknown_emb = get_embedding(detector, app, unknown_path)
    if unknown_emb is None:
        return

    scores = {name: float(np.dot(emb, unknown_emb)) for name, emb in known.items()}
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


def mode_benchmark(detector, app, image_path: str, runs: int = 20):
    img = Image.open(image_path)
    face = detect_and_crop_face(detector, img)
    if face is None:
        print("No face detected.")
        return

    _get_embedding(app, face)  # warmup

    times_det, times_emb = [], []
    for _ in range(runs):
        t0 = time.time()
        detect_and_crop_face(detector, img)
        times_det.append((time.time() - t0) * 1000)

        t0 = time.time()
        _get_embedding(app, face)
        times_emb.append((time.time() - t0) * 1000)

    total = [d + e for d, e in zip(times_det, times_emb)]
    print(f"\n--- Benchmark ({runs} runs) ---")
    print(f"{'Step':<22} {'Avg':>8} {'Min':>8} {'Max':>8}")
    print(f"{'Face detection':<22} {np.mean(times_det):>7.1f}ms {np.min(times_det):>7.1f}ms {np.max(times_det):>7.1f}ms")
    print(f"{'CavaFace embed':<22} {np.mean(times_emb):>7.1f}ms {np.min(times_emb):>7.1f}ms {np.max(times_emb):>7.1f}ms")
    print(f"{'Total':<22} {np.mean(total):>7.1f}ms {np.min(total):>7.1f}ms {np.max(total):>7.1f}ms")
    print(f"\nEstimated FPS : {1000/np.mean(total):.1f}")
    print(f"Platform      : {platform.system()} {platform.machine()}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Face identification: MediaPipe + CavaFace")
    sub = parser.add_subparsers(dest="mode")

    p = sub.add_parser("compare",   help="Compare two face images")
    p.add_argument("image1")
    p.add_argument("image2")

    p = sub.add_parser("identify",  help="Identify face against a database folder")
    p.add_argument("image")
    p.add_argument("--db", required=True)

    p = sub.add_parser("benchmark", help="Benchmark end-to-end latency")
    p.add_argument("image")
    p.add_argument("--runs", type=int, default=20)

    parser.add_argument("--image1")
    parser.add_argument("--image2")
    parser.add_argument("--identify")
    parser.add_argument("--db")

    args = parser.parse_args()

    print(f"Platform : {platform.system()} {platform.machine()} Python {sys.version.split()[0]}")
    print("Loading models...")
    t0 = time.time()
    detector = _build_detector()
    app      = _build_cavaface()
    print(f"Ready in {time.time()-t0:.1f}s\n")

    if args.mode == "compare" or (args.image1 and args.image2):
        mode_compare(detector, app, args.image1, args.image2)
    elif args.mode == "identify" or args.identify:
        mode_identify(detector, app, args.identify or args.image, args.db)
    elif args.mode == "benchmark":
        mode_benchmark(detector, app, args.image, args.runs)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
