/*
 * codec_cbor.c — the check-in exchange as canonical CBOR.
 *
 * Key numbers come from spec/v1/encodings/cbor.md and are FROZEN for v1. Reusing one for a
 * different field would silently change the meaning of documents from sensors already deployed,
 * which cannot be updated.
 *
 * Keys are emitted in ascending numeric order. For the values we use (1..14) the CBOR encoding of
 * each key is a single byte 0x01..0x0e, so ascending numeric order is also ascending bytewise
 * order — which is what RFC 8949 section 4.2.1 requires. That equivalence stops holding at key
 * 24, where the encoding grows a second byte; if this map ever reaches that far, the emit order
 * must be re-derived rather than assumed.
 */

#include "qonclave/codec.h"

#include <string.h>

#include "cbor.h"

/* --- key maps (spec/v1/encodings/cbor.md) ------------------------------------------------ */

enum {
    CK_SCHEMA = 1, CK_NODE_ID = 2, CK_TENANT = 3, CK_WAKE = 4,
    CK_EVENTS = 5, CK_POWER = 6, CK_CONFIG_VER = 7, CK_ACK = 8,
    CK_GRANT = 9, CK_SIG = 10
};

enum {
    RK_SCHEMA = 1, RK_SERVER_TIME = 2, RK_ACCEPTED = 3, RK_COMMANDS = 4,
    RK_CONFIG = 5, RK_NEXT_CHECKIN = 6, RK_SIG = 7
};

enum {
    EK_SCHEMA = 1, EK_EVENT_ID = 2, EK_SOURCE = 3, EK_TENANT = 4,
    EK_TIMESTAMP = 5, EK_RELATIVE = 6, EK_TRIGGER = 7, EK_CONFIDENCE = 8,
    EK_PAYLOAD = 9, EK_TASK = 10, EK_METADATA = 11, EK_POWER = 12,
    EK_SIG = 13, EK_HUB_RECEIVED = 14
};

enum { PK_BATTERY = 1, PK_ON_MAINS = 2, PK_THERMAL = 3, PK_DUTY = 4 };
enum { RTK_WAKE = 1, RTK_MS = 2, RTK_UNCERT = 3 };
enum { CMDK_SCHEMA = 1, CMDK_ID = 2, CMDK_ISSUER = 3, CMDK_TARGET = 4,
       CMDK_TENANT = 5, CMDK_ACTION = 6, CMDK_PARAMS = 7, CMDK_ISSUED = 8,
       CMDK_EXPIRES = 9, CMDK_SIG = 10 };

#define QC_SCHEMA_VERSION "1.0"

/* --- key emission ------------------------------------------------------------------------ */

/* Names used when int keys are disabled. Indexed by the enums above so the two forms cannot
 * drift apart. */
static const char *const CHECKIN_NAMES[] = {
    NULL, "schema_version", "node_id", "tenant_id", "wake_counter",
    "events", "power", "config_version", "ack", "grant", "signature"
};
static const char *const EVENT_NAMES[] = {
    NULL, "schema_version", "event_id", "source_node_id", "tenant_id",
    "timestamp", "relative_time", "trigger", "confidence", "payload",
    "task", "metadata", "power", "signature", "hub_received_at"
};
static const char *const POWER_NAMES[] = {
    NULL, "battery_pct", "on_mains", "thermal_headroom_c", "duty_cycle_s"
};
static const char *const RELTIME_NAMES[] = { NULL, "wake_counter", "ms_since_wake", "uncertainty_s" };

static void w_key(qc_cbor_writer_t *w, bool ints, const char *const *names, int key)
{
    if (ints) {
        qc_cbor_w_uint(w, (uint64_t)key);
    } else {
        qc_cbor_w_text(w, names[key]);
    }
}

/* --- encode ------------------------------------------------------------------------------ */

static uint8_t power_field_count(const qc_power_t *p)
{
    uint8_t n = 0;
    if (p->has_battery) n++;
    n++;                       /* on_mains is always meaningful, including when false */
    if (p->has_thermal) n++;
    if (p->duty_cycle_s) n++;
    return n;
}

static void w_power(qc_cbor_writer_t *w, const qc_power_t *p, bool ints)
{
    qc_cbor_w_map(w, power_field_count(p));

    if (p->has_battery) {
        w_key(w, ints, POWER_NAMES, PK_BATTERY);
        qc_cbor_w_float(w, (double)p->battery_pct);
    }
    w_key(w, ints, POWER_NAMES, PK_ON_MAINS);
    qc_cbor_w_bool(w, p->on_mains);

    if (p->has_thermal) {
        w_key(w, ints, POWER_NAMES, PK_THERMAL);
        qc_cbor_w_float(w, (double)p->thermal_headroom_c);
    }
    if (p->duty_cycle_s) {
        w_key(w, ints, POWER_NAMES, PK_DUTY);
        qc_cbor_w_uint(w, p->duty_cycle_s);
    }
}

