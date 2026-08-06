r"""
Pose estimation pipeline: HRNetPose w8a8 on the Hexagon NPU.

Top-down single-person pose: the caller supplies a person crop (and,
optionally, the person's tight box inside that crop); this module expands the
box to the model's 3:4 aspect with padding, affine-warps it to the 192x256
input, runs the quantized HRNet, and decodes 17 COCO keypoints from the
17x64x48 heatmap with quarter-offset sub-pixel refinement.

The tight box matters: the crop the edge sends is framed for FACE detection
(0.8 x box-height of headroom above the person), so the subject fills only
about half of it. Warping the tight person box instead of the whole crop puts
the subject at the model's full input height, which is what top-down pose
models are trained on. When no box is given the whole image is used.

Ported from the standalone AI Hub export prototype (run_video.py in the
hrnet_pose-onnx-w8a8 export); quantization constants come from that export's
metadata.json.

Works on:
  - Windows ARM64 (WoS)   (NPU via QNNExecutionProvider, CPU fallback)
  - anything else         (CPU, if an ONNX model is present)

Prefers models/hrnet_pose_ctx.onnx — the precompiled HTP context binary —
which cuts session init from ~6.0s to ~0.3s. Falls back to the raw QDQ
models/hrnet_pose.onnx. The context binary is SDK/HTP-specific: regenerate it
on the target host with `python pose_pipeline.py compile` (setup_pose.ps1
does this); never commit it.

Usage:
  python pose_pipeline.py benchmark image.jpg [--runs N]
  python pose_pipeline.py estimate  image.jpg
  python pose_pipeline.py compile
"""

import argparse
import platform
import sys
import time
from pathlib import Path

import numpy as np

try:
    from ..qnn_session import qnn_session, session_mode
except ImportError:  # run directly as a script (python pose_pipeline.py ...)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from qnn_session import qnn_session, session_mode

# ── Paths ─────────────────────────────────────────────────────────────────────

MODELS_DIR    = Path(__file__).parent / "models"
RAW_ONNX_PATH = MODELS_DIR / "hrnet_pose.onnx"       # raw QDQ graph (+ .data sidecar)
CTX_ONNX_PATH = MODELS_DIR / "hrnet_pose_ctx.onnx"   # precompiled HTP context binary

# ── Model contract (from the AI Hub export's metadata.json) ──────────────────

IN_SCALE, IN_ZP   = 0.003917243331670761, 0    # uint8 input quantization
OUT_SCALE, OUT_ZP = 0.0037365437019616365, 10  # uint8 heatmap quantization
IN_W, IN_H = 192, 256                           # model input (W x H)
HM_W, HM_H = 48, 64                             # heatmap grid  (W x H)

BOX_PADDING = 1.25  # standard top-down pose expansion around the tight box

KEYPOINT_NAMES = [
    "nose", "l_eye", "r_eye", "l_ear", "r_ear", "l_shoulder", "r_shoulder",
    "l_elbow", "r_elbow", "l_wrist", "r_wrist", "l_hip", "r_hip",
    "l_knee", "r_knee", "l_ankle", "r_ankle",
]

# QNN provider options: burst pins the HTP clock for latency-critical models.
# The prototype's 1.45ms/inference figure was measured with burst on; the
# default power profile is measurably slower.
_PROVIDER_OPTIONS = {"htp_performance_mode": "burst"}


# ── Session ───────────────────────────────────────────────────────────────────

def model_path() -> "Path | None":
    """The model file build_session() would load, or None if neither exists."""
    if CTX_ONNX_PATH.exists():
        return CTX_ONNX_PATH
    if RAW_ONNX_PATH.exists():
        return RAW_ONNX_PATH
    return None


def build_session():
    """Load HRNetPose on the Hexagon NPU (CPU fallback). -> (session, input_name)"""
    path = model_path()
    if path is None:
        raise FileNotFoundError(
            f"Pose model not found: {RAW_ONNX_PATH}\n"
            "Export it first (see models/README.txt):\n"
            "  hub/framework/pose/setup/setup_pose.ps1 -Token YOUR_TOKEN"
        )
    session = qnn_session(path, "HRNetPose", provider_options=_PROVIDER_OPTIONS)
    return session, session.get_inputs()[0].name


