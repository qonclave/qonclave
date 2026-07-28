All face-ID model files live here. Everything in this folder is gitignored
and generated/downloaded by setup — nothing here is committed.

NPU (ARM64 / Snapdragon X), exported by setup:
  CavaFace.onnx        - embedder graph  (~264 KB)
  CavaFace.data        - embedder weights (~250 MB, referenced by the .onnx)
  MediaPipeFace.onnx   - face detector graph (~80 KB)

CPU (any platform), downloaded from Google:
  face_detector.tflite - BlazeFace detector (~228 KB)

How the NPU files get generated (automated):
  ..\setup\setup.ps1 runs this for you on ARM64. To run the export alone:
    cd ..\setup
    .\setup_npu.ps1                        <- prompts for AI Hub token
    .\setup_npu.ps1 -Token YOUR_TOKEN      <- pass token directly
    .\setup_npu.ps1 -Token YOUR_TOKEN -MediaPipeFaceJobId jpeyev475 `
                    -CavaFaceJobId jg9dj44q5   <- reuse completed jobs

How the NPU files get generated (manual):
  1. Sign up at https://workbench.aihub.qualcomm.com (free)
  2. pip install qai-hub "qai-hub-models[mediapipe_face,cavaface]"
  3. qai-hub configure --api_token YOUR_TOKEN
  4. qai-hub-models export cavaface --target-runtime onnx --device "Snapdragon X Elite CRD"
     qai-hub-models export mediapipe_face --target-runtime onnx \
       --device "Snapdragon X Elite CRD" --components face_detector \
       --include-detector-postprocessing --compile-options "--output_names boxes,scores"
  5. Unzip each output *.onnx.zip
     Inside: job_<id>_optimized_onnx/model.onnx + model.data
  6. Copy them here as CavaFace.onnx / CavaFace.data / MediaPipeFace.onnx.
     Note: the .onnx references its .data sidecar by literal filename, so a
     plain rename breaks the link — setup_npu.ps1 rewrites it via onnx.save_model.

How face_detector.tflite gets here:
  Pre-fetched by setup on x86. On ARM64 the NPU detector is used instead, so
  it is only downloaded on demand, if the CPU detector is ever reached (see
  face_pipeline.ensure_detector_model).

Run with NPU after setup:
  python face_pipeline.py --npu identify  unknown.jpg
  python face_pipeline.py --npu benchmark photo.jpg
