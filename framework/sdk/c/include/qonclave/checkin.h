/*
 * checkin.h — the duty-cycle exchange.
 *
 * The entire network interaction of a `minimal`-profile device: wake, send everything you have,
 * receive everything the hub has been holding, sleep. One request, one response.
 *
 * Radio-on time is the battery budget, so round trips are what this API minimizes — not bytes,
 * and not lines of code. An implementation that needs two exchanges to do this is not conformant
 * regardless of how small its payloads are.
 *
 * No malloc. Every buffer is caller-provided, because a device with 40KB of usable RAM cannot
 * afford heap fragmentation across a multi-year uptime, and because a failed allocation three
 * days into a deployment is not a recoverable condition.
 *
 * Spec: framework/spec/v1/json-schema/checkin.schema.json
 *       framework/spec/v1/profiles/minimal.md
 */

#ifndef QONCLAVE_CHECKIN_H
#define QONCLAVE_CHECKIN_H

#include <stddef.h>
#include <stdint.h>

#include "qonclave/command.h"
#include "qonclave/event.h"

#ifdef __cplusplus
extern "C" {
#endif

/* A LoRaWAN payload is 51-242 bytes depending on data rate and region. Sizing the uplink buffer
 * to the ceiling means a conformant no-media check-in always fits. */
#define QC_CHECKIN_MAX_UPLINK   242
#define QC_CHECKIN_MAX_DOWNLINK 512

/* Bounded because the device has no heap. A wake that produced more events than this drains the
 * remainder on the following wake — the spool is persistent, so nothing is lost. */
#ifndef QC_CHECKIN_MAX_EVENTS
#define QC_CHECKIN_MAX_EVENTS 4
#endif

#ifndef QC_CHECKIN_MAX_COMMANDS
#define QC_CHECKIN_MAX_COMMANDS 4
#endif

typedef struct {
    const char *node_id;

    /* Optional. The `minimal` profile may omit it on the wire, because the hub reconstructs it
     * from the PSK it authenticated against — worth ~20 bytes on a link where the whole message
     * must fit in 242. Every other profile MUST send it. */
    const char *tenant_id;

    /* Monotonic across the device's lifetime, persisted in NVS. Doubles as the replay guard: a
     * hub MUST reject a check-in whose counter did not advance. Resetting it requires
     * re-commissioning. */
    uint32_t wake_counter;

    qc_event_t events[QC_CHECKIN_MAX_EVENTS];
    uint8_t    event_count;

    /* Config version currently held. The hub returns a delta only when this is stale, so an
     * unchanged config costs zero downlink bytes. */
    uint32_t config_version;

    /* command_ids delivered previously and now executed. Until acked, the hub retains them, so a
     * device that browns out mid-wake retries rather than losing the command. */
    const char *ack[QC_CHECKIN_MAX_COMMANDS];
    uint8_t     ack_count;

    qc_power_t power;
    bool       has_power;
} qc_checkin_request_t;

typedef struct {
    /* Authoritative time. How a device with a drifted or absent RTC learns what time it is, and
     * what anchors the relative_time on the events it just sent. */
    int64_t server_time_unix;

    qc_command_t commands[QC_CHECKIN_MAX_COMMANDS];
    uint8_t      command_count;

    uint32_t config_version;
    bool     has_config;

    /* Advisory. The device owns its power budget and MAY ignore this — it is how a hub applies
     * backpressure to a fleet with no always-on control channel. */
    uint32_t next_checkin_s;
    bool     has_next_checkin;
} qc_checkin_response_t;

typedef enum {
    QC_OK = 0,
    QC_ERR_BUFFER_TOO_SMALL = -1,
    QC_ERR_ENCODE           = -2,
    QC_ERR_DECODE           = -3,
    QC_ERR_SIGNATURE        = -4,
    QC_ERR_REPLAY           = -5,  /* wake_counter did not advance */
    QC_ERR_SCHEMA_VERSION   = -6,  /* major version we do not implement */
    QC_ERR_TRANSPORT        = -7
} qc_status_t;

/*
 * Encode an uplink to canonical CBOR with integer keys.
 *
 * `out_len` receives the byte count. Returns QC_ERR_BUFFER_TOO_SMALL rather than truncating —
 * a truncated uplink is a signature failure at the far end, which is far harder to diagnose in
 * the field than an explicit local error.
 *
 * The encoding MUST be deterministic (RFC 8949 §4.2.1): the signature covers these exact bytes.
 */
qc_status_t qc_checkin_encode(const qc_checkin_request_t *req,
                              const uint8_t *psk, size_t psk_len,
                              uint8_t *out, size_t out_cap, size_t *out_len);

/*
 * Decode and authenticate a downlink.
 *
 * Verifies the HS256 signature against the PSK before populating `resp`. A caller must never act
 * on an unverified downlink: the device's only defence against a spoofed hub is this check, since
 * it has no CA and no way to reach anyone for a second opinion.
 *
 * Commands whose expires_at has already passed are dropped here rather than surfaced. A device
 * that wakes to a day-old "unlock the door" and executes it is a security failure, not a late
 * delivery.
 */
qc_status_t qc_checkin_decode(const uint8_t *in, size_t in_len,
                              const uint8_t *psk, size_t psk_len,
                              int64_t now_unix,
                              qc_checkin_response_t *resp);

typedef struct {
    /* Established out-of-band at commissioning and held in NVS. Not copied — the caller owns the
     * storage, which on a real device is flash-backed and outlives every call. */
    const uint8_t *psk;
    size_t         psk_len;

    /* Required true on the `minimal` profile. */
    bool use_int_keys;

    /* Drop tenant_id and schema_version. Permitted ONLY on `minimal`, where both are fixed at
     * commissioning and the hub reconstructs them from the device record. */
    bool omit_identity;

    uint32_t timeout_ms;
} qc_checkin_config_t;

/*
 * Perform a whole check-in: encode, sign, send, receive, verify, decode.
 *
 * The only function a minimal-profile application needs to call. Everything else in this header
 * exists so this one can be tested without a radio.
 *
 * `now_unix` is used to drop expired commands. A device that does not know the time yet may pass
 * 0 on its very first check-in and use the returned `server_time_unix` from then on.
 *
 * Wire framing is `cbor_document || hmac_sha256(document)` — see spec/v1/encodings/cbor.md. The
 * MAC is appended rather than carried as a field inside the document because a signature cannot
 * cover itself, and appending avoids a two-pass encode on a device with no spare RAM.
 */
qc_status_t qc_checkin_perform(const qc_checkin_request_t *req,
                               const qc_checkin_config_t *cfg,
                               int64_t now_unix,
                               qc_checkin_response_t *resp);

#ifdef __cplusplus
}
#endif

#endif /* QONCLAVE_CHECKIN_H */
