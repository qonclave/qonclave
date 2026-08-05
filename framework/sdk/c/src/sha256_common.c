/*
 * sha256_common.c — everything built on top of a backend's streaming API.
 *
 * Lives here rather than in each backend so HMAC is written and tested exactly once. A backend
 * that supplied its own HMAC would be a second implementation to review, and mbedTLS's and ours
 * could then disagree about the oversized-key rule without anything catching it.
 */

#include <string.h>

#include "sha256.h"

void qc_sha256(const uint8_t *data, size_t len, uint8_t out[QC_SHA256_DIGEST_LEN])
{
    qc_sha256_t ctx;
    qc_sha256_init(&ctx);
    qc_sha256_update(&ctx, data, len);
    qc_sha256_final(&ctx, out);
}

void qc_hmac_sha256(const uint8_t *key, size_t key_len,
                    const uint8_t *data, size_t data_len,
                    uint8_t out[QC_SHA256_DIGEST_LEN])
{
    uint8_t k[QC_SHA256_BLOCK_LEN];
    uint8_t pad[QC_SHA256_BLOCK_LEN];
    uint8_t inner[QC_SHA256_DIGEST_LEN];

    memset(k, 0, sizeof k);
    if (key_len > QC_SHA256_BLOCK_LEN) {
        /* RFC 2104: an oversized key is replaced by its hash, not truncated. Getting this wrong
         * yields an implementation that works perfectly and agrees with nobody. */
        qc_sha256(key, key_len, k);
    } else {
        memcpy(k, key, key_len);
    }

    qc_sha256_t ctx;

    for (size_t i = 0; i < QC_SHA256_BLOCK_LEN; i++) {
        pad[i] = (uint8_t)(k[i] ^ 0x36u);
    }
    qc_sha256_init(&ctx);
    qc_sha256_update(&ctx, pad, sizeof pad);
    qc_sha256_update(&ctx, data, data_len);
    qc_sha256_final(&ctx, inner);

    for (size_t i = 0; i < QC_SHA256_BLOCK_LEN; i++) {
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

int qc_ct_equal(const uint8_t *a, const uint8_t *b, size_t len)
{
    uint8_t diff = 0;
    for (size_t i = 0; i < len; i++) {
        diff |= (uint8_t)(a[i] ^ b[i]);
    }
    /* No early exit anywhere: the point is that the time taken does not depend on where the first
     * mismatch is. */
    return diff == 0;
}
