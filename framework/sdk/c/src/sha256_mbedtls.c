/*
 * sha256_mbedtls.c — SHA-256 via mbedTLS.
 *
 * Selected with -DQONCLAVE_SHA256_BACKEND=mbedtls.
 *
 * Worth preferring on real hardware. ESP-IDF already links mbedTLS and routes SHA-256 through the
 * ESP32's SHA peripheral, so on that target this is both faster and *not a new dependency* —
 * whereas the builtin backend is pure software that a battery-powered sensor pays for on every
 * wake, for years. Zephyr likewise ships mbedTLS.
 *
 * Only the three streaming functions live here. The one-shot helper, HMAC, and the constant-time
 * compare stay in sha256_common.c so they are written and tested once regardless of backend —
 * notably, we do NOT use mbedtls_md_hmac, because a second HMAC implementation is a second thing
 * that could disagree about the oversized-key rule.
 */

#include <string.h>

#include "mbedtls/sha256.h"

#include "sha256.h"

/* C99 static assert. mbedtls_sha256_context is around 108 bytes; if a future mbedTLS grows it
 * past our opaque buffer this fails at build time rather than smashing the stack. */
typedef char qc__mbedtls_ctx_fits[(sizeof(mbedtls_sha256_context) <= sizeof(qc_sha256_t)) ? 1 : -1];

#define CTX(p) ((mbedtls_sha256_context *)(void *)(p))

void qc_sha256_init(qc_sha256_t *opaque)
{
    mbedtls_sha256_context *ctx = CTX(opaque);
    mbedtls_sha256_init(ctx);

    /* Second argument 0 selects SHA-256 rather than SHA-224. mbedTLS 3.x renamed the _ret
     * variants; both spellings are handled so this builds against 2.x and 3.x alike, which
     * matters because ESP-IDF and Zephyr track different releases. */
#if defined(MBEDTLS_VERSION_MAJOR) && MBEDTLS_VERSION_MAJOR >= 3
    (void)mbedtls_sha256_starts(ctx, 0);
#else
    (void)mbedtls_sha256_starts_ret(ctx, 0);
#endif
}

void qc_sha256_update(qc_sha256_t *opaque, const uint8_t *data, size_t len)
{
#if defined(MBEDTLS_VERSION_MAJOR) && MBEDTLS_VERSION_MAJOR >= 3
    (void)mbedtls_sha256_update(CTX(opaque), data, len);
#else
    (void)mbedtls_sha256_update_ret(CTX(opaque), data, len);
#endif
}

void qc_sha256_final(qc_sha256_t *opaque, uint8_t out[QC_SHA256_DIGEST_LEN])
{
    mbedtls_sha256_context *ctx = CTX(opaque);

#if defined(MBEDTLS_VERSION_MAJOR) && MBEDTLS_VERSION_MAJOR >= 3
    (void)mbedtls_sha256_finish(ctx, out);
#else
    (void)mbedtls_sha256_finish_ret(ctx, out);
#endif

    /* mbedtls_sha256_free zeroizes the context. The builtin backend memsets for the same reason:
     * leaving hash state on the stack after signing is a small but free disclosure. */
    mbedtls_sha256_free(ctx);
}

const char *qc_sha256_backend(void)
{
    return "mbedtls";
}
