# Person Detection Model Research

**Purpose:** Identify open-source / open-weight models for a two-stage Qonclave MVP:

1. **Edge coarse detection on Arduino UNO Q:** low-compute `person_present` detection on CPU.
2. **Laptop fine verification:** higher-confidence person verification, with optional known-vs-unknown identity recognition as a stretch.

**Research date:** 2026-07-22

**Isolation note:** This document is a standalone research artifact. It does not modify the main MVP plan, task board, or checkpoint files.

---

## 1. User-Confirmed Constraints

### 1.1 Edge Device

Arduino UNO Q Linux side:

- **MPU:** Qualcomm Dragonwing QRB2210, Quad-Core Arm Cortex-A53 @ 2.0 GHz.
- **RAM:** 2 GB or 4 GB LPDDR4.
- **Storage:** 16 GB or 32 GB eMMC.
- **OS:** Linux Debian OS on MPU.
- **Camera input:** TBD, assume standard image/frame input is available.
- **Package install:** Python packages can be installed through Arduino App Lab.

### 1.2 Runtime Constraints

- UNO Q must run real lightweight inference locally.
- CPU-only first; no GPU/NPU dependency for MVP.
- TensorFlow Lite / LiteRT is acceptable and preferred.
- Python and C++ APIs are acceptable.
- INT8 / quantized models are preferred but not mandatory.
- Model weights should be preloaded, not downloaded at runtime.
- 1 FPS or one inference every 1-3 seconds is acceptable.
- Input resolution is flexible.
- Edge task is strictly `person present / not present`.

### 1.3 Hub Constraints

- Hub runs on Snapdragon-powered laptop.
- Base hub task: verify person exists in image received from UNO Q.
- Stretch hub task: known vs unknown identity using a small teammate database.
- Face recognition is acceptable for known-vs-unknown.
- Only a few known teammates need to be enrolled.

### 1.4 Research Preferences

- Use off-the-shelf open-source / open-weight models only.
- Prefer official docs, official examples, or official model repositories.
- GPL/AGPL licensing is acceptable for hackathon purposes.
- Include broader survey and rejected options.
- No install commands needed yet.
- Final recommendation should pick exactly two base models: one edge model and one laptop model.
- Include fallback choices.

---

## 2. Final Recommendation

### 2.1 Pick These Two Base Models

| Role | Recommended Model | Why |
|---|---|---|
| UNO Q edge coarse detector | **EfficientDet-Lite0 / LiteRT Object Detector** | Best fit for CPU-only Linux edge inference, official Google AI Edge/TensorFlow Lite examples, COCO `person` class, simple image input, and enough accuracy for controlled MVP scenes. |
| Laptop hub verifier | **Ultralytics YOLO11n pretrained COCO model** | Very easy Python-side verification path, strong off-the-shelf detector, small YOLO-family nano model, good demo ergonomics, and more laptop compute available. |

### 2.2 Stretch Add-On for Known vs Unknown

If the base two-stage demo is stable, add this laptop-only identity pipeline:

| Stretch Role | Recommended Model(s) | Why |
|---|---|---|
| Face detection + recognition | **OpenCV YuNet + OpenCV SFace** | Official OpenCV DNN APIs, small face detector, open model zoo assets, no training required, works with a few enrolled teammate images. |

This is deliberately not part of the base two-model recommendation. It should only be added after the edge detector and hub verifier work end to end.

---

## 3. Recommended Base Architecture

### 3.1 Edge: EfficientDet-Lite0 / LiteRT

Use the UNO Q as a coarse trigger:

1. Capture camera frame.
2. Resize to model input.
3. Run EfficientDet-Lite0 / LiteRT object detector.
4. Filter detections to COCO class `person`.
5. If confidence exceeds threshold, save/send one selected frame with event metadata.

Why it fits:

- Official Google AI Edge docs describe object detection from images/video using TensorFlow Lite / LiteRT style deployment.
- TensorFlow Lite has an official Raspberry Pi object detection example designed for camera-based edge devices.
- EfficientDet-Lite models are designed for mobile/IoT-style object detection.
- 1 FPS target is forgiving; the model does not need to run continuously at video frame rate.

