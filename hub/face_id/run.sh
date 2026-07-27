#!/usr/bin/env bash
# Run the face identification pipeline.
# Prerequisites: run ./setup.sh once first.
#
# Usage:
#   ./run.sh identify  unknown.jpg
#   ./run.sh compare   photo1.jpg photo2.jpg
#   ./run.sh benchmark photo.jpg
#   ./run.sh --npu identify unknown.jpg

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# ── Sanity check ─────────────────────────────────────────────────────────────

if [ ! -f "$SCRIPT_DIR/face_detector.tflite" ]; then
    echo "[ERROR] Setup not complete. Run ./setup.sh first."
    exit 1
fi

# ── NPU flag ──────────────────────────────────────────────────────────────────

NPU_FLAG=""
if [ "$1" = "--npu" ]; then
    if [ ! -f "$SCRIPT_DIR/models/CavaFace.onnx" ]; then
        echo "[ERROR] NPU models not found. Run ./setup.sh --npu first."
        exit 1
    fi
    NPU_FLAG="--npu"
    shift
fi

# ── Run ───────────────────────────────────────────────────────────────────────

MODE="${1:-help}"

case "$MODE" in
  identify)
    if [ -z "$2" ]; then
      echo "Usage: ./run.sh [--npu] identify <unknown_image>"
      exit 1
    fi
    python face_pipeline.py $NPU_FLAG identify "$2" --db ./known_faces/
    ;;
  compare)
    if [ -z "$2" ] || [ -z "$3" ]; then
      echo "Usage: ./run.sh [--npu] compare <image1> <image2>"
      exit 1
    fi
    python face_pipeline.py $NPU_FLAG compare "$2" "$3"
    ;;
  benchmark)
    if [ -z "$2" ]; then
      echo "Usage: ./run.sh [--npu] benchmark <image>"
      exit 1
    fi
    python face_pipeline.py $NPU_FLAG benchmark "$2" --runs 20
    ;;
  *)
    echo ""
    echo "Face Identification Pipeline -- MediaPipe + CavaFace"
    echo ""
    echo "Usage:"
    echo "  ./run.sh [--npu] identify  <image>              Identify a person"
    echo "  ./run.sh [--npu] compare   <img1> <img2>        Compare two photos"
    echo "  ./run.sh [--npu] benchmark <image>              Benchmark speed"
    echo ""
    echo "  --npu  Use NPU inference (requires ./setup.sh --npu)"
    echo ""
    echo "First time? Run: ./setup.sh"
    echo ""
    ;;
esac
