#!/usr/bin/env bash
# Face identification pipeline -- Linux / macOS
# Usage:
#   ./run.sh compare  image1.jpg image2.jpg
#   ./run.sh identify unknown.jpg
#   ./run.sh benchmark image.jpg
#   ./run.sh --npu identify unknown.jpg   (NPU mode, needs models/CavaFace.onnx)

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# ── NPU flag ──────────────────────────────────────────────────────────────────
NPU_FLAG=""
if [ "$1" = "--npu" ]; then
    NPU_FLAG="--npu"
    shift
fi

# ── Install dependencies ──────────────────────────────────────────────────────
echo "Checking dependencies..."
pip install --quiet mediapipe --no-deps
pip install --quiet "qai-hub-models[cavaface]" pillow numpy -c constraints.txt
echo "Dependencies ready."

# ── Parse mode ────────────────────────────────────────────────────────────────
MODE="${1:-help}"

case "$MODE" in
  compare)
    if [ -z "$2" ] || [ -z "$3" ]; then
      echo "Usage: ./run.sh [--npu] compare <image1> <image2>"
      exit 1
    fi
    python face_pipeline.py $NPU_FLAG compare "$2" "$3"
    ;;
  identify)
    if [ -z "$2" ]; then
      echo "Usage: ./run.sh [--npu] identify <unknown_image>"
      exit 1
    fi
    python face_pipeline.py $NPU_FLAG identify "$2" --db ./known_faces/
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
    echo "  ./run.sh [--npu] compare   <image1> <image2>   Compare two face images"
    echo "  ./run.sh [--npu] identify  <image>             Identify against known_faces/"
    echo "  ./run.sh [--npu] benchmark <image>             Benchmark speed"
    echo ""
    echo "  --npu  Use NPU inference (needs models/CavaFace.onnx)"
    echo "         See models/README.txt for export instructions."
    echo ""
    echo "known_faces/ folder:"
    echo "  Put one reference image per person, named as the person's name."
    echo "  Example: known_faces/mahesh_babu.jpg"
    echo ""
    ;;
esac