/*
 * Metadata keys are text and therefore need explicit canonical ordering.
 *
 * All our keys are short (< 24 bytes), so each encodes to a 1-byte head plus the bytes, which
 * means bytewise order over the encoded form reduces to (length, then memcmp). Sorting an index
 * array rather than the entries keeps the caller's struct untouched.
 */
static void sort_metadata(const qc_event_t *ev, uint8_t *order)
{
    for (uint8_t i = 0; i < ev->metadata_count; i++) {
        order[i] = i;
    }
    for (uint8_t i = 1; i < ev->metadata_count; i++) {
        const uint8_t cur = order[i];
        const char   *ck  = ev->metadata[cur].key;
        const size_t  cl  = strlen(ck);

        int8_t j = (int8_t)(i - 1);
        while (j >= 0) {
            const char  *ok = ev->metadata[order[j]].key;
            const size_t ol = strlen(ok);
            const bool   greater =
                (ol > cl) || (ol == cl && memcmp(ok, ck, cl) > 0);
            if (!greater) {
                break;
            }
            order[j + 1] = order[j];
            j--;
        }
        order[j + 1] = cur;
    }
}

static uint8_t event_field_count(const qc_event_t *ev, bool omit_identity)
{
    uint8_t n = 0;
    if (!omit_identity) n++;              /* schema_version */
    n += 2;                               /* event_id, source_node_id */
    if (ev->has_timestamp) n++;
    if (ev->has_relative) n++;
    n++;                                  /* trigger */
    if (ev->has_confidence) n++;
    if (ev->metadata_count) n++;
    return n;
}

static void w_event(qc_cbor_writer_t *w, const qc_event_t *ev, bool ints, bool omit_identity)
{
    qc_cbor_w_map(w, event_field_count(ev, omit_identity));

    if (!omit_identity) {
        w_key(w, ints, EVENT_NAMES, EK_SCHEMA);
        qc_cbor_w_text(w, QC_SCHEMA_VERSION);
    }

    w_key(w, ints, EVENT_NAMES, EK_EVENT_ID);
    qc_cbor_w_text(w, ev->event_id);

    w_key(w, ints, EVENT_NAMES, EK_SOURCE);
    qc_cbor_w_text(w, ev->source_node_id);

    if (ev->has_timestamp) {
        w_key(w, ints, EVENT_NAMES, EK_TIMESTAMP);
        qc_cbor_w_epoch(w, ev->timestamp_unix);
    }

    if (ev->has_relative) {
        w_key(w, ints, EVENT_NAMES, EK_RELATIVE);
        qc_cbor_w_map(w, ev->relative.has_uncertainty ? 3u : 2u);
        w_key(w, ints, RELTIME_NAMES, RTK_WAKE);
        qc_cbor_w_uint(w, ev->relative.wake_counter);
        w_key(w, ints, RELTIME_NAMES, RTK_MS);
        qc_cbor_w_uint(w, ev->relative.ms_since_wake);
        if (ev->relative.has_uncertainty) {
            w_key(w, ints, RELTIME_NAMES, RTK_UNCERT);
            qc_cbor_w_uint(w, ev->relative.uncertainty_s);
        }
    }

    w_key(w, ints, EVENT_NAMES, EK_TRIGGER);
    qc_cbor_w_text(w, ev->trigger);

    if (ev->has_confidence) {
        w_key(w, ints, EVENT_NAMES, EK_CONFIDENCE);
        qc_cbor_w_float(w, (double)ev->confidence);
    }

    if (ev->metadata_count) {
        uint8_t order[sizeof ev->metadata / sizeof ev->metadata[0]];
        sort_metadata(ev, order);

        w_key(w, ints, EVENT_NAMES, EK_METADATA);
        qc_cbor_w_map(w, ev->metadata_count);
        for (uint8_t i = 0; i < ev->metadata_count; i++) {
            qc_cbor_w_text(w, ev->metadata[order[i]].key);
            qc_cbor_w_text(w, ev->metadata[order[i]].value);
        }
    }
}

static uint8_t checkin_field_count(const qc_checkin_request_t *req, bool omit_identity)
{
    uint8_t n = 0;
    if (!omit_identity) n++;                          /* schema_version */
    n++;                                              /* node_id */
    if (!omit_identity && req->tenant_id) n++;        /* otherwise carried by the PSK */
    n++;                                              /* wake_counter */
    if (req->event_count) n++;
    if (req->has_power) n++;
    if (req->config_version) n++;
    if (req->ack_count) n++;
    return n;
}

