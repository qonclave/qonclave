# JSON encoding — v1

JSON is the default wire format and the one every profile must support (the `minimal` profile may use
it for debugging even though CBOR is required on the wire). It is also the form in which
`spec/v1/json-schema/` is written, so a JSON document can be validated directly by any off-the-shelf
JSON Schema validator with no Qonclave-specific tooling.

## Rules

1. **UTF-8, no BOM.**

2. **Timestamps** are RFC 3339 with an explicit offset. `2026-08-04T18:41:00Z` and
   `2026-08-04T13:41:00-05:00` are both valid; a local time with no offset is not, because a hub
   aggregating events from several sites cannot order them.

3. **Binary payloads** are base64 (RFC 4648 §4, with padding), declared as
   `"data_encoding": "base64"`. This costs 33% over the raw bytes, which is the main reason CBOR
   exists for the small profiles.

4. **Unknown fields are preserved.** Every schema sets `additionalProperties: true` deliberately.
   A receiver MUST accept fields it does not recognize, and a component that forwards or archives a
   document MUST NOT strip them. This is what allows a v1.3 field to survive a hop through a v1.0
   node.

5. **Version handling.** A receiver MUST reject a `schema_version` whose major version it does not
   implement, and MUST accept any minor version within a major it does. `1.0` and `1.7` are mutually
   intelligible; `2.0` is not assumed to be.

6. **Canonical form for signing.** Signatures are computed over
   [RFC 8785 JSON Canonicalization Scheme](https://www.rfc-editor.org/rfc/rfc8785) output, with the
   `signature` field itself removed. Without a canonical form, two encoders that differ only in key
   order or number formatting produce different signatures over the same document.

## Numbers

Confidence and probability fields are JSON numbers in `[0, 1]`. Implementations SHOULD emit at most
four decimal places — a VLM's fifth decimal place of confidence is noise, and on constrained links it
is noise that costs bytes.

Integer fields (`deadline_ms`, `wake_counter`, `battery_pct` when whole) MUST be encoded without a
decimal point, so that a CBOR transcoder can use integer major types rather than floats.
