# Qonclave Python SDK

The `full` profile: all four node roles, on anything that runs CPython 3.10+. This is the binding
for a Snapdragon hub, a Raspberry Pi edge, a compute server, or a laptop running the whole mesh by
itself.

Devices that cannot run Python use [`../c/`](../c/) or implement
[`spec/v1/`](../../spec/v1/) directly.

## Status

**The contracts are implemented; most of the plumbing behind them is not.** This binding is at the
stage where the interfaces are settled and testable and the I/O is not yet written. That is a
deliberate order — the ABCs are what the spec, the C binding, and the conformance fixtures all have
to agree with, and discovering an interface was wrong after four subsystems depend on it is far
more expensive than discovering it now.

| package | | |
|---|---|---|
| `core/` | **implemented** | models for every schema, JSON + CBOR codecs with the integer key map, enums, ids |
| `placement/` | **implemented** | the whole ladder: `PlacementPolicy`, `DefaultPlacement`, tier probing, fallback chains, framework-enforced `deny` |
| `security/` | **partial** | `capability.py` (mint/verify grants, offline) and `signing.py` (canonicalization) are real. Identity, CA, mTLS, PSK, commissioning, federation, tenancy, at-rest are **not** |
| `inference/` | **partial** | `ModelBackend` ABC, `MockBackend`, and `resolve()` are real. `RemoteBackend` and the geniex/onnx backends are **not** |
| `transport/` | **contract only** | `Transport` / `PubSubTransport` ABCs. No HTTP, MQTT, CoAP, gRPC, or serial implementation |
| `hub/` | **contract only** | `Policy` / `Verdict` / `Notification` — the app contract. No app, ingest, router, mailbox, broker, or egress |
| `edge/` | **not implemented** | agent, triage, spool, sensors, actuators, hub client |
| `discovery/` | **not implemented** | announce, browse, health, election, mDNS/UDP backends |
| `storage/` `compute/` `archive/` | **not implemented** | `RecordStore` is not defined yet |
| `cli.py` | **implemented** | `spec-validate`, `conformance`, `placement-explain`, `doctor` |

Modules that are not implemented exist as files with a docstring stating their responsibility and
the spec section they will implement. They are placeholders, not stubs — nothing imports them and
nothing returns a fake success. An empty module that fails at import is a better outcome than one
that pretends to enqueue a command.

**What this means in practice:** you can write a `PlacementPolicy` or a `Policy` today and unit-test
it against real models and real placement. You cannot yet `qonclave run hub` and have a server.

## Install

```bash
pip install -e ".[hub]"      # orchestrator
pip install -e ".[edge]"     # sensing node — no Flask, no broker, no model runtime
pip install -e ".[dev]"      # tests
```

Extras are per-role because the layering rule has to have teeth: `qonclave[edge]` must never pull
Flask, and both `tests/test_layering.py` and CI check that it doesn't.

## Test

```bash
python -m pytest tests -v
```

| | |
|---|---|
| `test_layering.py` | parses imports and enforces the [dependency ladder](../../CONVENTIONS.md), including that role packages never import each other |
| `test_spec_conformance.py` | every model against its JSON Schema, and the fixtures in [`../../conformance/`](../../conformance/) |
| `test_placement.py` | the ladder mechanism — fallback, `deny` enforcement, deadline accounting |
| `test_interop.py` | decodes CBOR emitted by the **C** binding and checks it means the same thing |

`test_interop.py` skips unless the C SDK has been built, because the artifact it reads is produced
by `qc_conformance`. Build it first:

```bash
cmake -S ../c -B build && cmake --build build && ctest --test-dir build
```

Skipping rather than failing is right for a missing toolchain, but it also means a silent skip
would hide a real wire disagreement — so CI builds the C SDK first and fails if the artifact is
absent.

## Layout

```
core/         models, codecs, enums, ids        imports nothing from qonclave
transport/    Transport ABC + implementations
security/     grants, signing, identity, PSK
discovery/    peers, liveness, election
placement/    WHICH tier runs a task
inference/    ModelBackend + resolve()          storage/  RecordStore
edge/ hub/ compute/ archive/                    roles — never import each other
cli.py
```

The order is the dependency ladder from [`../../CONVENTIONS.md`](../../CONVENTIONS.md), and it is
enforced by a test rather than by review. It is also why `inference/` and `storage/` sit *below*
the roles: a hub doing its own VLM work must not have to import `qonclave.compute`, or "optional"
would not be true.
