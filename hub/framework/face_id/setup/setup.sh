#!/usr/bin/env bash
# One-time setup for the face identification pipeline (Linux / macOS).
# CPU mode only — NPU runs on Snapdragon X Windows (use setup.ps1 there).
#
# Usage:
#   ./setup.sh

set -e
# This script lives in hub/framework/face_id/setup/, alongside constraints.txt; PKG_DIR
# is the face_id package one level up, which owns models/ and the pipeline.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PKG_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PKG_DIR"

NPU=0
if [ "$1" = "--npu" ]; then NPU=1; fi

echo ""
echo "Face ID Pipeline -- Setup"
echo ""

# Step 1: opencv + mediapipe + qai-hub-models
echo "[INFO]  Installing dependencies..."
pip install --quiet opencv-python-headless
pip install --quiet mediapipe --no-deps
pip install --quiet "qai-hub-models[cavaface]" pillow numpy -c "$SCRIPT_DIR/constraints.txt"
echo "[ OK ]  Dependencies installed"

# Step 2: Download MediaPipe CPU model (skipped when step 3 exports for NPU)
# face_pipeline owns the URL and the destination path - call its helper rather
# than restating either here. Gated for the same reason as setup.ps1: with an
# NPU export the detector runs from MediaPipeFace.onnx and never opens this
# file, and ensure_detector_model() still fetches on demand if the CPU
# detector is ever reached.
if [ "$NPU" = "1" ]; then
    echo "[ OK ]  Skipping CPU detector download (step 3 exports it for NPU)"
elif [ ! -f "$PKG_DIR/models/face_detector.tflite" ]; then
    echo "[INFO]  Downloading MediaPipe face detector (~228KB)..."
    python -c "import sys; sys.path.insert(0, '$PKG_DIR'); import face_pipeline; face_pipeline.ensure_detector_model()"
    echo "[ OK ]  models/face_detector.tflite downloaded"
else
    echo "[ OK ]  models/face_detector.tflite already present"
fi

# Step 3: (Optional) NPU model export
if [ "$NPU" = "1" ]; then
    echo ""
    echo "[INFO]  Running NPU setup..."
    # Linux: use qai-hub-models export directly
    pip install --quiet qai-hub
    read -p "Enter your AI Hub API token: " TOKEN
    qai-hub configure --api_token "$TOKEN"

    mkdir -p "$PKG_DIR/models"
    mkdir -p /tmp/qonclave_npu_export

    echo "[INFO]  Exporting CavaFace..."
    cd /tmp/qonclave_npu_export
    qai-hub-models export cavaface --target-runtime onnx --device "Snapdragon X Elite CRD" \
        --output-dir /tmp/qonclave_npu_export/cavaface
    ONNX=$(find /tmp/qonclave_npu_export/cavaface -name "*.onnx.zip" | head -1)
    unzip -o "$ONNX" -d /tmp/qonclave_npu_export/cavaface_unzipped
    cp /tmp/qonclave_npu_export/cavaface_unzipped/*/model.onnx "$PKG_DIR/models/CavaFace.onnx"
    cp /tmp/qonclave_npu_export/cavaface_unzipped/*/model.data "$PKG_DIR/models/CavaFace.data" 2>/dev/null || true
    echo "[ OK ]  CavaFace.onnx copied"

    echo "[INFO]  Exporting MediaPipeFace..."
    qai-hub-models export mediapipe_face --target-runtime onnx --device "Snapdragon X Elite CRD" \
        --output-dir /tmp/qonclave_npu_export/mp_face
    ONNX2=$(find /tmp/qonclave_npu_export/mp_face -name "*.onnx.zip" | head -1)
    unzip -o "$ONNX2" -d /tmp/qonclave_npu_export/mp_face_unzipped
    cp /tmp/qonclave_npu_export/mp_face_unzipped/*/model.onnx "$PKG_DIR/models/MediaPipeFace.onnx"
    echo "[ OK ]  MediaPipeFace.onnx copied"

    cd "$PKG_DIR"
fi

echo ""
echo "================================================================"
echo " Setup complete!"
echo ""
echo " Add photos to known_faces/ then run:"
if [ "$NPU" = "1" ]; then
    echo "   python face_pipeline.py --npu identify unknown.jpg"
else
    echo "   python face_pipeline.py identify unknown.jpg"
fi
echo "================================================================"
