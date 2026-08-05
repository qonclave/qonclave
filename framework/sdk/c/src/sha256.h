/*
 * sha256.h — SHA-256 and HMAC, with a pluggable backend. Internal to the SDK.
 *
 * The interface is fixed; the implementation behind it is selected at build time:
 *
 *   builtin   FIPS 180-4 in ~150 lines, no dependencies. The default, so host builds and CI need
 *             nothing installed.
 *   mbedtls   Wraps mbedtls_sha256_*. On ESP32 this reaches the chip's SHA peripheral, which for
 *             a sensor hashing on every wake for five years is energy rather than a rounding
 *             error. ESP-IDF and Zephyr already ship mbedTLS, so on those targets it is not
 *             really a new dependency.
 *   custom    Point QONCLAVE_SHA256_SOURCE at your own implementation of the three streaming
 *             functions below.
 *
 * A backend supplies only `qc_sha256_init/update/final`. The one-shot helper, HMAC, and the
 * constant-time compare are built once on top of those in sha256_common.c — so HMAC is written
 * and tested a single time regardless of who computes the hash.
 */

#ifndef QONCLAVE_SHA256_H
#define QONCLAVE_SHA256_H

#include <stddef.h>
#include <stdint.h>

#define QC_SHA256_DIGEST_LEN 32
#define QC_SHA256_BLOCK_LEN  64

/*
 * Opaque context, sized to hold the state of any backend we ship.
 *
 * Opaque rather than a union of backend types so this header pulls in no backend headers — a
 * caller building against the builtin backend must not need mbedTLS on its include path. Each
 * backend static-asserts that its state fits.
 */
#ifndef QC_SHA256_CTX_SIZE
#define QC_SHA256_CTX_SIZE 128
#endif

typedef struct {
    /* Aligned via uint64_t so a backend may cast this to its own struct without an unaligned
     * access, which is a fault rather than a slowdown on some Cortex-M parts. */
    uint64_t opaque[QC_SHA256_CTX_SIZE / sizeof(uint64_t)];
} qc_sha256_t;

/* --- supplied by the backend ------------------------------------------------------------- */

void qc_sha256_init(qc_sha256_t *ctx);
void qc_sha256_update(qc_sha256_t *ctx, const uint8_t *data, size_t len);
void qc_sha256_final(qc_sha256_t *ctx, uint8_t out[QC_SHA256_DIGEST_LEN]);

/* --- backend-agnostic, built once (sha256_common.c) --------------------------------------- */

void qc_sha256(const uint8_t *data, size_t len, uint8_t out[QC_SHA256_DIGEST_LEN]);

/* HMAC-SHA256, RFC 2104. */
void qc_hmac_sha256(const uint8_t *key, size_t key_len,
                    const uint8_t *data, size_t data_len,
                    uint8_t out[QC_SHA256_DIGEST_LEN]);

/*
 * Constant-time comparison.
 *
 * Required, not optional: a byte-by-byte memcmp on a MAC leaks how many leading bytes matched,
 * which turns forging a signature into a per-byte search instead of a 2^256 one. The hub is
 * remote and the attacker can retry, so the timing channel is live.
 *
 * Deliberately ours rather than the backend's — it is the one piece of this that is a protocol
 * requirement rather than a crypto primitive.
 */
int qc_ct_equal(const uint8_t *a, const uint8_t *b, size_t len);

/* Name of the compiled-in backend, for `doctor`-style diagnostics. */
const char *qc_sha256_backend(void);

#endif /* QONCLAVE_SHA256_H */
