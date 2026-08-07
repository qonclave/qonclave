"""
audio_input.py — VOSK-based wake word detection + full STT.

Two modes (AUDIO_SOURCE env var):
  mic  — live USB microphone via sounddevice (default)
  file — reads AUDIO_FILE_PATH .wav once through the VOSK pipeline (no loop)

Pipeline:
  1. KWS phase: stream audio through a keyword-only recognizer;
     trigger when the wake word is detected.
  2. Command phase: after wake word, capture a fixed short command window,
     then transcribe the buffered audio offline.

Public API:
    ai = AudioInput(on_wake=cb, on_thinking=cb, on_utterance=cb)
    ai.run_forever()   # blocks; call from a daemon thread
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import wave

log = logging.getLogger(__name__)

_HW_CARD_RE = re.compile(r'\(hw:(\d+),(\d+)\)')

AUDIO_SOURCE = os.environ.get("AUDIO_SOURCE", "mic").lower()
AUDIO_FILE_PATH = os.environ.get("AUDIO_FILE_PATH", "/app/media/sample.wav")
WAKE_WORD = os.environ.get("WAKE_WORD", "conclave").lower()
SAMPLE_RATE = int(os.environ.get("SAMPLE_RATE", "16000"))
VOSK_MODEL_PATH = os.environ.get(
    "VOSK_MODEL_PATH",
    "/app/models/vosk-model-en-us-0.22-lgraph",
)
MIC_DEVICE = os.environ.get("MIC_DEVICE", "USB PnP Sound Device").strip()
MIC_CAPTURE_RATE = os.environ.get("MIC_CAPTURE_RATE", "").strip()
MIC_CHANNEL = os.environ.get("MIC_CHANNEL", "1").strip().lower()
MIC_INPUT_GAIN = float(os.environ.get("MIC_INPUT_GAIN", "0.5"))
COMMAND_RECORD_SEC = float(os.environ.get("COMMAND_RECORD_SEC", "5"))
CAPTURE_BLOCK_SEC = float(os.environ.get("CAPTURE_BLOCK_SEC", "0.25"))
COMMAND_BLOCK_SEC = float(os.environ.get("COMMAND_BLOCK_SEC", "0.25"))
_BLOCK_SIZE = 4000  # frames per VOSK chunk (~250 ms at 16 kHz)
_BLOCK_DURATION_SEC = CAPTURE_BLOCK_SEC


def _log_audio_devices(sd) -> None:
    """Log all audio devices so the user can confirm the right mic is picked."""
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


def _resolve_input_device(sd) -> "int | None":
    """
    Return the sounddevice device index to use for microphone input.

    Resolution order:
      1. MIC_DEVICE numeric  → use that index directly
      2. MIC_DEVICE string   → first device whose name contains it (case-insensitive)
      3. empty               → best USB mic via _find_best_usb_device()
      4. fallback            → None (sounddevice picks the system default)
    """
    channel_key = "max_input_channels"
    if MIC_DEVICE:
        if MIC_DEVICE.lstrip("-").isdigit():
            idx = int(MIC_DEVICE)
            try:
                name = sd.query_devices(idx)["name"]
            except Exception:
                name = "<unknown>"
            log.info("[MIC ] MIC_DEVICE=%r → pinned to device [%d] %s", MIC_DEVICE, idx, name)
            return idx
        devices = sd.query_devices()
        for i, dev in enumerate(devices):
            if dev[channel_key] > 0 and MIC_DEVICE.lower() in dev["name"].lower():
                log.info("[MIC ] MIC_DEVICE=%r → name-matched device [%d] %s", MIC_DEVICE, i, dev["name"])
                return i
        log.warning("[MIC ] MIC_DEVICE=%r matched no input device; falling back to USB auto-detect", MIC_DEVICE)

    idx, name = _find_best_usb_device(sd, want_input=True)
    if idx is not None:
        log.info("[MIC ] Auto-selected USB input device [%d] %s", idx, name)
        return idx

    # Last resort: sounddevice system default
    default_idx = sd.default.device[0]
    try:
        default_name = sd.query_devices(default_idx)["name"]
    except Exception:
        default_name = "<unknown>"
    log.info("[MIC ] No USB input device found; falling back to system default [%d] %s", default_idx, default_name)
    return None


def _find_best_usb_device(sd, want_input: bool) -> "tuple[int | None, str]":
    """
    Find the best sounddevice index for a USB mic (want_input=True) or speaker.

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

    # Map card number → True if that card has BOTH input and output raw hw: channels
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


