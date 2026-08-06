/*
 * checkin.c — the duty-cycle exchange, end to end.
 *
 * encode -> sign -> one round trip -> verify -> decode. That is the whole of a minimal-profile
 * device's network life, and keeping it to a single function is the point: every additional
 * exchange is radio-on time paid for out of a battery that has to last years.
 *
 * Frame layout is `cbor_document || hmac_sha256(document)`. The MAC is appended rather than
 * carried as a field inside the document for two reasons: a signature cannot cover itself, and
 * appending avoids the two-pass encode that embedding would require on a device with no spare
 * RAM to hold an intermediate copy.
 */

#include "qonclave/checkin.h"

#include <string.h>

#include "qonclave/codec.h"
#include "qonclave/port.h"
#include "qonclave/psk.h"

qc_status_t qc_checkin_encode(const qc_checkin_request_t *req,
                              const uint8_t *psk, size_t psk_len,
                              uint8_t *out, size_t out_cap, size_t *out_len)
{
    if (req == NULL || out_len == NULL) {
        return QC_ERR_ENCODE;
    }

    size_t      doc_len = 0;
    qc_status_t rc = qc_cbor_encode_checkin(req, true, false,
                                            out,
                                            out_cap > QC_PSK_SIG_LEN
                                                ? out_cap - QC_PSK_SIG_LEN
                                                : 0,
                                            &doc_len);
    if (rc != QC_OK) {
        /* Report the full framed size so a caller sizing a buffer gets the number it actually
         * needs, not the document size it would then have to remember to pad. */
        *out_len = doc_len + QC_PSK_SIG_LEN;
        return rc;
    }

    if (psk != NULL && psk_len > 0) {
        if (qc_psk_sign(psk, psk_len, out, doc_len, QC_PSK_UPLINK, out + doc_len) != 0) {
            return QC_ERR_SIGNATURE;
        }
        *out_len = doc_len + QC_PSK_SIG_LEN;
    } else {
        *out_len = doc_len;
    }
    return QC_OK;
}

qc_status_t qc_checkin_decode(const uint8_t *in, size_t in_len,
                              const uint8_t *psk, size_t psk_len,
                              int64_t now_unix,
                              qc_checkin_response_t *resp)
{
    if (in == NULL || resp == NULL) {
        return QC_ERR_DECODE;
    }

    size_t doc_len = in_len;

    if (psk != NULL && psk_len > 0) {
        if (in_len < QC_PSK_SIG_LEN) {
            return QC_ERR_SIGNATURE;
        }
        doc_len = in_len - QC_PSK_SIG_LEN;

        /* Verify BEFORE parsing. The device has no CA and no way to ask anyone for a second
         * opinion, so this check is its only defence against a spoofed hub — and running the
         * decoder over unauthenticated bytes would hand an attacker the parser as an attack
         * surface for free. */
        if (qc_psk_verify(psk, psk_len, in, doc_len, QC_PSK_DOWNLINK, in + doc_len) != 0) {
            return QC_ERR_SIGNATURE;
        }
    }

    return qc_cbor_decode_checkin(in, doc_len, now_unix, resp);
}

void qc_checkin_filter_expired(qc_checkin_response_t *resp, int64_t now_unix)
{
    uint8_t kept = 0;
    for (uint8_t i = 0; i < resp->command_count; i++) {
        if (!qc_command_expired(&resp->commands[i], now_unix)) {
            resp->commands[kept++] = resp->commands[i];
        }
    }
    resp->command_count = kept;
}

qc_status_t qc_checkin_perform(const qc_checkin_request_t *req,
                               const qc_checkin_config_t *cfg,
                               int64_t now_unix,
                               qc_checkin_response_t *resp)
{
    if (req == NULL || cfg == NULL || resp == NULL) {
        return QC_ERR_ENCODE;
    }

    /* Both buffers on the stack. A device that allocates here would be one heap fragmentation
     * away from failing three days into a multi-year deployment, with no way to report it. */
    uint8_t uplink[QC_CHECKIN_MAX_UPLINK + QC_PSK_SIG_LEN];
    uint8_t downlink[QC_CHECKIN_MAX_DOWNLINK];

    size_t      doc_len = 0;
    qc_status_t rc = qc_cbor_encode_checkin(req, cfg->use_int_keys, cfg->omit_identity,
                                            uplink, sizeof uplink - QC_PSK_SIG_LEN, &doc_len);
    if (rc != QC_OK) {
        return rc;
    }

    size_t frame_len = doc_len;
    if (cfg->psk != NULL && cfg->psk_len > 0) {
        if (qc_psk_sign(cfg->psk, cfg->psk_len, uplink, doc_len,
                        QC_PSK_UPLINK, uplink + doc_len) != 0) {
            return QC_ERR_SIGNATURE;
        }
        frame_len += QC_PSK_SIG_LEN;
    }

    const int got = qc_port_request(uplink, frame_len,
                                    downlink, sizeof downlink,
                                    cfg->timeout_ms);
    if (got < 0) {
        /* The caller spools and retries on a later wake. Retrying now would cost another radio
         * window for a link that just failed, and the next wake is only a duty cycle away. */
        return QC_ERR_TRANSPORT;
    }

    return qc_checkin_decode(downlink, (size_t)got, cfg->psk, cfg->psk_len, now_unix, resp);
}
