# Refactor hub/ into framework + app layout

## Context

`qonclave_proposal.md` (§3, §4.3) frames Qonclave as a **framework**, not an
application: reusable interfaces for transport, event escalation, hub-side
verification, and command routing — with use-case specifics (models, prompts,
thresholds, alert text) declared separately as a thin "app" on top. Today's
`hub/` code is all one flat module set (`state.py`, `vlm_backend.py`,
`edge_routes.py`, `user_routes.py`) with person-detection specifics
(`VERIFY_PROMPT`, `person_present`, "Person verified near camera") baked
directly into what should be generic transport/verification code. That makes
it hard to see, or actually reuse, the framework boundary the proposal
describes — a second use case (fall detection, hazard detection, etc.) would
currently mean copy-pasting the whole hub rather than writing a new policy.

This refactor splits `hub/` internally into `hub/framework/` (generic:
transport, event store, VLM primitive, policy interface, generic Flask
routes) and `hub/apps/security/` (the existing person-detection logic:
prompt, schema, thresholds, alert templates, test pages, sample images) — the
one use case that has real code today. No new use cases are stubbed in, and
no behavior changes: same wire schema, same endpoints, same test pages,
same `python hub/server.py` entrypoint (so `scripts/setup_geniex.ps1` keeps
working unmodified). Work happens on a new branch,
`refactor/framework-layout`.

Also: proposal's sequence diagram shows an optional hub→robot command
(e.g. `navigate_to: living_room`). No edge code consumes this yet, so this
refactor adds only a minimal extensibility hook (`Policy.command_for()` +
a `command` field in the response envelope) — not a real command channel.

## Target layout

```
hub/
  server.py                  # entrypoint (unchanged path/usage): wires one app's
                              # policy into framework.server.create_app(), argparse, run
  requirements.txt
  README.md                  # rewritten: framework vs app architecture + diagrams
  framework/
    __init__.py
    transport.py             # from state.py: save_incoming_image, parse_edge_event,
                              # request_prompt, timestamp, now_iso, UPLOAD_DIR/ALLOWED_EXT
    events.py                # from state.py: event ring buffer (record_event,
                              # recent_events, latest_frame_name), SCHEMA_VERSION
    vlm.py                   # from vlm_backend.py: VLMBackend — keeps reason() and
                              # the generic structured_query() (generate + _extract_json)
                              # mechanics; drops the person-specific VERIFY_PROMPT/verify()
    policy.py                # NEW: Policy ABC + Verdict dataclass — the app contract
                              # (evaluate(image_path, event) -> Verdict; command_for())
    server.py                # from server.py + edge_routes.py + user_routes.py, generalized:
                              # create_app(policy, static_dir) -> Flask; /health, /,
                              # /edge/event (policy-driven), /user/* (uses policy for
                              # nothing except is generic dashboard/frame/reason routes)
  apps/
    __init__.py
    security/
      __init__.py
      policy.py               # NEW: SecurityPolicy(Policy) — VERIFY_PROMPT, person_present
                               # schema, verdict_from_verify mapping, identity_status stub
      static/                 # moved as-is: dashboard.html, test_event.html, test_reason.html
      samples/                # moved as-is: *.jpg/*.png, make_samples.py, send_sample.py, README.md
```

`hub/uploads/` and `hub/__pycache__/` are untracked/gitignored — left alone;
new `__pycache__` regenerates under the new module paths.

## Key design points

- **`framework/policy.py`** defines the seam the proposal calls "declare
  sensor inputs, model, event types, thresholds, alert policy":
  ```python
  @dataclass
  class Verdict:
      verified: bool
      confidence: float | None
      alert: str
      reasoning_text: str | None = None
      reasoning_available: bool | None = None
      latency_s: float | None = None
      extra: dict = field(default_factory=dict)   # app-specific wire fields, e.g. identity_status

  class Policy(ABC):
      name: str
      def evaluate(self, image_path: str, event: dict) -> Verdict: ...
      def command_for(self, verdict: Verdict, event: dict) -> dict | None:
          return None   # default: no command routing (minimal hook for hub->edge commands)
  ```
- **`framework/vlm.py`**: keep `VLMBackend.reason()` untouched (generic, used
  by `/user/reason` regardless of app). Extract the current `verify()`'s
  generate+`_extract_json` mechanics into a generic `structured_query(image_path,
  prompt, max_new_tokens, **gen_kwargs) -> dict` helper. `SecurityPolicy`
  calls `structured_query()` with its own `VERIFY_PROMPT` and does its own
  field mapping (this is what `verdict_from_verify` in today's `state.py`
  does) — that mapping is security-specific and moves to `apps/security/policy.py`.
