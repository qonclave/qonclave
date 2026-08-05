/*
 * psk.c — the constrained crypto path.
 *
 * HS256 over the canonical CBOR encoding, keyed by the pre-shared key established during
 * out-of-band commissioning. This rather than mTLS because a full handshake on an ESP32 costs
 * seconds and hundreds of KB of RAM — more energy than the message it protects. SECURITY.md §3
 * anticipates exactly this for the constrained class; spec/v1/profiles/constrained.md makes it
 * the floor.
 *
 * Signing covers the message bytes plus a domain separator. Without separation, a downlink could
 * be replayed as an uplink (or vice versa) whenever the two happen to encode identically, since
 * both directions are authenticated with the same symmetric key.
 */

#include "qonclave/psk.h"

#include <string.h>

#include "sha256.h"

/* Domain separators. Short because every byte is hashed, not transmitted. */
static const uint8_t DOMAIN_UP[]   = { 'q', 'c', '1', 'u' };
static const uint8_t DOMAIN_DOWN[] = { 'q', 'c', '1', 'd' };

static void mac(const uint8_t *psk, size_t psk_len,
                const uint8_t *domain, size_t domain_len,
                const uint8_t *payload, size_t payload_len,
                uint8_t out[QC_PSK_SIG_LEN])
{
    /* HMAC over domain || payload. Prefixing inside the HMAC rather than concatenating buffers
     * avoids needing a scratch allocation the size of the message. */
    uint8_t     k[QC_SHA256_BLOCK_LEN];
    uint8_t     pad[QC_SHA256_BLOCK_LEN];
    uint8_t     inner[QC_SHA256_DIGEST_LEN];
    qc_sha256_t ctx;

    memset(k, 0, sizeof k);
    if (psk_len > QC_SHA256_BLOCK_LEN) {
        qc_sha256(psk, psk_len, k);
    } else {
        memcpy(k, psk, psk_len);
    }

    for (size_t i = 0; i < sizeof pad; i++) {
        pad[i] = (uint8_t)(k[i] ^ 0x36u);
    }
    qc_sha256_init(&ctx);
    qc_sha256_update(&ctx, pad, sizeof pad);
    qc_sha256_update(&ctx, domain, domain_len);
    qc_sha256_update(&ctx, payload, payload_len);
    qc_sha256_final(&ctx, inner);

    for (size_t i = 0; i < sizeof pad; i++) {
        pad[i] = (uint8_t)(k[i] ^ 0x5cu);
    }
    qc_sha256_init(&ctx);
    qc_sha256_update(&ctx, pad, sizeof pad);
    qc_sha256_update(&ctx, inner, sizeof inner);
    qc_sha256_final(&ctx, out);

    memset(k, 0, sizeof k);
    memset(pad, 0, sizeof pad);
    memset(inner, 0, sizeof inner);
}

int qc_psk_sign(const uint8_t *psk, size_t psk_len,
                const uint8_t *payload, size_t payload_len,
                qc_psk_dir_t dir,
                uint8_t out[QC_PSK_SIG_LEN])
{
    if (psk == NULL || psk_len == 0 || payload == NULL || out == NULL) {
        return -1;
    }
    if (dir == QC_PSK_UPLINK) {
        mac(psk, psk_len, DOMAIN_UP, sizeof DOMAIN_UP, payload, payload_len, out);
    } else {
        mac(psk, psk_len, DOMAIN_DOWN, sizeof DOMAIN_DOWN, payload, payload_len, out);
    }
    return 0;
}

int qc_psk_verify(const uint8_t *psk, size_t psk_len,
                  const uint8_t *payload, size_t payload_len,
                  qc_psk_dir_t dir,
                  const uint8_t sig[QC_PSK_SIG_LEN])
{
    if (psk == NULL || psk_len == 0 || payload == NULL || sig == NULL) {
        return -1;
    }

    uint8_t expected[QC_PSK_SIG_LEN];
    if (qc_psk_sign(psk, psk_len, payload, payload_len, dir, expected) != 0) {
        return -1;
    }

    /* Constant time. A byte-by-byte compare leaks how many leading bytes matched, which reduces
     * forgery from a 2^256 search to a per-byte one — and the hub is remote, so the attacker can
     * retry as often as they like. */
    const int ok = qc_ct_equal(expected, sig, QC_PSK_SIG_LEN);
    memset(expected, 0, sizeof expected);
    return ok ? 0 : -1;
}
