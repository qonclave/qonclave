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
  framework/   # reusable: transport, event store, VLM, HTTP routes, Policy contract
  apps/        # use cases built on the framework (today: apps/security/)
shared/   # event schema, sample events
demo/     # runbook, fallback assets
scripts/  # environment setup (e.g. GenieX bootstrap)
```

See `hub/README.md` for the framework/app split and how to add a new use case.

## Hub environment (Snapdragon X, Windows ARM64)

First sync this repo yourself (`git clone https://github.com/jogendar/Qonclave.git`
or `git pull` in an existing checkout). Then, from inside that checkout, **one
command** bootstraps everything else on a fresh Snapdragon X box:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup_geniex.ps1

# if the .ps1 is blocked by execution policy:
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

This installs Git + ARM64 Python, creates the `geniex-env` venv, installs the
GenieX SDK and `hub/requirements.txt`, then runs `hub/server.py`. It does
**not** clone or pull the repo — that's on you, first.

- Stop after installing, without starting the server: `.\scripts\setup_geniex.ps1 -NoRun`
- By default, the heavy VLM model is pre-loaded into memory immediately upon startup. To launch the server faster and load the model lazily on the first request instead, use: `.\scripts\setup_geniex.ps1 -NoWarmup`
- Pass server flags through: `.\scripts\setup_geniex.ps1 -- --verbose --port 8080`
- Re-running is idempotent: existing Python/venv/deps are detected and reused
  or upgraded as needed.

## Configuration

Copy `.env.example` to `.env` and fill in your values. `.env` is gitignored and must
never be committed.
