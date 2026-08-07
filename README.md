# Qonclave

[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Framework CI](https://github.com/qonclave/qonclave/actions/workflows/framework.yml/badge.svg)](https://github.com/qonclave/qonclave/actions/workflows/framework.yml)
[![Hub CI](https://github.com/qonclave/qonclave/actions/workflows/hub.yml/badge.svg)](https://github.com/qonclave/qonclave/actions/workflows/hub.yml)
[![Edge CI](https://github.com/qonclave/qonclave/actions/workflows/edge.yml/badge.svg)](https://github.com/qonclave/qonclave/actions/workflows/edge.yml)

**An Open-Source Framework for Privacy-First, Autonomous Distributed Intelligence**

Qonclave is an edge-AI framework for privacy-first autonomous systems: sense local
context on-device, exchange compact events, verify important events with heavier
models on a hub, and take a minimal, privacy-preserving action — without the cloud.

## Core Features

1. **Distributed AI:** Intelligence is pushed to the edge, creating a resilient mesh network that operates entirely without reliance on centralized cloud servers.
2. **Private Data:** Designed around zero-trust air-gapping — raw sensor data (like video or audio) is never sent to the cloud; only abstracted conclusions or verified events escalate through the network. See [`framework/docs/SECURITY.md`](framework/docs/SECURITY.md#6-implementation-status-enforced-vs-designed) for what's enforced today versus what's designed but not yet built.
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
framework/       # the qonclave SDK: spec (normative), Python + C bindings, docs, conformance
  spec/v1/       # wire schemas, profiles, CBOR encoding, AsyncAPI
  sdk/python/    # installable package `qonclave`, all four roles
  sdk/c/         # constrained binding (edge devices, C99)
  docs/          # architecture, security, communication, placement, profiles
  conformance/   # language-neutral test fixtures

examples/        # reference implementations (consume framework via `pip install qonclave`)
  hub/           # Snapdragon X laptop: HTTP server, verification, reasoning, alert
    setup_hub.ps1  # environment bootstrap (GenieX + hub deps + face ID)
    framework/     # (re-export shims over qonclave.* modules)
    apps/          # use cases built on the framework (today: apps/security/)
    tests/         # GenieX / VLM smoke tests
  edge/          # UNO Q: capture, local detection, event sender
    arduino_uno_q_00/
  shared/        # event schema, sample events
  demo/          # runbook, fallback assets

LICENSE          # Apache-2.0 (governs everything except examples/)
examples/LICENSE # 0BSD (copy reference code freely)
```

See `examples/hub/README.md` for the framework/app split and how to add a new use case.

## Hub environment (Snapdragon X, Windows ARM64)

First sync this repo yourself (`git clone https://github.com/qonclave/qonclave.git`
or `git pull` in an existing checkout). Then, from inside that checkout, **one
command** bootstraps everything else on a fresh Snapdragon X box:

```powershell
powershell -ExecutionPolicy Bypass -File .\examples\hub\setup_hub.ps1

# if the .ps1 is blocked by execution policy:
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

This installs Git + ARM64 Python, creates the `geniex-env` venv, installs the
GenieX SDK and `examples/hub/requirements.txt`, installs face ID into that same venv,
then runs `examples/hub/server.py`. It does **not** clone or pull the repo — that's on
you, first.

> **Windows Long Path requirement** — `examples/hub/requirements.txt` includes packages
> (e.g. `twilio`) with deeply nested install paths that exceed Windows' default
> 260-character limit. Enable long paths once before running `pip install`,
> from an **Administrator** PowerShell:
> ```powershell
> New-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" `
>     -Name "LongPathsEnabled" -Value 1 -PropertyType DWORD -Force
> ```
> No reboot needed. This is a one-time machine setting.

- Stop after installing, without starting the server: `.\examples\hub\setup_hub.ps1 -NoRun`
- By default, the heavy VLM model is pre-loaded into memory immediately upon startup. To launch the server faster and load the model lazily on the first request instead, use: `.\examples\hub\setup_hub.ps1 -NoWarmup`
- Pass server flags through: `.\examples\hub\setup_hub.ps1 -- --verbose --port 8080`
- Re-running is idempotent: existing Python/venv/deps are detected and reused
  or upgraded as needed.

Face ID (`examples/hub/framework/face_id/`) is set up as part of that same run, into the same venv
— `examples/hub/server.py` imports it in-process, so it has to live there. On ARM64 the
NPU model export needs a Qualcomm AI Hub token; pass it up front to keep the
run unattended, otherwise you'll be prompted:

```powershell
.\examples\hub\setup_hub.ps1 -AiHubToken YOUR_TOKEN
```

- Skip face ID entirely: `.\examples\hub\setup_hub.ps1 -SkipFaceId` (the hub still
  runs; face-ID reports `not_enabled`)
- Reuse already-compiled AI Hub jobs instead of recompiling:
  `-MediaPipeFaceJobId jXXXXXXXX -CavaFaceJobId jXXXXXXXX` — see `examples/hub/framework/face_id/README.md`.
  Passing both also skips the `qai-hub-models`/torch install, since NPU
  inference needs neither (at the cost of the CPU embedder fallback).
- Already-installed face ID is detected and skipped, and a face-ID failure warns
  rather than aborting the bootstrap.

## Configuration

Copy `.env.example` to `.env` and fill in your values. `.env` is gitignored and must
never be committed.

## Third-Party Models

Model weights are downloaded separately at setup/runtime and are **not** covered by
this repository's license (Apache 2.0 for `framework/`, 0BSD for `examples/`) — each
carries its own license from its publisher:

- **Qwen2.5-VL-7B-Instruct** and **Qwen3-4B** (Qwen license, Alibaba Cloud) —
  vision-language reasoning and text-only LLM reasoning, via Qualcomm AI Hub's
  `ai-hub-models` packaging (`examples/hub/framework/vlm.py`, `llm.py`)
- **CavaFace** and **MediaPipe** face models — face detection/embedding
  (`examples/hub/framework/face_id/`)
- **Vosk** — offline speech-to-text (`examples/edge/arduino_uno_q_00/edge_assistant/`)
- **Piper** (via `qai_hub_models`' `pipertts_en`/`pipertts_de`/`pipertts_it`) — offline
  text-to-speech (`edge_assistant/`, `examples/hub/apps/assistant/`)

Review each project's own license before redistributing model weights themselves —
this repository only ships code that downloads and runs them.
