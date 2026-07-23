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

**One command** bootstraps everything on a fresh Snapdragon X box — copy
[`scripts/setup_geniex.ps1`](scripts/setup_geniex.ps1) and
[`scripts/setup_project.ps1`](scripts/setup_project.ps1) into a folder, then:

```powershell
powershell -ExecutionPolicy Bypass -File .\setup_geniex.ps1

when ps1 script doesn't work through permissions
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

This: installs Git + ARM64 Python, creates the `geniex-env` venv and installs
the GenieX SDK, then hands off to `setup_project.ps1` which clones this repo,
installs `hub/requirements.txt`, and runs `hub/server.py`.

- Stop after just the GenieX env: `.\setup_geniex.ps1 -NoProject`
- Pass server flags through: `.\setup_geniex.ps1 -ProjectArgs '--','--verbose','--port','8080'`
- If the repo is already cloned, re-running clones-or-pulls, reinstalls, and
  restarts the server.

## Configuration

Copy `.env.example` to `.env` and fill in your values. `.env` is gitignored and must
never be committed.
