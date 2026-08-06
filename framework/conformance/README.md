# Conformance Fixtures

Language-neutral test cases that **every** Qonclave binding must pass.

Schema validation proves an implementation agrees with the spec. These fixtures prove
implementations agree with *each other* — which is the failure that actually breaks a mixed fleet,
and the one schema validation cannot catch. Two implementations can both satisfy a schema and
still disagree about whether an unknown field survives a round trip, or whether a grant with the
right issuer but the wrong audience is acceptable.

Protocol Buffers ships a top-level `conformance/` directory for the same reason.

## Layout

Every case is a directory with an input and a `case.json` describing what must happen. Each
carries a `why` field — a fixture whose purpose is not obvious gets deleted during the first
cleanup by someone who could not tell whether it mattered.

```
cases/
├─ codec/<name>/     input.json  + case.json   round trips, rejections, unknown-field survival
├─ encoding/<name>/  input.json  + case.json   JSON and CBOR must decode identically
├─ grant/<name>/     grant.json  + case.json   capability verification outcomes
└─ checkin/<name>/   input.json  + case.json   duty-cycle exchange, incl. the LoRa size bound
```

## Running them

```bash
# C first — it emits the interop artifact that Python's cross-language tests consume
cmake -S sdk/c -B build && cmake --build build
ctest --test-dir build --output-on-failure

# Python
python -m pytest sdk/python/tests -v
```

An implementation that links none of our code — the expected situation on the smallest devices —
proves conformance the same way ours does. That is the point of keeping these as data rather than
as tests in one language.

### Two directions, and why they differ

**Python reads the fixtures directly.** It has a JSON parser and a schema validator, so it
validates, round-trips, and checks JSON/CBOR equivalence case by case.

**C does not, deliberately.** A JSON parser is exactly the dependency the `minimal` profile exists
to avoid. Instead `qc_conformance` builds the documented fixture from a C struct, encodes it
canonically, and writes the bytes to `generated/`. Python's `test_interop.py` decodes them and
asserts they agree with the JSON fixture.

That covers the direction that carries real traffic. A device-to-hub disagreement drops sensor data
silently — the device believes it reported and the hub never knew — whereas a hub-to-device one
surfaces immediately as a command that never arrives.

`generated/` is build output and gitignored. If it is empty, the cross-language tests **skip**
rather than fail, so CI checks the artifact exists.

## What the categories cover

**`codec/`** — round trips, and the cases that separate a careless implementation from a correct
one: an event carrying `relative_time` instead of an absolute timestamp must be *accepted*
(requiring absolute time would exclude the entire `minimal` profile); an event carrying neither
must be *rejected*; unknown fields from a later 1.x must *survive*, because otherwise a document
loses data merely by transiting an older hop.

**`encoding/`** — `decode(cbor) == decode(json)` for the same logical document. This is what lets
a C sensor and a Python hub disagree about serialization without disagreeing about meaning.

**`grant/`** — the authorization matrix for brokered access: valid, expired, not-yet-valid, wrong
audience node, wrong audience *kind*, cross-tenant denied, cross-tenant explicitly allowed, scope
denied, untrusted issuer, revoked. The two easiest to get wrong are wrong-audience-kind (same
`node_id`, different `kind` — a compute grant is not a hub grant) and cross-tenant, which must
deny by default.

**`checkin/`** — the duty-cycle exchange, including a hard byte ceiling. A LoRaWAN payload is
51–242 bytes; `minimal-lora-sized` asserts a no-media check-in fits. That single number decides
whether the `minimal` profile is real or merely described.

## Adding a case

1. Create the directory with an input and a `case.json`.
2. Fill in `why`. If you cannot state why the case matters, it probably does not.
3. Confirm every binding still passes, or that the ones which now fail *should* fail.

A fixture is a promise to every future implementer. Adding one is cheap; changing one after
devices ship is not.
