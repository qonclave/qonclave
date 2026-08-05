# Qonclave Wire Specification — v1

**This directory is normative.** Everything under `../../sdk/` is a binding of it. Where an SDK and
this spec disagree, the SDK is wrong.

That inversion is the point. Qonclave has to run on a Snapdragon laptop and on a coin-cell sensor
that will never execute Python, so the framework cannot *be* a library — it has to be a contract that
several implementations satisfy independently.

## Layout

| Path | What it is |
|---|---|
| `dictionary.md` | The enumerated values. Closed sets a receiver must reject outside of; open sets applications extend |
| `profiles/` | Tiered conformance: `full`, `constrained`, `minimal`. What each device class MUST implement and MAY skip |
| `encodings/` | `json.md` and `cbor.md` — two serializations of the same schemas |
| `json-schema/` | The documents themselves, as JSON Schema 2020-12 |
| `proto/` | `compute.proto` — the gRPC offload plane |
| `asyncapi/` | MQTT topics and payload bindings |
| `openapi/` | The hub's REST surface |

## The documents

| Schema | Plane | Direction |
|---|---|---|
| `node-manifest` | §1 discovery | any → any |
| `edge-event` | §2 ingestion | edge → hub |
| `compute.proto` | §3 offload | hub → compute |
| `command` | §4 actuation | hub → edge |
| `checkin` | duty-cycle exchange | edge ⇄ hub, one round trip |
| `capability-grant` | brokered access | hub → edge, presented to a third party |
| `archive-record` | §7 archiving | hub → archive |

`common.schema.json` holds the shared `$defs` every other schema references.

## Versioning

The directory is the major version. A receiver MUST reject a `schema_version` whose major it does not
implement, and MUST accept any minor within a major it does — `1.0` and `1.7` are mutually
intelligible, `2.0` is not assumed to be.

Within v1 the only permitted changes are **additive**: new optional fields, new values in open sets,
new entries appended to the CBOR key map. Removing a field, tightening a constraint, or reusing a
CBOR key number requires v2, because devices commissioned years earlier are still in the field and
cannot be updated.

## Proving an implementation conforms

```
qonclave conformance --profile <full|constrained|minimal> --cases ../../conformance/cases
```

The fixtures in `../../conformance/` are language-neutral. An implementation that links none of our
code — the expected situation on the smallest devices — proves conformance the same way ours does.

## Prose

The narrative rationale for these planes lives in [`../../docs/COMMUNICATION.md`](../../docs/COMMUNICATION.md);
the threat model behind the security fields is in [`../../docs/SECURITY.md`](../../docs/SECURITY.md).
Where prose and schema disagree, the schema wins and the prose is a bug.
