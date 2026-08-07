# Qonclave Developer Guide

This guide covers the reference hub implementation at `examples/hub/framework/` — how it's
structured, how a request flows through it, and how to build your own app on top of it,
**including with no hardware at all**.

## Architecture & Code Structure

This architecture outlines the separation between the generic framework and a specific
application (living in `examples/hub/apps/`).

![Architecture Diagram](assets/developer_guide/architecture.svg)
*[Edit Architecture in Excalidraw](assets/developer_guide/architecture.excalidraw)*

### Framework vs. app

- **`examples/hub/framework/`** is reusable across use cases: it knows how to accept a frame +
  edge event over HTTP, run VLM/LLM inference, keep a ring buffer of recent events for a
  dashboard, and serve an app's static test pages. It has no idea what "person_present" or
  "fall_detected" means. Several of its modules (`adapter.py`, `events.py`, `policy.py`, ...) are
  thin re-export shims over the installable `qonclave` SDK package — see
  [`CONVENTIONS.md`](CONVENTIONS.md)'s "Where existing code lands" table for exactly which.
- **`examples/hub/apps/security/`** declares everything specific to stationary person-detection,
  fall detection, and intrusion scenarios. A new use case means writing a new `Policy` subclass
  in a new `apps/<name>/` package — no framework code changes.

The `Policy` contract (`framework/policy.py`, a re-export shim over `qonclave.hub.policy`):
```python
class Policy(ABC):
    name: str
    def evaluate(self, event: EdgeEvent, image_path: str | None = None) -> Verdict: ...
    def command_for(self, verdict: Verdict, event: EdgeEvent) -> Command | None:
        return None   # override to route a hub->edge command
```

Backends (`vlm`, `llm`, `face_id`) reach a Policy through its **constructor**, not through
`evaluate()`. That keeps the call signature stable: adding a capability would otherwise force
every existing Policy to change in order to gain something it does not use.

### Request flow: `POST /edge/event`

> [!NOTE]
> The reference implementation uses `POST /edge/event` for edge ingestion. `POST /api/v1/events`
> (the spec-aligned equivalent) is also served alongside it — see `framework/server.py`'s own
> docstring for how the two coexist.

![Request Flow Sequence Diagram](assets/developer_guide/request_flow.svg)
*[Edit Sequence Flow in Excalidraw](assets/developer_guide/request_flow.excalidraw)*

`POST /user/reason` follows the same save-image -> VLM step, but calls `VLMBackend.reason()`
directly (bypassing any Policy) and returns raw text instead of a structured verdict.

### Data Movement & Privacy Cascade

