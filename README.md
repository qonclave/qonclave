# Qonclave

**An Open-Source Framework for Privacy-First, Autonomous Distributed Intelligence**

Qonclave is an edge-AI framework for privacy-first autonomous systems: sense local
context on-device, exchange compact events, verify important events with heavier
models on a hub, and take a minimal, privacy-preserving action — without the cloud.

## Core Features

1. **Distributed AI:** Intelligence is pushed to the edge, creating a resilient mesh network that operates entirely without reliance on centralized cloud servers.
2. **Private Data:** Enforces strict zero-trust air-gapping. Raw sensor data (like video or audio) never leaves the local subnet; only abstracted conclusions or verified events escalate through the network.
3. **Hierarchical Intelligence:** Employs a multi-tier architecture where lightweight Edge nodes handle continuous sensing and triage, while powerful Hubs and Compute nodes execute heavy Vision-Language Models (VLMs) on demand.
4. **Auto Discovery:** Network nodes self-organize dynamically using protocols like mDNS. New devices advertise their capabilities (e.g., "Camera: 1080p", "Compute: NPU") and instantly join the mesh without hardcoded IP configurations.

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
  setup_hub.ps1  # environment bootstrap (GenieX + hub deps + face ID)
  framework/     # reusable: transport, event store, VLM, face ID, SMS, HTTP routes, Policy contract
    face_id/     # face identification (MediaPipe + CavaFace)
  apps/          # use cases built on the framework (today: apps/security/)
  tests/         # GenieX / VLM smoke tests
shared/   # event schema, sample events
demo/     # runbook, fallback assets
```

See `hub/README.md` for the framework/app split and how to add a new use case.

## Hub environment (Snapdragon X, Windows ARM64)

First sync this repo yourself (`git clone https://github.com/jogendar/Qonclave.git`
or `git pull` in an existing checkout). Then, from inside that checkout, **one
command** bootstraps everything else on a fresh Snapdragon X box:

```powershell
powershell -ExecutionPolicy Bypass -File .\hub\setup_hub.ps1

# if the .ps1 is blocked by execution policy:
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

This installs Git + ARM64 Python, creates the `geniex-env` venv, installs the
GenieX SDK and `hub/requirements.txt`, installs face ID into that same venv,
then runs `hub/server.py`. It does **not** clone or pull the repo — that's on
you, first.

> **Windows Long Path requirement** — `hub/requirements.txt` includes packages
> (e.g. `twilio`) with deeply nested install paths that exceed Windows' default
> 260-character limit. Enable long paths once before running `pip install`,
> from an **Administrator** PowerShell:
> ```powershell
> New-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" `
>     -Name "LongPathsEnabled" -Value 1 -PropertyType DWORD -Force
> ```
> No reboot needed. This is a one-time machine setting.

- Stop after installing, without starting the server: `.\hub\setup_hub.ps1 -NoRun`
- By default, the heavy VLM model is pre-loaded into memory immediately upon startup. To launch the server faster and load the model lazily on the first request instead, use: `.\hub\setup_hub.ps1 -NoWarmup`
- Pass server flags through: `.\hub\setup_hub.ps1 -- --verbose --port 8080`
- Re-running is idempotent: existing Python/venv/deps are detected and reused
  or upgraded as needed.

Face ID (`hub/framework/face_id/`) is set up as part of that same run, into the same venv
— `hub/server.py` imports it in-process, so it has to live there. On ARM64 the
NPU model export needs a Qualcomm AI Hub token; pass it up front to keep the
run unattended, otherwise you'll be prompted:

```powershell
.\hub\setup_hub.ps1 -AiHubToken YOUR_TOKEN
```

- Skip face ID entirely: `.\hub\setup_hub.ps1 -SkipFaceId` (the hub still
  runs; face-ID reports `not_enabled`)
- Reuse already-compiled AI Hub jobs instead of recompiling:
  `-MediaPipeFaceJobId jXXXXXXXX -CavaFaceJobId jXXXXXXXX` — see `hub/framework/face_id/README.md`.
  Passing both also skips the `qai-hub-models`/torch install, since NPU
  inference needs neither (at the cost of the CPU embedder fallback).
- Already-installed face ID is detected and skipped, and a face-ID failure warns
  rather than aborting the bootstrap.

## Configuration

Copy `.env.example` to `.env` and fill in your values. `.env` is gitignored and must
never be committed.
