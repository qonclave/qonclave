"""
audio_output.py — piper-tts synthesis + USB speaker playback.

speak(text) is a no-op when AUDIO_SOURCE=file (text-only test mode).

On first call, downloads the piper voice model if not present.
"""
from __future__ import annotations

import logging
import os
import re
import wave

log = logging.getLogger(__name__)

_HW_CARD_RE = re.compile(r'\(hw:(\d+),(\d+)\)')

# Set ESPEAKNG_DATA_PATH before any piper/piper_phonemize import.
# piper_phonemize is a C extension that reads this env var at import time;
# setting it afterwards has no effect.
def _set_espeak_env_early() -> None:
    import importlib.util
    spec = importlib.util.find_spec("piper")
    if spec and spec.origin:
        import pathlib
        data_dir = pathlib.Path(spec.origin).parent / "espeak-ng-data"
        if data_dir.is_dir():
            os.environ["ESPEAKNG_DATA_PATH"] = str(data_dir)

_set_espeak_env_early()

AUDIO_SOURCE = os.environ.get("AUDIO_SOURCE", "mic").lower()
PIPER_MODEL_DIR = os.environ.get("PIPER_MODEL_DIR", "/app/models/piper")
PIPER_VOICE = os.environ.get("PIPER_VOICE", "en_US-lessac-medium")
TTS_OUTPUT_FILE = os.environ.get("TTS_OUTPUT_FILE", "").strip()  # e.g. /app/media/tts_output.wav
TTS_PLAYBACK_RATE = os.environ.get("TTS_PLAYBACK_RATE", "").strip()
TTS_VOLUME_GAIN = float(os.environ.get("TTS_VOLUME_GAIN", "1.5"))
SPEAKER_DEVICE = os.environ.get("SPEAKER_DEVICE", "").strip()

_VOICE_URL_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0"
_VOICE_PATHS = {
    "en_US-lessac-medium": "en/en_US/lessac/medium",
}

_voice_instance = None
_speaker_device = None  # resolved once at warmup, reused for every speak()


def _log_audio_devices(sd) -> None:
    """Log all audio devices so the user can confirm the right speaker is picked."""
    devices = sd.query_devices()
    defaults = sd.default.device  # (input_idx, output_idx)
    log.info("[AUDIO] Available audio devices:")
    for i, dev in enumerate(devices):
        ins = dev.get("max_input_channels", 0)
        outs = dev.get("max_output_channels", 0)
        tags = []
        if i == defaults[0]:
            tags.append("default-in")
        if i == defaults[1]:
            tags.append("default-out")
        tag_str = f"  [{', '.join(tags)}]" if tags else ""
        log.info("[AUDIO]   [%d] %-40s  in=%d out=%d%s", i, dev["name"], ins, outs, tag_str)


def _find_best_usb_device(sd, want_input: bool) -> "tuple[int | None, str]":
    """
    Find the best sounddevice index for a USB speaker (want_input=False) or mic.

    Preference order:
      1. plug_card_N for a USB card that has BOTH input AND output channels
         (full-duplex headset like Jabra) — ensures mic and speaker share one card.
      2. plug_card_N for any USB card with the required channels.
      3. Any device whose name contains "usb" with the required channels.

    Returns (index, name) or (None, "").
    """
    channel_key = "max_input_channels" if want_input else "max_output_channels"
    plug_suffix = "_mic" if want_input else "_spk"
    devices = sd.query_devices()

    usb_duplex_cards: set = set()
    usb_cards: set = set()
    card_has_input: dict = {}
    card_has_output: dict = {}
    for dev in devices:
        m = _HW_CARD_RE.search(dev["name"])
        if not m or "usb" not in dev["name"].lower():
            continue
        c = m.group(1)
        usb_cards.add(c)
        if dev.get("max_input_channels", 0) > 0:
            card_has_input[c] = True
        if dev.get("max_output_channels", 0) > 0:
            card_has_output[c] = True
    for c in usb_cards:
        if card_has_input.get(c) and card_has_output.get(c):
            usb_duplex_cards.add(c)

    # Pass 1: prefer plug_ device on a full-duplex USB card
    for i, dev in enumerate(devices):
        name = dev["name"]
        if dev[channel_key] > 0 and name.startswith("plug_card_") and name.endswith(plug_suffix):
            card_num = name.split("_")[2]
            if card_num in usb_duplex_cards:
                log.info("[AUDIO] Preferred full-duplex USB card %s for %s",
                         card_num, "input" if want_input else "output")
                return i, name

    # Pass 2: any plug_ device on any USB card with the required channels
    for i, dev in enumerate(devices):
        name = dev["name"]
        if dev[channel_key] > 0 and name.startswith("plug_card_") and name.endswith(plug_suffix):
            card_num = name.split("_")[2]
            if card_num in usb_cards:
                return i, name

    # Fallback: any device with "usb" in its name and the right channels
    for i, dev in enumerate(devices):
        if dev[channel_key] > 0 and "usb" in dev["name"].lower():
            return i, dev["name"]

    return None, ""