qc_status_t qc_cbor_encode_checkin(const qc_checkin_request_t *req,
                                   bool use_int_keys,
                                   bool omit_identity,
                                   uint8_t *out, size_t out_cap, size_t *out_len)
{
    if (req == NULL || out_len == NULL) {
        return QC_ERR_ENCODE;
    }

    qc_cbor_writer_t w;
    qc_cbor_w_init(&w, out, out_cap);

    const bool ints = use_int_keys;

    qc_cbor_w_map(&w, checkin_field_count(req, omit_identity));

    if (!omit_identity) {
        w_key(&w, ints, CHECKIN_NAMES, CK_SCHEMA);
        qc_cbor_w_text(&w, QC_SCHEMA_VERSION);
    }

    w_key(&w, ints, CHECKIN_NAMES, CK_NODE_ID);
    qc_cbor_w_text(&w, req->node_id);

    if (!omit_identity && req->tenant_id) {
        w_key(&w, ints, CHECKIN_NAMES, CK_TENANT);
        qc_cbor_w_text(&w, req->tenant_id);
    }

    w_key(&w, ints, CHECKIN_NAMES, CK_WAKE);
    qc_cbor_w_uint(&w, req->wake_counter);

    if (req->event_count) {
        w_key(&w, ints, CHECKIN_NAMES, CK_EVENTS);
        qc_cbor_w_array(&w, req->event_count);
        for (uint8_t i = 0; i < req->event_count; i++) {
            w_event(&w, &req->events[i], ints, omit_identity);
        }
    }

    if (req->has_power) {
        w_key(&w, ints, CHECKIN_NAMES, CK_POWER);
        w_power(&w, &req->power, ints);
    }

    if (req->config_version) {
        w_key(&w, ints, CHECKIN_NAMES, CK_CONFIG_VER);
        qc_cbor_w_uint(&w, req->config_version);
    }

    if (req->ack_count) {
        w_key(&w, ints, CHECKIN_NAMES, CK_ACK);
        qc_cbor_w_array(&w, req->ack_count);
        for (uint8_t i = 0; i < req->ack_count; i++) {
            qc_cbor_w_text(&w, req->ack[i]);
        }
    }

    *out_len = w.len;
    return qc_cbor_w_ok(&w) ? QC_OK : QC_ERR_BUFFER_TOO_SMALL;
}

/* --- decode ------------------------------------------------------------------------------ */

/*
 * Read a map key that may be an integer or a text name, and normalize to the integer.
 *
 * Accepting both is what keeps the two encodings interchangeable: a gateway transcoding from JSON
 * will not have applied the key map, and refusing it would mean CBOR and JSON were merely similar
 * rather than equivalent. Returns -1 for a key this decoder does not know.
 */
static int r_key(qc_cbor_reader_t *r, const char *const *names, int max_key)
{
    const uint8_t major = qc_cbor_r_peek_major(r);

    if (major == QC_CBOR_UINT) {
        uint64_t v;
        if (!qc_cbor_r_uint(r, &v)) {
            return -1;
        }
        return (v <= (uint64_t)max_key) ? (int)v : -1;
    }

    if (major == QC_CBOR_TEXT) {
        const char *s;
        size_t      n;
        if (!qc_cbor_r_text(r, &s, &n)) {
            return -1;
        }
        for (int k = 1; k <= max_key; k++) {
            if (names[k] && strlen(names[k]) == n && memcmp(names[k], s, n) == 0) {
                return k;
            }
        }
        return -1;
    }

    /* Some other key type entirely. Consume it so the caller can skip the value and continue. */
    qc_cbor_r_skip(r);
    return -1;
}

static const char *const COMMAND_NAMES[] = {
    NULL, "schema_version", "command_id", "issuer_id", "target_id",
    "tenant_id", "action", "parameters", "issued_at", "expires_at", "signature"
};
static const char *const RESP_NAMES[] = {
    NULL, "schema_version", "server_time", "accepted", "commands",
    "config", "next_checkin_s", "signature"
};

