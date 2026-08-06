# Qonclave C SDK

The `constrained` and `minimal` profiles, in C99. This is the binding for devices that will never
run Python: an ESP32, a Zephyr node, a LoRa sensor on a coin cell.

## Status

| | |
|---|---|
| `cbor.c` | **implemented** — canonical writer/reader, shortest-form heads and floats, RFC 3339 |
| `codec_cbor.c` | **implemented** — check-in encode/decode with the integer key map |
| `sha256_*.c`, `psk.c` | **implemented** — pluggable SHA-256, HMAC, domain-separated signing, constant-time compare |
| `checkin.c` | **implemented** — the full exchange: encode, sign, one round trip, verify, decode |
| `placement.c` | **implemented** — mirrors the Python ladder including `deny` enforcement |
| `event.c`, `command.c` | **implemented** — struct helpers and the command-expiry rule |
| `ports/posix` | clock and a settable test handler; NVS is in-memory, **not** persistence |
| your platform | **yours** — three functions against `include/qonclave/port.h` |

The edge path is complete and tested on the host. What remains for a real device is a port.

The headers were written before any of it, on purpose — they are what the spec, the Python
binding, and the conformance fixtures all have to agree with. Implementing behind a settled header
is straightforward; discovering the header was wrong afterwards is not.

## Cross-language interop

`qc_conformance` builds the documented fixture from a C struct, encodes it canonically, and writes
the bytes to `conformance/generated/`. Python's `test_interop.py` decodes those bytes and asserts
they mean the same thing as the JSON fixture.

That direction is the one that carries real traffic, and a disagreement there drops sensor data
silently — the device believes it reported and the hub never knew. Schema validation cannot catch
it; only a second implementation can.

Current numbers: C encodes the minimal check-in to **63 bytes**, Python to **71**. The gap is not
a disagreement, and it is worth knowing exactly what it is:

| | |
|---|---|
| −10 | C omits `schema_version` from the check-in and its nested event. A `minimal`-only allowance (`spec/v1/encodings/cbor.md`), not a free choice — a `constrained` device sending this would be non-conformant |
| +2 | C types the moisture reading as a text string where Python has an integer. The schema permits either, and neither side does anything different with it |

`test_interop.py` pins that accounting rather than allowing a size band, so a new field appearing
in one binding and not the other fails the build instead of being absorbed by a tolerance.

## Scope

Edge only. No hub, no compute server, no archive — those are `full`-profile roles and live in
`../python/`.

That is "today", not by definition. `COMMUNICATION.md` §3 explicitly invites *"a new Compute Node
in C++"*, so this is laid out to gain a `compute/` next to the edge code rather than being named
`sdk/c-edge/` and needing to move when someone does.

## Building

```bash
# host build and tests
cmake -S . -B build
cmake --build build
ctest --test-dir build --output-on-failure

# for a real device: supply your own port
cmake -S . -B build-dev -DQONCLAVE_PROFILE=minimal \
      -DQONCLAVE_PORT_SOURCE=$PWD/my_port.c
```

On a multi-config generator (Visual Studio), `ctest` needs the configuration:
`ctest --test-dir build -C Debug --output-on-failure`.

All five tests pass: `placement`, `codec`, `psk`, `checkin`, `conformance`.

Run `ctest` before the Python suite — `conformance` emits the interop artifact that Python's
cross-language tests consume, and they skip without it.

Selecting `minimal` compiles discovery out entirely. That is not an optimization —
`spec/v1/profiles/minimal.md` forbids discovery on that profile, because an mDNS browse costs more
radio time than the device's whole useful exchange.

## Porting

A port supplies three functions. Keeping the surface this small is what makes a new platform a
day of work rather than a fork:

| | |
|---|---|
| `qc_port_request()` | one network round trip |
| `qc_port_nvs_read/write()` | non-volatile storage for the spool and wake counter |
| `qc_port_now_ms()` | monotonic milliseconds |

Write them in one file and point CMake at it:

```bash
cmake -S . -B build -DQONCLAVE_PORT_SOURCE=$PWD/my_esp32_port.c
```

