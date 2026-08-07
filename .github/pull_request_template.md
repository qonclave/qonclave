## What does this PR do, and why?

<!-- The diff shows what changed; explain why. Link an issue if there is one. -->

## Component(s) touched

<!-- Check all that apply -->
- [ ] `framework/sdk/python`
- [ ] `framework/sdk/c`
- [ ] `framework/spec` (see the note below — this one needs extra scrutiny)
- [ ] `examples/hub`
- [ ] `examples/edge`
- [ ] Docs only

## Checklist

- [ ] Tests pass locally for everything touched
- [ ] New behavior has a test; changed behavior has an updated one
- [ ] Commits are signed off (`git commit -s`) — see `CONTRIBUTING.md`
- [ ] If `framework/spec/` changed: the change is additive only (new optional
      field / new value in an open set), `spec-validate` and `conformance`
      both pass, and a conformance fixture was added if the behavior crosses
      implementations
- [ ] If a new dependency was added to `framework/`: it's permissively
      licensed (no GPL/LGPL/AGPL — see `CONTRIBUTING.md`'s license section)
- [ ] Docs updated if this changes documented behavior

## Anything reviewers should know?

<!-- Design tradeoffs, things you're unsure about, follow-up work you're
     deliberately not doing here. -->
