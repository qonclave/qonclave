# Qonclave Conformance Profiles — v1

Qonclave runs on a Snapdragon X laptop and on a coin-cell sensor that wakes once a day. Those two
devices cannot implement the same protocol surface, and pretending otherwise produces the usual
outcome: small devices claim partial compliance against a spec written for Linux, and nobody can say
whether a given sensor is conformant.

So conformance is **tiered**. A node declares its profile in its manifest (`profile` field) or at
commissioning, and is judged only against that profile's obligations.

| | [full](full.md) | [constrained](constrained.md) | [minimal](minimal.md) |
|---|---|---|---|
| **Target** | Linux/Windows: hub, compute, archive, Linux-class edge | ESP32, RTOS, always-on-ish MCU | coin cell, LoRa, duty-cycled sensor |
| **Roles** | any | edge only | edge only |
| **Discovery** | REQUIRED (mDNS + UDP) | OPTIONAL — persisted endpoint first | **FORBIDDEN** — endpoint fixed at commissioning |
| **Transport** | HTTP, MQTT, gRPC; persistent connections | HTTP or CoAP; short-lived | any link; one exchange |
| **Crypto** | mTLS + signed payloads | PSK + signed payloads | PSK + signed payloads |
| **Encoding** | JSON | JSON or CBOR | **CBOR** |
| **Ingestion** | `POST /api/v1/events` or MQTT publish | events or check-in | **check-in only** |
| **Commands** | MQTT subscribe (push) | subscribe or mailbox drain | **mailbox drain (pull)** |
| **Time** | own clock, absolute `timestamp` | own clock, absolute `timestamp` | `relative_time` + hub stamping |
| **Media payload** | yes | yes | normally none |
| **Placement** | full `PlacementPolicy` | callback | usually static |

## How to read the obligation keywords

`MUST` / `MUST NOT` / `REQUIRED` / `SHOULD` / `MAY` / `OPTIONAL` are used as in RFC 2119.

## Why the split falls where it does

The boundary between `full` and `constrained` is **whether the device can hold a connection**. A
device that can keep an MQTT subscription open receives commands by push; one that cannot must pull.

The boundary between `constrained` and `minimal` is **whether the device is awake**. An always-on
ESP32 can afford discovery, a TLS-ish handshake, and several round trips. A device that wakes for
200 ms once a day can afford exactly one exchange, and every byte of downlink costs radio-on time it
must pay for out of a battery that has to last years.

## Proving conformance

Run the fixtures in [`../../../conformance/`](../../../conformance/) with your profile selected. A
profile only requires the case directories it declares; a `minimal` implementation is not expected to
pass discovery cases, and is not penalized for it.

```
qonclave conformance --profile minimal --cases framework/conformance/cases
```

The fixtures are language-neutral JSON and CBOR. An implementation that links none of our code — the
expected situation on the smallest devices — proves conformance the same way ours does.
