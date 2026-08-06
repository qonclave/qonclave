All pose model files live here. Everything in this folder is gitignored and
generated/downloaded by setup — nothing here is committed, nothing is
vendored in.

NPU (ARM64 / Snapdragon X), exported by setup:
  hrnet_pose.onnx      - HRNetPose w8a8 graph (~1 MB)
  hrnet_pose.data      - weights (~110 MB, referenced by the .onnx by literal
                         filename — see rename note below)
  metadata.json        - AI Hub export metadata (quantization params; the
                         constants are mirrored in ../pose_pipeline.py)

Generated locally, per host:
  hrnet_pose_ctx.onnx  - precompiled HTP context binary (~29 MB). Cuts
                         session init from ~6.0s to ~0.3s. Tied to the QAIRT
                         build + HTP architecture that produced it: REGENERATE
                         on each host (python ..\pose_pipeline.py compile),
                         NEVER copy between machines or commit. A stale one
                         degrades to the raw-model fallback (slower start),
                         not a failure.

Model contract (from metadata.json):
  input  "image"  [1, 3, 256, 192] uint8, scale 0.003917243331670761, zp 0
  output heatmaps [1, 17, 64, 48]  uint8, scale 0.0037365437019616365, zp 10

How the files get generated (automated):
  ..\setup\setup_pose.ps1 runs the export + context compile on ARM64:
    cd ..\setup
    .\setup_pose.ps1                        <- prompts for AI Hub token
    .\setup_pose.ps1 -Token YOUR_TOKEN      <- pass token directly
    .\setup_pose.ps1 -Token YOUR_TOKEN -HrnetPoseJobId jXXXXXXXX
                                            <- reuse a completed compile job

How the files get generated (manual):
  1. Sign up at https://workbench.aihub.qualcomm.com (free)
  2. pip install qai-hub "qai-hub-models[hrnet-pose]"
  3. qai-hub configure --api_token YOUR_TOKEN
  4. qai-hub-models export hrnet_pose --target-runtime onnx \
       --precision w8a8 --device "Snapdragon X Elite CRD"
  5. Unzip the output *.onnx.zip
     Inside: job_<id>_optimized_onnx/model.onnx + model.data
  6. Copy them here as hrnet_pose.onnx / hrnet_pose.data.
     Note: the .onnx references its .data sidecar by literal filename, so a
     plain rename breaks the link — setup_pose.ps1 rewrites it via
     onnx.save_model (save_as_external_data + location), same as the face-ID
     setup does for CavaFace.
  7. python ..\pose_pipeline.py compile     <- bake hrnet_pose_ctx.onnx

Run after setup:
  python ..\pose_pipeline.py benchmark photo.jpg
  python ..\pose_pipeline.py estimate  photo.jpg
