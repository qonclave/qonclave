# Conventions

The rules that keep this package coherent. Two of them are enforced by tests rather than by
review, because a layering rule that lives only in a document is one that gets broken during the
first tired refactor and stays broken until someone finds Flask on a sensor.

![The three layers: spec, sdk, app](assets/conventions/layering.svg)  
*[Edit in Excalidraw](assets/conventions/layering.excalidraw)*

Three layers, three questions:

| Layer | The question it answers | The test |
|---|---|---|
| `spec/v1/` | What must every implementation agree on? | Could a C edge and a Python hub disagree about it? |
| `sdk/` | How is that agreement expressed in code? | Is it a contract, a name, or an encoding? Then yes. Is it a library that opens a socket? Then no. |
| `hub/apps/<name>/` | What does any of it *mean*, and what carries it? | The application-specific blocks: the only layer where "person" appears, and the only layer that picks a client library. |

`security/` is this repo's example, not the definition. Every app supplies all three of the green
blocks — its own meaning, its own pipes, its own egress.

**The SDK owns the contract, the naming, and the encoding. The developer supplies the pipe.**

This is the rule that is easiest to get backwards, so state it concretely. `transport/` holds the
`Transport` and `PubSubTransport` ABCs and the scheme registry. It does **not** hold a `paho`
client, a `requests` session, or an `aiocoap` context — those are the developer's choice, and they
live wherever that developer wants them. The same applies to egress: `EgressChannel` is the
contract, and a Twilio client is not.

What stays normative is only the part two implementations could disagree about. A developer who
writes their own MQTT transport must still publish to `qonclave/commands/<node_id>` carrying a
spec `Command` — so that naming lives in `core/`, as spec-derived data, testable by
`conformance/` with no broker anywhere in sight. Put the topic string in the transport
implementation instead and it drifts once per application, which is exactly how this repo already
ended up with two incompatible topic layouts.

"The developer's choice" is bounded, not unbounded: [`spec/v1/profiles/`](../spec/v1/profiles/) says
a `full` node must be reachable over HTTP, MQTT, and gRPC, while a `minimal` one owes nothing more
than "one exchange, any link." The profile fixes *what must be supported*; the developer picks
*what implements it*.

---

## 1. The spec is normative; code is a binding

`spec/v1/` defines what a document means. `sdk/*/` are implementations. Where they disagree, the
SDK is wrong and the fix goes in the SDK.

Consequences worth stating:

- **Never change a schema to match code.** Change the code, or version the spec.
- **Additive changes only within v1.** New optional fields, new values in open sets, new entries
  appended to the CBOR key map. Removing a field, tightening a constraint, or reusing a CBOR key
  number requires v2 — devices commissioned years earlier are still in the field and cannot be
  updated.
- **`conformance/` is the arbiter.** A behavior that matters across implementations belongs in a
  fixture, not only in a Python test.

---

## 2. The dependency ladder

*Enforced by `sdk/python/tests/test_layering.py`.*

```
core                              imports nothing from qonclave
  ↑
transport, security               import core only
  ↑
discovery                         peer manifests, liveness, load
  ↑
placement                         decides WHICH tier runs a task
  ↑
inference, storage                ask placement, then execute
  ↑
edge | hub | compute | archive    import layers below — NEVER each other
  ↑
app, cli
```

**Roles never import siblings.** A hub reaches a compute node through `transport`, not by
importing `qonclave.compute`.

This is not tidiness. It is what makes two claims in the README true rather than aspirational:

- `pip install qonclave[edge]` lands without Flask, paho, or Twilio.
- Compute and Archive are genuinely optional, because nothing structurally depends on them.

It also encodes at the import level what `SECURITY.md` §2 asserts architecturally: compute is a
*remote, stateless* resource, not an in-process library call.

### Where a capability goes

The rule that decides this: **if more than one role needs it, it belongs below the roles.**

| Capability | Layer | Optional role that serves it remotely |
|---|---|---|
| running a model | `inference/` | `compute/` |
| persisting a record | `storage/` | `archive/` |
| deciding where work runs | `placement/` | — |
| naming a topic or route | `core/` | — |
| **carrying bytes** | `transport/` — **the ABCs and registry only** | the client library lives in the **app** |
| **reaching a human** | `hub/egress/` — **the ABC only** | the vendor client lives in the **app** |

The last two rows are what this table originally got wrong, in the same way twice. The framework
must be in both paths — `SECURITY.md` §1 makes the hub the only node permitted egress, and
placement cannot promise a deadline the layer beneath it may block past, which is why
`Transport.request` obliges implementations to honor `timeout_s`. But being in the path means
owning the **contract**, not shipping the **client**. `paho`, `requests`, `aiocoap`, `grpcio`, and
`twilio` are all the same kind of thing: somebody else's library for reaching somebody else's
process. None of them belong in a binding whose whole claim is to be transport- and
vendor-agnostic.

