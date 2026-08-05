# CBOR encoding — v1

CBOR ([RFC 8949](https://www.rfc-editor.org/rfc/rfc8949)) is the wire format for constrained links.
It carries the **same logical schemas** as JSON — `spec/v1/json-schema/` remains the single
definition of what a document means. This file defines only how those documents are serialized.

Required for the `minimal` profile, recommended for `constrained`, optional for `full`.

## Why not JSON down here

A LoRaWAN payload is 51–242 bytes depending on data rate and region. A JSON check-in with string
keys and an ISO-8601 timestamp does not fit, and base64 costs a further 33% on any binary field.

Measured on the reference fixture:

| form | bytes |
|---|---|
| JSON | 285 |
| CBOR, string keys | 223 |
| CBOR, integer keys | **71** |

Note where the saving actually is. CBOR alone takes off ~22%; the **integer key map does ~68% of
what remains**. An implementation that adopts CBOR and stops there gets roughly a fifth of the
available benefit — and still does not fit a slow-rate frame. Apply the key map.

## Frame layout

A check-in frame on the wire is the CBOR document followed by its MAC:

```
+-------------------------------+----------------------+
|  canonical CBOR document      |  HMAC-SHA256 (32 B)  |
+-------------------------------+----------------------+
```

The MAC is **appended**, not carried as a `signature` field inside the document, for two reasons.
A signature cannot cover itself, so embedding one requires either encoding twice or reserving and
back-patching — both of which need a second buffer the size of the message on a device that has
no spare RAM. Appending also lets a receiver authenticate before parsing, so a spoofed peer never
reaches the decoder.

The MAC covers a domain separator followed by the document bytes:

| direction | separator |
|---|---|
| device → hub | `qc1u` |
| hub → device | `qc1d` |

Both directions are authenticated with the same symmetric key, so without separation a downlink
could be replayed back as an uplink whenever the two happen to encode identically.

Documents on the HTTP and MQTT planes carry `signature` as a field instead, since those profiles
have the memory to encode twice and gain interoperability with generic JSON tooling by doing so.

## Rules

1. **Canonical form.** Encoders MUST use CBOR Core Deterministic Encoding (RFC 8949 §4.2.1):
   shortest-form integers, definite-length containers, keys sorted in bytewise lexicographic order of
   their encoded form. Signatures are computed over the canonical encoding, so a non-deterministic
   encoder produces signatures that fail to verify at the far end for no visible reason.

2. **Binary is binary.** A `mediaPayload` with `data_encoding: "raw"` puts the bytes in a CBOR byte
   string (major type 2). Base64 in CBOR is permitted for JSON round-tripping but wastes the format's
   only real advantage.

3. **Timestamps.** Absolute times MAY be encoded as a CBOR epoch tag (tag 1, RFC 8949 §3.4.2) instead
   of an RFC 3339 string. This saves ~18 bytes per timestamp. A decoder MUST accept both.

4. **Unknown keys are preserved, not dropped.** Same forward-compatibility rule as JSON: a receiver
   tolerates fields it does not recognize. A gateway transcoding CBOR to JSON MUST NOT silently
   discard them.

5. **Round-trip fidelity is normative.** `conformance/cases/encoding/` pairs each JSON document with
   its CBOR equivalent. `decode(cbor) == decode(json)` MUST hold for every case. This is what lets a
   C sensor and a Python hub disagree about serialization without disagreeing about meaning.

## Integer key map

String keys are the largest avoidable cost in a small document. On the `minimal` profile, encoders
MUST use these integer keys; on other profiles they MAY.

The map is **append-only and frozen for v1** — a number is never reused for a different field, since
that would silently change the meaning of documents from older devices still in the field.

### Check-in uplink

| key | field |
|---|---|
| 1 | `schema_version` |
| 2 | `node_id` |
| 3 | `tenant_id` |
| 4 | `wake_counter` |
| 5 | `events` |
| 6 | `power` |
| 7 | `config_version` |
| 8 | `ack` |
| 9 | `grant` |
| 10 | `signature` |

### Check-in downlink

| key | field |
|---|---|
| 1 | `schema_version` |
| 2 | `server_time` |
| 3 | `accepted` |
| 4 | `commands` |
| 5 | `config` |
| 6 | `next_checkin_s` |
| 7 | `signature` |

### Edge event

| key | field |
|---|---|
| 1 | `schema_version` |
| 2 | `event_id` |
| 3 | `source_node_id` |
| 4 | `tenant_id` |
| 5 | `timestamp` |
| 6 | `relative_time` |
| 7 | `trigger` |
| 8 | `confidence` |
| 9 | `payload` |
| 10 | `task` |
| 11 | `metadata` |
| 12 | `power` |
| 13 | `signature` |
| 14 | `hub_received_at` |

### Command

| key | field |
|---|---|
| 1 | `schema_version` |
| 2 | `command_id` |
| 3 | `issuer_id` |
| 4 | `target_id` |
| 5 | `tenant_id` |
| 6 | `action` |
| 7 | `parameters` |
| 8 | `issued_at` |
| 9 | `expires_at` |
| 10 | `signature` |

### Nested objects

`power`, `relative_time`, `task`, and `signature` use their own maps, scoped to the parent:

| key | `power` | `relative_time` | `task` | `signature` |
|---|---|---|---|---|
| 1 | `battery_pct` | `wake_counter` | `complexity` | `alg` |
| 2 | `on_mains` | `ms_since_wake` | `urgency` | `key_id` |
| 3 | `thermal_headroom_c` | `uncertainty_s` | `privacy` | `value` |
| 4 | `duty_cycle_s` | — | `use_case` | — |
| 5 | — | — | `deadline_ms` | — |
| 6 | — | — | `remaining_ms` | — |
| 7 | — | — | `hops` | — |

## Fields a `minimal` device may omit

Permitted **only** on the `minimal` profile, because both values are fixed at commissioning and the
hub reconstructs them from the device record before schema validation:

- `tenant_id` — implied by the PSK the hub authenticated against
- `schema_version` — implied by the commissioned contract

Measured on the reference fixture, dropping both takes a check-in from 71 to **61 bytes**. With
integer keys already applied the field *names* cost nothing, so the saving is just the values —
smaller than it looks, and worth taking only on the slowest data rates. That fixture carries no
`tenant_id`, so the 10 bytes are `schema_version` alone, dropped once from the check-in and once
from the event nested inside it.

A hub MUST reject these omissions from any device not commissioned on the `minimal` profile.
