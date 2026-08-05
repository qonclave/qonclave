# Qonclave Framework

The reusable, use-case-agnostic core of Qonclave: a **specification**, its **language bindings**,
and the **documentation** for both.

```
framework/
├─ spec/v1/        NORMATIVE. Schemas, profiles, encodings. The contract.
├─ conformance/    Language-neutral fixtures every binding must pass.
├─ sdk/python/     `full` binding — all four roles. Contracts done, I/O in progress.
├─ sdk/c/          Constrained binding — edge only, C99, no malloc.
└─ docs/           Architecture, security, communication, placement, profiles.
```

There are no applications here, by design. `framework/` is the reusable core; use cases live in
`hub/apps/` and `edge/`, and that separation is the whole framework-versus-app split the project
is built on. An example that drifted in here would be the first thing to blur it.

## The spec is the framework

`spec/v1/` is normative; everything under `sdk/` is a binding of it. Where an SDK and the spec
disagree, the SDK is wrong.

That inversion is not ceremony. Qonclave has to run on a Snapdragon X laptop *and* on a coin-cell
sensor that will never execute Python, so the framework cannot **be** a library — it has to be a
contract that several implementations satisfy independently. `conformance/` is what turns that
from an assertion into something testable: the smallest devices are expected to implement the
protocol directly and link none of our code, and they prove conformance by running the same
fixtures we do.

## Four roles, two of them optional

| Role | | |
|---|---|---|
| **edge** | sense, triage, actuate | required |
| **hub** | orchestrate, apply Policy, broker authorization, the only node with egress | required |
| **compute** | stateless heavy inference | **optional** |
| **archive** | per-tenant long-term storage | **optional** |

Compute and Archive are optional in a way that has teeth. The *capability* lives in a shared layer
(`inference/`, `storage/`) and the *role* is just a server that exposes it over the network. A hub
calls `inference.resolve(task, tiers, policy, backends)` and gets back whichever `ModelBackend`
placement chose — local or remote, same interface — and never imports `qonclave.compute`. A
single-laptop deployment is a supported production topology, not a development mode.

## Placement: any node runs local AI if it can

Where inference runs is a **per-request decision**, not a property of where code is deployed. The
developer writes a `PlacementPolicy`; the framework measures the facts and enforces the outcome.

| The app declares | The framework measures |
|---|---|
| complexity, urgency, privacy, deadline, use case | power, thermal, load, RTT, capability |

```python
class MyPlacement(PlacementPolicy):
    def decide(self, task, tiers):
        if task.descriptor.privacy == "no_egress":
            return Placement(Tier.HUB, deny=[Tier.COMPUTE])
        if tiers.local.power.is_constrained:
            return Placement(Tier.HUB, fallback=[Tier.COMPUTE])
        return Placement(Tier.EDGE, fallback=[Tier.HUB])
```

No rule DSL, no ruleset file — the decision is code, in the same idiom as `hub.Policy`. See
[docs/PLACEMENT.md](docs/PLACEMENT.md).

## Three device profiles

Conformance is tiered, because a Snapdragon laptop and a coin cell cannot implement the same
protocol surface and pretending otherwise means nobody can say what "conformant" means.

| | discovery | crypto | encoding | commands |
|---|---|---|---|---|
| **full** | mDNS + UDP | mTLS | JSON | MQTT push |
| **constrained** | optional | PSK | JSON or CBOR | push or pull |
| **minimal** | forbidden | PSK | CBOR | pull only |

A `minimal` device wakes once a day, completes **one** round trip, and sleeps. See
[spec/v1/profiles/](spec/v1/profiles/).

## Install

```bash
pip install -e "sdk/python[hub]"      # orchestrator
pip install -e "sdk/python[edge]"     # sensing node — no Flask, no broker, no model runtime
pip install -e "sdk/python[dev]"      # tests
```

```bash
# host build and tests
cmake -S sdk/c -B build && cmake --build build && ctest --test-dir build

# for a real device: supply your own port (three functions, see include/qonclave/port.h)
cmake -S sdk/c -B build-dev -DQONCLAVE_PROFILE=minimal \
      -DQONCLAVE_PORT_SOURCE=/path/to/your_port.c
```

## Test

```bash
python -m pytest sdk/python/tests -v
```

`tests/test_layering.py` enforces the dependency ladder in [CONVENTIONS.md](docs/CONVENTIONS.md) by
parsing imports — including the rule that role packages never import each other, which is what
keeps an edge install free of a web framework.

## Relationship to `hub/` and `edge/`

`hub/framework/` still runs the working demo and is **not** modified by this package.
`framework/` is the forward-looking implementation; pointing `hub/server.py` at `qonclave.hub` is
a later, separate change. [CONVENTIONS.md](docs/CONVENTIONS.md) carries the module-by-module map for
when that happens.