Putting `ModelBackend` inside `compute/` was the original design and it was wrong: a hub doing its
own VLM work would have had to import the optional server it is supposed to be able to do without.

---

## 3. Documentation lives next to the thing

- A module docstring states the module's responsibility, the spec/doc section it implements, and
  the `hub/framework/` module it will absorb.
- Comments explain **why**, not what. `# increment counter` is noise; `# doubles as the replay
  guard` is the reason the line exists.
- Prose in `docs/` explains rationale. Where prose and schema disagree, the schema wins and the
  prose is a bug.

---

## 4. Failure conventions

- **Best-effort subsystems return, they do not raise.** An unreachable broker, a missing model, or
  an absent battery sensor must not fail a request that was otherwise handled. This is the
  existing framework's "runs anywhere" behavior and it is deliberate.
- **Verification paths never crash on bad input.** `capability.verify` returns
  `VerificationResult(valid=False, ...)` for anything malformed. A device presenting garbage
  should be rejected, not able to take a hub down.
- **Different outcomes get different exceptions.** `PlacementError` and `PlacementDeferred` are
  not interchangeable: on a duty-cycled device, "retry later" may mean tomorrow.

---

## 5. Constrained devices are a design constraint, not a special case

Before adding anything to a message or a flow, ask what it costs a device that wakes for 200 ms
once a day:

- **Round trips are the budget**, not bytes. Radio-on time is what drains the battery.
- **Discovery is not free.** An mDNS browse can cost more than the entire useful exchange.
- **A day-old command is a hazard.** Anything time-sensitive carries `expires_at`, and the
  decoder drops expired commands below the application layer rather than trusting each firmware
  author to check.
- **A spool in RAM is not a spool.** It must survive the sleep cycle.

---

## Where existing code lands

Convergence is underway, not finished. This is the map — kept current, not aspirational, so a
merge on either side of the framework/`hub/` split can tell what it's actually colliding with:

| Today (`hub/framework/`) | Target (`sdk/python/src/qonclave/`) | Status |
|---|---|---|
| `adapter.py`, `transport.py` | `hub/ingest.py` — the Flask half (upload handling) stays in the app | ✅ done |
| `events.py` | `hub/events.py` | ✅ done |
| `recognize_activity.py` | `hub/events.py` | ⬜ not started |
| `policy.py` | `hub/policy.py` | ✅ done — lifted, reverted, redone; see below |
| `server.py` | `hub/app.py` | ⬜ not started |
| `mqtt_bus.py` | **stays put**, now wrapping `transport/mqtt.py`'s `MQTTTransport` (`PubSubTransport`) | ✅ done — JSON encoding, ring buffer, dual-topic publish, registry wiring all stay hub-side |
| `sms_bus.py` | `apps/security/egress/twilio_sms.py` — **leaves the framework entirely**, no generic contract in the SDK either | ✅ done — see below |
| `server.py`'s `POST /sms` + `GET /user/sms_activity` routes | `apps/security/sms_routes.py`, a Blueprint registered from `hub/server.py` | ✅ done — see below |
| `discovery.py` | `discovery/announce.py` + `discovery/backends/udp.py` | ✅ done — payload not yet spec-compliant, `browse.py` not touched; see below |
| `vlm.py`, `llm.py` | `inference/local/geniex.py` | ⬜ not started |
| `face_id/` | `inference/local/face_id/` | ⬜ not started |
| `pose/`, `qnn_session.py` | `inference/local/` — didn't exist when this table was first written | ⬜ not started |
| `device_registry.py` | `discovery/registry.py` — new module; `peers.py`/`health.py` turned out to be placement-scoped (federation candidates, heartbeat liveness), not a general sighting ledger | ✅ done — RTT probing stays hub-local, see below |
| edge-side `edge_confidence` threshold | a `PlacementPolicy` — no longer hardcoded | ⬜ not started; `edge/` imports nothing from `qonclave.*` yet |
| `icons.py` | **stays app-level** | n/a — correctly not lifted |

`hub/` is no longer untouched: `adapter.py`, `events.py`, `transport.py`, `policy.py`,
`device_registry.py`, `discovery.py`, and `mqtt_bus.py` build on the SDK today, proved by
`hub/tests/` running against all seven (`mqtt_bus.py` is a wrapper rather than a pure re-export
shim like the others — see below for why). `sms_bus.py` is a different case again: it left
`hub/framework/` entirely, without gaining any SDK-side counterpart — see below. Everything else
in the table above is still the pre-convergence, framework-agnostic implementation.

### `policy.py` was lifted, then reverted, then redone (2026-08-06)