**We ship exactly one port — `posix` — and only because the library has to be testable without
hardware.** There is no bundled ESP32 or Zephyr port, deliberately. We cannot build or test them
here, so they would rot, and an empty stub returning `-1` is worse than nothing: it invites you to
link against it and wonder why the hub never hears from your device.

Three notes from writing the posix one:

- `qc_port_nvs_*` must survive a **power cycle**, not merely deep sleep. The wake counter is the
  hub's replay guard, so a device whose count restarts gets rejected and needs re-commissioning.
  The posix port keeps NVS in memory, which exercises the logic but is *not* persistence.
- `qc_port_request` should honor its timeout. A placement deadline is meaningless if the transport
  below it can block indefinitely.
- Return negative on failure rather than blocking or retrying. The spool already holds the event,
  and the next wake is a duty cycle away.

## SHA-256 backend

Pluggable, for the same reason the port is:

```bash
cmake -S . -B build                                        # builtin (default)
cmake -S . -B build -DQONCLAVE_SHA256_BACKEND=mbedtls      # hardware-accelerated on ESP32
cmake -S . -B build -DQONCLAVE_SHA256_SOURCE=my_sha256.c   # your own
```

| backend | when |
|---|---|
| `builtin` | default. FIPS 180-4 in ~150 lines, no dependencies, so host builds and CI need nothing installed |
| `mbedtls` | **prefer on real hardware.** ESP-IDF already links mbedTLS and routes SHA-256 through the ESP32's SHA peripheral, so this is faster *and* not a new dependency there. Zephyr ships it too |
| custom | implement the three streaming functions in `src/sha256.h` |

The builtin backend is pure software. A sensor that hashes on every wake for five years pays that
difference in battery, which is why the default is a build-time convenience rather than a
recommendation for deployment.

**Only the three streaming functions vary.** HMAC and the constant-time compare live in
`sha256_common.c` and are built once — a backend supplying its own HMAC would be a second
implementation that could disagree about RFC 2104's oversized-key rule with nothing to catch it.
`qc_ct_equal` in particular stays ours, because it is a protocol requirement rather than a crypto
primitive.

Swapping backends cannot change the wire: every backend produces identical digests, so a device
using its accelerator and a hub using the bundled software still agree on every MAC. `test_psk.c`
runs the full FIPS and RFC 4231 vector set against whichever backend is compiled in.

## Design constraints

**No malloc.** Every buffer is caller-provided. A device with 40KB of usable RAM cannot afford
heap fragmentation across a multi-year uptime, and an allocation failure three days into a
deployment is not a recoverable condition.

**Deterministic CBOR.** Signatures cover the encoded bytes, so a non-deterministic encoder
produces signatures the hub rejects for no visible reason. RFC 8949 §4.2.1, not optional.

**Explicit errors over truncation.** `qc_checkin_encode` returns `QC_ERR_BUFFER_TOO_SMALL` rather
than emitting a short message. A truncated uplink fails as a signature error at the far end, which
is far harder to diagnose in the field than a local return code.

**Expired commands are dropped in the decoder.** A device that wakes to a day-old *"unlock the
door"* and executes it is a security failure, not a late delivery — so the check lives below the
application rather than being something each firmware author must remember.

## Proving conformance

```bash
ctest --test-dir build --output-on-failure     # runs qc_conformance among the rest
```

The fixtures are JSON, and this binding has no JSON parser and should not grow one — a parser is
exactly the dependency the `minimal` profile exists to avoid. So the cross-check runs the other
way: `qc_conformance` builds the documented fixture from a C struct, encodes it canonically, and
writes the bytes to `../../conformance/generated/`. Python's `test_interop.py` then decodes those
bytes and asserts they mean the same thing as the JSON fixture.

That direction is also the one that matters operationally. Device-to-hub is the traffic that
exists, and a disagreement there drops sensor data silently — the device believes it reported and
the hub never knew.

Run `ctest` **before** the Python suite. Without the emitted artifact the cross-language tests
skip rather than fail, which would hide exactly the problem they exist to catch. CI enforces the
ordering and fails if the artifact is missing.
