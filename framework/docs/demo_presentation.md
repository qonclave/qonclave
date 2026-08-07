
---

## The Problem

Every AI product you use today phones home. Alexa uploads your voice. Smart cameras stream to AWS.

That means **latency, privacy exposure, compliance violations, and total failure when the Cloud drops.**

The answer isn't a better cloud. It's **no cloud at all.**

---

## What Qonclave Is

**Qonclave is a framework** — a reusable runtime that lets any developer build private, distributed AI applications without touching a cloud API. The two demos you're about to see (intruder detection and voice assistant) are applications *built on top of* Qonclave, not Qonclave itself. Swap the Policy file and you have a different application. The mesh, the privacy guarantees, the auto-discovery, the tiered inference — those stay.

Four pillars — baked into the framework, not bolted on:

| Pillar | What It Means |
|---|---|
| **Distributed AI** | Intelligence runs across every node. Edge, Hub, and Compute each carry the AI appropriate to their role — no single point of failure, no central bottleneck. |
| **Private by Architecture** | Raw sensor data never leaves the local network. Only a stripped text alert escapes. Privacy is structural, not a setting — enforced by the data lifecycle, not by policy. |
| **Hierarchical Intelligence** | Three tiers of reasoning: Edge filters 99% of noise with lightweight heuristics, Hub applies your business logic (Policy), Compute runs the heavy VLM. Each tier handles only what it must. |
| **Auto-Discovery** | Nodes announce themselves via mDNS at boot — no hardcoded IPs, no manual config. If a Hub dies, another elects itself and takes over orphaned edges. Zero human intervention. |

---

## Architecture in One Line

> Edge filters noise → Hub enforces policy → Compute runs the model — the mesh self-organizes, and keeps every raw byte local.



## Technical Implementation 

### Resource Utilization & Efficiency

- **Tiered triage eliminates wasted compute:** Edge runs lightweight motion/shape detection (OpenCV, C99, no malloc). The Hub only receives events that passed triage — the VLM is never invoked for empty frames.
- **NPU-accelerated inference — two models, two purposes:** Hexagon NPU runs the vision VLM for intruder detection (~1–2 sec) and Qwen3-4B LLM for voice assistant replies (~1.5 sec). Both on-device, no cloud API.
- **Audio never crosses the network:** VOSK wake-word and STT run entirely on the Arduino UNO Q. Only the transcribed text (~bytes) is sent to the Hub — the assistant uses orders of magnitude less bandwidth than a cloud voice assistant.

## Application Use Cases — Two Live Demos 

### Demo 1 — Security: Intruder Detection 

**Flow:** Arduino UNO Q (camera) → Snapdragon X Hub → VLM verification → MQTT buzzer + SMS

1. **Edge (Distributed AI):** Camera detects motion on-device, sends only the relevant encrypted frame + event JSON — not a raw video stream
2. **Hub (Hierarchical Intelligence):** Policy evaluates: *"Is this a known face or an intruder?"* — delegates to local VLM only if triage passed
3. **Compute:** Hexagon NPU returns a structured JSON verdict in ~1–2 sec, statelessly
4. **Actuation:** Hub fires MQTT command → buzzer triggers on the Arduino
5. **Alert (Private):** Hub sends SMS via Twilio — only a human-readable string, no image ever leaves the LAN

---

### Demo 2 — Edge Voice Assistant: On-Device Private AI 

**Flow:** USB mic → Arduino UNO Q (wake word + STT) → Snapdragon X Hub (Qwen3-4B LLM) → Arduino UNO Q (TTS) → USB speaker

This is the **voice equivalent of private AI** — no Alexa, no Google, no audio ever leaving the room.

1. **Wake word on-device:** VOSK wake-word recognizer listens for *"Conclave"* — entirely on the UNO Q, no network traffic at all during idle
2. **Offline STT on-device:** After wake, records command audio, transcribes it locally with VOSK — only the **text** is sent to the Hub, never the audio
3. **LLM on the Hub:** Snapdragon X Hub runs Qwen3-4B via GenieX, generates a reply capped at 96 tokens for spoken latency
4. **TTS on-device:** piper synthesizes the response locally on the UNO Q, plays through USB speaker
5. **LED matrix feedback:** UNO Q LED matrix mirrors the state machine — microphone icon while listening, three-dot spinner while thinking, speaker icon while talking