def _get_capture_rate(sd, device) -> int:
    """Return the sample rate to use when opening the capture device.

    Prefer opening the mic directly at VOSK's target rate so no hidden Python
    resampling touches the samples. If a device cannot do SAMPLE_RATE, fall back
    to its advertised native rate and resample explicitly in _resample_pcm().
    Set MIC_CAPTURE_RATE to force a specific capture rate while debugging.
    """
    try:
        if MIC_CAPTURE_RATE:
            forced_rate = int(MIC_CAPTURE_RATE)
            log.info("[MIC ] MIC_CAPTURE_RATE=%d Hz — forcing capture rate", forced_rate)
            return forced_rate

        info = sd.query_devices(device)
        native = int(info.get("default_samplerate", SAMPLE_RATE))

        try:
            sd.check_input_settings(device=device, channels=1, samplerate=SAMPLE_RATE, dtype="int16")
            log.info("[MIC ] Opening capture at %d Hz for VOSK (device default is %d Hz)",
                     SAMPLE_RATE, native)
            return SAMPLE_RATE
        except Exception as exc:
            log.warning("[MIC ] Device cannot open at %d Hz (%s); using %d Hz and resampling to %d Hz",
                        SAMPLE_RATE, exc, native, SAMPLE_RATE)
            return native
    except Exception as exc:
        log.warning("[MIC ] Could not query device sample rate: %s — using %d Hz", exc, SAMPLE_RATE)
        return SAMPLE_RATE


def _block_frames(capture_rate: int) -> int:
    """Return capture frames per chunk while preserving the target chunk duration."""
    return max(1, int(round(capture_rate * _BLOCK_DURATION_SEC)))


def _command_block_frames(capture_rate: int) -> int:
    """Return smaller read chunks for fixed command capture."""
    return max(1, int(round(capture_rate * COMMAND_BLOCK_SEC)))


def _frames_in_buffer(data: bytes, channels: int) -> int:
    """Return the number of interleaved int16 audio frames in a raw buffer."""
    return len(data) // (2 * max(1, channels))


