# Governance

This document exists to answer the question a company evaluating Qonclave
as a dependency will always ask: **what happens if the person who started
this disappears?** ("Bus factor.") It states the model plainly rather than
leaving it implicit.

## Current model: BDFL

Qonclave is currently governed by a **Benevolent Dictator for Life (BDFL)**
model, the standard early-stage pattern (Python used it for decades;
countless smaller projects still do). One person has final say on technical
direction, releases, and who gets commit access, informed by discussion with
contributors but not bound by consensus.

<!-- TODO(2026-08-07): name the BDFL explicitly before this goes public.
     See steps_to_open_source.md §4/§3 -- who this is may depend on the
     account/ownership decisions being made there. -->

**BDFL:** *(to be named)*

This is a deliberate, temporary simplification, not a permanent claim to
sole authority — see "Path to a core team" below.

## Decision-making

- **Day-to-day**: PRs are reviewed and merged by anyone with commit access;
  the spec-is-normative rule (`CONTRIBUTING.md`) and existing tests are the
  actual arbiters for most changes, not a person's judgment call.
- **Breaking changes / new spec major version**: requires BDFL sign-off.
  `framework/spec/v1/` is normative and additive-only (`CONTRIBUTING.md`);
  a v2 is a deliberately rare, high-scrutiny event.
- **New maintainers**: nominated by an existing maintainer, approved by the
  BDFL. Sustained, high-quality contribution is the only real qualification.
- **Disputes**: default to discussion in the relevant issue/PR; if that
  doesn't converge, the BDFL decides and explains why.

## Path to a core team

As the contributor base grows past what one person can reasonably review,
the intent is to move to a **core-team model**: a small group with shared
commit access and joint decision authority, with the BDFL role either
retired or narrowed to a tie-breaking vote. There's no fixed trigger for
this (e.g. "N maintainers" or "N months") — it happens when review latency
or bus-factor risk makes it obviously worth doing, decided in the open via
a GitHub Discussion, not unilaterally.

## Scope

This document covers the framework and reference apps in this repository.
It does not govern the `qonclave` trademark (reserved under Apache 2.0 §6,
per `steps_to_open_source.md` §2) — trademark use is a separate, narrower
decision the BDFL/core team makes independently of code contribution rights.