Qonclave enforces a strict data lifecycle where the further data travels from the edge, the
smaller and more abstract it becomes. **This describes the design; see
[`SECURITY.md`](SECURITY.md#6-implementation-status-enforced-vs-designed) for what the reference
deployment actually enforces today versus what's still aspirational** — most notably,
`examples/hub` ships with no authentication on any route, so treat the table below as intent, not
a guarantee, for anything reachable outside a trusted LAN.

| Stage | Data Form | Movement & Lifecycle |
|---|---|---|
| **Edge Device** | Raw Video / Sensor Feed | Processed continuously on-device. Never leaves the edge device during normal operation. |
| **Local Network (Edge → Hub)** | Frame + JSON Event | Sent over the local network only when a local confidence threshold is crossed. |
| **Hub / Compute Node** | Ephemeral File | The frame is temporarily saved to `examples/hub/uploads/` for VLM/Face-ID verification, then kept briefly for the local dashboard before being overwritten/discarded. |
| **External (Hub → SMS)** | Plain Text Alert | Only a human-readable text string (e.g., "Person verified near camera") is sent via Twilio to the operator. No images or raw data ever leave the local network. |

## Endpoints

All routes are generic (`framework/server.py`); only the app's `Policy` and static test pages
vary per use case. The list below is the common subset for getting oriented —
**`framework/server.py`'s own module docstring is the authoritative, complete list** (it's kept
current there specifically so this guide doesn't have to duplicate and drift from it).

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Liveness + VLM/LLM/MQTT/face-ID availability |
| GET | `/` | Redirects to `/user/dashboard` |
| GET | `/test/edge` | Edge-device simulator page |
| GET | `/test/hub` | Hub-side MQTT console |
| POST | `/edge/event` | Edge event JSON + frame in, policy-driven verification response out |
| POST | `/track/analyze` | Per-track-id face/pose analysis |
| POST | `/edge/investigation` | Edge's answer to a capture-image command |
| GET | `/user/dashboard` | Live event / verification dashboard page |
| GET | `/user/events` | Recent events + results (JSON) |
| GET | `/user/network` | This hub + devices seen on the LAN |
| POST | `/user/reason` | Raw VLM tester: image in, reasoning text out |
| POST | `/assistant/query` | Edge voice assistant: transcript in, LLM/template reply out |
| POST | `/sms` | Twilio inbound-reply webhook (security app only) |

## File Layout (`examples/hub/framework/`)

```
framework/               # reusable, use-case agnostic
  server.py              # create_app(policy, vlm, mqtt, sms, STATIC_DIR, face_id, llm, ...) -> Flask
  transport.py           # upload handling + edge-event parsing (shim over qonclave.hub.ingest)
  events.py              # event ring buffer for the dashboard (shim over qonclave.hub.events)
  vlm.py                 # VLMBackend: reason() + structured_query(), wraps qonclave GenieXBackend
  llm.py                 # LLMBackend: generate(), same wrapping as vlm.py
  mqtt_bus.py            # MQTTBus: publish_command() hub->edge push channel
  policy.py              # shim: Policy/Verdict/Notification from qonclave.hub.policy
  device_registry.py     # shim over qonclave.discovery.registry
  discovery.py           # shim over qonclave.discovery.announce
  face_id/               # face detection + identification (stays app-level permanently)
  pose/                  # pose estimation (stays app-level permanently)

apps/security/
  policy.py              # SecurityPolicy(Policy) -- the actual use-case logic
  investigation.py       # event-driven VLM investigation state machine
  egress/twilio_sms.py   # SMSBus: send() SMS notifications via Twilio (app-owned, not framework/)
```

See [`CONVENTIONS.md`](CONVENTIONS.md) for the full, currently-accurate map of what's a thin
shim over `qonclave.*` versus what's staying app/hub-local permanently, and why.

## Push Channels

### MQTT broker (hub->edge push channel)
`/edge/event`'s `command` field only reaches a device if it has an HTTP request open at that
moment. `framework/mqtt_bus.py` gives the hub a second, independent path to push the same
command over MQTT.

### SMS notifications (hub->operator push channel)
`apps/security/egress/twilio_sms.py`'s `SMSBus` gives a Policy a way to push an SMS to an
operator when a significant event is verified — this is app-owned, not `framework/`, since the
vendor (Twilio) is the developer's choice, not part of the framework contract. The framework just
sends whatever the Policy's `notify_for()` method returns.

## Quickstart: Build Your First App

This walks through writing a custom Policy that detects whether a person is holding a coffee
cup, and running it against a local hub — **entirely without a Snapdragon laptop or any other
special hardware.**

### 1. Prerequisites

```bash
git clone https://github.com/qonclave/qonclave.git
cd qonclave
pip install -e "framework/sdk/python[dev]"
pip install -r examples/hub/requirements.txt
```

Python 3.10+. No GPU/NPU, no ARM64 — see "Run without hardware" below for why.

### 2. Create Your App Directory

```bash
mkdir -p examples/hub/apps/coffeecam/static
cd examples/hub/apps/coffeecam
```

### 3. Write Your Policy

The Policy is where your business logic lives. It takes an incoming edge event and decides what
to do with it. Create `policy.py` inside your `coffeecam` folder:

```python
# examples/hub/apps/coffeecam/policy.py
from framework.policy import Policy, Verdict

class CoffeePolicy(Policy):
    name = "coffeecam"

    def __init__(self, vlm):
        # Backends arrive here, not in evaluate() -- see "Framework vs. app" above.
        self.vlm = vlm

    def evaluate(self, event: EdgeEvent, image_path: str | None = None) -> Verdict:
        if event.trigger != "motion_detected":
            return Verdict(verified=False, confidence=None,
                           alert="Ignored: not a motion event.")

        prompt = ("Is there a person in this image? If so, are they holding a coffee cup? "
                  "Return a JSON object with keys 'person' (bool) and 'holding_coffee' (bool).")

        result = self.vlm.structured_query(image_path, prompt, max_new_tokens=100,
                                           json_mode=True, temperature=0.1)

        if not result.get("available"):
            return Verdict(verified=False, confidence=None,
                           alert="Compute node unavailable.",
                           reasoning_available=False)

        # structured_query returns the model's JSON under "parsed", alongside the
        # raw "text" -- read your fields out of parsed, never off the top level.
        # Running under the mock fallback (see below), "parsed" is always {} --
        # a deliberately honest "nothing detected" rather than a fabricated hit.
        parsed = result.get("parsed") or {}
        has_person = parsed.get("person", False)
        has_coffee = parsed.get("holding_coffee", False)

        if has_person and has_coffee:
            return Verdict(verified=True, confidence=None,
                           alert="Alert: Person with coffee detected!",
                           latency_s=result.get("latency_s"))
        elif has_person:
            return Verdict(verified=False, confidence=None,
                           alert="Person detected, but no coffee.")
        else:
            return Verdict(verified=False, confidence=None,
                           alert="False alarm. No person.")
```

### 4. Hook It Into The Server

Tell the hub to load your Policy instead of the default security app. Open
`examples/hub/server.py` and swap out the Policy:

```python
# In examples/hub/server.py
from apps.coffeecam.policy import CoffeePolicy

policy = CoffeePolicy(vlm)
STATIC_DIR = os.path.join(os.path.dirname(__file__), "apps", "coffeecam", "static")
app = create_app(policy, vlm, mqtt, sms, STATIC_DIR, face_id, llm)
```

### 5. Run without hardware

VLM/LLM inference (`framework/vlm.py`/`llm.py`) needs a Snapdragon X laptop with GenieX
installed — on any other machine, `vlm.available()` is `False` and every Policy that depends on
reasoning gets an honest "Compute node unavailable" by default. To exercise the **full HTTP
surface, your Policy, and the dashboard** without that hardware, set one environment variable:

```bash
export QONCLAVE_MOCK_INFERENCE=1   # PowerShell: $env:QONCLAVE_MOCK_INFERENCE = "1"
cd examples/hub
python server.py
```

This makes `vlm`/`llm` fall back to `qonclave.inference.local.mock.MockBackend` — a
deterministic, zero-hardware stand-in — **only** when the real GenieX backend is genuinely
unavailable (it's opt-in and never silently masks a real load failure on hardware that should
work; see `vlm.py`'s module docstring). Every response this produces carries `"mock": true` so
you can always tell it apart from a real inference result, and `/health` reports
`"vlm": {"mock": true, ...}` the same way.

Open `http://localhost:8000/test/edge` (the Edge Simulator — it pretends to be an IoT camera):

1. Click **Choose File** and upload any image.
2. Ensure the event type is set to `motion_detected`.
3. Click **Send Event to Hub**.

The image is sent to the hub, your `CoffeePolicy` runs, the mock backend returns a deterministic
(always-empty `parsed`) response, and you get back `Verdict(False, alert="False alarm. No
person.")` — proving the whole pipeline end-to-end. Swap in real hardware later and nothing
about your Policy needs to change.

## Roadmap & Future Ideas

### Hardware Capabilities API

Currently, developers have to manually check whether a given hub node supports specific features
(like the Snapdragon Hexagon NPU for VLM acceleration). This already has a concrete home in the
spec: `GET /api/v1/capabilities` is defined (`spec/v1/openapi/hub.yaml`) to return the node's
manifest — but the reference hub doesn't implement it yet (`framework/server.py` answers it
`501`, naming discovery's UDP broadcaster as the current alternative). Wiring it up is real,
scoped, tracked work, not a speculative idea.
