# Qonclave Hub

HTTP server for the Snapdragon laptop hub. Receives an image, runs
vision-language reasoning on it, and returns the result as JSON.

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Liveness + whether the VLM is available on this machine |
| POST | `/reason` | Raw VLM tester: upload an image, get reasoning text back |
| POST | `/event` | **Edge contract**: edge event JSON + frame in, schema-compliant verification response out |
| GET | `/events` | **Dashboard data**: recent events + results (JSON) |
| GET | `/frames/<name>` | Serve a stored frame |
| GET | `/latest.jpg` | Serve the most recent frame |
| GET | `/dashboard` | **Dashboard page**: live event / verification view |
| GET | `/` | Test webpage: upload an image and see the reasoning |

### `/reason` vs `/event`

- **`/reason`** is a developer tool — it returns the raw VLM text and timing.
  Used by the upload test page (`/`).
- **`/event`** is the real edge-device contract. It ingests the edge event
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

- On a Snapdragon X laptop with GenieX installed → `/reason` returns real VLM output.
- On any other machine → the server, upload, logging, and test page all work;
  `/reason` returns `{"available": false, ...}` so you can test the plumbing.

## Run

```bash
pip install -r hub/requirements.txt
python hub/server.py                       # http://0.0.0.0:8000
```

Then open <http://localhost:8000> for the test page.

Environment options:

| Var | Default | Meaning |
|-----|---------|---------|
| `QONCLAVE_HOST` | `0.0.0.0` | Bind address |
| `QONCLAVE_PORT` | `8000` | Port |
| `QONCLAVE_WARMUP` | – | Set `1` to load the VLM model at startup |
| `QONCLAVE_MAX_UPLOAD_MB` | `16` | Max upload size |

## Calling `/reason`

**Browser / curl (multipart):**
```bash
curl -F "image=@frame.jpg" -F "prompt=Is there a person?" http://HUB_IP:8000/reason
```

## Calling `/event` (edge device)

**curl (multipart form + event JSON):**
```bash
curl -F "image=@frame.jpg" \
     -F 'event={"device_id":"unoq-01","event_id":"evt-123","edge_confidence":0.82}' \
     http://HUB_IP:8000/event
```

**Arduino UNO Q (raw bytes — simplest for a constrained device):**
POST the JPEG bytes directly with an image content type. Edge metadata goes in
the query string; optional prompt via the `X-Prompt` header.
```
POST /event?device_id=unoq-01&event_id=evt-123&edge_confidence=0.82 HTTP/1.1
Host: HUB_IP:8000
Content-Type: image/jpeg
Content-Length: <n>

<...raw jpeg bytes...>
```

Both shapes return the schema-compliant verification response shown above.

## Dashboard

Open <http://HUB_IP:8000/dashboard>. It polls `/events` every 2s and shows the
latest escalation frame, hub verification, edge/hub confidence, alert state, and
a table of recent events.
