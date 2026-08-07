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
| `recognize_activity.py` | **stays app-level** | n/a — decided 2026-08-06, see below |
| `track_store.py` | **stays app-level** | n/a — decided 2026-08-06, see below (missing from this table until now) |
| `policy.py` | `hub/policy.py` | ✅ done — lifted, reverted, redone; see below |
| `server.py` | `hub/app.py` | 🔄 backwards-dependency bug fixed; full move descoped, see below |
| `mqtt_bus.py` | **stays put**, entirely — no `qonclave.transport.mqtt` implementation | ✅ done — paho lives here, not in the SDK; see below (amended 2026-08-06) |
| `sms_bus.py` | `apps/security/egress/twilio_sms.py` — **leaves the framework entirely**, no generic contract in the SDK either | ✅ done — see below |
| `server.py`'s `POST /sms` + `GET /user/sms_activity` routes | `apps/security/sms_routes.py`, a Blueprint registered from `hub/server.py` | ✅ done — see below |
| `discovery.py` | `discovery/announce.py` + `discovery/backends/udp.py` | ✅ done — payload not yet spec-compliant, `browse.py` not touched; see below |
| `vlm.py`, `llm.py` | `inference/local/geniex.py` — one `GenieXBackend` for both roles | ✅ done — wrapper, not a shim; see below |
| `face_id/` | **stays app-level**, permanently | n/a — decided 2026-08-06, see below |
| `pose/`, `qnn_session.py` | **stays app-level**, permanently | n/a — decided 2026-08-06, see below |
| `device_registry.py` | `discovery/registry.py` — new module; `peers.py`/`health.py` turned out to be placement-scoped (federation candidates, heartbeat liveness), not a general sighting ledger | ✅ done — RTT probing stays hub-local, see below |
| edge-side `edge_confidence` threshold | a `PlacementPolicy` — no longer hardcoded | ⬜ not started; `edge/` imports nothing from `qonclave.*` yet |
| `icons.py` | **stays app-level** | n/a — correctly not lifted |

`hub/` is no longer untouched: `adapter.py`, `events.py`, `transport.py`, `policy.py`,
`device_registry.py`, `discovery.py`, `vlm.py`, and `llm.py` build on the SDK today, proved by
`hub/tests/` running against all eight (`vlm.py` and `llm.py` are wrappers rather than pure
re-export shims like the others — see below for why). `mqtt_bus.py` and `sms_bus.py` are both
cases where the SDK-side counterpart was tried and then deliberately removed — see below for each;
`mqtt_bus.py` stays entirely hub-owned, same as `sms_bus.py`'s Twilio implementation, just without
the file also relocating. `face_id/`, `pose/`, and `qnn_session.py` are staying app-level
permanently — also below. `server.py` had its one concrete, long-flagged bug fixed (the
`apps.assistant.routes` backwards import) without the full `hub/app.py` move it was originally
scoped for — see below for
why that turned out to be its own, larger effort. Everything else in the table above is still the
pre-convergence, framework-agnostic implementation.

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

### `mqtt_bus.py` briefly gained a `qonclave.transport.mqtt.MQTTTransport`, then didn't (2026-08-06)

First pass: every earlier migration in this table produced a thin re-export shim —
`hub/framework/x.py` becomes a handful of lines pointing at the real implementation in `qonclave`,
same shape as `adapter.py`. `mqtt_bus.py` got a variant of that instead — a
`qonclave.transport.mqtt.MQTTTransport` class holding a real `paho.mqtt.Client` (lazy-connect with
a reconnect cooldown, bytes-in/bytes-out publish, idempotent-by-topic subscribe), with `MQTTBus`
kept as a thin wrapper composing it for the hub-specific parts (JSON encoding, the `/test/hub`
ring buffer, dual-topic legacy publishing, `discovery.registry` wiring).

That was wrong, and this document already said so before it happened: §2's "Where a capability
goes" table states plainly that `transport/` holds "the ABCs and registry only" and "the client
library lives in the app," naming `paho` specifically as an example of what does not belong here
— "the last two rows are what this table originally got wrong, in the same way twice." Landing
`MQTTTransport` made it three. The mistake survived `test_layering.py` because that test checks
role-to-role `qonclave.*` imports, not whether a `qonclave` module imports a third-party vendor
library — a different, review-only rule this file already stated but a change didn't get checked
against.

**Reverted the same day.** `qonclave/transport/mqtt.py` is back to a placeholder. The paho client
— connect/publish/subscribe, lazy-connect, reconnect cooldown — moved back into
`hub/framework/mqtt_bus.py`'s `MQTTBus` directly, alongside the JSON encoding/ring buffer/
dual-topic logic that never left. Its test coverage moved with it:
`framework/sdk/python/tests/test_transport_mqtt.py` (real amqtt broker, real paho client) became
`hub/tests/test_mqtt_bus.py`, and `amqtt` moved from the SDK's `dev` extra to a CI-only
`hub.yml` install step. `MQTTBus`'s public API and every caller are unchanged throughout — this
was purely about where the paho-dependent code lives, not what it does.

### `vlm.py`/`llm.py` became one `GenieXBackend`; `face_id`/`pose`/`qnn_session.py` stay app-level (2026-08-06)

