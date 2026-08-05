# Device Profiles

Qonclave runs on a Snapdragon X laptop and on a coin-cell sensor that wakes once a day. Those
devices cannot implement the same protocol surface, and pretending otherwise produces the usual
outcome: small devices claim partial compliance against a spec written for Linux, and nobody can
say whether a given sensor is conformant.

So conformance is **tiered**. The normative definitions live in
[`../spec/v1/profiles/`](../spec/v1/profiles/); this page is the orientation.

| | full | constrained | minimal |
|---|---|---|---|
| **Target** | hub, compute, archive, Linux edge | ESP32, RTOS | coin cell, LoRa, daily wake |
| **Roles** | any | edge only | edge only |
| **Discovery** | required | optional | **forbidden** |
| **Crypto** | mTLS | PSK | PSK |
| **Encoding** | JSON | JSON or CBOR | **CBOR** |
| **Commands** | MQTT push | push or pull | **pull only** |
| **Time** | own clock | own clock | hub-stamped |
| **Round trips** | unbounded | few | **one** |

## Where the boundaries fall, and why

**full ↔ constrained: can the device hold a connection?** One that can keeps an MQTT subscription
and receives commands by push. One that cannot must pull, which means the hub needs a mailbox.

**constrained ↔ minimal: is the device awake?** An always-on ESP32 can afford discovery, a
handshake, and several round trips. A device that wakes for 200 ms once a day can afford exactly
one exchange, and every downlink byte is radio-on time paid for from a battery that must last
years.

## Consequences worth knowing before you design against a profile

A `minimal` device has **no push commands**. Worst-case command latency is one wake interval — a
daily sensor cannot be told to do something *now*. It also has no federation: it talks to the hub
that commissioned it, and moving it is a re-commissioning operation rather than a runtime grant.

If an application needs either, it needs `constrained` and a device that can stay awake.

## Proving conformance

```bash
qonclave conformance --profile minimal --cases ../conformance/cases
```

A profile only requires the case categories it declares. A `minimal` implementation is not
expected to pass grant fixtures and is not penalized for skipping them.
