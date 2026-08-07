# Edge Assistant

**Local voice assistant for Arduino UNO Q + Snapdragon X hub.**

The UNO Q handles microphone capture, wake-word detection, command transcription,
and text-to-speech locally. Only the transcribed command text is sent over the
local network to the Snapdragon X hub. The hub runs the assistant endpoint,
conversation history, and Qwen3-4B/GenieX LLM generation, with a template
fallback for testing without a model.

No microphone audio leaves the edge device.

---

## Current Flow

### Mic mode

```text
USB mic
  ↓
VOSK wake-word recognizer
  listens for WAKE_WORD, default: "conclave"
  ↓
Fixed command capture
  records COMMAND_RECORD_SEC seconds, default: 5
  ↓
Offline VOSK transcription
  transcribes buffered command audio after capture completes
  ↓
HubClient
  POST /assistant/query with query + device_id
  ↓
Snapdragon X hub
  Qwen3-4B via GenieX, or a canned template response
  when ASSISTANT_LLM_ENABLED=0 or the LLM is unavailable
  ↓
piper-tts
  synthesizes response locally on UNO Q
  ↓
USB speaker
```

The wake-word phase is the only realtime recognition phase. After the wake word
is detected, the command window is captured first and transcribed afterward. This
keeps command capture reliable on edge hardware because full STT is not running
while the microphone is being drained.

### File mode

`AUDIO_SOURCE=file` reads `AUDIO_FILE_PATH`, converts it to 16 kHz mono PCM if
needed, transcribes the whole file with VOSK, and sends the resulting text through
the same assistant path. File mode does not require a microphone or speaker.

---

## State Machine

```text
IDLE → LISTENING → THINKING → SPEAKING → IDLE
```

| State | Meaning |
|-------|---------|
| `IDLE` | Waiting for the wake word |
| `LISTENING` | Wake word detected; fixed command audio is being recorded |
| `THINKING` | Recording is complete; STT, hub request, and TTS synthesis are running |
| `SPEAKING` | Synthesized audio is being played through the speaker |

The Web UI state badge and the UNO Q onboard LED matrix are updated from the same
state transition path. `IDLE` clears the LED matrix. `LISTENING` shows a static
microphone icon, `THINKING` shows a sketch-side three-dot spinner animation, and
`SPEAKING` shows a static speaker/waves icon.

After `SPEAKING`, the app returns to `IDLE` and starts listening for the wake word
again. While in `THINKING` or `SPEAKING`, the audio thread is not listening for a
new wake word.

---

## Repository Layout

```text
edge_assistant/
├── app.yaml
├── .env.example
├── README.md
├── python/
│   ├── main.py           # App entry point, state machine, Web UI callbacks
│   ├── audio_input.py    # VOSK wake word + fixed command capture + STT
│   ├── audio_output.py   # piper-tts synthesis + USB speaker playback
│   ├── led_matrix.py     # UNO Q LED matrix state icons via Bridge
│   ├── hub_client.py     # Hub HTTP client + health monitor
│   └── requirements.txt
├── sketch/
│   ├── sketch.ino        # Arduino LED matrix Bridge callback
│   └── sketch.yaml
├── assets/
│   ├── index.html
│   ├── app.js
│   ├── style.css
│   └── libs/
└── media/
    └── sample.wav
```

Hub-side assistant files:

```text
hub/apps/assistant/
├── routes.py      # POST /assistant/query + GET /user/assistant_activity
├── history.py     # In-memory per-device conversation history
├── activity.py    # In-memory feed of recent exchanges, for the dashboard
└── tools.py       # Weather stub tool dispatch (currently unused by routes.py)
```

`hub/framework/server.py` registers the assistant blueprint.

---

## Audio Input Design

### Wake-word detection

`audio_input.py` opens the configured microphone with `sounddevice.RawInputStream`.
It captures blocks of `CAPTURE_BLOCK_SEC` seconds and feeds them to a VOSK
`KaldiRecognizer` built with a small grammar:

```json
["conclave", "[unk]"]
```

The default wake word is configurable with `WAKE_WORD`. Once the wake word appears
in either a partial or final VOSK result, wake-word detection stops for that cycle.

### Fixed command capture

After wake detection, the app records exactly `COMMAND_RECORD_SEC` seconds of
command audio. The default is 5 seconds. Command capture reuses the already-open
microphone stream from wake-word detection, avoiding USB/PortAudio stream reopen
latency between the wake word and the command window.

The mic stream is drained in smaller `COMMAND_BLOCK_SEC` reads to keep PortAudio
moving in real time. Wake-word detection batches those smaller reads into
`CAPTURE_BLOCK_SEC` chunks before calling VOSK, so KWS overhead stays low while
command capture still uses low-latency reads.

