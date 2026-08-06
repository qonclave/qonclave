# Profile: `full`

**Target.** A general-purpose computer: the Snapdragon X hub, an NPU compute rig, a storage server,
or a Linux-class edge such as a Raspberry Pi or the Arduino UNO Q's Linux side. It has an OS, a real
network stack, RAM to spare, and continuous power.

This is the only profile that may fill a role other than `edge`.

---

## Obligations

### MUST

- Implement **discovery** — announce a manifest and browse for peers
  (`node-manifest.schema.json`, `COMMUNICATION.md` §1), over mDNS/DNS-SD and/or UDP broadcast.
- Emit **heartbeats** carrying current `load` and `power`, so peers can detect death and route
  around utilization.
- Secure IP traffic with **mTLS**, with certificates provisioned by the hub acting as local CA
  (`SECURITY.md` §3). Refuse connections from unauthenticated peers.
- Send absolute `timestamp` on everything it originates.
- Support **JSON** on every plane it implements.
- Validate every inbound document against the schema for its type, and reject a `schema_version`
  whose major version it does not implement.

### Hubs additionally MUST

- Expose ingestion at `POST /api/v1/events`, and the check-in endpoint for duty-cycled devices.
- Maintain a per-device **mailbox** with TTL enforcement, and drain it on check-in. A hub that
  discards commands for a sleeping device breaks the only delivery mechanism that device has.
- Stamp `hub_received_at` on events arriving with `relative_time`, and return `server_time`.
- Enforce tenant isolation on every inbound document, before the Policy sees it.
- Act as the **only** node permitted outbound internet access (`SECURITY.md` §1), under the three
  governed exceptions: metadata alerts, brokered Direct-Bind, and compliant archiving.
- Verify capability grants offline against pinned peer CA roots when accepting foreign edges.

### Compute nodes additionally MUST

- Be **stateless between calls**. Flush model context, prompts, and intermediate tensors between
  back-to-back inferences, including across tenants (`SECURITY.md` §2). This is a guarantee, not an
  optimization target.
- Advertise `capabilities.supported_models` and `capabilities.max_complexity` truthfully. Placement
  filters candidates on these before a policy ever chooses, so an inaccurate manifest produces
  routing failures that look like model failures.

### Archive nodes additionally MUST

- Serve exactly one tenant (`SECURITY.md` §2). Archive nodes are never shared.
- Encrypt at rest, log every read to an append-only audit ledger, and support cryptographic erasure
  by key destruction.

### SHOULD

- Implement gRPC for compute offload (`COMMUNICATION.md` §3). HTTP/JSON works and is the fallback,
  but multi-megabyte tensors over JSON is the wrong trade at volume.
- Support CBOR on ingestion, so constrained peers are not forced to transcode.
- Implement hub election and role demotion (`ARCHITECTURE.md` §3).

---

## Placement

A `full` node is the only profile expected to make genuinely dynamic placement decisions, because it
is the only one with a real choice: it can run inference locally, hand it to a peer hub, or hand it
to a compute node. It implements `PlacementPolicy` in full, including fallback chains and `deny`
enforcement.

Note that "hub" and "has a compute node" are independent. A monolith deployment — one laptop running
everything — is a `full` hub whose placement ladder collapses to `[edge, hub]`, and that is a
supported production topology (`DEPLOYMENT.md`, Topology A/B), not merely a development mode.