Risk:

- Actual UNO Q runtime/package availability must be verified.
- Camera pipeline may be more work than model runtime.

Fallback:

- Try OpenCV DNN with a small MobileNet SSD-style model.
- If TFLite runtime works but EfficientDet-Lite0 is slow, reduce resolution, run every N frames, or test a smaller MobileNet SSD / NanoDet-style fallback.

### 3.2 Hub: YOLO11n

Use the laptop as the verification stage:

1. Receive JSON event and one image from UNO Q.
2. Run YOLO11n on the received image.
3. Filter detections to `person`.
4. Return `hub_verified=true/false` and confidence.
5. Raise local terminal/dashboard alert only if hub verification passes.

Why it fits:

- YOLO11n is an official Ultralytics pretrained nano detector option.
- The Python API is simple for image prediction.
- The laptop can tolerate a larger and more accurate detector than the edge device.
- It creates a clear two-level story: low-cost edge trigger, stronger hub verification.

Risk:

- Ultralytics package install and PyTorch stack may be heavier than necessary.

Fallback:

- Use YOLOv8n if YOLO11n docs/package path is simpler in the development environment.
- Use OpenCV DNN with a COCO object detector if PyTorch/Ultralytics installation is blocked.

---

## 4. Survey of Candidate Models

### 4.1 Edge Candidates for UNO Q

| Candidate | Task | Runtime Fit | Strengths | Concerns | Verdict |
|---|---|---|---|---|---|
| EfficientDet-Lite0 / LiteRT Object Detector | Person/object detection | TFLite/LiteRT | Official Google edge examples, COCO person class, good first choice for Linux edge CPU | Must validate actual runtime speed on UNO Q | **Recommended edge model** |
| MobileNet SSD / SSD MobileNet variants | Person/object detection | TFLite or OpenCV DNN | Classic lightweight detector, often easy to run on CPU | Older accuracy, model/source fragmentation | **Fallback** |
| NanoDet / NanoDet-m | Person/object detection | Usually NCNN/MNN/OpenVINO/Torch-style export | Very small; official repo reports sub-megabyte INT8 model variants | Less aligned with preferred TFLite/LiteRT path; integration may take longer | Fallback/research-only |
| YOLOX-Nano | Person/object detection | PyTorch/ONNX/NCNN-style | Official nano model, COCO detector, stronger than many old SSDs | Runtime/export path may be more work on UNO Q than TFLite | Fallback/research-only |
| OpenCV Zoo MediaPipe person detector | Person/body detection | OpenCV DNN | Official OpenCV Zoo asset; targets person detection | More specialized body/pose-ish path; may not be easiest for COCO `person` MVP | Secondary fallback |
| YOLO11n / YOLOv8n exported to TFLite/ONNX | Person/object detection | Possible export | One model family across edge and hub | More moving parts; exported runtime may still be heavier than EfficientDet-Lite0 | Defer until base works |
| MediaPipe Selfie Segmentation | Human segmentation | MediaPipe/TFLite | Very lightweight human foreground signal | Not a detector; no normal `person` bbox/class confidence | Reject for MVP detector |
| MoveNet / BlazePose | Human pose | TFLite/MediaPipe | Useful for future fall detection | Solves pose, not simple person detection; extra logic required | Reject for current MVP |

### 4.2 Laptop Verification Candidates