During this command window, the code only drains the microphone and stores PCM in
memory. It does not run full VOSK STT live. When capture completes, the buffered
audio is converted to VOSK's target format and transcribed offline.

### Resampling and gain

VOSK expects 16 kHz mono PCM. If the microphone cannot open at `SAMPLE_RATE`, the
app captures at the device rate and resamples to `SAMPLE_RATE` before sending
audio to VOSK.

For the current working USB PnP microphone, the default configuration is:

```text
MIC_DEVICE=USB PnP Sound Device
MIC_CHANNEL=1
MIC_INPUT_GAIN=0.5
SAMPLE_RATE=16000
CAPTURE_BLOCK_SEC=0.25
COMMAND_RECORD_SEC=5
```

`MIC_INPUT_GAIN=0.5` attenuates the microphone before VOSK to avoid clipping.

---

## Text-to-Speech Design

`audio_output.py` uses the piper CLI to synthesize raw PCM from the assistant
response. It then plays the audio through `sounddevice`.

The piper voice files are downloaded on first use into:

```text
PIPER_MODEL_DIR/PIPER_VOICE/
```

The default voice is `en_US-lessac-medium`.

If the selected speaker cannot play piper's native sample rate, the app resamples
TTS playback to the speaker's supported/default rate. Set `TTS_PLAYBACK_RATE` only
when you need to force a specific output rate.

Set `TTS_VOLUME_GAIN` above `1.0` to boost quiet USB speakers that do not expose
hardware volume control. Start with `1.5`; use `2.0` if needed. Higher values can
clip peaks and make speech sound distorted.

Set `TTS_OUTPUT_FILE` to save synthesized speech to a WAV file instead of playing
it through the speaker.

---

## Hub API

The edge app posts transcribed command text to:

```http
POST /assistant/query
```

Request body:

```json
{
  "query": "what is the weather today",
  "device_id": "unoq-01"
}
```

Response body:

```json
{
  "response": "The capital of France is Paris.",
  "tool_used": "llm"
}
```

The hub endpoint:

1. Reads `query` and `device_id` from the request body.
2. Generates a reply with Qwen3-4B (GenieX), using the query alone as the
   prompt plus a fixed system prompt, capped at 96 new tokens.
3. Trims the reply to at most 2 sentences (see below).
4. Stores the user and assistant turns in memory.
5. Returns the response text and `tool_used`.

### Keeping spoken replies short

The reply is read aloud, so it is bounded twice:

- `_MAX_NEW_TOKENS = 96` in `routes.py` caps generation, which also bounds
  worst-case latency against the edge's `HUB_TIMEOUT_SEC`.
- `_shorten()` then collapses whitespace, drops a sentence that repeats the one
  before it, keeps at most `_MAX_SENTENCES = 2`, and discards the unfinished
  trailing fragment the token cap leaves behind.

The second step matters because a cap on its own cuts the reply mid-word, and
piper will read that fragment out loud. The hub log shows both figures so you
can tune them:

```text
LLM reply in 1.548s (65 chars spoken, 163 generated)
```

A large gap between the two means the model is rambling — usually because the
transcript it received was garbled. The system prompt tells it to ask for a
repeat rather than guess, and the dashboard shows the transcript the hub
actually received.

`tool_used` tells you which path answered:

| Value | Meaning |
|-------|---------|
| `llm` | Generated by Qwen3-4B |
| `template_*` | Canned reply — the LLM is switched off, unavailable, or failed |

Conversation history is recorded per `device_id` but is **not** fed back into
the prompt; each query is single-turn. History is in-memory only and is lost
when the hub restarts.

### Template fallback

Setting `ASSISTANT_LLM_ENABLED=0` on the hub makes every query return a
deterministic canned reply keyed off keywords in the query text
(`template_weather`, `template_joke`, `template_time`, `template_lights`,
`template_greeting`, else `template_default`). This is the fastest way to
verify the edge STT → hub → edge TTS path with no model in the loop.

The same templates are served automatically when the LLM is enabled but cannot
answer — non-ARM64 host, GenieX missing, generation error, or empty output. The
hub logs a warning in that case and the edge still receives speakable text, so
watch `tool_used` to tell a real LLM reply from a silent fallback.

Note that template keywords are matched as substrings, so `"what is this
thing"` matches `hi` and returns `template_greeting`.

### Watching from the hub dashboard

The hub dashboard at `http://<HUB_IP>:8000/user/dashboard` has an **Edge voice
assistant** card showing, live:

- a status pill — the loaded model name, `LLM unavailable` (hover for the load
  error), or `LLM off — templates` when `ASSISTANT_LLM_ENABLED=0`