def compile_context_model() -> Path:
    """One-time: bake the HTP context binary into hrnet_pose_ctx.onnx.

    Cuts session init ~6.0s -> ~0.3s. The output is tied to the QAIRT build
    and HTP architecture that produced it — regenerate per host, never commit.
    """
    import onnxruntime as ort
    import onnxruntime_qnn as qnn

    if not RAW_ONNX_PATH.exists():
        raise FileNotFoundError(f"raw model not found: {RAW_ONNX_PATH}")

    try:
        ort.register_execution_provider_library(qnn.get_ep_name(), qnn.get_library_path())
    except Exception:
        pass  # already registered

    npu_devices = [
        d for d in ort.get_ep_devices()
        if d.ep_name == qnn.get_ep_name() and d.device.type == ort.OrtHardwareDeviceType.NPU
    ]
    if not npu_devices:
        raise RuntimeError("no QNN NPU device found — context compile needs the NPU")

    so = ort.SessionOptions()
    options = {"backend_path": qnn.get_qnn_htp_path()}
    options.update(_PROVIDER_OPTIONS)
    so.add_provider_for_devices(npu_devices, options)

    # ORT_ENABLE_ALL is required — the default (ORT_DISABLE_ALL) breaks the
    # NHWC layout transform QNN asks for and the compile fails with
    # "Conv_token_61 ... com.ms.internal.nhwc ... not selected by that EP".
    # embed_compiled_data_into_model makes the output a single self-contained
    # file (no .data sidecar to keep in sync).
    ort.ModelCompiler(
        so, str(RAW_ONNX_PATH), embed_compiled_data_into_model=True,
        graph_optimization_level=ort.GraphOptimizationLevel.ORT_ENABLE_ALL,
    ).compile_to_file(str(CTX_ONNX_PATH))
    return CTX_ONNX_PATH


# ── Crop math + pre/post processing (ported from the prototype) ──────────────

def box_to_input_crop(box_xyxy, pad: float = BOX_PADDING):
    """Expand a tight person box to the model's 3:4 aspect with padding.

    Returns (x, y, w, h) in source-image coordinates. Aspect-fit: whichever of
    width/height falls short of 3:4 is GROWN (never shrunk), so nothing inside
    the padded box is cropped away — the warp letterboxes in source
    coordinates instead of padding a pre-cut image.
    """
    x1, y1, x2, y2 = box_xyxy
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    bw, bh = (x2 - x1) * pad, (y2 - y1) * pad
    ar = IN_W / IN_H  # 0.75
    if bw / bh > ar:
        bh = bw / ar
    else:
        bw = bh * ar
    return cx - bw / 2, cy - bh / 2, bw, bh


def preprocess(image_bgr: np.ndarray, crop_rect,
               input_type: str = "tensor(uint8)") -> np.ndarray:
    """Affine-crop crop_rect to 192x256 and match the ONNX input dtype.

    AI Hub exports have used both a uint8 quantized boundary and a float32
    boundary for the same w8a8 model. The float graph performs its own
    normalization and expects RGB values in [0, 1].
    """
    import cv2

    rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
    x, y, w, h = crop_rect
    M = np.array([[IN_W / w, 0, -x * IN_W / w],
                  [0, IN_H / h, -y * IN_H / h]], dtype=np.float32)
    patch = cv2.warpAffine(rgb, M, (IN_W, IN_H), flags=cv2.INTER_LINEAR,
                           borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0))
    normalized = patch.astype(np.float32) / 255.0
    if input_type == "tensor(float)":
        return np.ascontiguousarray(normalized.transpose(2, 0, 1)[None])
    if input_type != "tensor(uint8)":
        raise ValueError(f"unsupported HRNetPose input type: {input_type}")
    q = np.rint(normalized / IN_SCALE) + IN_ZP
    q = np.clip(q, 0, 255).astype(np.uint8)
    return np.ascontiguousarray(q.transpose(2, 0, 1)[None])


