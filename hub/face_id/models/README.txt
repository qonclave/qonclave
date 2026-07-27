Put the exported CavaFace ONNX model here for NPU inference.

How to export (run once, on any machine with an AI Hub account):

  1. Sign up at https://workbench.aihub.qualcomm.com (free)
  2. pip install qai-hub-models
  3. qai-hub configure --api_token YOUR_TOKEN
  4. qai-hub-models export cavaface --target-runtime onnx --device "Snapdragon X Elite"
  5. Copy the resulting CavaFace.onnx here: hub/face_id/models/CavaFace.onnx

Then run with --npu flag:
  python face_pipeline.py --npu identify image.jpg
  .\run.ps1 identify -Image image.jpg -Npu

Without this file the pipeline runs on CPU automatically.