- **`framework/server.py`**: generalizes today's `edge_routes.py` +
  `user_routes.py` into one factory. The `/edge/event` handler becomes
  generic: `transport.save_incoming_image()` → `policy.evaluate()` →
  build the response envelope → `events.record_event()`. To keep the wire
  schema byte-for-byte identical (no dashboard/test-page JS changes needed),
  the envelope keeps today's field names — `hub_verified`, `hub_confidence`
  — mapped from `Verdict.verified`/`Verdict.confidence` inside
  `framework/server.py`, with `Verdict.extra` (e.g. `identity_status`)
  merged in flat. New: a `command` key, always present, populated from
  `policy.command_for(...)` (defaults to `null`).
- **`apps/security/policy.py`**: today's `VERIFY_PROMPT`, `person_present`/
  `confidence`/`description` schema, and `verdict_from_verify`'s alert-text
  logic (`"Person verified near camera"` / `"No person confirmed in frame"`),
  `identity_status: "not_enabled"` stub. `command_for()` not overridden
  (stays `None` — stationary person detection has no robot to command).
- **`hub/server.py`** (entrypoint) shrinks to: build a `VLMBackend`, build a
  `SecurityPolicy(vlm)`, call `framework.server.create_app(policy=...,
  static_dir=apps/security/static)`, keep the existing argparse/logging/
  `--verbose`/`QONCLAVE_WARMUP` main().

## Migration mapping (source → destination)

| Today | Becomes |
|---|---|
| `hub/state.py` (upload/parsing helpers) | `hub/framework/transport.py` |
| `hub/state.py` (event ring buffer) | `hub/framework/events.py` |
| `hub/vlm_backend.py` | `hub/framework/vlm.py` (generic parts) |
| `hub/vlm_backend.py` `VERIFY_PROMPT`/`verify()` mapping | `hub/apps/security/policy.py` |
| `hub/edge_routes.py` + `hub/user_routes.py` | `hub/framework/server.py` (generic routes) + `create_app()` |
| `hub/server.py` | thin entrypoint, wires `apps/security` into `framework/server.py` |
| `hub/static/*.html` | `hub/apps/security/static/*.html` (unchanged content) |
| `hub/samples/*` | `hub/apps/security/samples/*` (unchanged content) |
| `hub/README.md` | rewritten: framework vs. app split, updated mermaid diagrams, updated file-layout section |
| `README.md` (root) | small update to the `hub/` layout blurb to mention `framework/` + `apps/` |

`hub/requirements.txt`, `scripts/*` unchanged — `scripts/setup_geniex.ps1`
already targets `hub/server.py` and `hub/requirements.txt`, both still valid
paths.

## Steps

1. `git checkout -b refactor/framework-layout`
2. Create `hub/framework/` and `hub/apps/security/` packages per the mapping
   above; update all intra-package imports (`import state` → `from
   framework import transport, events`, etc.).
3. Move `static/` and `samples/` under `apps/security/`.
4. Rewrite `hub/server.py` as the thin entrypoint.
5. Rewrite `hub/README.md` (architecture + request-flow diagrams, updated
   file layout table) and tweak the root `README.md` layout blurb.
6. Delete the now-empty old files (`state.py`, `vlm_backend.py`,
   `edge_routes.py`, `user_routes.py`, old `static/`, `samples/`) via `git mv`
   where possible to preserve history.

## Verification

- `python hub/server.py --verbose` starts cleanly on this (non-ARM64) box;
  `/health` reports `vlm.available: false` with a clear `load_error`.
- `python hub/apps/security/samples/send_sample.py room_with_person` →
  `POST /edge/event` still returns the same envelope shape
  (`schema_version`, `event_id`, `received`, `hub_verified`,
  `hub_confidence`, `identity_status`, `alert`, plus new `command: null`).
- `curl -F "image=@hub/apps/security/samples/empty_room.jpg"
  http://127.0.0.1:8000/user/reason` still returns `{"available": false,
  ...}` on this machine (reasoning-unavailable path unchanged).
- Open `/user/test_event`, `/user/test_reason`, `/user/dashboard` in a
  browser (or curl the underlying endpoints) and confirm the pages load and
  poll correctly with no JS changes needed.
- `grep -r "state\.\|vlm_backend\|edge_routes\|user_routes" hub/` returns
  nothing outside of comments/README history references — confirms no
  dangling imports.
