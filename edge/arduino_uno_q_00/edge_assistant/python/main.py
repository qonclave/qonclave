"""
main.py — Qonclave Edge Assistant App Lab entry point.

State machine:
  IDLE → LISTENING → THINKING → SPEAKING → IDLE

Modes
-----
Normal          Hub LLM generates the response (default).
EDGE_TEST_MODE  Hub is skipped entirely; VOSK transcription is echoed back
                via piper-tts. Tests the full STT → TTS path in isolation.
File            AUDIO_SOURCE=file reads a .wav instead of the live mic; TTS
                output is skipped (response shown in UI + terminal only).
"""
from __future__ import annotations

import logging
import os
import threading

from dotenv import load_dotenv
load_dotenv()

from arduino.app_utils import App
from arduino.app_bricks.web_ui import WebUI

from audio_input import AudioInput
from led_matrix import set_state as set_led_state
from audio_output import speak, warmup as tts_warmup, TTS_OUTPUT_FILE
from hub_client import HubClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)

AUDIO_SOURCE = os.environ.get("AUDIO_SOURCE", "mic").lower()
DEVICE_ID = os.environ.get("DEVICE_ID", "unoq-01")
EDGE_TEST_MODE = os.environ.get("EDGE_TEST_MODE", "0").lower() in ("1", "true", "yes", "on")

# ---------------------------------------------------------------------------
# App Lab bricks
# ---------------------------------------------------------------------------
ui = WebUI()
hub = HubClient(ui) if not EDGE_TEST_MODE else None

# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------
_STATE_LOCK = threading.Lock()
_current_state: str = "idle"


def _set_state(state: str) -> None:
    global _current_state
    with _STATE_LOCK:
        _current_state = state
    log.info("State → %s", state)
    set_led_state(state)
    try:
        ui.send_message("state_change", {"state": state})
    except Exception as exc:
        log.debug("UI send_message failed: %s", exc)


# ---------------------------------------------------------------------------
# Audio pipeline callbacks
# ---------------------------------------------------------------------------
def _on_wake() -> None:
    _set_state("listening")


def _on_thinking() -> None:
    _set_state("thinking")


def _on_idle() -> None:
    _set_state("idle")


def _on_utterance(text: str) -> None:
    log.info("Utterance: %r", text)
    try:
        ui.send_message("query_text", {"text": text})
    except Exception:
        pass

    if EDGE_TEST_MODE:
        response_text = f"I heard: {text}"
        tool_used = None
        log.info("[TEST] Echo response (hub skipped): %r", response_text)
    else:
        response_text = ""
        tool_used = None
        try:
            result = hub.query(text, DEVICE_ID)
            response_text = result.get("response", "")
            tool_used = result.get("tool_used")
        except Exception as exc:
            log.error("Hub query failed: %s", exc)
            response_text = "Sorry, I couldn't reach the hub right now."

    log.info("Response (tool=%s): %r", tool_used, response_text[:120])
    print(f"\n[Query]    {text}")
    print(f"[Response] {response_text}\n")
    try:
        ui.send_message("response_text", {"text": response_text, "tool_used": tool_used})
    except Exception:
        pass

    if response_text and (AUDIO_SOURCE != "file" or TTS_OUTPUT_FILE):
        speak(response_text, on_playback_start=lambda: _set_state("speaking"))

    _set_state("idle")


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------
def main() -> None:
    mode_label = "EDGE_TEST (no hub)" if EDGE_TEST_MODE else "normal"
    log.info("=" * 60)
    log.info("  Qonclave Edge Assistant")
    log.info("  device_id   : %s", DEVICE_ID)
    log.info("  audio_source: %s", AUDIO_SOURCE)
    log.info("  mode        : %s", mode_label)
    log.info("  wake_word   : %s", os.environ.get("WAKE_WORD", "conclave"))
    if not EDGE_TEST_MODE:
        log.info("  hub         : %s:%s", os.environ.get("HUB_IP", "192.168.18.62"), os.environ.get("HUB_PORT", "8000"))
    log.info("=" * 60)

    if not EDGE_TEST_MODE:
        hub.start_health_monitor()

    if AUDIO_SOURCE != "file":
        tts_warmup()

    audio = AudioInput(
        on_wake=_on_wake,
        on_thinking=_on_thinking,
        on_idle=_on_idle,
        on_utterance=_on_utterance,
    )
    _set_state("idle")

    # AudioInput.run_forever() blocks; run in daemon thread so App.run() keeps the process alive.
    t = threading.Thread(target=audio.run_forever, daemon=True, name="audio-input")
    t.start()

    App.run()


if __name__ == "__main__":
    main()