static bool r_command(qc_cbor_reader_t *r, qc_command_t *cmd)
{
    memset(cmd, 0, sizeof *cmd);

    size_t n;
    if (!qc_cbor_r_map(r, &n)) {
        return false;
    }

    for (size_t i = 0; i < n; i++) {
        switch (r_key(r, COMMAND_NAMES, CMDK_SIG)) {
            case CMDK_ID:
                if (!qc_cbor_r_text_copy(r, cmd->command_id, sizeof cmd->command_id)) return false;
                break;
            case CMDK_ISSUER:
                if (!qc_cbor_r_text_copy(r, cmd->issuer_id, sizeof cmd->issuer_id)) return false;
                break;
            case CMDK_ACTION:
                if (!qc_cbor_r_text_copy(r, cmd->action, sizeof cmd->action)) return false;
                break;
            case CMDK_ISSUED:
                if (!qc_cbor_r_time(r, &cmd->issued_at_unix)) return false;
                break;
            case CMDK_EXPIRES:
                if (!qc_cbor_r_time(r, &cmd->expires_at_unix)) return false;
                cmd->has_expiry = true;
                break;
            default:
                /* Unknown or unsupported-on-this-profile field. Skipping rather than failing is
                 * what lets a v1.0 device keep working against a v1.7 hub. */
                if (!qc_cbor_r_skip(r)) return false;
                break;
        }
    }
    return !r->error;
}

qc_status_t qc_cbor_decode_checkin(const uint8_t *in, size_t in_len,
                                   int64_t now_unix,
                                   qc_checkin_response_t *resp)
{
    if (in == NULL || resp == NULL) {
        return QC_ERR_DECODE;
    }

    memset(resp, 0, sizeof *resp);

    qc_cbor_reader_t r;
    qc_cbor_r_init(&r, in, in_len);

    size_t n;
    if (!qc_cbor_r_map(&r, &n)) {
        return QC_ERR_DECODE;
    }

    for (size_t i = 0; i < n; i++) {
        switch (r_key(&r, RESP_NAMES, RK_SIG)) {
            case RK_SCHEMA: {
                /* Reject a major version we do not implement; tolerate any minor within it. */
                const char *s;
                size_t      sn;
                if (!qc_cbor_r_text(&r, &s, &sn)) return QC_ERR_DECODE;
                if (sn < 1 || s[0] != '1') return QC_ERR_SCHEMA_VERSION;
                break;
            }

            case RK_SERVER_TIME:
                if (!qc_cbor_r_time(&r, &resp->server_time_unix)) return QC_ERR_DECODE;
                break;

            case RK_COMMANDS: {
                size_t count;
                if (!qc_cbor_r_array(&r, &count)) return QC_ERR_DECODE;
                for (size_t c = 0; c < count; c++) {
                    qc_command_t cmd;
                    if (!r_command(&r, &cmd)) return QC_ERR_DECODE;

                    /* Drop expired commands here rather than surfacing them. A device that wakes
                     * to a day-old "unlock the door" and performs it is a security failure, not a
                     * late delivery — so the rule lives below the application. */
                    if (now_unix != 0 && qc_command_expired(&cmd, now_unix)) {
                        continue;
                    }
                    if (resp->command_count < QC_CHECKIN_MAX_COMMANDS) {
                        resp->commands[resp->command_count++] = cmd;
                    }
                }
                break;
            }

            case RK_NEXT_CHECKIN: {
                uint64_t v;
                if (!qc_cbor_r_uint(&r, &v)) return QC_ERR_DECODE;
                resp->next_checkin_s     = (uint32_t)v;
                resp->has_next_checkin   = true;
                break;
            }

            case RK_CONFIG: {
                size_t cn;
                if (!qc_cbor_r_map(&r, &cn)) return QC_ERR_DECODE;
                for (size_t c = 0; c < cn; c++) {
                    const uint8_t major = qc_cbor_r_peek_major(&r);
                    bool is_version = false;

                    if (major == QC_CBOR_TEXT) {
                        const char *s;
                        size_t      sn;
                        if (!qc_cbor_r_text(&r, &s, &sn)) return QC_ERR_DECODE;
                        is_version = (sn == 7 && memcmp(s, "version", 7) == 0);
                    } else {
                        uint64_t k;
                        if (!qc_cbor_r_uint(&r, &k)) return QC_ERR_DECODE;
                        is_version = (k == 1);
                    }

                    if (is_version) {
                        uint64_t v;
                        if (!qc_cbor_r_uint(&r, &v)) return QC_ERR_DECODE;
                        resp->config_version = (uint32_t)v;
                        resp->has_config     = true;
                    } else if (!qc_cbor_r_skip(&r)) {
                        return QC_ERR_DECODE;
                    }
                }
                break;
            }

            case RK_ACCEPTED:
            default:
                if (!qc_cbor_r_skip(&r)) return QC_ERR_DECODE;
                break;
        }

        if (r.error) {
            return QC_ERR_DECODE;
        }
    }

    return QC_OK;
}
