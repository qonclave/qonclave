#!/usr/bin/env bash
# Face identification pipeline — Linux / macOS
# Usage:
#   ./run.sh compare  image1.jpg image2.jpg
#   ./run.sh identify unknown.jpg
#   ./run.sh benchmark image.jpg

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# ── Install dependencies ──────────────────────────────────────────────────────
echo "Checking dependencies..."

# constraints.txt pins opencv-python-headless so pip uses it instead of
# opencv-python (no ARM64 Windows wheel) when resolving qai-hub-models deps
pip install --quiet mediapipe --no-deps
pip install --quiet "qai-hub-models[cavaface]" pillow numpy -c constraints.txt

echo "Dependencies ready."

# ── Parse mode ────────────────────────────────────────────────────────────────
MODE="${1:-help}"

case "$MODE" in
  compare)
    if [ -z "$2" ] || [ -z "$3" ]; then
      echo "Usage: ./run.sh compare <image1> <image2>"
      exit 1
    fi
    python face_pipeline.py --image1 "$2" --image2 "$3"
    ;;
  identify)
    if [ -z "$2" ]; then
      echo "Usage: ./run.sh identify <unknown_image>"
      exit 1
    fi
    python face_pipeline.py --identify "$2" --db ./known_faces/
    ;;
  benchmark)
    if [ -z "$2" ]; then
      echo "Usage: ./run.sh benchmark <image>"
      exit 1
    fi
    python face_pipeline.py benchmark "$2" --runs 20
    ;;
  *)
    echo ""
    echo "Face Identification Pipeline — MediaPipe + CavaFace"
    echo ""
    echo "Usage:"
    echo "  ./run.sh compare   <image1> <image2>    Compare two face images"
    echo "  ./run.sh identify  <image>              Identify face against known_faces/"
    echo "  ./run.sh benchmark <image>              Benchmark end-to-end speed"
    echo ""
    echo "known_faces/ folder:"
    echo "  Put one reference image per person, named as the person's name."
    echo "  Example: known_faces/john_doe.jpg"
    echo ""
    ;;
esac