`hub/framework/policy.py` briefly re-exported `Policy`/`Verdict`/`Notification` from
`qonclave.hub.policy`, with three hooks renamed: `evaluate(image_path, event: dict)` →
`evaluate(event: EdgeEvent, image_path=None)`, `on_sms_reply`/`reply_for_sms`/`last_sms_analysis` →
`on_reply`/`reply_for`/`dashboard_state`.

Merging the branch that did that lift with the `hub/` mainline found the mainline had kept
building on the **old**, unrenamed contract the whole time — an investigation flow, known-person
follow priorities, buzzer control, and pose-driven per-track analysis, all real and tested,
none of it aware the rename had happened. Re-lifting `policy.py` as-is would have silently
dropped every one of those. `policy.py` went back to the pre-lift shape (old names, plus the new
hooks upstream added — `analyze_track`, `track_settings`, `update_track_settings`, none of which
existed the first time this was lifted) rather than resolve that conflict file-by-file and risk
losing tested behavior. See the `TODO` at the top of `hub/framework/policy.py`.

**Redoing this lift correctly** means, in order: (1) add `analyze_track`, `track_settings`, and
`update_track_settings` to `qonclave.hub.policy.Policy` — they're real hooks now, not a gap; (2)
reapply the rename against *that* method set, not the one from the first attempt; (3) update every
`apps/*/policy.py` subclass and every `hub/framework/server.py` call site in the same change, so
the contract and its only caller never disagree mid-commit.

**Done, same day.** All three steps landed together: `qonclave.hub.policy.Policy` gained the three
track hooks, `hub/framework/policy.py` is now a re-export shim (same shape as `adapter.py`), and
`hub/apps/security/policy.py` plus every call site in `hub/framework/server.py` (`/edge/event`,
`/sms`, `/user/llm_response`) moved to the typed contract in the same change. `command_for`'s
return is converted to a wire dict via `adapter.command_to_wire()` before it reaches MQTT or the
HTTP response — the one place a `qonclave.core.models.Command` object would otherwise leak past
the framework boundary.

### `device_registry.py`'s assumed destination didn't exist (2026-08-06)

The migration plan for this file assumed `discovery/peers.py` and `discovery/health.py` were its
SDK destination — a reasonable guess from their location, wrong on inspection. Both are scoped to
placement: `peers.py` is "the peer registry, **including grants held for each**... the entirety of
what federation adds to placement," and `health.py` is heartbeat liveness that "feeds placement: a
peer whose heartbeat has lapsed stops being a candidate tier." Neither carried an `Origin:` comment
pointing at `device_registry.py`, unlike `discovery/announce.py`/`browse.py` (confirmed real
targets for `discovery.py`, a later migration) or `inference/local/geniex.py` (confirmed for
`vlm.py`/`llm.py`).

