"""
test_roundtrip.py — STT → TTS → STT round-trip test.

Pipeline:
  1. Transcribe AUDIO_FILE_PATH with VOSK          → transcript_1
  2. Synthesize transcript_1 with piper-tts        → tts_output.wav
  3. Transcribe tts_output.wav with VOSK again     → transcript_2
  4. Compare transcript_1 vs transcript_2 and report similarity

Run:
    python python/test_roundtrip.py

All config is picked up from .env / environment variables (same as main.py).
"""
from __future__ import annotations

import difflib
import json
import logging
import os
import wave

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("roundtrip")

AUDIO_FILE_PATH = os.environ.get("AUDIO_FILE_PATH", "/app/media/sample.wav")
VOSK_MODEL_PATH = os.environ.get("VOSK_MODEL_PATH", "/app/models/vosk-model-en-us-0.22-lgraph")
SAMPLE_RATE     = int(os.environ.get("SAMPLE_RATE", "16000"))
PIPER_MODEL_DIR = os.environ.get("PIPER_MODEL_DIR", "/app/models/piper")
PIPER_VOICE     = os.environ.get("PIPER_VOICE", "en_US-lessac-medium")

_BLOCK_SIZE = 4000  # int16 frames per VOSK chunk


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resample_pcm(data: bytes, src_rate: int, dst_rate: int) -> bytes:
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


def _load_wav_as_16k_mono(path: str) -> bytes:
    """Read a wav file and return 16-bit mono PCM at SAMPLE_RATE."""
    with wave.open(path, "rb") as wf:
        if wf.getsampwidth() != 2:
            raise ValueError(f"Expected 16-bit PCM, got {wf.getsampwidth() * 8}-bit")
        src_rate  = wf.getframerate()
        n_channels = wf.getnchannels()
        raw = wf.readframes(wf.getnframes())

    # stereo → mono
    if n_channels == 2:
        import array
        stereo = array.array("h", raw)
        raw = array.array("h",
            ((stereo[i] + stereo[i + 1]) // 2 for i in range(0, len(stereo), 2))
        ).tobytes()

    return _resample_pcm(raw, src_rate, SAMPLE_RATE)


def _transcribe(model, pcm: bytes) -> str:
    """Run VOSK over raw 16 kHz mono PCM and return the full transcript."""
    import vosk
    rec = vosk.KaldiRecognizer(model, SAMPLE_RATE)
    parts: list[str] = []
    offset = 0
    chunk_bytes = _BLOCK_SIZE * 2
    while offset < len(pcm):
        chunk = pcm[offset:offset + chunk_bytes]
        offset += chunk_bytes
        if rec.AcceptWaveform(chunk):
            t = json.loads(rec.Result()).get("text", "").strip()
            if t:
                parts.append(t)
    final = json.loads(rec.FinalResult()).get("text", "").strip()
    if final:
        parts.append(final)
    return " ".join(parts).strip()


def _synthesize_to_wav(text: str, out_path: str) -> None:
    """Synthesize text with piper-tts and write a wav to out_path."""
    from audio_output import _get_voice, _get_sample_rate
    voice = _get_voice()
    sample_rate = _get_sample_rate(voice)
    chunks = list(voice.synthesize_stream_raw(text))
    raw_pcm = b"".join(chunks)
    log.info("[TTS ] Synthesized %d bytes (%.1f s)", len(raw_pcm),
             len(raw_pcm) / (sample_rate * 2))
    if not raw_pcm:
        raise RuntimeError("synthesize_stream_raw() returned 0 bytes")
    with wave.open(out_path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(raw_pcm)


def _similarity(a: str, b: str) -> float:
    """Return 0-1 similarity ratio between two strings (word-normalised)."""
    a_words = a.lower().split()
    b_words = b.lower().split()
    return difflib.SequenceMatcher(None, a_words, b_words).ratio()


def _word_diff(a: str, b: str) -> str:
    """Return a compact word-level diff for display."""
    a_words = a.lower().split()
    b_words = b.lower().split()
    matcher = difflib.SequenceMatcher(None, a_words, b_words)
    parts = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            parts.append(" ".join(a_words[i1:i2]))
        elif tag == "replace":
            parts.append(f"[{' '.join(a_words[i1:i2])} → {' '.join(b_words[j1:j2])}]")
        elif tag == "delete":
            parts.append(f"[-{' '.join(a_words[i1:i2])}]")
        elif tag == "insert":
            parts.append(f"[+{' '.join(b_words[j1:j2])}]")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run() -> None:
    log.info("=" * 60)
    log.info("  STT → TTS → STT Round-Trip Test")
    log.info("  input : %s", AUDIO_FILE_PATH)
    log.info("  model : %s", VOSK_MODEL_PATH)
    log.info("  voice : %s", PIPER_VOICE)
    log.info("=" * 60)

    # --- Load VOSK model once (shared for both passes) ---
    import vosk
    vosk.SetLogLevel(-1)
    log.info("[STEP 0] Loading VOSK model …")
    model = vosk.Model(VOSK_MODEL_PATH)
    log.info("[STEP 0] Model ready")

    # --- Step 1: STT on input file ---
    log.info("[STEP 1] STT — transcribing %s …", AUDIO_FILE_PATH)
    pcm1 = _load_wav_as_16k_mono(AUDIO_FILE_PATH)
    transcript_1 = _transcribe(model, pcm1)
    if not transcript_1:
        log.error("[STEP 1] No speech detected in input file — aborting")
        return
    log.info("[STEP 1] Transcript 1: %r", transcript_1)

    # --- Step 2: TTS on transcript_1 ---
    tts_wav = os.path.join(os.path.dirname(AUDIO_FILE_PATH), "tts_roundtrip.wav")
    log.info("[STEP 2] TTS — synthesizing to %s …", tts_wav)
    _synthesize_to_wav(transcript_1, tts_wav)
    log.info("[STEP 2] TTS wav written")

    # --- Step 3: STT on TTS output ---
    log.info("[STEP 3] STT — re-transcribing TTS output …")
    pcm2 = _load_wav_as_16k_mono(tts_wav)
    transcript_2 = _transcribe(model, pcm2)
    if not transcript_2:
        log.warning("[STEP 3] No speech detected in TTS output")
        transcript_2 = ""
    log.info("[STEP 3] Transcript 2: %r", transcript_2)

    # --- Step 4: Compare ---
    score = _similarity(transcript_1, transcript_2)
    passed = score >= 0.8

    print()
    print("=" * 60)
    print("  ROUND-TRIP RESULT")
    print("=" * 60)
    print(f"  Transcript 1 (your audio → VOSK) : {transcript_1!r}")
    print(f"  Transcript 2 (TTS wav   → VOSK)  : {transcript_2!r}")
    print(f"  Similarity score                  : {score:.0%}")
    print(f"  Result                            : {'PASS ✓' if passed else 'FAIL ✗'}")
    if not passed:
        print(f"  Word diff : {_word_diff(transcript_1, transcript_2)}")
    print("=" * 60)
    print(f"  TTS wav saved to: {tts_wav}")
    print("=" * 60)
    print()


if __name__ == "__main__":
    run()
