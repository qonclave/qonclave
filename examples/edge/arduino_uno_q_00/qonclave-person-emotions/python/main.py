"""
main.py — Linux MPU Facial Emotion Recognition & LED Matrix Controller for Arduino UNO Q.

1. Analyzes live webcam frames for facial emotions (Happy, Sad, Surprise, Angry, Neutral, Fear).
2. Bridges detected emotions to sketch.ino to render 12x8 LED Matrix facial expressions.
3. Hosts interactive Web UI (port 7000) with instant emotion testing buttons & virtual matrix.
4. Syncs potentiometer knob on A0 to live recognition confidence threshold.
"""

import time
import threading
from datetime import datetime, UTC
from arduino.app_utils import App, Bridge
from arduino.app_bricks.web_ui import WebUI

ui = WebUI()
current_threshold = 0.50
active_emotion = "clear"

try:
    import cv2
    from fer import FER
    detector = FER(mtcnn=False)
    LIVE_VISION_ENABLED = True
    print("[✓] FER Facial Emotion Recognition engine initialized successfully.")
except Exception as err:
    LIVE_VISION_ENABLED = False
    print(f"[!] FER / OpenCV not available ({err}). Running in Interactive Web Tester & Simulator mode.")

def handle_knob_change(percentage_str):
    global current_threshold
    try:
        val = float(percentage_str) / 100.0
        current_threshold = val
        ui.send_message("knob_update", message={"threshold": val})
    except Exception:
        pass

Bridge.provide("on_knob_change", handle_knob_change)
Bridge.call("set_emotion", "clear")

def trigger_emotion_expression(emotion: str, scores: dict = None, source: str = "camera"):
    global active_emotion
    active_emotion = emotion
    print(f"[🎭 EMOTION -> LED MATRIX] Expression triggered: '{emotion.upper()}' (source: {source})")
    
    Bridge.call("set_emotion", emotion)
    
    entry = {
        "emotion": emotion,
        "scores": scores or {emotion: 0.95},
        "source": source,
        "timestamp": datetime.now(UTC).isoformat()
    }
    ui.send_message("emotion_update", message=entry)

def on_web_test_emotion(sid, emotion):
    print(f"[🌐 WEB TESTER] Override button clicked for emotion: '{emotion}'")
    trigger_emotion_expression(emotion, scores={emotion: 1.0}, source="web_override")

ui.on_message("test_emotion", on_web_test_emotion)
ui.on_message("override_th", lambda sid, val: handle_knob_change(str(int(float(val)*100))))

def live_emotion_loop():
    if not LIVE_VISION_ENABLED:
        return
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("[!] Webcam could not be opened for live emotion capture.")
        return
    
    print("[*] Starting live webcam facial emotion detection loop...")
    while True:
        ret, frame = cap.read()
        if not ret:
            time.sleep(0.1)
            continue
        
        try:
            results = detector.detect_emotions(frame)
            if results:
                emotions_dict = results[0]["emotions"]
                dom_emo = max(emotions_dict, key=emotions_dict.get)
                if emotions_dict[dom_emo] >= current_threshold and dom_emo != active_emotion:
                    trigger_emotion_expression(dom_emo, scores=emotions_dict, source="camera")
        except Exception as e:
            pass
        time.sleep(0.15)

if LIVE_VISION_ENABLED:
    t = threading.Thread(target=live_emotion_loop, daemon=True)
    t.start()

print("=== Qonclave Person Emotions App Running (Port 7000) ===")
App.run()
