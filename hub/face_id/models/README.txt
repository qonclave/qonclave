Put the exported CavaFace ONNX model files here for NPU inference.

Required files (both needed):
  CavaFace.onnx   - model graph (~264 KB)
  CavaFace.data   - model weights (~250 MB)

How to generate (automated):
  Run setup_npu.ps1 from this folder — it handles everything:
    .\setup_npu.ps1                     <- prompts for AI Hub token
    .\setup_npu.ps1 -Token YOUR_TOKEN   <- pass token directly
    .\setup_npu.ps1 -JobId jpymz7w7p    <- reuse a completed job

How to generate (manual):
  1. Sign up at https://workbench.aihub.qualcomm.com (free)
  2. pip install qai-hub "qai-hub-models[cavaface]"
  3. qai-hub configure --api_token YOUR_TOKEN
  4. qai-hub-models export cavaface --target-runtime onnx --device "Snapdragon X Elite CRD"
  5. Unzip the output *.onnx.zip
     Inside: job_<id>_optimized_onnx/model.onnx + model.data
  6. Copy both here as CavaFace.onnx and CavaFace.data

Run with NPU after setup:
  python face_pipeline.py --npu identify  unknown.jpg
  python face_pipeline.py --npu benchmark photo.jpg
