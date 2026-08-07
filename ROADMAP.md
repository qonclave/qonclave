# Roadmap

This is an honest snapshot of what's real versus what's a placeholder in `qonclave` today —
not a marketing roadmap. Every "not started" item below names an actual stub file already
shipping in `framework/sdk/python/src/qonclave/` (7-15 line docstring, no logic) — read the
source, it's the same list. See [`framework/docs/CONVENTIONS.md`](framework/docs/CONVENTIONS.md)
for the fuller "where existing code lands" picture and [`ARCHITECTURE.md`](framework/docs/ARCHITECTURE.md)
for the four-role design (Edge / Hub / Compute / Archive) all of this fills in.

Contributions on any item below are welcome — see [`CONTRIBUTING.md`](CONTRIBUTING.md). Items
marked 🟢 are a reasonable entry point if you're new to the codebase (self-contained, existing
tests to model, no cross-module coordination needed); items marked 🔴 touch more of the system
and are worth opening an issue to discuss shape before diving in.

## What's real today

- **`core/`, `placement/`, `cli.py`** — genuinely done and tested.
- **`security/capability.py`** (grant verification), **`security/signing.py`** (canonical
  JSON/CBOR signing) — real, conformance-tested.
- **`discovery/announce.py` + `backends/udp.py`, `discovery/registry.py`** — real, migrated
  from the reference hub.
- **`inference/local/geniex.py`, `inference/local/mock.py`** — real; the mock backend is what
  makes "run the framework with no hardware" possible (see `DEVELOPER_GUIDE.md`).
- **The C SDK** (`framework/sdk/c/`) — cbor/sha256/psk/checkin/placement/event/command all
  implemented and host-tested. Ahead of the Python SDK in several places.

## Near-term

- 🟢 **Implement `GET /api/v1/capabilities`** in the reference hub (`examples/hub/framework/server.py`
  currently answers `501`). The node-manifest shape is already spec'd
  (`spec/v1/json-schema/node-manifest.schema.json`); the discovery broadcaster already produces
  something close to it. A contained, well-scoped first PR.
- 🟢 **API reference generation** (mkdocs + mkdocstrings, or Sphinx) for the Python SDK — no
  generated reference exists yet; docstrings are already written throughout the SDK, so this is
  mostly configuration, not new writing.
- 🔴 **`GET /api/v1/checkin`, `POST /api/v1/grants`** — same 501 pattern; `grants` depends on
  `hub/broker.py` below existing first.

## Edge role — largely unimplemented at the SDK level

`examples/edge` imports nothing from `qonclave.edge.*` today — it's a fully standalone
implementation that predates the SDK. Every file in `qonclave/edge/` is a 5-12 line stub:
`agent.py`, `sensors.py`, `actuators.py`, `triage.py`, `spool.py`, `hub_client.py`. Lifting the
reference edge app onto real `qonclave.edge` modules (mirroring what already happened for the
hub's `ingest`/`events`/`policy`) is the single highest-leverage edge-side project — see
`CONVENTIONS.md`'s migration pattern before starting: implement the SDK module *and* update the
call site in the same change, never let the contract and its only caller diverge.

## Multi-hub federation

Placement already models a trusted peer hub as just another placement candidate
(`placement/tiers.py`'s `Candidate.is_peer`, tested), and grant verification already covers the
`peer-hub` audience (conformance fixtures in `conformance/cases/grant/valid-peer-hub`). What's
missing is everything around that: 🔴 `discovery/peers.py`, `discovery/browse.py`,
`discovery/election.py` (hub role promotion/demotion, `ARCHITECTURE.md` §3), `hub/broker.py`
(mints capability grants — verification exists, nothing issues one yet), `edge/hub_client.py`'s
foreign-hub failover mode, and the trust-establishment layer: `security/ca.py`,
`security/commissioning.py`, `security/mtls.py`, `security/federation.py`, `security/tenancy.py`.
(`security/at_rest.py` — encryption at rest + cryptographic erasure — is a related but separate
compliance concern, `SECURITY.md` §4 rather than the federation §3/§5 machinery above.) See
`framework/docs/SECURITY.md`'s "Implementation Status" section for exactly what each of these is
supposed to do. This is a multi-PR effort, not a single one — open an issue first if you want to
take on a piece of it.

## Storage, Archive, Compute roles

The least mature of the four roles — almost entirely stub: `storage/{local,remote,s3,store}.py`,
`archive/{retention,audit,erasure,server}.py`, `compute/{sandbox,server}.py`. These are the two
roles `ARCHITECTURE.md` describes as always-optional (a deployment works without them), which is
exactly why they're last — nothing else in the framework blocks on them existing.

## Transport backends

`transport/mqtt` was tried as a real SDK module, then deliberately reverted — `paho` stays
hub-owned (see `CONVENTIONS.md`'s postmortem on this). `transport/{grpc,coap,serial,http}.py` and
`transport/backpressure.py` remain 8-9 line stubs; nothing currently depends on any of them.

## Release process

- [ ] Tag `v0.1.0-alpha`; state clearly in the README that APIs may still change pre-1.0.
  Strict SemVer after that — apps subclass `framework.policy.Policy` directly, so a breaking
  change breaks every downstream app.
- [ ] Publish the Python SDK to PyPI with a repeatable release workflow.
- [x] C SDK distribution: **vendoring for now** (decided) — copy `sdk/c/` or add it as a git
  submodule. A PlatformIO/ESP-IDF/Zephyr registry listing is a real future option once there's
  external demand for one, but isn't blocking anything today.

## Questions or ideas?

Open a GitHub Discussion or an issue. There's no separate chat/Discord yet — GitHub is the
single home for now, kept that way deliberately until the project is large enough to need more.
