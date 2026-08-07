# Qonclave Framework Documentation

The design documents behind the Qonclave Framework — a reusable, use-case-agnostic set of building
blocks for privacy-first autonomous AI systems.

These explain **why**. The normative contract lives in [`../spec/v1/`](../spec/v1/), and where prose
here disagrees with a schema there, the schema wins and the prose is a bug.

## The documents

| Document | Purpose |
|----------|---------|
| [**CONVENTIONS.md**](CONVENTIONS.md) | Where code goes and why: the three layers (`spec/` / `sdk/` / app), the dependency ladder enforced by `test_layering.py`, and the rule that the SDK owns the contract while the developer supplies the pipe. Read this before writing an app **or** contributing to the framework — it is the one document both audiences need. |
| [**ARCHITECTURE.md**](ARCHITECTURE.md) | The abstraction of the network. Roles of the Edge, Hub, Compute, and Archive nodes, discovery and network-health protocols (hub election, Direct-Bind brokering), and §4 on why placement is a per-request decision rather than a property of a role. |
| [**COMMUNICATION.md**](COMMUNICATION.md) | The communication planes and how nodes talk across transports (mDNS, REST, MQTT, CoAP, WebRTC, S3) — including §5.1 brokered access and §9, the one-round-trip check-in a duty-cycled device uses instead of all the others. |
| [**SECURITY.md**](SECURITY.md) | The threat model: zero-trust air-gapping, multi-tenant isolation, capability grants, DTLS/SRTP brokering, and compliance mechanics (GDPR/HIPAA). |
| [**PLACEMENT.md**](PLACEMENT.md) | How the framework decides *which tier* runs a given inference — the `PlacementPolicy` contract, the declared-vs-measured split, and what the framework enforces regardless of what a policy returns. |
| [**PROFILES.md**](PROFILES.md) | Why conformance is tiered, and what `full` / `constrained` / `minimal` each oblige a device to implement. |
| [**DEPLOYMENT.md**](DEPLOYMENT.md) | Deployment topologies and hardware, from a single laptop (Monolith) to an Industrial Mesh. |
| [**VISION.md**](VISION.md) | Roadmap and industry use cases — Defense, Healthcare, Manufacturing, Smart Cities. Two entries are no longer roadmap: sleep-queues and the authorization half of mesh handoff are now specified. |
| [**DEVELOPER_GUIDE.md**](DEVELOPER_GUIDE.md) | Manual for the **current** `hub/framework/` implementation: API endpoints, file layout, MQTT/SMS routing, and a quickstart for building an app. |

> **Note on `DEVELOPER_GUIDE.md`** — it documents `hub/framework/`, which still runs the working
> demo and is unchanged. The `framework/` package here is the forward-looking implementation; the
> two have not been merged yet. [`CONVENTIONS.md`](CONVENTIONS.md) carries the module-by-module
> map for when they are.

## Where everything else lives

| | |
|---|---|
| [`../spec/v1/`](../spec/v1/) | **Normative.** JSON Schemas, profiles, encodings, the value dictionary, `.proto`, OpenAPI, AsyncAPI |
| [`../conformance/`](../conformance/) | Language-neutral fixtures every binding must pass |
| [`../sdk/python/`](../sdk/python/) | Full binding — all four roles |
| [`../sdk/c/`](../sdk/c/) | Constrained binding — edge only, C99, no malloc |
