# Qonclave Hub

HTTP server for the Snapdragon laptop hub. Receives an image, runs
vision-language reasoning on it, and returns the result as JSON.

## Design

### Architecture

```mermaid
flowchart TB
    subgraph EdgeDevice["Edge device (Arduino UNO Q)"]
        CAM["Camera + local person detector"]
    end

    subgraph HubProcess["Hub process (this repo, hub/)"]
        direction TB

        subgraph Blueprints["Flask app (server.py)"]
            EDGE["edge_routes.py\n/edge/*"]
            USER["user_routes.py\n/user/*"]
            HEALTH["/health, / (redirect)"]
        end

        STATE["state.py\nshared config, event ring buffer,\nrequest/parsing helpers"]
        VLM["vlm_backend.py\nVLMBackend"]
        DISK["hub/uploads/\nsaved frames"]

        EDGE --> STATE
        USER --> STATE
        STATE --> VLM
        STATE --> DISK
    end

    subgraph Runtime["Conditional runtime"]
        GENIEX["GenieX SDK\n(ARM64 / Snapdragon X only)"]
        QWEN["Qwen2.5-VL-7B-Instruct\nvia qairt / Hexagon NPU"]
        GENIEX --> QWEN
    end

    subgraph Browser["Operator browser"]
        DASH["/user/dashboard"]
        TEVENT["/user/test_event"]
        TREASON["/user/test_reason"]
    end

    CAM -- "POST /edge/event\n(frame + event JSON)" --> EDGE
    VLM -. "lazy import, ARM64 only" .-> GENIEX
    Browser --> USER
    HubProcess -- "hub_verified / alert" --> CAM
```

On non-Snapdragon machines the `geniex` import inside `vlm_backend.py` is
never attempted at module load — only when a request actually needs
reasoning, and only if the host is ARM64. Everything else in the diagram
(routes, event store, uploads, dashboard) runs identically on any OS.

### Request flow: `POST /edge/event`

```mermaid
sequenceDiagram
    participant Edge as Edge device
    participant Route as edge_routes.py
    participant State as state.py
    participant VLM as VLMBackend.verify()
    participant Model as GenieX / Qwen2.5-VL

    Edge->>Route: POST /edge/event (frame + event JSON)
    Route->>State: parse_edge_event()
    Route->>State: save_incoming_image()
    State-->>Route: saved frame path

    Route->>VLM: verify(path)
    alt VLM available (ARM64 + GenieX loaded)
        VLM->>Model: reset(), then generate() with json_mode=True, temperature=0.1
        Model-->>VLM: JSON text - person_present, confidence, description
        VLM-->>Route: available=true, person_present, confidence, alert
    else VLM unavailable (non-ARM64 or geniex missing)
        VLM-->>Route: available=false, error message
    end

    Route->>State: verdict_from_verify() derives hub_verified, hub_confidence, alert
    Route->>State: record_event() into ring buffer plus latest frame
    Route-->>Edge: schema_version, event_id, received, hub_verified, hub_confidence, identity_status, alert

    Note over Route,Edge: The dashboard (/user/events, /user/latest.jpg) polls the same event store just updated.
```

`POST /user/reason` follows the same save-image -> VLM step, but calls the
free-form `reason()` method instead of `verify()`, returns raw text instead
of a structured verdict, and does **not** call `record_event()` — so it never
appears on the dashboard. That split is deliberate: `/user/reason` is a
reasoning-only developer tool; `/edge/event` is the real device contract.

## Endpoints

Routes are split into two groups, in separate files:

- **`/edge/*`** — device-facing (Arduino UNO Q -> hub), in `edge_routes.py`
- **`/user/*`** — human-facing (browser / operator), in `user_routes.py`

| Method | Path | Group | Purpose |
|--------|------|-------|---------|
| GET | `/health` | — | Liveness + VLM availability |
| GET | `/` | — | Redirects to `/user/` |
| POST | `/edge/event` | edge | Edge event JSON + frame in, schema-compliant verification response out |
| GET | `/user/dashboard` | user | Live event / verification dashboard page |
| GET | `/user/events` | user | Recent events + results (JSON) |
| GET | `/user/latest.jpg` | user | Most recent frame |
| GET | `/user/frames/<name>` | user | A specific stored frame |
| POST | `/user/reason` | user | Raw VLM tester: image in, reasoning text out |
| GET | `/user/test_reason` | user | Reason tester page (posts to `/user/reason`) |
| GET | `/user/test_event` | user | Edge-event tester page (posts to `/edge/event`) |
| GET | `/user/` | user | Default landing → reason tester page |