def decode_heatmaps(heatmaps: np.ndarray, crop_rect) -> np.ndarray:
    """argmax + quarter-offset sub-pixel refinement, mapped back to
    source-image coordinates. -> (17, 3) array of x, y, score."""
    if heatmaps.dtype == np.uint8:
        hm = (heatmaps[0].astype(np.float32) - OUT_ZP) * OUT_SCALE
    elif np.issubdtype(heatmaps.dtype, np.floating):
        hm = heatmaps[0].astype(np.float32, copy=False)
    else:
        raise ValueError(f"unsupported HRNetPose output dtype: {heatmaps.dtype}")
    k = hm.shape[0]
    flat = hm.reshape(k, -1)
    idx = flat.argmax(1)
    scores = flat.max(1)
    ys, xs = np.divmod(idx, HM_W)
    px = xs.astype(np.float32)
    py = ys.astype(np.float32)
    # standard HRNet sub-pixel nudge toward the larger neighbour
    for j in range(k):
        x, y = int(xs[j]), int(ys[j])
        if 0 < x < HM_W - 1:
            px[j] += 0.25 * np.sign(hm[j, y, x + 1] - hm[j, y, x - 1])
        if 0 < y < HM_H - 1:
            py[j] += 0.25 * np.sign(hm[j, y + 1, x] - hm[j, y - 1, x])
    cx, cy, cw, ch = crop_rect
    fx = cx + (px + 0.5) * (cw / HM_W)
    fy = cy + (py + 0.5) * (ch / HM_H)
    return np.stack([fx, fy, scores], 1)


def estimate(session, input_name: str, image_bgr: np.ndarray,
             person_box=None) -> np.ndarray:
    """Run pose on one image. person_box is the tight (x1,y1,x2,y2) person
    rect in image pixels; None uses the whole image. -> (17, 3) keypoints in
    image pixels."""
    h, w = image_bgr.shape[:2]
    box = person_box if person_box is not None else (0, 0, w, h)
    crop_rect = box_to_input_crop(box)
    input_type = session.get_inputs()[0].type
    x = preprocess(image_bgr, crop_rect, input_type)
    hm = session.run(None, {input_name: x})[0]
    return decode_heatmaps(hm, crop_rect)


# ── CLI (matches face_pipeline.py's convention) ──────────────────────────────

def _load_image(path: str) -> np.ndarray:
    import cv2

    img = cv2.imread(path, cv2.IMREAD_COLOR)
    if img is None:
        print(f"Cannot read image: {path}")
        sys.exit(1)
    return img


def mode_estimate(image_path: str):
    img = _load_image(image_path)
    t0 = time.time()
    session, input_name = build_session()
    print(f"Session ready in {time.time() - t0:.2f}s (mode={session_mode(session)})")

    kps = estimate(session, input_name, img)
    print(f"\nKeypoints for {image_path} ({img.shape[1]}x{img.shape[0]}):")
    for name, (x, y, s) in zip(KEYPOINT_NAMES, kps):
        print(f"  {name:12s} x={x:7.1f} y={y:7.1f} score={s:.3f}")
    print(f"\nmean score: {float(kps[:, 2].mean()):.3f}")


def mode_benchmark(image_path: str, runs: int):
    img = _load_image(image_path)

    t0 = time.time()
    session, input_name = build_session()
    init_s = time.time() - t0
    which = model_path()
    print(f"Session init : {init_s:.2f}s ({which.name if which else '?'}, mode={session_mode(session)})")

    x = preprocess(img, box_to_input_crop((0, 0, img.shape[1], img.shape[0])))
    for _ in range(5):  # warmup
        session.run(None, {input_name: x})

    times = []
    for _ in range(runs):
        t0 = time.perf_counter()
        session.run(None, {input_name: x})
        times.append((time.perf_counter() - t0) * 1000)
    times.sort()

    print(f"\n--- Benchmark ({runs} runs, pure inference) ---")
    print(f"latency mean {np.mean(times):.2f} ms | p50 {times[len(times) // 2]:.2f} | "
          f"p95 {times[int(len(times) * .95)]:.2f} | {1000 / np.mean(times):.0f} FPS")
    print(f"Mode          : {session_mode(session).upper()}")
    print(f"Platform      : {platform.system()} {platform.machine()}")


def main():
    parser = argparse.ArgumentParser(description="HRNetPose w8a8 on the Hexagon NPU")
    sub = parser.add_subparsers(dest="mode")

    p = sub.add_parser("estimate", help="Print keypoints for one image")
    p.add_argument("image")

    p = sub.add_parser("benchmark", help="Benchmark inference latency")
    p.add_argument("image")
    p.add_argument("--runs", type=int, default=50)

    sub.add_parser("compile", help="Bake the HTP context binary (hrnet_pose_ctx.onnx)")

    args = parser.parse_args()

    if args.mode == "estimate":
        mode_estimate(args.image)
    elif args.mode == "benchmark":
        mode_benchmark(args.image, args.runs)
    elif args.mode == "compile":
        print("Compiling HTP context binary (needs the raw model + NPU)...")
        t0 = time.time()
        out = compile_context_model()
        print(f"Wrote {out} in {time.time() - t0:.1f}s")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
