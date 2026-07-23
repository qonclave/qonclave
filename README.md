# Qonclave

**An Open-Source Framework for Privacy-First, Autonomous Distributed Intelligence**

Snapdragon Multiverse Hackathon | August 3–7, 2026

Qonclave is an edge-AI framework for privacy-first autonomous systems: sense local
context on-device, exchange compact events, verify important events with heavier
models on a hub, and take a minimal, privacy-preserving action — without the cloud.

## MVP Demo

Stationary **person detection with hub-side verification**:

1. An Arduino UNO Q watches a scene and detects a person locally.
2. It sends only an event (JSON) plus one selected frame to a Snapdragon X laptop.
3. The laptop verifies the person with a heavier model and raises a local alert.

Stretch: reason over the frame with a vision-language model (Qwen-VL) and label the
person as `known` / `unknown`.

## Layout

```
edge/     # UNO Q: capture, local detection, event sender
hub/      # Snapdragon X laptop: HTTP server, verification, reasoning, alert
shared/   # event schema, sample events
demo/     # runbook, fallback assets
scripts/  # environment setup (e.g. GenieX bootstrap)
```

## Hub environment (Snapdragon X, Windows ARM64)

See [`scripts/setup_geniex.ps1`](scripts/setup_geniex.ps1) — bootstraps ARM64 Python,
Git, and the GenieX SDK for running Qwen-VL models locally.

## Configuration

Copy `.env.example` to `.env` and fill in your values. `.env` is gitignored and must
never be committed.
