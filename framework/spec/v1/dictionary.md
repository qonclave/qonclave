# Qonclave Global Dictionary — v1

The enumerated values used across every plane. `COMMUNICATION.md` carries these as inline `//`
comments inside its JSON examples; this file is the normative list, and the schemas in
`json-schema/` are generated against it.

Values marked **closed** are exhaustive — a receiver MUST reject anything else. Values marked
**open** are conventions: the framework does not interpret them, applications define their own.

---

## `node_type` — closed

| value | meaning |
|---|---|
| `edge` | senses, triages, actuates. Never reaches the internet directly |
| `hub` | orchestrates, applies Policy, brokers authorization, only node with egress |
| `compute` | stateless inference. Optional — a hub may serve its own |
| `archive` | long-term storage, per-tenant. Optional |

## `profile` — closed

| value | meaning |
|---|---|
| `full` | OS-class node, any role. See `profiles/full.md` |
| `constrained` | MCU-class edge, awake. See `profiles/constrained.md` |
| `minimal` | duty-cycled edge. See `profiles/minimal.md` |

## `complexity` — closed, **ordered**

Ordering is significant: a node advertising `max_complexity: detect` cannot serve a `vlm_reason`
task, and placement filters on this before a policy chooses.

| rank | value | meaning |
|---|---|---|
| 0 | `heuristic` | threshold, motion delta, no model |
| 1 | `detect` | bounding boxes, small CNN |
| 2 | `classify` | label from a fixed set |
| 3 | `embed` | feature vector — face ID, similarity |
| 4 | `vlm_reason` | vision-language reasoning over an image |
| 5 | `llm_reason` | text reasoning, planning, summarization |

## `urgency` — closed

| value | meaning |
|---|---|
| `background` | may be deferred or batched |
| `normal` | default |
| `high` | preempt background work |
| `critical` | safety-relevant; latency dominates every other consideration |

## `privacy` — closed

Enforced by the framework, not by the placement policy. A policy that returns a denied tier is
overridden — the isolation guarantee in `SECURITY.md` §2 cannot depend on every app author getting
it right.

| value | effect |
|---|---|
| `unrestricted` | any tier |
| `no_egress` | MUST NOT reach a shared multi-tenant compute node |
| `local_only` | MUST NOT leave the originating device |

## `data_encoding` — closed

| value | meaning |
|---|---|
| `base64` | RFC 4648 §4 with padding. The only option in JSON |
| `raw` | CBOR byte string. Invalid in JSON |

## `signature.alg` — closed

| value | used by |
|---|---|
| `ed25519` | default for node identity and grants |
| `es256` | where a secure element mandates P-256 |
| `hs256` | PSK-derived symmetric, for `constrained` and `minimal` |

## `scope` — closed

Operations a capability grant may authorize.

| value | permits |
|---|---|
| `post_events` | send events to the audience |
| `checkin` | perform the duty-cycle exchange |
| `stream` | open a Direct-Bind media stream |
| `recognize` | call per-track identification |
| `infer` | submit an inference task |
| `subscribe_commands` | receive pushed commands |

## `trigger` — **open**

Application-defined. The framework routes on it but never interprets it. Conventional values, worth
reusing so dashboards and archives stay comparable across apps:

`motion_detected` · `person_detected` · `sound_detected` · `threshold_crossed` ·
`door_opened` · `fall_detected` · `heartbeat` · `manual`

## `action` — **open**

Application-defined command verbs. Conventional values:

`lock_door` · `unlock_door` · `navigate_to` · `dispatch` · `set_led` · `capture_frame` ·
`set_threshold` · `sleep_until`

---

## Reserved prefixes

`qonclave_*` on any field name, and `_*` on any metadata key, are reserved for the framework.
Applications MUST NOT introduce fields with these prefixes — a future spec version may define them,
and a collision would change the meaning of documents already in the field.