def _resolve_output_device(sd) -> "int | None":
    """
    Return the sounddevice device index to use for speaker output.

    Resolution order:
      1. SPEAKER_DEVICE numeric  → use that index directly
      2. SPEAKER_DEVICE string   → first device whose name contains it (case-insensitive)
      3. empty                   → best USB speaker via _find_best_usb_device()
      4. fallback                → None (sounddevice picks the system default)
    """
    channel_key = "max_output_channels"
    if SPEAKER_DEVICE:
        if SPEAKER_DEVICE.lstrip("-").isdigit():
            idx = int(SPEAKER_DEVICE)
            try:
                name = sd.query_devices(idx)["name"]
            except Exception:
                name = "<unknown>"
            log.info("[TTS ] SPEAKER_DEVICE=%r → pinned to device [%d] %s", SPEAKER_DEVICE, idx, name)
            return idx
        devices = sd.query_devices()
        for i, dev in enumerate(devices):
            if dev[channel_key] > 0 and SPEAKER_DEVICE.lower() in dev["name"].lower():
                log.info("[TTS ] SPEAKER_DEVICE=%r → name-matched device [%d] %s", SPEAKER_DEVICE, i, dev["name"])
                return i
        log.warning("[TTS ] SPEAKER_DEVICE=%r matched no output device; falling back to USB auto-detect", SPEAKER_DEVICE)

    idx, name = _find_best_usb_device(sd, want_input=False)
    if idx is not None:
        log.info("[TTS ] Auto-selected USB output device [%d] %s", idx, name)
        return idx

    # Last resort: sounddevice system default
    default_idx = sd.default.device[1]
    try:
        default_name = sd.query_devices(default_idx)["name"]
    except Exception:
        default_name = "<unknown>"
    log.info("[TTS ] No USB output device found; falling back to system default [%d] %s", default_idx, default_name)
    return None


def _get_playback_rate(sd, device, desired_rate: int) -> int:
    """Return a speaker-supported playback rate for synthesized audio."""
    if TTS_PLAYBACK_RATE:
        forced_rate = int(TTS_PLAYBACK_RATE)
        log.info("[TTS ] TTS_PLAYBACK_RATE=%d Hz — forcing playback rate", forced_rate)
        return forced_rate

    try:
        sd.check_output_settings(device=device, channels=1, samplerate=desired_rate, dtype="float32")
        return desired_rate
    except Exception as exc:
        try:
            info = sd.query_devices(device if device is not None else sd.default.device[1])
            fallback_rate = int(info.get("default_samplerate", 44100))
        except Exception:
            fallback_rate = 44100
        log.warning("[TTS ] Speaker cannot open at piper rate %d Hz (%s); "
                    "resampling playback to %d Hz", desired_rate, exc, fallback_rate)
        return fallback_rate