`vlm.py`/`llm.py` land in `inference/local/`, **not** `compute/`. That is rule 2 in practice, and
it is what lets today's single-laptop hub keep doing its own VLM work with no compute node
present. The two files were near-duplicates of each other (lazy ARM64-gated GenieX load, a
threading lock, a `close()`) differing only in whether a call carries an image, which is exactly
what `ModelBackend.infer()`'s `image_path`/`payloads` parameters already distinguish — so
`qonclave.inference.local.geniex.GenieXBackend` is one class, and `hub/server.py` constructs two
instances of it (`vlm = VLMBackend()` with the Qwen2.5-VL model id, `llm = LLMBackend()` with
Qwen3-4B), same as before.

Wrappers, not shims: `GenieXBackend.infer()` returns a
generic `InferResult`, but `apps/security/policy.py`, `apps/security/investigation.py`, and
`apps/assistant/routes.py` all read the existing dict-shaped `reason()`/`structured_query()`/
`generate()` returns. Translating in the wrapper means none of those callers change. One small,
deliberate behavior fix: `LLMBackend.status()` used to check `self._model is not None` directly,
under-reporting availability until something else (a real call, or the assistant's own startup
warmup) happened to trigger a load; it now calls `available()` like `VLMBackend.status()` already
did (and already explained why), so the first `/health` call after startup is accurate either way.

**`face_id/`, `pose/`, and `qnn_session.py` are not migrating — decided, not deferred.** All three
exist only to serve `face_id/` and `pose/`'s own vision pipelines (`qnn_session.py` is a raw
onnxruntime-QNN `InferenceSession` factory used by both; neither `VLMBackend` nor `LLMBackend` ever
called it — GenieX handles NPU dispatch internally through its own `device_map="qairt"` API).
Reasons, in order: (1) keeping biometric/PII-adjacent code (face recognition) out of the
framework's default install surface matters for the open-source privacy-claims audit
(`steps_to_open_source.md` §7); (2) `inference.ModelBackend` being sufficient to build `vlm.py`/
`llm.py` on is itself proof an app can supply its own vision backends the same way, without a
`qonclave.vision` namespace; (3) `qnn_session.py` has no consumer left once face_id/pose stay put,
so there's nothing to migrate it *for* — the same "no second consumer to prove it generalizes"
reasoning `sms_bus.py`'s note above landed on for a different file.

### `server.py`'s backwards dependency is fixed; the rest of the move is descoped (2026-08-06)

This was planned as the migration's capstone: implement `qonclave.hub.app` (a Flask app factory)
using everything the six migrations above stood up, and reduce `hub/framework/server.py` to a
composition root. Attempting that revealed the plan's own premise didn't survive contact with the
file: `server.py` is ~1050 lines and ~40 routes, and — following the exact reasoning that already
moved `/sms` and `/user/sms_activity` out to an app blueprint (§ above) — a real, honest pass would
first have to sort every one of those 40 routes into "generic framework HTTP surface" versus
"reads an app-specific data shape and belongs in a blueprint instead." Several are obviously the
latter on inspection (`/user/known_faces`, `/user/known-person-priorities`, `/user/investigation`,
`/user/investigate` all read `SecurityPolicy`-specific optional hooks; `/user/recognize_activity`
and the `/user/tracks*` family read `recognize_activity.py`/`track_store.py`, both just decided
"stays app-level" two sections up). That is a route-by-route audit at least as large as this
entire migration series has been, file by file — not something to rush through inside what was
supposed to be the last, wrap-up phase.

So this phase shipped only what was already fully specified and low-risk: the concrete bug named
in every earlier phase's note, `hub/framework/server.py` doing
`from apps.assistant.routes import create_assistant_blueprint` — the framework layer reaching into
one specific app. `create_app()` gained a generic `blueprints: list[Blueprint] = ()` parameter;
`assistant_llm` (which existed solely to build that one blueprint internally) is gone. Each app now
builds its own blueprints and hands the finished objects to `create_app()` — `hub/server.py` builds
both `apps.assistant.routes.create_assistant_blueprint(...)` and
`apps.security.sms_routes.create_sms_blueprint(...)` and passes both through the same list.
`framework/server.py` now imports nothing from `apps/`, verified by a test that greps its own
source for `apps.` imports and fails if one reappears.

**What's still open**: `qonclave.hub.app` remains the placeholder it always was. The actual
`hub/app.py` migration — the route-by-route framework/app sort described above, then moving
whatever's left into the SDK — is real, valuable, future work, but it's its own multi-phase effort
now that the assumption it would be a single capstone phase has been tested and found wrong.

Three more of the table's rows are worth explaining.

`icons.py` is LED-icon rendering — use-case-specific logic that already violates `AGENTS.md`'s own
rule against app logic in the framework. The new layout gives it nowhere to go, which is the
correct outcome rather than an oversight.

`recognize_activity.py` and `track_store.py` are dashboard ring buffers (`/recognize` calls,
`/track/analyze` history + live MJPEG pose frames), not wire-spec state — reclassified from
"not started" (`recognize_activity.py`) and simply missing from this table (`track_store.py`) to
"stays app-level" on the same 2026-08-06 pass that finished `policy.py`'s `analyze_track`/
`track_settings` hooks, specifically to check whether either backs that new data closely enough to
be worth promoting. Neither does: `track_store.py` stores `Policy.analyze_track()`'s return value
as an opaque blob for the dashboard to display, same as it already stores `face_result`/
`pose_result` from the (app-owned) face-ID and pose analyzers — it doesn't interpret any of the
three, and moving a passthrough container for other-app-owned data into the framework would just
relocate the coupling, not remove it. Both also have no `Origin:` pointer in any SDK stub, unlike
every file that did move in this same round of migrations.

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
