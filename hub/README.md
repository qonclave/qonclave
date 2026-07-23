# Qonclave Hub

HTTP server for the Snapdragon laptop hub. Receives an image, runs
vision-language reasoning on it, and returns the result as JSON.

## Endpoints

| Method | Path       | Purpose |
|--------|------------|---------|
| GET    | `/health`  | Liveness + whether the VLM is available on this machine |
| POST   | `/reason`  | Upload an image, get VLM reasoning back as JSON |
| GET    | `/`        | Test webpage: upload an image and see the reasoning |

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

**Arduino UNO Q (raw bytes — simplest for a constrained device):**
POST the JPEG bytes directly with an image content type. Optional prompt via
the `X-Prompt` header or `?prompt=` query string.
```
POST /reason?prompt=Is%20there%20a%20person HTTP/1.1
Host: HUB_IP:8000
Content-Type: image/jpeg
Content-Length: <n>

<...raw jpeg bytes...>
```

Both shapes return the same JSON:
```json
{
  "ok": true,
  "available": true,
  "text": "A person is standing near a doorway...",
  "prompt": "...",
  "model_id": "ai-hub-models/Qwen2.5-VL-7B-Instruct",
  "latency_s": 3.42,
  "image_saved_as": "20260723T....jpg",
  "profile": {"generated_tokens": 88, "decode_speed": 12.3, "stop_reason": "..."}
}
```