`device_registry.py` answers a plainer, placement-independent question — "what has this deployment
ever heard from" — for an operator-facing view (`/user/network`), not placement candidate
selection. Rather than stretch `peers.py` past its documented scope, it got a new module:
`qonclave.discovery.registry`. `hub/framework/device_registry.py` is now a re-export shim over it
(same shape as `adapter.py`), translating that module's spec-consistent `node_id` back to this
file's historical `device_id` field name, since `/user/devices` and `network.html` already speak
it. RTT probing (`_ping_once`/`start_rtt_prober`) stayed hub-local — deployment-specific (a
subprocess `ping` call needs OS privileges that don't make sense on every binding) — reading the
new module's `probe_targets()`/`record_rtt()` seam instead of touching its internals directly.

### `discovery.py`'s wire format is not yet spec-compliant, and `browse.py` wasn't touched (2026-08-06)

`discovery/announce.py`'s `Origin:` comment confirmed it as this file's real target, and the
socket-level send/receive/reply mechanics moved to `discovery/backends/udp.py` (a small
`UDPAnnounceBackend` class) underneath it. Two things this migration deliberately did **not** do:

1. **The announced payload is still the pre-spec ad-hoc shape**
   (`{"service": "qonclave-hub", "hostname": ..., "port": ..., "version": "1.0"}`), not a real
   `node-manifest.schema.json` document (`schema_version`, `service: "qonclave-node"`, `node_id`,
   `node_type`, `capabilities`, ...). Edge devices already flashed against the old shape — notably
   `edge/arduino_uno_q_00/qonclave-detect-objects-on-camera`'s discovery client, checked against
   this exact hub earlier the same day — would break silently on a format change. `announce.start()`
   takes `payload: dict` generically for exactly this reason: the caller supplies the shape, the
   SDK doesn't assume it's spec-compliant yet. Fixing this is a real, cross-cutting `hub/`+`edge/`
   change, not something to slip into a structural move.
2. **`browse.py` was left untouched.** Its docstring ("find peers and cache their manifests with a
   TTL") describes actively discovering and remembering *other* nodes for placement/federation
   purposes. `hub/framework/discovery.py` never did this — it only answers probes aimed at itself
   and records the prober as a sighting (now `discovery.registry`, not a peer-manifest cache).
   There was nothing in the old file to port into `browse.py`; it remains a placeholder until
   something actually needs to browse for peers.

### `mqtt_bus.py` is a wrapper, not a re-export shim (2026-08-06)

Every earlier migration in this table produced a thin re-export shim — `hub/framework/x.py`
becomes a handful of lines pointing at the real implementation in `qonclave`, same shape as
`adapter.py`. `mqtt_bus.py` doesn't, because `transport/base.py`'s `PubSubTransport` ABC and
`MQTTBus`'s existing public surface are contracts at different altitudes on purpose:

* `PubSubTransport.publish(topic, body: bytes, ...)` / `.subscribe(topic, handler)` — bytes in,
  bytes out, one handler per topic filter. Generic enough that HTTP, CoAP, and gRPC transports
  implement the same shape's request/response cousin.
* `MQTTBus` — JSON dict in/out, a received-message ring buffer (`/test/hub`'s console), dual-topic
  legacy publishing during the spec migration (`command_topic`/`legacy_command_topic`), and
  feeding `discovery.registry` from status-topic sightings.

None of that second list belongs in a generic transport — a CoAP transport has no ring buffer to
speak of, and "legacy topic" is meaningless outside this specific migration. So
`qonclave.transport.mqtt.MQTTTransport` implements only the first list (paho-mqtt underneath,
lazy-connect with a reconnect cooldown, idempotent-by-topic subscribe), and
`hub/framework/mqtt_bus.py`'s `MQTTBus` keeps its existing name, existing public methods, and
existing callers unchanged, now composing `MQTTTransport` internally instead of holding a raw
paho client. One small, deliberate behavior delta: the original `MQTTBus.subscribe()` could return
`False` if a connected broker's own `client.subscribe()` call raised; `MQTTTransport.subscribe()`
logs that case and no-ops rather than surfacing it, so the wrapper's `subscribe()` now returns
`True` whenever the broker was reachable at all. No test exercised the old failure path.

Three more of the table's rows are worth explaining.

`vlm.py`/`llm.py`/`face_id/` land in `inference/local/`, **not** `compute/`. That is rule 2 in
practice, and it is what lets today's single-laptop hub keep doing its own VLM work with no
compute node present.

`icons.py` is LED-icon rendering — use-case-specific logic that already violates `AGENTS.md`'s own
rule against app logic in the framework. The new layout gives it nowhere to go, which is the
correct outcome rather than an oversight.

`sms_bus.py` is the same mistake, less visibly, because "notifications" sounds infrastructural.
It is 195 lines of `TWILIO_ACCOUNT_SID`, `from twilio.rest import Client`, and one hardcoded
recipient. Twilio appears nowhere in `spec/v1/`, and the SDK's own `egress/webhook.py` docstring
already argues the point: enterprises "do not want a hardcoded SMS vendor."

**Migrated 2026-08-06, further than first planned.** The original plan for this migration kept a
generic `SMSTransport` contract (`send`/`is_available`/`status`) in `qonclave.hub.egress.sms`, with
only the Twilio implementation moving to the app — the same shape as `Policy`/`PlacementPolicy`.
That went in, then came back out: SMS turned out not to need a framework-level contract at all.
Nothing in `hub/framework/server.py` calls anything on `sms` beyond `.status()` (for `/health`) and
`.send(notification)` (from `notify_for()`'s result) — both already expressible as plain duck
typing, the same way `create_app()`'s untyped `face_id`/`pose` parameters work. Forcing an ABC into
the SDK for that would be a contract with one implementation and no second consumer to prove it
generalizes — exactly the trap `qonclave.discovery.registry`'s design note above warns against
inverted. So `qonclave.hub.egress.sms` stays the placeholder it always was, and
`hub/apps/security/egress/twilio_sms.py`'s `SMSBus` doesn't subclass anything from the SDK.

The route migration went with it: `POST /sms` and `GET /user/sms_activity` both read Twilio-shaped
data (`request.form["From"]`/`["Body"]`, `sms._suppressed`) that has no business in generic
framework HTTP surface. Both moved to `hub/apps/security/sms_routes.py`, a `Blueprint` — but
registered from `hub/server.py` *after* `create_app()` returns, not threaded through
`create_app()`'s parameters the way `apps/assistant/routes.py`'s blueprint currently is. That
avoids adding a second `from apps.x.routes import ...` into `hub/framework/server.py` next to the
one already flagged as backwards and awaiting Phase 8's pluggable-registration fix — no reason to
write a second copy of a bug already scheduled for removal.