def _resample_pcm(data: bytes, src_rate: int, dst_rate: int) -> bytes:
    """Resample 16-bit mono PCM from src_rate to dst_rate using scipy (high quality)."""
    if src_rate == dst_rate:
        return data
    try:
        import numpy as np
        from math import gcd
        from scipy.signal import resample_poly
        samples = np.frombuffer(data, dtype=np.int16).astype(np.float32)
        g = gcd(dst_rate, src_rate)
        resampled = resample_poly(samples, dst_rate // g, src_rate // g)
        return np.clip(resampled, -32768, 32767).astype(np.int16).tobytes()
    except ImportError:
        import array
        samples_in = array.array("h", data)
        n_out = int(len(samples_in) * dst_rate / src_rate)
        out = array.array("h")
        for i in range(n_out):
            out.append(samples_in[min(int(i * src_rate / dst_rate), len(samples_in) - 1)])
        return out.tobytes()


def _apply_gain(data: bytes) -> bytes:
    """Apply software gain to signed 16-bit mono PCM."""
    if MIC_INPUT_GAIN == 1.0 or not data:
        return data
    try:
        import numpy as np
        samples = np.frombuffer(data, dtype=np.int16).astype(np.float32) * MIC_INPUT_GAIN
        return np.clip(samples, -32768, 32767).astype(np.int16).tobytes()
    except Exception:
        import audioop
        return audioop.mul(data, 2, MIC_INPUT_GAIN)


def _extract_mono(data: bytes, channels: int, channel) -> bytes:
    """Convert interleaved int16 input to mono PCM using selected channel/all channels."""
    if channels <= 1:
        return data
    try:
        import numpy as np
        samples = np.frombuffer(data, dtype=np.int16)
        frame_count = len(samples) // channels
        if frame_count <= 0:
            return b""
        frames = samples[:frame_count * channels].reshape(frame_count, channels)
        if channel == "all":
            mono = frames.astype(np.int32).mean(axis=1)
        else:
            mono = frames[:, int(channel)]
        return np.clip(mono, -32768, 32767).astype(np.int16).tobytes()
    except Exception:
        import array
        samples = array.array("h", data)
        frame_count = len(samples) // channels
        out = array.array("h")
        if channel == "all":
            for frame in range(frame_count):
                start = frame * channels
                out.append(sum(samples[start:start + channels]) // channels)
        else:
            idx = int(channel)
            for frame in range(frame_count):
                out.append(samples[frame * channels + idx])
        return out.tobytes()


def _prepare_pcm(data: bytes, capture_rate: int, capture_channels: int, selected_channel) -> bytes:
    """Return mono int16 PCM at SAMPLE_RATE ready for VOSK."""
    pcm = _extract_mono(data, capture_channels, selected_channel)
    pcm = _apply_gain(pcm)
    if capture_rate != SAMPLE_RATE:
        pcm = _resample_pcm(pcm, capture_rate, SAMPLE_RATE)
    return pcm


def _device_max_input_channels(sd, device) -> int:
    try:
        return max(1, int(sd.query_devices(device).get("max_input_channels", 1)))
    except Exception:
        return 1


def _channel_label(channel) -> str:
    if channel == "all":
        return "all/mix"
    return str(int(channel) + 1)


def _resolve_input_channels(sd, device, capture_rate: int) -> "tuple[int, int | str]":
    """Return (channels_to_open, selected_channel) for RawInputStream capture."""
    max_channels = _device_max_input_channels(sd, device)
    if MIC_CHANNEL in ("all", "mix", "mono"):
        log.info("[MIC ] Mixing %d input channel(s) to mono", max_channels)
        return max_channels, "all"

    try:
        selected = max(0, int(MIC_CHANNEL or "1") - 1)
    except ValueError:
        log.warning("[MIC ] Invalid MIC_CHANNEL=%r; using channel 1", MIC_CHANNEL)
        selected = 0

    if selected >= max_channels:
        log.warning("[MIC ] MIC_CHANNEL=%s exceeds max_input_channels=%d; using channel 1",
                    MIC_CHANNEL, max_channels)
        selected = 0
    capture_channels = selected + 1
    log.info("[MIC ] Using input channel %d/%d", selected + 1, max_channels)
    return capture_channels, selected


def _transcribe_pcm(model, vosk, pcm: bytes) -> str:
    """Run VOSK over already-prepared 16 kHz mono PCM."""
    rec = vosk.KaldiRecognizer(model, SAMPLE_RATE)
    parts: list[str] = []
    offset = 0
    chunk_bytes = _block_frames(SAMPLE_RATE) * 2
    while offset < len(pcm):
        chunk = pcm[offset:offset + chunk_bytes]
        offset += chunk_bytes
        if rec.AcceptWaveform(chunk):
            text = json.loads(rec.Result()).get("text", "").strip()
            if text:
                parts.append(text)
    final = json.loads(rec.FinalResult()).get("text", "").strip()
    if final:
        parts.append(final)
    return " ".join(parts).strip()


def _ensure_model() -> None:
    """Download the VOSK model if not already present."""
    if os.path.isdir(VOSK_MODEL_PATH):
        return
    log.info("VOSK model not found at %s — downloading vosk-model-en-us-0.22-lgraph (~128 MB)…", VOSK_MODEL_PATH)
    try:
        import urllib.request
        import zipfile
        url = "https://alphacephei.com/vosk/models/vosk-model-en-us-0.22-lgraph.zip"
        zip_path = VOSK_MODEL_PATH + ".zip"
        os.makedirs(os.path.dirname(VOSK_MODEL_PATH) or ".", exist_ok=True)
        urllib.request.urlretrieve(url, zip_path, reporthook=_download_progress)
        log.info("Extracting VOSK model…")
        with zipfile.ZipFile(zip_path, "r") as z:
            # extract into parent dir; the zip contains a single top-level folder
            parent = os.path.dirname(VOSK_MODEL_PATH)
            z.extractall(parent or ".")
        os.remove(zip_path)
        log.info("VOSK model ready at %s", VOSK_MODEL_PATH)
    except Exception as exc:
        log.error("Failed to download VOSK model: %s", exc)
        raise


def _download_progress(block_num: int, block_size: int, total_size: int) -> None:
    downloaded = block_num * block_size
    if total_size > 0 and block_num % 200 == 0:
        pct = min(100, downloaded * 100 // total_size)
        log.info("  Downloading VOSK model… %d%%", pct)


class AudioInput:
    def __init__(
        self,
        on_wake: "Callable[[], None] | None" = None,
        on_thinking: "Callable[[], None] | None" = None,
        on_idle: "Callable[[], None] | None" = None,
        on_utterance: "Callable[[str], None] | None" = None,
    ) -> None:
        self._on_wake = on_wake
        self._on_thinking = on_thinking
        self._on_idle = on_idle
        self._on_utterance = on_utterance

    def run_forever(self) -> None:
        _ensure_model()
        import vosk
        vosk.SetLogLevel(-1)
        log.info("[VOSK] Loading model from %s …", VOSK_MODEL_PATH)
        model = vosk.Model(VOSK_MODEL_PATH)
        log.info("[VOSK] Model loaded OK  (source=%s, wake_word=%r, command_record=%.1fs)",
                 AUDIO_SOURCE, WAKE_WORD, COMMAND_RECORD_SEC)

        if AUDIO_SOURCE == "file":
            self._run_file(model)
        else:
            self._run_mic(model)

    # ------------------------------------------------------------------
    # File mode
    # ------------------------------------------------------------------
    def _run_file(self, model) -> None:
        import vosk
        if not os.path.exists(AUDIO_FILE_PATH):
            log.error("[FILE] Audio file not found: %s", AUDIO_FILE_PATH)
            return
        log.info("[FILE] Opening audio file: %s", AUDIO_FILE_PATH)
        with wave.open(AUDIO_FILE_PATH, "rb") as wf:
            if wf.getsampwidth() != 2:
                log.error("[FILE] Audio file must be 16-bit PCM; got %d-byte samples", wf.getsampwidth())
                return
            src_rate = wf.getframerate()
            n_channels = wf.getnchannels()
            raw = wf.readframes(wf.getnframes())

        duration = len(raw) / (src_rate * 2 * n_channels)
        log.info("[FILE] Audio: %.1f s, %d Hz, %d ch", duration, src_rate, n_channels)

        # Convert stereo to mono
        if n_channels == 2:
            import array
            stereo = array.array("h", raw)
            mono = array.array("h", ((stereo[i] + stereo[i + 1]) // 2 for i in range(0, len(stereo), 2)))
            raw = mono.tobytes()
            log.info("[FILE] Converted stereo → mono")

        # Resample to 16000 Hz if needed — VOSK only works correctly at 16 kHz
        if src_rate != SAMPLE_RATE:
            log.info("[FILE] Resampling %d Hz → %d Hz …", src_rate, SAMPLE_RATE)
            raw = _resample_pcm(raw, src_rate, SAMPLE_RATE)
            log.info("[FILE] Resample complete")

        log.info("[STT ] Transcribing (wake word skipped in file mode) …")
        if self._on_thinking:
            self._on_thinking()

        stt_rec = vosk.KaldiRecognizer(model, SAMPLE_RATE)
        text_parts: list[str] = []
        offset = 0
        chunk_bytes = _BLOCK_SIZE * 2  # 2 bytes per int16 sample
        while offset < len(raw):
            data = raw[offset:offset + chunk_bytes]
            offset += chunk_bytes
            if stt_rec.AcceptWaveform(data):
                chunk = json.loads(stt_rec.Result()).get("text", "").strip()
                if chunk:
                    log.debug("[STT ] Segment: %r", chunk)
                    text_parts.append(chunk)
            else:
                partial = json.loads(stt_rec.PartialResult()).get("partial", "")
                if partial:
                    log.debug("[STT ] Partial: %r", partial)

        final = json.loads(stt_rec.FinalResult()).get("text", "").strip()
        if final:
            log.debug("[STT ] Final segment: %r", final)
            text_parts.append(final)

        utterance = " ".join(text_parts).strip()
        if utterance:
            log.info("[STT ] Transcription: %r", utterance)
        else:
            log.warning("[STT ] No speech detected — check the audio file contains clear speech")
        if utterance and self._on_utterance:
            self._on_utterance(utterance)
        elif self._on_idle:
            self._on_idle()

    # ------------------------------------------------------------------
    # Mic mode
    # ------------------------------------------------------------------
    def _run_mic(self, model) -> None:
        import sounddevice as sd
        import vosk
        _log_audio_devices(sd)
        mic_device = _resolve_input_device(sd)
        if mic_device is None:
            log.warning("[MIC ] No USB mic found; using system default — set MIC_DEVICE=<index or name> to pin a device")
        capture_rate = _get_capture_rate(sd, mic_device if mic_device is not None else sd.default.device[0])
        capture_channels, selected_channel = _resolve_input_channels(sd, mic_device, capture_rate)
        kws_block_size = _block_frames(capture_rate)
        stream_block_size = _command_block_frames(capture_rate)
        log.info("[MIC ] Capture config: device=%s, rate=%d Hz, channels=%d, selected=%s, "
                 "stream_block=%d frames, kws_block=%d frames",
                 mic_device, capture_rate, capture_channels, _channel_label(selected_channel),
                 stream_block_size, kws_block_size)
        log.info("[MIC ] Listening for wake word %r …", WAKE_WORD)
        cycle = 0
        while True:
            cycle += 1
            log.info("[MIC ] --- Cycle %d: waiting for wake word ---", cycle)
            self._mic_kws_cycle(model, sd, vosk, mic_device, capture_rate, capture_channels, selected_channel)

    def _capture_command_pcm(self, stream, capture_rate: int, capture_channels: int, selected_channel) -> bytes:
        """Capture fixed-length command audio from the already-open mic stream."""
        block_size = _command_block_frames(capture_rate)
        frames_remaining = max(1, int(capture_rate * COMMAND_RECORD_SEC))
        native_chunks: list[bytes] = []
        overflow_count = 0
        log.info("[CMD ] Capturing %.1f s command audio (block=%.2f s) …",
                 COMMAND_RECORD_SEC, COMMAND_BLOCK_SEC)
        t_capture = time.monotonic()
        read_wall_sec = 0.0
        while frames_remaining > 0:
            frames = min(block_size, frames_remaining)
            t_read = time.monotonic()
            data, overflowed = stream.read(frames)
            read_wall_sec += time.monotonic() - t_read
            if overflowed:
                overflow_count += 1
            native_chunks.append(bytes(data))
            frames_remaining -= frames
        capture_wall_sec = time.monotonic() - t_capture

        if self._on_thinking:
            self._on_thinking()
        if overflow_count:
            log.warning("[CMD ] PortAudio reported %d command-capture overflow(s)", overflow_count)

        t_prepare = time.monotonic()
        native_pcm = _extract_mono(b"".join(native_chunks), capture_channels, selected_channel)
        native_pcm = _apply_gain(native_pcm)
        pcm = _resample_pcm(native_pcm, capture_rate, SAMPLE_RATE) if capture_rate != SAMPLE_RATE else native_pcm
        prepare_wall_sec = time.monotonic() - t_prepare
        log.info("[CMD ] Command audio ready: %.1f s at %d Hz "
                 "(capture wall=%.2f s, read=%.2f s, prepare=%.2f s)",
                 len(pcm) / (SAMPLE_RATE * 2), SAMPLE_RATE,
                 capture_wall_sec, read_wall_sec, prepare_wall_sec)
        return pcm

    def _mic_kws_cycle(self, model, sd, vosk, mic_device, capture_rate: int, capture_channels: int, selected_channel) -> None:
        """One wake-word → fixed command-capture → transcription cycle."""
        kws_grammar = json.dumps([WAKE_WORD, "[unk]"])
        kws_rec = vosk.KaldiRecognizer(model, SAMPLE_RATE, kws_grammar)
        chunk_count = 0
        overflow_count = 0
        kws_block_size = _block_frames(capture_rate)
        stream_block_size = _command_block_frames(capture_rate)
        kws_chunks: list[bytes] = []
        kws_frames = 0
        read_wall_sec = 0.0
        prepare_wall_sec = 0.0
        vosk_wall_sec = 0.0
        with sd.RawInputStream(samplerate=capture_rate, blocksize=stream_block_size, dtype="int16",
                               channels=capture_channels, device=mic_device) as stream:
            while True:
                t_read = time.monotonic()
                data, overflowed = stream.read(stream_block_size)
                read_wall_sec += time.monotonic() - t_read
                if overflowed:
                    overflow_count += 1
                kws_chunks.append(bytes(data))
                kws_frames += _frames_in_buffer(data, capture_channels)
                if kws_frames < kws_block_size:
                    continue

                raw_kws = b"".join(kws_chunks)
                kws_chunks.clear()
                kws_frames = 0
                chunk_count += 1
                t_prepare = time.monotonic()
                pcm = _prepare_pcm(raw_kws, capture_rate, capture_channels, selected_channel)
                prepare_wall_sec += time.monotonic() - t_prepare
                t_vosk = time.monotonic()
                if kws_rec.AcceptWaveform(pcm):
                    vosk_wall_sec += time.monotonic() - t_vosk
                    text = json.loads(kws_rec.Result()).get("text", "").lower()
                    if WAKE_WORD in text:
                        log.info("[KWS ] Wake word %r detected (chunk %d)", WAKE_WORD, chunk_count)
                        break
                else:
                    vosk_wall_sec += time.monotonic() - t_vosk
                    partial = json.loads(kws_rec.PartialResult()).get("partial", "").lower()
                    if WAKE_WORD in partial:
                        log.info("[KWS ] Wake word %r detected in partial (chunk %d)", WAKE_WORD, chunk_count)
                        break

            if overflow_count:
                log.warning("[KWS ] PortAudio reported %d wake-listen overflow(s) before detection", overflow_count)
            log.info("[KWS ] Wake timing: chunks=%d, read=%.2f s, prepare=%.2f s, vosk=%.2f s",
                     chunk_count, read_wall_sec, prepare_wall_sec, vosk_wall_sec)
            log.info("[KWS ] Wake word confirmed — recording fixed %.1f s command", COMMAND_RECORD_SEC)
            if self._on_wake:
                self._on_wake()

            pcm = self._capture_command_pcm(stream, capture_rate, capture_channels, selected_channel)
        t_proc = time.monotonic()
        utterance = _transcribe_pcm(model, vosk, pcm)
        proc_ms = (time.monotonic() - t_proc) * 1000
        if utterance:
            log.info("[STT ] Processing took %.0f ms → Transcription: %r", proc_ms, utterance)
        else:
            log.warning("[STT ] No speech detected in fixed command window")
        if utterance and self._on_utterance:
            self._on_utterance(utterance)
        elif self._on_idle:
            self._on_idle()