| Candidate | Task | Runtime Fit | Strengths | Concerns | Verdict |
|---|---|---|---|---|---|
| YOLO11n | Person/object detection | Python / Ultralytics | Official nano model, easy Python prediction, strong demo path | PyTorch dependency may be heavier | **Recommended hub model** |
| YOLOv8n | Person/object detection | Python / Ultralytics | Mature, widely used, similar integration path | Slightly older than YOLO11 family | Fallback |
| EfficientDet-Lite / TFLite reused on laptop | Person/object detection | TFLite | Keeps stack simple | Less compelling as heavier second stage | Fallback if YOLO install blocked |
| OpenCV DNN COCO detector | Person/object detection | OpenCV DNN | Lightweight dependency if OpenCV already installed | Model selection can be fragmented | Fallback |
| OpenCV YuNet + SFace | Known/unknown face recognition | OpenCV DNN | Official OpenCV APIs, open model zoo, good for small teammate enrollment | Needs visible/frontal face; not full-body identity | **Stretch identity add-on** |
| FaceNet / facenet-pytorch | Face recognition | PyTorch | Popular face embedding approach | More dependency/setup burden, older ecosystem fragmentation | Defer/reject for MVP |
| DeepFace package | Face recognition wrapper | Python | Convenient wrapper over multiple face models | Heavy wrapper, may download assets, less controlled for hackathon demo | Reject for MVP |
| Dlib / face_recognition | Face recognition | Python/C++ | Simple known/unknown API | Install/build can be painful; CPU cost and dependency issues | Reject unless already installed |

---

## 5. Notes on Known vs Unknown Recognition

For known-vs-unknown, the cleanest MVP-compatible path is face recognition on the laptop only.

Recommended behavior:

1. Keep YOLO11n as the person verifier.
2. If person is verified and identity stretch is enabled, run face detection on the same received frame.
3. If a face is found, compute a face embedding and compare against 1-4 enrolled teammate embeddings.
4. Return one of:
   - `known_person`
   - `unknown_person`
   - `no_face_visible`
   - `identity_not_enabled`

Important guardrail:

- Do not let identity recognition block the base demo.
- If the face is side-facing, too far away, or missing, return `no_face_visible` and still show the base person verification.

---

## 6. Source Notes and Evidence

### 6.1 Google AI Edge / TensorFlow Lite

- Google AI Edge documents object detection tasks for image/video inputs and LiteRT-style deployment.
- TensorFlow Lite provides an official Raspberry Pi object detection example for camera-based edge inference.
- TensorFlow Hub hosts EfficientDet-Lite model entries intended for TensorFlow Lite object detection use.

Research implication:

- EfficientDet-Lite0 / LiteRT is the safest first edge path because it matches the preferred runtime and has official examples.

### 6.2 Ultralytics YOLO

- Ultralytics documents Python usage for loading pretrained YOLO models and running image prediction.
- Ultralytics model listings include YOLO11 nano variants intended as small pretrained detectors.
- Ultralytics licensing is AGPL/commercial dual-license for the software stack, which is acceptable for this hackathon based on user constraints.

Research implication:

- YOLO11n is a strong laptop verifier because setup and inference code are straightforward and accuracy is likely better than the edge trigger.

### 6.3 OpenCV Face Detection / Recognition

- OpenCV documents `FaceDetectorYN` and `FaceRecognizerSF` APIs for DNN-based face detection and recognition.
- OpenCV Zoo includes YuNet face detection and SFace face recognition model assets.
- OpenCV documentation shows YuNet as very small and SFace as a larger face recognition model.

Research implication:

- YuNet + SFace is the best stretch path for known-vs-unknown identity when there are only a few enrolled teammates.

### 6.4 NanoDet and YOLOX

- NanoDet official repo highlights very small model variants, including INT8-focused sizes.
- YOLOX official repo includes nano model variants for COCO detection.

Research implication:

- These are promising fallbacks if EfficientDet-Lite0 is too slow or inaccurate, but they are not the first pick because they add runtime/export/integration uncertainty compared with TFLite/LiteRT.

---

## 7. Practical Selection Criteria

### 7.1 Edge Model Must Pass

- Runs locally on UNO Q CPU.
- Detects a standing or partially visible person in controlled lighting.
- Produces class label, confidence, and bounding box.
- Runs at least once every 1-3 seconds.
- Can be packaged with preloaded weights.
- Does not require internet at demo time.

### 7.2 Hub Model Must Pass

