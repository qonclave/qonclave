# Qonclave Person Emotions & 12x8 LED Matrix

This Arduino UNO Q app detects facial emotions (Happy, Sad, Surprise, Angry, Neutral, Fear) from a live camera feed and renders corresponding pixel-art facial expressions on the onboard 12x8 LED Matrix.

It also features an **Interactive Web Tester & Simulator** on port 7000 where you can click buttons to instantly trigger and test expressions on both the webpage and the physical LED matrix!

## Hardware Requirements
- **Arduino UNO Q**
- USB Webcam (optional for live recognition; web tester works standalone!)

## Emotion to 12x8 LED Matrix Mapping
| Emotion | LED Matrix Display | Source |
| :--- | :--- | :--- |
| **Happy** | Smile Icon | `gallery.h` (`LEDMATRIX_SMILE`) |
| **Sad** | Frown Icon | `gallery.h` (`LEDMATRIX_FROWN`) |
| **Surprise** | Wide Open Circular Mouth | Custom 12x8 Bitmap |
| **Angry** | Angled Eyebrows & Tensed Mouth | Custom 12x8 Bitmap |
| **Neutral** | Straight Horizontal Mouth | Custom 12x8 Bitmap |
| **Fear** | Wavy / Shaky Mouth Line | Custom 12x8 Bitmap |
| **Clear** | Safe Checkmark | `gallery.h` (`LEDMATRIX_CHECKMARK`) |

## Controls
- **Interactive Web Buttons:** Click `[Happy]`, `[Sad]`, `[Surprise]`, etc. in your browser to immediately test LED matrix patterns.
- **Potentiometer Knob (A0):** Turn the physical knob on pin A0 to dynamically adjust the live emotion recognition confidence threshold!
