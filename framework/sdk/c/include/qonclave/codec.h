/*
 * codec.h — CBOR encoding of the check-in exchange.
 *
 * Separate from checkin.h because encoding and transport are independently useful: tests and the
 * conformance runner exercise the codec with no radio, and a gateway may transcode without ever
 * performing an exchange.
 *
 * Spec: framework/spec/v1/encodings/cbor.md
 */

#ifndef QONCLAVE_CODEC_H
#define QONCLAVE_CODEC_H

#include <stddef.h>
#include <stdint.h>

#include "qonclave/checkin.h"

#ifdef __cplusplus
extern "C" {
#endif

/*
 * Encode a check-in uplink to canonical CBOR with integer keys.
 *
 * Pass out=NULL, out_cap=0 to measure: *out_len receives the required size and the call returns
 * QC_ERR_BUFFER_TOO_SMALL. Sizing through the same code path that encodes is deliberate — a
 * separate size estimator is a second implementation that will eventually disagree.
 *
 * `use_int_keys` must be true on the `minimal` profile. String keys cost roughly 3x — the
 * reference fixture is 223 bytes with them and 71 without — and are useful mainly when debugging
 * with a generic CBOR viewer.
 *
 * `omit_identity` drops tenant_id and schema_version, permitted ONLY on the `minimal` profile
 * because both are fixed at commissioning and the hub reconstructs them from the device record.
 * Saves ~10 bytes on the reference fixture, more when tenant_id is long. Small, but on the slowest
 * LoRa data rates it is spent from a budget of 51.
 */
qc_status_t qc_cbor_encode_checkin(const qc_checkin_request_t *req,
                                   bool use_int_keys,
                                   bool omit_identity,
                                   uint8_t *out, size_t out_cap, size_t *out_len);

/*
 * Decode a check-in downlink.
 *
 * Accepts integer or string keys regardless of what this device emits: a gateway transcoding from
 * JSON will not have applied the key map, and rejecting that would make the two encodings not
 * actually interchangeable.
 *
 * Unknown keys are skipped rather than rejected, so a v1.0 device keeps working against a v1.7
 * hub.
 *
 * `now_unix` is used to drop expired commands before they reach the caller. Pass 0 to keep them
 * (tests only) — a device must always pass its real clock.
 */
qc_status_t qc_cbor_decode_checkin(const uint8_t *in, size_t in_len,
                                   int64_t now_unix,
                                   qc_checkin_response_t *resp);

#ifdef __cplusplus
}
#endif

#endif /* QONCLAVE_CODEC_H */