- Runs on laptop CPU or available default laptop runtime.
- Accepts received image file.
- Verifies person/no-person with higher confidence than edge stage.
- Produces clear terminal logs and optional annotated image.
- Does not block demo if identity stretch is disabled.

### 7.3 Identity Stretch Must Pass

- Enrolls 1-4 teammates from controlled images.
- Recognizes a known teammate facing camera in controlled lighting.
- Labels non-enrolled person as unknown, or returns `no_face_visible` safely.
- Never breaks base person verification flow.

---

## 8. Implementation Order After Model Selection

1. Test camera frame capture independent of ML.
2. Test EfficientDet-Lite0 / LiteRT on one saved image on UNO Q.
3. Test EfficientDet-Lite0 / LiteRT on live frames at low frequency.
4. Send selected frame to laptop after local person trigger.
5. Test YOLO11n on local laptop images.
6. Connect YOLO11n verifier to uploaded UNO Q frame.
7. Record fallback video immediately after first successful end-to-end run.
8. Only then try YuNet + SFace for known-vs-unknown.

---

## 9. Final Ranked Shortlist

### 9.1 Edge Shortlist

1. **EfficientDet-Lite0 / LiteRT Object Detector** — recommended.
2. **MobileNet SSD / SSD MobileNet TFLite or OpenCV DNN** — first fallback.
3. **NanoDet INT8 / NanoDet-m** — promising small fallback, but more integration risk.
4. **YOLOX-Nano** — capable fallback, but likely more integration work.
5. **OpenCV Zoo MediaPipe person detector** — specialized fallback if OpenCV DNN path is easiest.

### 9.2 Laptop Shortlist

1. **YOLO11n** — recommended base verifier.
2. **YOLOv8n** — first fallback if YOLO11n path is inconvenient.
3. **OpenCV DNN COCO detector** — fallback if PyTorch/Ultralytics stack is blocked.
4. **EfficientDet-Lite reused on laptop** — fallback for simplest shared runtime.
5. **OpenCV YuNet + SFace** — identity stretch, not base verifier.

---

## 10. Final Answer

Use exactly these two models for the base MVP:

1. **UNO Q:** EfficientDet-Lite0 / LiteRT Object Detector for local coarse `person_present` detection.
2. **Laptop:** Ultralytics YOLO11n for second-stage `person_present` verification.

Then, if time remains, add:

- **Laptop identity stretch:** OpenCV YuNet + SFace for known-vs-unknown face recognition.

This gives the cleanest demo narrative:

- UNO Q does cheap local sensing.
- UNO Q escalates only a selected frame.
- Laptop performs stronger verification.
- Optional laptop-only identity recognition adds sophistication without risking the base demo.

---

## 11. References

- [Google AI Edge: Object detection guide](https://ai.google.dev/edge/litert/libraries/task_library/object_detector)
- [TensorFlow Lite Raspberry Pi object detection example](https://github.com/tensorflow/examples/blob/master/lite/examples/object_detection/raspberry_pi/README.md)
- [TensorFlow Hub: EfficientDet-Lite0 feature vector model family](https://www.tensorflow.org/hub/tutorials/tf2_object_detection)
- [Ultralytics Python usage documentation](https://docs.ultralytics.com/usage/python/)
- [Ultralytics model page for YOLO11n](https://platform.ultralytics.com/models/yolo11n)
- [Ultralytics GitHub repository](https://github.com/ultralytics/ultralytics)
- [OpenCV DNN face detection and recognition tutorial](https://docs.opencv.org/4.x/d0/dd4/tutorial_dnn_face.html)
- [OpenCV Zoo: YuNet face detection](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet)
- [OpenCV Zoo: SFace face recognition](https://github.com/opencv/opencv_zoo/tree/main/models/face_recognition_sface)
- [NanoDet official repository](https://github.com/RangiLyu/nanodet)
- [YOLOX official repository](https://github.com/Megvii-BaseDetection/YOLOX)
- [OpenCV Zoo: MediaPipe person detection](https://github.com/opencv/opencv_zoo/tree/main/models/person_detection_mediapipe)
