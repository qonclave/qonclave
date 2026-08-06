# Contributing

## The one rule that is not negotiable

**`spec/v1/` is normative.** Where an SDK and the spec disagree, the SDK is wrong and the fix goes
in the SDK. Never change a schema to make code pass.

Within v1, changes must be **additive**: new optional fields, new values in open sets, new entries
appended to the CBOR key map. Removing a field, tightening a constraint, or reusing a CBOR key
number requires v2 — devices commissioned years ago are still in the field and cannot be updated.

## Before you open a PR

```bash
pip install -e "sdk/python[dev]"
python -m pytest sdk/python/tests -v
python -m qonclave.cli spec-validate
python -m qonclave.cli conformance --cases conformance/cases
```

## What reviewers will ask

**Does it respect the layering?** `tests/test_layering.py` enforces it, but understand *why*
before working around it: role packages never import each other, which is what keeps an edge
install free of a web framework and makes the Compute and Archive roles genuinely optional.

**What does it cost a device that wakes for 200 ms once a day?** Round trips are the budget, not
bytes. A field added to the check-in exchange is paid for by every sensor in every deployment,
forever.

**Does behavior that crosses implementations have a fixture?** If a C binding could plausibly get
it wrong, it belongs in `conformance/`, not only in a Python test.

**Do the comments say why?** `# increment counter` is noise. `# doubles as the replay guard` is
the reason the line exists.

## Adding a conformance fixture

Create the case directory, write `case.json`, and fill in `why`. If you cannot state why the case
matters, it probably does not. A fixture is a promise to every future implementer — adding one is
cheap, changing one after devices ship is not.

## Commit messages

Imperative mood, capitalized, no trailing period, under 72 characters. Explain what and why in the
body. See `AGENTS.md` at the repo root.