- the last 12 exchanges: device ID, transcribed query, hub reply, whether the
  LLM or a template answered, total round-trip and generation time
- for a template fallback, the reason the LLM could not answer

This is the fastest way to confirm what the board actually heard — a wrong
answer is usually a wrong transcript, and the card shows the transcript the hub
received. It polls `GET /user/assistant_activity` every 1.5 s. The feed is
in-memory, capped at 30 entries, and cleared when the hub restarts.

```bash
curl "http://<HUB_IP>:8000/user/assistant_activity?limit=5"
```

---

## Setup

### Hub

Start the normal hub server. The assistant blueprint is registered by the hub
server automatically.

Run the hub inside `hub/geniex-env` — GenieX is only installed there, and
without it the assistant silently serves template replies:

```powershell
hub\geniex-env\Scripts\python.exe hub\server.py
```

With `ASSISTANT_LLM_ENABLED=1` (the default) the hub loads Qwen3-4B during
startup rather than on the first query, so it takes noticeably longer to become
ready. Watch for `LLM status after warmup: {'available': True, ...}`; if that
says `False`, every query will fall back to a template.

Smoke test:

```bash
curl -X POST http://<HUB_IP>:8000/assistant/query \
  -H "Content-Type: application/json" \
  -d '{"query": "what is the capital of France", "device_id": "test-01"}'
```

Automated tests (no GenieX or model needed, runs on any host):

```bash
python hub/tests/test_assistant_endpoint.py
```

### Edge mic mode

Connect:

- USB microphone
- USB speaker
- Snapdragon X hub reachable on the local network

Set at minimum:

```text
AUDIO_SOURCE=mic
HUB_IP=<your hub IP>
```

Recommended current mic settings:

```text
MIC_DEVICE=USB PnP Sound Device
MIC_CHANNEL=1
MIC_INPUT_GAIN=0.5
COMMAND_RECORD_SEC=5
CAPTURE_BLOCK_SEC=0.25
```

Run the app, then:

1. Open `<board-name>.local:7000` for the Web UI.
2. Say **"Conclave"**.
3. When the UI shows `LISTENING`, speak a command that fits within `COMMAND_RECORD_SEC`.
4. Wait while the UI shows `THINKING`; the command is transcribed, sent to the hub, and synthesized.
5. Listen when the UI shows `SPEAKING`; audio is being played through the speaker.
6. The app returns to wake-word listening.

### Edge file mode

Set:

```text
AUDIO_SOURCE=file
AUDIO_FILE_PATH=/app/media/sample.wav
HUB_IP=<your hub IP>
```

Run the app. It transcribes the file once, sends the text to the hub, and shows
the response in the terminal and Web UI. TTS playback is skipped in file mode
unless `TTS_OUTPUT_FILE` is set.

### Edge test mode

Set `EDGE_TEST_MODE=1` to skip the hub. The app echoes the transcription as:

```text
I heard: <transcribed text>
```

This is useful for verifying the local STT/TTS path without hub connectivity.

---

## Web UI

Open `<board-name>.local:7000`.

| Element | Description |
|---------|-------------|
| Hub status badge | Shows whether the hub health check is online |
| State badge | Shows `IDLE`, `LISTENING`, `THINKING`, or `SPEAKING` |
| You bubble | Last transcribed command |
| Conclave bubble | Last assistant response and optional tool badge |

The UNO Q LED matrix mirrors the same state sequence as the Web UI. Python sends
state names over Bridge with `set_led_state`. The sketch renders the static icons
and runs the `THINKING` three-dot spinner locally, so Python does not need to send
animation frames while STT, hub, or TTS synthesis is running. LED Bridge calls run
on a background thread so state indicators do not block wake-word handling or
command audio capture. The sketch also keeps `set_custom_led_array` as a 96-bit
bitmap fallback.

---

## Environment Variables

### Edge