### File layout

```
hub/
  server.py         # app factory; registers blueprints; /health, / redirect
  state.py          # shared config, event store, VLM, request helpers
  edge_routes.py    # /edge/*  blueprint
  user_routes.py    # /user/*  blueprint
  vlm_backend.py    # conditional GenieX VLM
  static/           # test_reason.html, test_event.html, dashboard.html
```

### Test pages

Two browser testers (linked to each other and the dashboard via a nav bar):

- **`/user/test_reason`** — posts to `/user/reason`; shows raw VLM reasoning.
  Does **not** record to the dashboard.
- **`/user/test_event`** — simulates an edge device: posts a frame + edge
  metadata (`device_id`, `event_id`, `edge_confidence`) to `/edge/event` and
  shows the schema-compliant verification response. **Records to the dashboard.**

### `/user/reason` vs `/edge/event`

- **`/user/reason`** is a developer tool — it returns the raw VLM text and
  timing. Used by the upload test page (`/user/`).
- **`/edge/event`** is the real edge-device contract. It ingests the edge event
  metadata (`device_id`, `event_id`, `edge_confidence`, …) alongside the frame,
  runs hub verification, records the result for the dashboard, and returns the
  schema from `qonclave_plan.md` §5.3:
  ```json
  {"schema_version":"0.1","event_id":"…","received":true,
   "hub_verified":true,"hub_confidence":0.91,
   "identity_status":"not_enabled","alert":"Person verified near camera"}
  ```

## Runs anywhere; reasoning only on Snapdragon

The server itself runs on **any** laptop (regular x86 Windows/Linux included).
The heavy reasoning uses **GenieX + Qwen2.5-VL-7B**, which is Snapdragon-X-only,
so `geniex` is imported **conditionally at runtime** — never at module load.

- On a Snapdragon X laptop with GenieX installed → reasoning returns real VLM output.
- On any other machine → the server, upload, logging, dashboard, and test page
  all work; reasoning returns `{"available": false, ...}` so you can test the
  plumbing end to end.

## Run

```bash
pip install -r hub/requirements.txt
python hub/server.py                       # http://0.0.0.0:8000
```

By default, per-request access logs are **hidden** (the dashboard polls every
2s and would flood the console); the hub's own event/alert logs still show. To
see per-request logs:

```bash
python hub/server.py --verbose             # show werkzeug access logs
python hub/server.py --host 0.0.0.0 --port 8000 -v
```

Then open <http://localhost:8000/user/> for the test page, or
<http://localhost:8000/user/dashboard> for the dashboard.

Environment options:

| Var | Default | Meaning |
|-----|---------|---------|
| `QONCLAVE_HOST` | `0.0.0.0` | Bind address |
| `QONCLAVE_PORT` | `8000` | Port |
| `QONCLAVE_WARMUP` | – | Set `1` to load the VLM model at startup |
| `QONCLAVE_MAX_UPLOAD_MB` | `16` | Max upload size |
| `QONCLAVE_EVENTS_MAX` | `50` | Dashboard event ring-buffer size |

## Calling `/user/reason`

**Browser / curl (multipart):**
```bash
curl -F "image=@frame.jpg" -F "prompt=Is there a person?" http://HUB_IP:8000/user/reason
```

## Calling `/edge/event` (edge device)

**curl (multipart form + event JSON):**
```bash
curl -F "image=@frame.jpg" \
     -F 'event={"device_id":"unoq-01","event_id":"evt-123","edge_confidence":0.82}' \
     http://HUB_IP:8000/edge/event
```

**Arduino UNO Q (raw bytes — simplest for a constrained device):**
POST the JPEG bytes directly with an image content type. Edge metadata goes in
the query string; optional prompt via the `X-Prompt` header.
```
POST /edge/event?device_id=unoq-01&event_id=evt-123&edge_confidence=0.82 HTTP/1.1
Host: HUB_IP:8000
Content-Type: image/jpeg
Content-Length: <n>

<...raw jpeg bytes...>
```

Both shapes return the schema-compliant verification response shown above.

## Dashboard

Open <http://HUB_IP:8000/user/dashboard>. It polls `/user/events` every 2s and
shows the latest escalation frame, hub verification, edge/hub confidence, alert
state, and a table of recent events.

## Sample images

`hub/samples/` ships ready-to-use test images (a person scene, an empty scene,
and Qualcomm's GenieX demo photo) plus helpers. See `hub/samples/README.md`.
Quick test against a running hub:
```bash
python hub/samples/send_sample.py room_with_person   # -> /edge/event
```