def _resample_audio(audio, src_rate: int, dst_rate: int):
    """Resample mono float audio for speaker playback."""
    if src_rate == dst_rate:
        return audio
    try:
        from math import gcd
        from scipy.signal import resample_poly
        g = gcd(dst_rate, src_rate)
        return resample_poly(audio, dst_rate // g, src_rate // g).astype(audio.dtype)
    except ImportError:
        import numpy as np
        if len(audio) == 0:
            return audio
        duration = len(audio) / src_rate
        src_t = np.linspace(0.0, duration, num=len(audio), endpoint=False)
        dst_len = max(1, int(round(duration * dst_rate)))
        dst_t = np.linspace(0.0, duration, num=dst_len, endpoint=False)
        return np.interp(dst_t, src_t, audio).astype(audio.dtype)


def _apply_output_gain(audio):
    """Apply software output gain and clip to the valid float playback range."""
    if TTS_VOLUME_GAIN == 1.0 or len(audio) == 0:
        return audio
    import numpy as np
    amplified = audio * TTS_VOLUME_GAIN
    clipped = np.clip(amplified, -1.0, 1.0).astype(audio.dtype)
    clipped_samples = int(np.count_nonzero(amplified != clipped))
    if clipped_samples:
        clipped_pct = clipped_samples * 100.0 / len(amplified)
        log.warning("[TTS ] TTS_VOLUME_GAIN=%.2f clipped %.1f%% of playback samples; lower it if speech sounds distorted",
                    TTS_VOLUME_GAIN, clipped_pct)
    else:
        log.info("[TTS ] TTS_VOLUME_GAIN=%.2f applied", TTS_VOLUME_GAIN)
    return clipped


def _voice_files_ready(onnx_path: str, json_path: str) -> bool:
    """Return True when both piper voice files exist and look usable."""
    if not os.path.exists(onnx_path) or not os.path.exists(json_path):
        return False
    onnx_size = os.path.getsize(onnx_path)
    json_size = os.path.getsize(json_path)
    if onnx_size < 1024 * 1024 or json_size < 100:
        log.warning("[TTS ] Piper voice files look incomplete "
                    "(onnx=%d bytes, json=%d bytes); redownloading", onnx_size, json_size)
        return False
    return True


def _ensure_voice() -> None:
    """Download the piper voice .onnx + .json if not already present."""
    voice_dir = os.path.join(PIPER_MODEL_DIR, PIPER_VOICE)
    onnx_path = os.path.join(voice_dir, f"{PIPER_VOICE}.onnx")
    json_path = os.path.join(voice_dir, f"{PIPER_VOICE}.onnx.json")
    if _voice_files_ready(onnx_path, json_path):
        return

    subpath = _VOICE_PATHS.get(PIPER_VOICE)
    if not subpath:
        raise ValueError(f"Unknown piper voice {PIPER_VOICE!r}; add it to _VOICE_PATHS")

    import urllib.request
    os.makedirs(voice_dir, exist_ok=True)
    for fname, dest in [(f"{PIPER_VOICE}.onnx", onnx_path), (f"{PIPER_VOICE}.onnx.json", json_path)]:
        url = f"{_VOICE_URL_BASE}/{subpath}/{fname}"
        tmp_dest = dest + ".tmp"
        log.info("Downloading piper voice file: %s", fname)
        try:
            urllib.request.urlretrieve(url, tmp_dest)
            os.replace(tmp_dest, dest)
        except Exception as exc:
            if os.path.exists(tmp_dest):
                os.remove(tmp_dest)
            log.error("Failed to download %s: %s", fname, exc)
            raise

    if not _voice_files_ready(onnx_path, json_path):
        raise RuntimeError(f"Piper voice download did not produce valid files in {voice_dir}")


def _log_piper_info() -> None:
    """Log the installed piper-tts version and whether espeak-ng data is bundled."""
    try:
        import importlib.metadata
        version = importlib.metadata.version("piper-tts")
        log.info("[TTS ] piper-tts version: %s", version)
    except Exception:
        log.warning("[TTS ] Could not determine piper-tts version")

    # piper >= 1.2 bundles espeak-ng data inside the wheel under piper/espeak-ng-data/
    try:
        import piper as _piper_mod
        import pathlib
        piper_dir = pathlib.Path(_piper_mod.__file__).parent
        data_dir = piper_dir / "espeak-ng-data"
        if data_dir.is_dir():
            log.info("[TTS ] Bundled espeak-ng-data found at %s", data_dir)
        else:
            import shutil
            system_espeak = shutil.which("espeak-ng") or shutil.which("espeak")
            if system_espeak:
                log.info("[TTS ] No bundled espeak-ng-data; using system espeak-ng: %s", system_espeak)
            else:
                log.error("[TTS ] No bundled espeak-ng-data and no system espeak-ng on PATH — "
                          "TTS will produce silence. Upgrade piper-tts: pip install --upgrade 'piper-tts>=1.2.0'")
    except Exception as exc:
        log.warning("[TTS ] Could not check espeak-ng-data: %s", exc)


def _configure_espeak_data() -> None:
    """Log the ESPEAKNG_DATA_PATH that was set at module load time."""
    val = os.environ.get("ESPEAKNG_DATA_PATH", "")
    if val:
        log.info("[TTS ] ESPEAKNG_DATA_PATH = %s", val)
    else:
        log.warning("[TTS ] ESPEAKNG_DATA_PATH not set — piper_phonemize may fail to phonemize")


def _get_voice():
    global _voice_instance
    if _voice_instance is None:
        _log_piper_info()
        _configure_espeak_data()
        _ensure_voice()
        from piper import PiperVoice
        onnx_path = os.path.join(PIPER_MODEL_DIR, PIPER_VOICE, f"{PIPER_VOICE}.onnx")
        _voice_instance = PiperVoice.load(onnx_path)
        log.info("Piper voice loaded: %s", PIPER_VOICE)
    return _voice_instance


def _find_piper_bin() -> str:
    """Return path to the piper CLI binary."""
    import shutil
    import sys
    # Try the venv's bin dir first (same Python prefix), then PATH
    venv_bin = os.path.join(os.path.dirname(sys.executable), "piper")
    if os.path.isfile(venv_bin):
        return venv_bin
    found = shutil.which("piper")
    if found:
        return found
    raise FileNotFoundError("piper CLI not found — is piper-tts installed in this venv?")


def _get_espeak_data_dir() -> str:
    """Return the bundled espeak-ng-data path, when available."""
    import importlib.util, pathlib
    spec = importlib.util.find_spec("piper")
    if spec and spec.origin:
        data_dir = pathlib.Path(spec.origin).parent / "espeak-ng-data"
        if data_dir.is_dir():
            return str(data_dir)
    return ""


def _synthesize_raw(voice, text: str) -> bytes:
    """Synthesize text to raw PCM using the piper CLI subprocess."""
    try:
        import subprocess
        _ensure_voice()
        piper_bin = _find_piper_bin()
        onnx_path = os.path.join(PIPER_MODEL_DIR, PIPER_VOICE, f"{PIPER_VOICE}.onnx")
        json_path = os.path.join(PIPER_MODEL_DIR, PIPER_VOICE, f"{PIPER_VOICE}.onnx.json")
        espeak_dir = _get_espeak_data_dir()
        env = os.environ.copy()
        if espeak_dir:
            env["ESPEAKNG_DATA_PATH"] = espeak_dir
        cmd = [piper_bin, "--model", onnx_path, "--config", json_path, "--output_raw"]
        if espeak_dir:
            log.info("[TTS ] Using bundled espeak-ng-data: %s", espeak_dir)
        log.info("[TTS ] Running: %s", " ".join(cmd))
        result = subprocess.run(
            cmd,
            input=(text.rstrip() + "\n").encode("utf-8"),
            capture_output=True,
            env=env,
            timeout=float(os.environ.get("PIPER_TIMEOUT_SEC", "60")),
        )
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        if result.returncode != 0:
            log.error("[TTS ] piper subprocess failed (rc=%d): %s", result.returncode, stderr)
            return b""
        raw = result.stdout
        if stderr:
            log.debug("[TTS ] piper stderr: %s", stderr)
        if not raw:
            log.error("[TTS ] piper subprocess returned 0 bytes; check espeak-ng-data and voice files")
            return b""
        log.info("[TTS ] piper subprocess → %d bytes raw PCM", len(raw))
        return raw
    except Exception as exc:
        log.error("[TTS ] piper subprocess error: %s", exc, exc_info=True)
        return b""



def _get_sample_rate(voice=None) -> int:
    """Return the voice's sample rate from the .onnx.json config file."""
    try:
        import json as _json
        json_path = os.path.join(PIPER_MODEL_DIR, PIPER_VOICE, f"{PIPER_VOICE}.onnx.json")
        with open(json_path) as f:
            cfg = _json.load(f)
        return int(cfg.get("audio", {}).get("sample_rate", 22050))
    except Exception:
        pass
    if voice is not None:
        try:
            return int(voice.config.sample_rate)
        except (AttributeError, TypeError):
            pass
    return 22050


def speak(text: str, on_playback_start=None) -> None:
    """Synthesize text to audio.
    - TTS_OUTPUT_FILE set: save wav to that path (no speaker needed).
    - AUDIO_SOURCE=file without TTS_OUTPUT_FILE: no-op (text shown in UI/terminal only).
    - Otherwise: play through USB speaker.
    - on_playback_start: optional callback fired immediately before speaker playback.
    """
    if AUDIO_SOURCE == "file" and not TTS_OUTPUT_FILE:
        log.info("[TTS ] Skipped (AUDIO_SOURCE=file, no TTS_OUTPUT_FILE) — text: %r", text)
        return
    text = text.strip()
    if not text:
        return
    log.info("[TTS ] Synthesizing: %r", text[:120])
    try:
        _ensure_voice()
        sample_rate = _get_sample_rate()
        raw_pcm = _synthesize_raw(None, text)
        if not raw_pcm:
            return

        if TTS_OUTPUT_FILE:
            os.makedirs(os.path.dirname(TTS_OUTPUT_FILE) or ".", exist_ok=True)
            with wave.open(TTS_OUTPUT_FILE, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sample_rate)
                wf.writeframes(raw_pcm)
            size_kb = os.path.getsize(TTS_OUTPUT_FILE) / 1024
            log.info("[TTS ] Saved to %s (%.1f KB)", TTS_OUTPUT_FILE, size_kb)
        else:
            import sounddevice as sd
            import numpy as np
            speaker_device = _speaker_device if _speaker_device is not None else _resolve_output_device(sd)
            audio = np.frombuffer(raw_pcm, dtype=np.int16).astype(np.float32) / 32768.0
            playback_rate = _get_playback_rate(sd, speaker_device, sample_rate)
            if playback_rate != sample_rate:
                audio = _resample_audio(audio, sample_rate, playback_rate)
            audio = _apply_output_gain(audio)
            duration = len(audio) / playback_rate
            peak = float(np.max(np.abs(audio))) if audio.size else 0.0
            log.info("[TTS ] Playing %.1f s of audio at %d Hz on device [%s] "
                     "(piper=%d Hz, peak=%.3f) …",
                     duration, playback_rate, speaker_device, sample_rate, peak)
            if on_playback_start:
                on_playback_start()
            sd.play(audio, samplerate=playback_rate, device=speaker_device)
            sd.wait()
            log.info("[TTS ] Playback complete")
    except Exception as exc:
        log.error("[TTS ] Failed: %s", exc, exc_info=True)


def warmup() -> None:
    """Pre-load the voice model and resolve + verify the speaker device at startup."""
    global _speaker_device
    if AUDIO_SOURCE == "file" and not TTS_OUTPUT_FILE:
        return
    try:
        import sounddevice as sd
        _log_audio_devices(sd)
        _speaker_device = _resolve_output_device(sd)
        if _speaker_device is None:
            log.warning("[TTS ] No USB speaker found; using system default — set SPEAKER_DEVICE=<index or name> to pin a device")
        else:
            log.info("[TTS ] Speaker device ready (device=%s)", _speaker_device)
    except Exception as exc:
        log.warning("[TTS ] Speaker device setup failed: %s", exc)
    try:
        _log_piper_info()
        _ensure_voice()
        log.info("[TTS ] piper model files verified OK")
    except Exception as exc:
        log.warning("[TTS ] Piper warmup failed: %s", exc)


def beep(freq_hz: int = 880, duration_s: float = 0.3) -> None:
    """Play a sine-wave beep to verify the speaker/sounddevice path works independently of piper."""
    if AUDIO_SOURCE == "file" and not TTS_OUTPUT_FILE:
        return
    try:
        import sounddevice as sd
        import numpy as np
        sample_rate = 44100
        t = np.linspace(0, duration_s, int(sample_rate * duration_s), endpoint=False)
        tone = (np.sin(2 * np.pi * freq_hz * t) * 0.4).astype(np.float32)
        tone = _apply_output_gain(tone)
        speaker_device = _speaker_device if _speaker_device is not None else _resolve_output_device(sd)
        log.info("[TTS ] Playing beep (%d Hz, %.1f s) on device [%s] …", freq_hz, duration_s, speaker_device)
        sd.play(tone, samplerate=sample_rate, device=speaker_device)
        sd.wait()
        log.info("[TTS ] Beep complete")
    except Exception as exc:
        log.error("[TTS ] Beep failed: %s", exc, exc_info=True)


def speaker_test() -> None:
    """Play a beep then speak a short phrase to confirm the speaker is working."""
    log.info("[TTS ] Running speaker test …")
    beep()
    speak("Speaker test. Audio output is working.")
