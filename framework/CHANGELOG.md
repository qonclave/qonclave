# Changelog

All notable changes to the Qonclave framework. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the wire spec is versioned separately
and independently under `spec/v1/`.

## [Unreleased]

### Added

- **`spec/v1/`** — the normative wire contract. JSON Schemas for every plane, `compute.proto`,
  AsyncAPI for the MQTT topics, OpenAPI for the hub, and the global value dictionary. Extracted
  from `docs/COMMUNICATION.md`, which carried these as prose and inline JSON examples.
- **Device profiles** (`spec/v1/profiles/`) — `full`, `constrained`, `minimal`. Conformance is now
  tiered, so an ESP32 has a defined obligation set rather than partial compliance against a spec
  written for Linux.
- **CBOR encoding** (`spec/v1/encodings/cbor.md`) with a frozen integer key map, implemented in
  both bindings. A no-media check-in encodes to 71 bytes (61 with identity omitted) and fits a
  LoRaWAN frame; the JSON form is 285 and does not.
- **The check-in exchange** (`checkin.schema.json`) — one round trip carrying events, mailbox
  commands, config delta, and authoritative time. The entire network interaction of a duty-cycled
  device.
- **Capability grants** (`capability-grant.schema.json`, `security/capability.py`) — generalizes
  the Direct-Bind broker to three audiences, adding edge-to-peer-hub. Verified offline against a
  pinned issuer root.
- **Placement** (`qonclave.placement`) — per-request tier selection with a `PlacementPolicy` ABC,
  fallback chains, framework-enforced privacy denials, and deadline accounting across hops.
- **`conformance/`** — 18 language-neutral fixtures every binding must pass.
- **`sdk/python/`** — the `full`-profile binding. Models, codecs, the complete placement ladder,
  capability grants, and the role contracts (`Policy`, `Transport`, `ModelBackend`,
  `PlacementPolicy`). The I/O behind those contracts — transports, discovery, the hub server, the
  edge agent — is **not implemented yet**; `sdk/python/README.md` has the per-package status.
- **`sdk/c/`** — the constrained binding: CBOR codec, SHA-256/HMAC, the check-in exchange, and
  placement. Complete and tested on the host; a real device supplies its own three-function port.
  This is the binding a duty-cycled device uses, and it is the more finished of the two.
- **`qonclave.hub.events.EventStore.note_node()`** — update the latest-seen node without recording
  a full event. `hub/framework/events.py`'s `note_device()` needs this: a `/track/analyze` sample
  has no full edge event to record, only a device id worth remembering. Added while merging
  `hub/`'s independent feature work back into the branch that lifted `events.py` onto `EventStore`
  — without it, `note_device()` referenced module-level state that no longer existed post-lift, a
  silent `NameError` waiting on the first `/track/analyze` or `/edge/investigation` call.

### Changed

- Framework documentation moved from `framework/*.md` to `framework/docs/`, with assets
  consolidated under `docs/assets/`.
- `docs/SECURITY.md` image paths corrected — they used `../SECURITY_assets/`, one level too high,
  so both diagrams rendered broken on GitHub.
- **`docs/ARCHITECTURE.md`** — new §4 on placement. The Tier 1 / Tier 2+ language in §1 read as
  though tiers were fixed properties of a role; they are per-request decisions. Direct-Bind in §3
  gained its third target, a peer Hub.
- **`docs/SECURITY.md`** — new §5 on capability grants: the three audiences, offline verification
  against a pinned issuer root, federation, and revocation. §2 now states where multi-tenant
  isolation is actually enforced (deny-by-default on the grant, plus the framework overriding a
  placement policy that would send `no_egress` work to shared compute).
- **`docs/COMMUNICATION.md`** — new §5.1 generalizing Direct-Bind to brokered access, and new §9
  for the check-in plane, which is an eighth plane the original seven did not anticipate. The
  header now points at `spec/v1/` as normative and notes the CBOR binding.
- **`docs/VISION.md`** — asynchronous sleep-queues and the authorization half of self-healing
  handoff are no longer roadmap items; both now point at the schema that implements them.

### Notes

`edge/` is still untouched — nothing under it imports `qonclave.*` yet. `hub/` is no longer fully
untouched: `hub/framework/adapter.py`, `events.py`, and `transport.py` are thin shims over this
SDK today. `hub/framework/policy.py` was lifted the same way and then reverted while merging
`hub/`'s own feature work (2026-08-06) — `docs/CONVENTIONS.md`'s "Where existing code lands"
section is the current, maintained status of every module, including that revert and what redoing
it correctly requires. Pointing the rest of `hub/server.py` at `qonclave.hub` remains a separate,
later change.
