# Profile: `minimal`

**Target.** A device that is asleep almost all the time: a coin-cell sensor with a five-year battery
budget, a LoRa node, a bare ESP32 that wakes once a day, reports, and sleeps. `ARCHITECTURE.md` calls
this class *Constrained Edge* — *"permanently locked into the Edge role… lack the hardware to ever
dynamically promote to a Hub or run complex discovery protocols."*

**Design premise.** Radio-on time is the battery budget. Round trips, not bytes, are the metric to
minimize. A device on this profile completes its entire interaction with the network in **one
request and one response**.

---

## Obligations

### MUST

- Implement the **check-in exchange** (`checkin.schema.json`) as its only network interaction.
  Uplink carries events, power, and acks; downlink carries time, mailbox commands, and config delta.
- Encode in **CBOR** (`../encodings/cbor.md`). JSON is permitted only for debugging.
- Use the endpoint written to non-volatile storage at commissioning. Discovery **MUST NOT** be
  attempted — an mDNS browse costs more radio time than the entire useful exchange.
- Authenticate with the pre-shared key established during out-of-band commissioning
  (`SECURITY.md` §3) and sign the uplink (`common.schema.json#/$defs/signature`, `alg: hs256`).
- Emit a monotonically increasing `wake_counter` on every check-in. This is both the time reference
  and the replay guard.
- Persist unsent events to non-volatile storage and retry on a later wake. A spool that lives in RAM
  is lost to the sleep cycle and is therefore not a spool.
- Retain a delivered command until it has been acknowledged in a subsequent check-in.
- Honor `expires_at` on a command and discard it silently if the deadline has passed. A device that
  wakes to a day-old *"unlock the door"* and executes it is a security failure, not a late delivery.

### MUST NOT

- Advertise a discovery manifest.
- Accept unsolicited inbound connections. The device has no listening socket; it is unreachable
  between wakes by design, which is also what makes it un-attackable between wakes.
- Promote itself to any role other than `edge`.

### MAY

- Omit `payload` entirely. A temperature reading belongs in `metadata`; there is no frame to send,
  and a LoRa frame could not carry one.
- Omit absolute `timestamp` and send `relative_time` instead — see below.
- Ignore `next_checkin_s`. It is advisory; the device owns its own power budget.
- Implement a placement decision as a compile-time constant. With one local model and one remote
  tier, `decide()` collapses to "triage here, escalate the rest."

---

## Time

The device is not required to know what time it is. After a day of deep sleep an RTC has drifted, and
many devices in this class have no RTC at all.

1. Device sends `relative_time` — `{wake_counter, ms_since_wake}`.
2. Hub stamps `hub_received_at` with authoritative time on receipt.
3. Hub returns `server_time` in the check-in response.
4. Device MAY use `server_time` to set its clock, and MAY ignore it if it has no clock to set.

The hub's stamp is the record of when the event arrived, not when it occurred. For a daily sensor
those differ by up to a wake interval, and consumers of archived records must treat
`hub_received_at` accordingly.

---

## Size budget

A LoRaWAN payload is 51–242 bytes depending on data rate and region. A conformant no-media check-in
uplink **MUST** fit in 242 bytes when CBOR-encoded, and **SHOULD** fit in 51.

The conformance case `checkin/minimal-lora-sized` asserts this. It is the single number that decides
whether this profile is real or merely described.

Measured on the reference fixture — a moisture reading with relative time and battery state:

| form | bytes | |
|---|---|---|
| JSON | 285 | **exceeds the ceiling** |
| CBOR, string keys | 223 | fits |
| CBOR, integer keys | 71 | fits comfortably |
| CBOR, integer keys, identity omitted | **61** | |
| the above, signed (+32-byte MAC) | **93** | still fits |

Neither form reaches 51. A device on the slowest data rate must shed a metadata field or spread its
report across two wakes.

Techniques, in order of effect:

| | Saving |
|---|---|
| CBOR instead of JSON | ~22% |
| Integer keys via the CBOR key map (`../encodings/cbor.md`) | ~68% of what remains |
| Omit `payload` | everything it would have cost |
| Omit empty collections — an `ack` with nothing to acknowledge is not worth a key | 2 bytes each |
| Omit `schema_version` — implied by the commissioned contract | 5 bytes per document, so 10 here |
| Omit `tenant_id` — implied by the PSK the hub authenticated with | 2 bytes plus the id's length |

The reference fixture carries no `tenant_id`, so the 71 → 61 saving above is `schema_version`
alone, dropped from both the check-in and the event nested inside it.

The last two are permitted **only** on this profile, and only because a `minimal` device's identity
and contract are both fixed at commissioning. A hub reconstructs them from the device record before
validating against the schema.

Note the integer key map is doing most of the work, not CBOR itself. An implementation that adopts
CBOR and stops there gets roughly a fifth of the available saving — and the Python binding made
exactly that mistake for nested events until the C binding was measured against it.

---

## What this profile does not get

Stated plainly, so nobody designs against a capability that isn't there. Each of these is a
consequence of the device being asleep, not an omission waiting to be filled in:

| absent | because |
|---|---|
| discovery | an mDNS browse costs more radio time than the entire useful exchange |
| MQTT subscribe / push commands | nothing can reach a device that is asleep 99.998% of the time. Worst-case command latency is one wake interval — a daily sensor cannot be told to do something *now* |
| TLS | a handshake is seconds and hundreds of KB of RAM, more energy than the message it protects |
| absolute timestamps | after a day of deep sleep the clock has drifted, or was never set |
| media payloads | a sensor reading is a number, and a LoRa frame could not carry a frame regardless |
| more than one round trip | round trips are the battery budget |
| live streaming, WebRTC, Direct-Bind | all require an always-on peer connection |
| hub election, role promotion | the device is permanently an edge |
| federation | it talks to the hub that commissioned it. Moving it is a re-commissioning operation, not a runtime grant |

Retrying a failed check-in immediately is also **not** correct here: the spool already holds the
event and the next wake is a duty cycle away, so a retry buys nothing and spends a second radio
window. Burning battery on retries is how a five-year deployment becomes a six-month one.

If an application needs any of the above, it needs the `constrained` profile and a device that can
stay awake.
