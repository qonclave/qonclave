"""
pose_pipeline.py — HRNetPose w8a8 inference on the Hexagon NPU.

Low-level: sessions, preprocessing, heatmap decode. The policy layer is `pose.py`.

Mirrors `face_id/face_pipeline.py` rather than `vlm.py`. GenieX is a *generative*
runtime — tokenizer, chat template, generate(), KV cache. HRNetPose is a CNN that
returns a heatmap tensor, so it takes the path this repo already uses for face
ID: ONNX Runtime with the QNN execution provider.

Model files come from an AI Hub export and are never vendored — see
models/README.txt. Nothing here works until that export has run, which is
deliberate: a 109 MB weight file does not belong in git.

CLI self-test, matching face_pipeline's convention:
    python hub/framework/pose/pose_pipeline.py benchmark <crop.jpg>
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np

MODELS_DIR = Path(__file__).resolve().parent / "models"
POSE_ONNX_PATH = MODELS_DIR / "hrnet_pose.onnx"
POSE_CTX_PATH = MODELS_DIR / "hrnet_pose_ctx.onnx"
METADATA_PATH = MODELS_DIR / "metadata.json"

# HRNetPose w8a8 geometry. Input is 192 wide x 256 high; the heatmap is a
# quarter of that per side, 17 COCO keypoints deep.
INPUT_W, INPUT_H = 192, 256
HEATMAP_W, HEATMAP_H = 48, 64
NUM_KEYPOINTS = 17

# Quantization constants from the AI Hub export's metadata.json. Defaults are
# the values the plan recorded; metadata.json wins when present, because a
# re-export can change them and a silently wrong scale produces plausible
# keypoints in the wrong places — the worst kind of failure.
DEFAULT_INPUT_SCALE = 0.003917243331670761
DEFAULT_INPUT_ZP = 0
DEFAULT_OUTPUT_SCALE = 0.0037365437019616365
DEFAULT_OUTPUT_ZP = 10

# A top-down pose model expects a tight box with the subject filling the frame.
# The edge's crop is framed for FACE detection (padding_top=0.8), so the person
# occupies about half of it — see the pose plan's Phase 4 note. Expanding the
# supplied person box by this factor reproduces the framing HRNet was trained on.
BOX_EXPANSION = 1.25

COCO_KEYPOINT_NAMES = (
    "nose", "left_eye", "right_eye", "left_ear", "right_ear",
    "left_shoulder", "right_shoulder", "left_elbow", "right_elbow",
    "left_wrist", "right_wrist", "left_hip", "right_hip",
    "left_knee", "right_knee", "left_ankle", "right_ankle",
)


def load_metadata() -> dict:
    """Quantization params from the export, falling back to the recorded ones."""
    meta = {
        "input_scale": DEFAULT_INPUT_SCALE,
        "input_zp": DEFAULT_INPUT_ZP,
        "output_scale": DEFAULT_OUTPUT_SCALE,
        "output_zp": DEFAULT_OUTPUT_ZP,
    }
    if METADATA_PATH.exists():
        try:
            raw = json.loads(METADATA_PATH.read_text(encoding="utf-8"))
            for key in meta:
                if key in raw:
                    meta[key] = raw[key]
        except (ValueError, OSError):
            pass  # a malformed metadata.json must not stop inference
    return meta


def model_path() -> Path | None:
    """The model to load, preferring the compiled HTP context binary.

    The context binary cuts session init from ~6.0 s to ~0.30 s. It is tied to
    the QAIRT build and HTP architecture that produced it, so a stale one is a
    slow start rather than a failure — which is why the raw model stays as a
    fallback and neither is committed.
    """
    if POSE_CTX_PATH.exists():
        return POSE_CTX_PATH
    if POSE_ONNX_PATH.exists():
        return POSE_ONNX_PATH
    return None


def build_session(quiet: bool = False):
    """Load HRNetPose. Raises FileNotFoundError when no export has been run."""
    path = model_path()
    if path is None:
        raise FileNotFoundError(
            f"No pose model in {MODELS_DIR}. Run hub/framework/pose/setup/setup_pose.ps1, "
            "or see models/README.txt for the manual export steps."
        )
    try:
        from ..qnn_session import qnn_session
    except ImportError:  # direct script execution
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from qnn_session import qnn_session  # type: ignore

    session = qnn_session(path, "HRNetPose", quiet=quiet)
    return session, session.get_inputs()[0].name


# ── preprocessing ────────────────────────────────────────────────────────────

def letterbox(image: np.ndarray, out_w: int = INPUT_W, out_h: int = INPUT_H):
    """Aspect-preserving resize with padding. Returns (image, transform).

    The transform is what maps a keypoint back out of model space, so it is
    returned rather than recomputed — recomputing it is where off-by-a-pad
    errors come from.
    """
    import cv2

    h, w = image.shape[:2]
    if h == 0 or w == 0:
        raise ValueError("cannot letterbox an empty image")

    scale = min(out_w / w, out_h / h)
    new_w, new_h = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
    resized = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_LINEAR)

    canvas = np.zeros((out_h, out_w, 3), dtype=image.dtype)
    pad_x, pad_y = (out_w - new_w) // 2, (out_h - new_h) // 2
    canvas[pad_y:pad_y + new_h, pad_x:pad_x + new_w] = resized
    return canvas, {"scale": scale, "pad_x": pad_x, "pad_y": pad_y}


def expand_box(box, img_w: int, img_h: int, factor: float = BOX_EXPANSION):
    """Grow a person box about its centre, clamped to the image."""
    x1, y1, x2, y2 = box
    cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
    half_w, half_h = (x2 - x1) * factor / 2.0, (y2 - y1) * factor / 2.0
    return (
        max(0, int(round(cx - half_w))), max(0, int(round(cy - half_h))),
        min(img_w, int(round(cx + half_w))), min(img_h, int(round(cy + half_h))),
    )


def preprocess(image: np.ndarray, person_box=None, meta: dict | None = None):
    """Crop to the person box, letterbox, quantize. Returns (tensor, transform)."""
    meta = meta or load_metadata()
    h, w = image.shape[:2]

    offset_x = offset_y = 0
    if person_box is not None:
        x1, y1, x2, y2 = expand_box(person_box, w, h)
        if x2 > x1 and y2 > y1:
            image = image[y1:y2, x1:x2]
            offset_x, offset_y = x1, y1

    boxed, transform = letterbox(image)
    transform["offset_x"], transform["offset_y"] = offset_x, offset_y

    # uint8 in, per the w8a8 quantization. The scale/zp come from the export.
    tensor = boxed.astype(np.uint8)[np.newaxis]  # [1, H, W, 3]
    return tensor, transform


# ── heatmap decode ───────────────────────────────────────────────────────────

def decode_heatmap(heatmap: np.ndarray, transform: dict, meta: dict | None = None):
    """Per-channel argmax with quarter-offset sub-pixel refinement.

    Returns keypoints in the coordinate space of the image that was passed to
    `preprocess` — crop-relative, not frame-relative. Mapping further out is the
    caller's job, because only the caller knows what the crop was cut from.
    """
    meta = meta or load_metadata()
    hm = np.asarray(heatmap)

    # Accept [1, K, H, W] and [K, H, W]; the export has produced both shapes.
    if hm.ndim == 4:
        hm = hm[0]
    if hm.shape[0] != NUM_KEYPOINTS and hm.shape[-1] == NUM_KEYPOINTS:
        hm = np.transpose(hm, (2, 0, 1))  # [H, W, K] -> [K, H, W]

    if hm.dtype != np.float32:
        hm = (hm.astype(np.float32) - meta["output_zp"]) * meta["output_scale"]

    k, h, w = hm.shape
    scale, pad_x, pad_y = transform["scale"], transform["pad_x"], transform["pad_y"]
    offset_x, offset_y = transform.get("offset_x", 0), transform.get("offset_y", 0)
    stride_x, stride_y = INPUT_W / w, INPUT_H / h

    keypoints = []
    for i in range(k):
        plane = hm[i]
        idx = int(np.argmax(plane))
        py, px = divmod(idx, w)
        score = float(plane[py, px])

        # Quarter-offset refinement: nudge a quarter pixel toward the brighter
        # neighbour. Cheap, and worth roughly a pixel of accuracy at this
        # heatmap resolution.
        fx, fy = float(px), float(py)
        if 0 < px < w - 1:
            fx += 0.25 * np.sign(plane[py, px + 1] - plane[py, px - 1])
        if 0 < py < h - 1:
            fy += 0.25 * np.sign(plane[py + 1, px] - plane[py - 1, px])

        # heatmap -> model input -> undo letterbox -> undo the box crop
        mx, my = fx * stride_x, fy * stride_y
        x = (mx - pad_x) / scale + offset_x
        y = (my - pad_y) / scale + offset_y
        keypoints.append([float(x), float(y), score])

    return keypoints


def mean_score(keypoints) -> float:
    if not keypoints:
        return 0.0
    return float(np.mean([kp[2] for kp in keypoints]))


# ── CLI ──────────────────────────────────────────────────────────────────────

def _benchmark(image_path: str, runs: int = 50) -> int:
    import cv2

    image = cv2.imread(image_path)
    if image is None:
        print(f"could not read {image_path}")
        return 1

    t0 = time.monotonic()
    session, input_name = build_session()
    init_s = time.monotonic() - t0
    print(f"session init: {init_s:.2f}s  (expect ~0.30s with the context binary, ~6.0s without)")

    try:
        from ..qnn_session import resolved_mode
    except ImportError:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from qnn_session import resolved_mode  # type: ignore
    print(f"mode: {resolved_mode(session)}  (npu expected on Snapdragon; cpu is ~30x slower)")

    meta = load_metadata()
    tensor, transform = preprocess(image, meta=meta)

    session.run(None, {input_name: tensor})  # warm
    t0 = time.monotonic()
    for _ in range(runs):
        outputs = session.run(None, {input_name: tensor})
    per_run_ms = (time.monotonic() - t0) / runs * 1000
    print(f"inference: {per_run_ms:.2f} ms/run over {runs} runs  (expect ~1.45 ms on NPU)")

    kps = decode_heatmap(outputs[0], transform, meta)
    print(f"keypoints: {len(kps)}  mean_score: {mean_score(kps):.3f}")
    for name, kp in zip(COCO_KEYPOINT_NAMES, kps):
        print(f"  {name:<16} x={kp[0]:7.1f} y={kp[1]:7.1f} score={kp[2]:.3f}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "benchmark":
        raise SystemExit(_benchmark(sys.argv[2]))
    print(__doc__)
    print("usage: python pose_pipeline.py benchmark <crop.jpg>")
    raise SystemExit(2)