| Variable | Default | Meaning |
|----------|---------|---------|
| `EDGE_TEST_MODE` | `0` | Set to `1` to skip hub calls and echo the transcription |
| `AUDIO_SOURCE` | `mic` | `mic` for live microphone; `file` for WAV input |
| `AUDIO_FILE_PATH` | `/app/media/sample.wav` | WAV file path used in file mode |
| `DEVICE_ID` | `unoq-01` | Device ID sent to the hub with every query |
| `HUB_IP` | `192.168.18.62` | Static hub IP when discovery is disabled or fails |
| `HUB_PORT` | `8000` | Hub HTTP port |
| `HUB_TIMEOUT_SEC` | `30` | Hub query timeout. Raise it if LLM generation on the hub is slower than this |
| `HUB_DISCOVERY_ENABLED` | `0` | Set to `1` to try mDNS before static IP |
| `HUB_MDNS_NAME` | `qonclave-hub.local` | mDNS hostname used when discovery is enabled |
| `WAKE_WORD` | `conclave` | Wake word recognized by VOSK KWS |
| `COMMAND_RECORD_SEC` | `5` | Seconds of command audio captured after wake word |
| `CAPTURE_BLOCK_SEC` | `0.25` | Wake-word processing chunk duration; lower values drain the mic more often |
| `COMMAND_BLOCK_SEC` | `0.25` | Command-capture read duration; smaller values reduce PortAudio overflows |
| `SAMPLE_RATE` | `16000` | Target sample rate for VOSK input |
| `MIC_DEVICE` | `USB PnP Sound Device` | Mic device index or name substring |
| `MIC_CAPTURE_RATE` | unset | Optional forced mic capture rate |
| `MIC_CHANNEL` | `1` | 1-based input channel, or `all`/`mix` to combine channels |
| `MIC_INPUT_GAIN` | `0.5` | Software gain applied before VOSK |
| `VOSK_MODEL_PATH` | `/app/models/vosk-model-en-us-0.22-lgraph` | VOSK model directory |
| `SPEAKER_DEVICE` | unset | Speaker device index or name substring |
| `PIPER_MODEL_DIR` | `/app/models/piper` | piper voice model root |
| `PIPER_VOICE` | `en_US-lessac-medium` | piper voice name |
| `PIPER_TIMEOUT_SEC` | `60` | Max seconds allowed for piper synthesis |
| `TTS_VOLUME_GAIN` | `1.0` | Software playback gain for quiet speakers |
| `TTS_PLAYBACK_RATE` | unset | Optional forced playback sample rate |
| `TTS_OUTPUT_FILE` | unset | Save synthesized speech to WAV instead of playing it |

### Hub

| Variable | Default | Meaning |
|----------|---------|---------|
| `ASSISTANT_LLM_ENABLED` | `1` | Set to `0` to serve canned template replies instead of generating with Qwen3-4B. Read once at hub startup, so changing it needs a hub restart |
| `QONCLAVE_WARMUP` | unset | `1` also warms the VLM and face-ID models. The assistant LLM is warmed at startup regardless whenever `ASSISTANT_LLM_ENABLED=1` |

---

## Models

Models download automatically on first use if missing.

| Model | Default path | Purpose |
|-------|--------------|---------|
| `vosk-model-en-us-0.22-lgraph` | `VOSK_MODEL_PATH` | Wake-word KWS and command STT |
| `en_US-lessac-medium` | `PIPER_MODEL_DIR/PIPER_VOICE` | Local TTS voice |

---

## Tuning

| Goal | Setting |
|------|---------|
| Longer spoken commands | Increase `COMMAND_RECORD_SEC` |
| Less wake-listen overhead | Increase `CAPTURE_BLOCK_SEC` only if no overflows occur |
| Faster wake response / fewer wake overflows | Decrease `CAPTURE_BLOCK_SEC` |
| Reduce mic clipping | Lower `MIC_INPUT_GAIN` |
| Select a different mic | Set `MIC_DEVICE` to an index or name substring |
| Select a different speaker | Set `SPEAKER_DEVICE` to an index or name substring |

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| Wake word never triggers | Wrong mic or channel | Check startup device list; set `MIC_DEVICE` and `MIC_CHANNEL` |
| Wake word is slow | Large capture blocks | Lower `CAPTURE_BLOCK_SEC` |
| Wake-listen overflow summary appears | UNO Q is not draining or processing KWS audio fast enough | Lower `CAPTURE_BLOCK_SEC`; use a mic/capture rate that reduces resampling if available |
| Command misses the end | Command window too short | Increase `COMMAND_RECORD_SEC` |
| STT is distorted | Mic input is clipping | Lower `MIC_INPUT_GAIN` |
| Hub returns 404 | Hub server did not register the assistant blueprint | Restart the hub |
| Response is a `template_*` reply when you expected the LLM | LLM disabled, or unavailable/failed on the hub | Check the hub log for `Assistant  :` at startup and any `falling back to template` warning; confirm the hub runs inside `hub/geniex-env` and that `ASSISTANT_LLM_ENABLED` is not `0` |
| Hub query times out on the edge | Generation is slower than `HUB_TIMEOUT_SEC` | Raise `HUB_TIMEOUT_SEC`; check the `LLM reply in Ns` line in the hub log for the real latency |
| TTS is silent | Wrong speaker or playback rate | Set `SPEAKER_DEVICE`; optionally set `TTS_PLAYBACK_RATE` |
| TTS is too quiet | USB speaker has no hardware volume | Increase `TTS_VOLUME_GAIN` gradually, for example `1.5` then `2.0` |
| Model download fails | No network from device | Manually place models at configured paths |
