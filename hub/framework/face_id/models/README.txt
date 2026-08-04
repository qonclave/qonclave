All face-ID model files live here. Everything in this folder is gitignored
and generated/downloaded by setup — nothing here is committed.

NPU (ARM64 / Snapdragon X), exported by setup:
  CavaFace.onnx        - embedder graph  (~264 KB)
  CavaFace.data        - embedder weights (~250 MB, referenced by the .onnx)
  MediaPipeFace.onnx   - face detector graph (~64 KB)
  MediaPipeFace.data   - face detector weights (~2 MB, referenced by the .onnx)

CPU (any platform), downloaded from Google:
  blaze_face_full_range.tflite - BlazeFace full_range detector (~1 MB)

IMPORTANT - MediaPipeFace.onnx is NOT a qai-hub-models catalog export.
  It is a conversion of Google's blaze_face_full_range.tflite (the same
  weights the CPU path uses), compiled for NPU via AI Hub. Its input is
  [1, 3, 192, 192] named "input", with TWO raw per-anchor outputs
  ([1, 2304, 16] + [1, 2304, 1]) that face_pipeline._detect_faces_npu()
  anchor-decodes itself.

  The qai-hub-models "mediapipe_face" catalog model is a DIFFERENT, less
  accurate checkpoint: 256x256, input named "image", with pre-decoded
  boxes/scores outputs. Installing it here does not raise an obvious error -
  it fails at first inference with an opaque onnxruntime shape mismatch
  ("Got: 192 Expected: 256"). setup_npu.ps1 now verifies the input shape
  after install to catch exactly that. See ..\README.md ("Rebuilding the
  detector") for why the catalog model was dropped.

How the NPU files get generated (automated):
  ..\setup\setup.ps1 runs this for you on ARM64. To run the export alone:
    cd ..\setup
    .\setup_npu.ps1                        <- prompts for AI Hub token
    .\setup_npu.ps1 -Token YOUR_TOKEN      <- pass token directly
    .\setup_npu.ps1 -Token YOUR_TOKEN -MediaPipeFaceJobId jg9dx40v5 `
                    -CavaFaceJobId jg9dj44q5   <- reuse completed jobs

  Do NOT pass an older MediaPipeFace job ID here (e.g. jpeyev475): those are
  catalog exports and will install the wrong model per the note above.
  Omitting -MediaPipeFaceJobId rebuilds the correct detector from scratch.

How the NPU files get generated (manual):
  1. Sign up at https://workbench.aihub.qualcomm.com (free)
  2. pip install qai-hub "qai-hub-models[cavaface]" tflite2onnx tflite
  3. qai-hub configure --api_token YOUR_TOKEN
  4. CavaFace (catalog export is correct for this one):
       qai-hub-models export cavaface --target-runtime onnx --device "Snapdragon X Elite CRD"
     MediaPipeFace: do NOT use the catalog export. Follow the TFLite->ONNX
     conversion steps in ..\README.md ("Rebuilding the detector"), which
     setup_npu.ps1's Export-FullRangeDetector automates.
  5. Unzip each output *.onnx.zip
     Inside: job_<id>_optimized_onnx/model.onnx + model.data
  6. Copy them here as CavaFace.onnx / CavaFace.data / MediaPipeFace.onnx /
     MediaPipeFace.data.
     Note: the .onnx references its .data sidecar by literal filename, so a
     plain rename breaks the link — setup_npu.ps1 rewrites it via onnx.save_model.

How blaze_face_full_range.tflite gets here:
  Pre-fetched by setup on x86. On ARM64 the NPU detector is used instead, so
  it is only downloaded on demand, if the CPU detector is ever reached (see
  face_pipeline.ensure_detector_model).

Run with NPU after setup:
  python face_pipeline.py --npu identify  unknown.jpg
  python face_pipeline.py --npu benchmark photo.jpg
