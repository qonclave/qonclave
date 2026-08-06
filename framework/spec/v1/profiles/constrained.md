# Profile: `constrained`

**Target.** An MCU-class device that is powered or frequently awake: an ESP32 on mains or a large
battery, a Zephyr/FreeRTOS node, an Arduino UNO Q's microcontroller side. It can hold a socket open
for a while and can afford a handshake, but it cannot run a full TLS stack comfortably, cannot
allocate freely, and has flash measured in hundreds of kilobytes.

This is the middle profile: more capable than a coin cell, but not a Linux box.

---

## Obligations

### MUST

- Implement edge ingestion by **either** `POST /api/v1/events` (or its CoAP equivalent) **or** the
  check-in exchange. Check-in is RECOMMENDED even here — it halves the round trips and costs nothing
  when the device is awake anyway.
- Support **JSON**; CBOR is RECOMMENDED.
- Authenticate with a PSK established at commissioning and sign payloads
  (`common.schema.json#/$defs/signature`). mTLS is permitted where the platform supports it.
- Send absolute `timestamp` on events. A device on this profile is expected to have a usable clock,
  synchronized at least once per power cycle.
- Honor `expires_at` on commands.
- Persist its spool across reboots.

### SHOULD

- Prefer the endpoint persisted at commissioning, and fall back to discovery only when that endpoint
  fails. Discovery is a recovery path, not the happy path — a device that browses mDNS on every boot
  wastes seconds of radio time to learn something it already knew.
- Report `power` in its manifest and events, so hub-side placement can avoid handing work to a node
  running on a depleting battery.
- Implement the placement callback (`placement.h`). Even a trivial implementation is worth having,
  because it puts the decision in one auditable place instead of scattering thresholds through the
  application.

### MAY

- Advertise a discovery manifest and be discovered by hubs.
- Hold an MQTT subscription for pushed commands. A device that does this gets command latency
  measured in milliseconds instead of one check-in interval — the main practical reason to choose
  this profile over `minimal`.
- Attach media payloads.
- Present a capability grant to a foreign hub (`capability-grant.schema.json`). This is the smallest
  profile that supports federation, because it requires the device to hold and present a credential
  it did not originate.

### MUST NOT

- Promote itself to a role other than `edge`.
- Assume it can complete an unbounded number of round trips. Even on mains, an ESP32 sharing a
  channel with fifty peers is bandwidth-constrained; the design pressure toward few exchanges does
  not disappear just because the battery did.

---

## Crypto

The reason this profile does not simply require mTLS: a full handshake on an ESP32 costs seconds of
wall time and hundreds of kilobytes of RAM — enough that a device doing it on every connection
spends more energy on key exchange than on its actual job. `SECURITY.md` §3 already anticipates this
for the constrained class: commission out-of-band, derive a session key from the PSK, sign the
payload.

Where the platform has hardware crypto acceleration and RAM to spare, mTLS is strictly better and is
permitted. The profile requires a floor, not a ceiling.

---

## Relationship to the other profiles

Moving **up** to `full` means gaining: role promotion, hub election, WebRTC/Direct-Bind, gRPC compute
offload, and the ability to host a Policy.

Moving **down** to `minimal` means losing: discovery, push commands, media payloads, federation, and
JSON — in exchange for an interaction that fits in one LoRa frame and a battery that lasts years.

A device that is *sometimes* asleep for long periods and *sometimes* awake should implement
`constrained` and simply use check-in while sleeping. The check-in exchange is valid on both
profiles precisely so this case doesn't require two implementations.
