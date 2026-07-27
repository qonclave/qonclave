#!/usr/bin/env bash
# One-time setup for the face identification pipeline.
# Run once after cloning the repo, then use run.sh for every identify/compare.
#
# Usage:
#   ./setup.sh            # CPU mode
#   ./setup.sh --npu      # CPU + NPU (exports AI Hub models, prompts for token)

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

NPU=0
if [ "$1" = "--npu" ]; then NPU=1; fi

echo ""
echo "Face ID Pipeline -- Setup"
echo ""

# Step 1: opencv + mediapipe + qai-hub-models
echo "[INFO]  Installing dependencies..."
pip install --quiet opencv-python-headless
pip install --quiet mediapipe --no-deps
pip install --quiet "qai-hub-models[cavaface]" pillow numpy -c constraints.txt
echo "[ OK ]  Dependencies installed"

# Step 2: Download MediaPipe CPU model
if [ ! -f "$SCRIPT_DIR/face_detector.tflite" ]; then
    echo "[INFO]  Downloading MediaPipe face detector (~228KB)..."
    curl -sL "https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/1/blaze_face_short_range.tflite" \
        -o "$SCRIPT_DIR/face_detector.tflite"
    echo "[ OK ]  face_detector.tflite downloaded"
else
    echo "[ OK ]  face_detector.tflite already present"
fi

# Step 3: (Optional) NPU model export
if [ "$NPU" = "1" ]; then
    echo ""
    echo "[INFO]  Running NPU setup..."
    # Linux: use qai-hub-models export directly
    pip install --quiet qai-hub
    read -p "Enter your AI Hub API token: " TOKEN
    qai-hub configure --api_token "$TOKEN"

    mkdir -p "$SCRIPT_DIR/models"
    mkdir -p /tmp/qonclave_npu_export

    echo "[INFO]  Exporting CavaFace..."
    cd /tmp/qonclave_npu_export
    qai-hub-models export cavaface --target-runtime onnx --device "Snapdragon X Elite CRD" \
        --output-dir /tmp/qonclave_npu_export/cavaface
    ONNX=$(find /tmp/qonclave_npu_export/cavaface -name "*.onnx.zip" | head -1)
    unzip -o "$ONNX" -d /tmp/qonclave_npu_export/cavaface_unzipped
    cp /tmp/qonclave_npu_export/cavaface_unzipped/*/model.onnx "$SCRIPT_DIR/models/CavaFace.onnx"
    cp /tmp/qonclave_npu_export/cavaface_unzipped/*/model.data "$SCRIPT_DIR/models/CavaFace.data" 2>/dev/null || true
    echo "[ OK ]  CavaFace.onnx copied"

    echo "[INFO]  Exporting MediaPipeFace..."
    qai-hub-models export mediapipe_face --target-runtime onnx --device "Snapdragon X Elite CRD" \
        --output-dir /tmp/qonclave_npu_export/mp_face
    ONNX2=$(find /tmp/qonclave_npu_export/mp_face -name "*.onnx.zip" | head -1)
    unzip -o "$ONNX2" -d /tmp/qonclave_npu_export/mp_face_unzipped
    cp /tmp/qonclave_npu_export/mp_face_unzipped/*/model.onnx "$SCRIPT_DIR/models/MediaPipeFace.onnx"
    echo "[ OK ]  MediaPipeFace.onnx copied"

    cd "$SCRIPT_DIR"
fi

echo ""
echo "================================================================"
echo " Setup complete!"
echo ""
echo " Add photos to known_faces/ then run:"
if [ "$NPU" = "1" ]; then
    echo "   ./run.sh --npu identify unknown.jpg"
else
    echo "   ./run.sh identify unknown.jpg"
fi
echo "================================================================"
