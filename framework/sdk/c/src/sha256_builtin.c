/*
 * sha256_builtin.c — FIPS 180-4 SHA-256, the default backend.
 *
 * Bundled so the library builds and its tests run with nothing installed. On a real device
 * prefer the mbedtls backend where the platform has a hardware SHA peripheral: this is pure
 * software, and a sensor that hashes on every wake for five years pays the difference in battery.
 *
 * Straight transcription of the standard. Verified against the NIST one-block/two-block vectors
 * in tests/test_psk.c — reading a hash implementation for correctness does not work, so
 * known-answer tests are the only review that means anything.
 */

#include <string.h>

#include "sha256.h"

/* Backend state, hidden behind qc_sha256_t's opaque buffer. */
typedef struct {
    uint32_t state[8];
    uint64_t bitlen;
    uint8_t  buf[QC_SHA256_BLOCK_LEN];
    size_t   buflen;
} builtin_ctx;

/* C99 static assert: a negative array size is a compile error. If a future change grows the
 * state past the opaque buffer, this fails at build time rather than corrupting the stack. */
typedef char qc__builtin_ctx_fits[(sizeof(builtin_ctx) <= sizeof(qc_sha256_t)) ? 1 : -1];

#define CTX(p) ((builtin_ctx *)(void *)(p))

static const uint32_t K[64] = {
    0x428a2f98u, 0x71374491u, 0xb5c0fbcfu, 0xe9b5dba5u,
    0x3956c25bu, 0x59f111f1u, 0x923f82a4u, 0xab1c5ed5u,
    0xd807aa98u, 0x12835b01u, 0x243185beu, 0x550c7dc3u,
    0x72be5d74u, 0x80deb1feu, 0x9bdc06a7u, 0xc19bf174u,
    0xe49b69c1u, 0xefbe4786u, 0x0fc19dc6u, 0x240ca1ccu,
    0x2de92c6fu, 0x4a7484aau, 0x5cb0a9dcu, 0x76f988dau,
    0x983e5152u, 0xa831c66du, 0xb00327c8u, 0xbf597fc7u,
    0xc6e00bf3u, 0xd5a79147u, 0x06ca6351u, 0x14292967u,
    0x27b70a85u, 0x2e1b2138u, 0x4d2c6dfcu, 0x53380d13u,
    0x650a7354u, 0x766a0abbu, 0x81c2c92eu, 0x92722c85u,
    0xa2bfe8a1u, 0xa81a664bu, 0xc24b8b70u, 0xc76c51a3u,
    0xd192e819u, 0xd6990624u, 0xf40e3585u, 0x106aa070u,
    0x19a4c116u, 0x1e376c08u, 0x2748774cu, 0x34b0bcb5u,
    0x391c0cb3u, 0x4ed8aa4au, 0x5b9cca4fu, 0x682e6ff3u,
    0x748f82eeu, 0x78a5636fu, 0x84c87814u, 0x8cc70208u,
    0x90befffau, 0xa4506cebu, 0xbef9a3f7u, 0xc67178f2u
};

#define ROTR(x, n) (((x) >> (n)) | ((x) << (32 - (n))))
#define CH(x, y, z)  (((x) & (y)) ^ (~(x) & (z)))
#define MAJ(x, y, z) (((x) & (y)) ^ ((x) & (z)) ^ ((y) & (z)))
#define BSIG0(x) (ROTR(x, 2) ^ ROTR(x, 13) ^ ROTR(x, 22))
#define BSIG1(x) (ROTR(x, 6) ^ ROTR(x, 11) ^ ROTR(x, 25))
#define SSIG0(x) (ROTR(x, 7) ^ ROTR(x, 18) ^ ((x) >> 3))
#define SSIG1(x) (ROTR(x, 17) ^ ROTR(x, 19) ^ ((x) >> 10))

static void transform(builtin_ctx *ctx, const uint8_t block[QC_SHA256_BLOCK_LEN])
{
    uint32_t w[64];

    for (int i = 0; i < 16; i++) {
        w[i] = ((uint32_t)block[i * 4] << 24) |
               ((uint32_t)block[i * 4 + 1] << 16) |
               ((uint32_t)block[i * 4 + 2] << 8) |
               ((uint32_t)block[i * 4 + 3]);
    }
    for (int i = 16; i < 64; i++) {
        w[i] = SSIG1(w[i - 2]) + w[i - 7] + SSIG0(w[i - 15]) + w[i - 16];
    }

    uint32_t a = ctx->state[0], b = ctx->state[1], c = ctx->state[2], d = ctx->state[3];
    uint32_t e = ctx->state[4], f = ctx->state[5], g = ctx->state[6], h = ctx->state[7];

    for (int i = 0; i < 64; i++) {
        const uint32_t t1 = h + BSIG1(e) + CH(e, f, g) + K[i] + w[i];
        const uint32_t t2 = BSIG0(a) + MAJ(a, b, c);
        h = g; g = f; f = e; e = d + t1;
        d = c; c = b; b = a; a = t1 + t2;
    }

    ctx->state[0] += a; ctx->state[1] += b; ctx->state[2] += c; ctx->state[3] += d;
    ctx->state[4] += e; ctx->state[5] += f; ctx->state[6] += g; ctx->state[7] += h;

    /* The message schedule is derived entirely from the input block, so leaving it on the stack
     * is a small but free disclosure. Compute cost here is irrelevant next to the 64 rounds. */
    memset(w, 0, sizeof w);
}

void qc_sha256_init(qc_sha256_t *opaque)
{
    builtin_ctx *ctx = CTX(opaque);
    ctx->state[0] = 0x6a09e667u; ctx->state[1] = 0xbb67ae85u;
    ctx->state[2] = 0x3c6ef372u; ctx->state[3] = 0xa54ff53au;
    ctx->state[4] = 0x510e527fu; ctx->state[5] = 0x9b05688cu;
    ctx->state[6] = 0x1f83d9abu; ctx->state[7] = 0x5be0cd19u;
    ctx->bitlen   = 0;
    ctx->buflen   = 0;
}

void qc_sha256_update(qc_sha256_t *opaque, const uint8_t *data, size_t len)
{
    builtin_ctx *ctx = CTX(opaque);
    for (size_t i = 0; i < len; i++) {
        ctx->buf[ctx->buflen++] = data[i];
        if (ctx->buflen == QC_SHA256_BLOCK_LEN) {
            transform(ctx, ctx->buf);
            ctx->bitlen += QC_SHA256_BLOCK_LEN * 8u;
            ctx->buflen = 0;
        }
    }
}

void qc_sha256_final(qc_sha256_t *opaque, uint8_t out[QC_SHA256_DIGEST_LEN])
{
    builtin_ctx *ctx = CTX(opaque);
    size_t i = ctx->buflen;

    ctx->bitlen += (uint64_t)ctx->buflen * 8u;

    /* Pad with 0x80 then zeros, leaving 8 bytes for the length. If the length will not fit in
     * this block, flush and pad a further full block. */
    ctx->buf[i++] = 0x80u;
    if (i > 56) {
        while (i < QC_SHA256_BLOCK_LEN) {
            ctx->buf[i++] = 0;
        }
        transform(ctx, ctx->buf);
        i = 0;
    }
    while (i < 56) {
        ctx->buf[i++] = 0;
    }

    for (int b = 7; b >= 0; b--) {
        ctx->buf[i++] = (uint8_t)(ctx->bitlen >> (b * 8));
    }
    transform(ctx, ctx->buf);

    for (int w = 0; w < 8; w++) {
        out[w * 4]     = (uint8_t)(ctx->state[w] >> 24);
        out[w * 4 + 1] = (uint8_t)(ctx->state[w] >> 16);
        out[w * 4 + 2] = (uint8_t)(ctx->state[w] >> 8);
        out[w * 4 + 3] = (uint8_t)(ctx->state[w]);
    }

    memset(ctx, 0, sizeof *ctx);
}

const char *qc_sha256_backend(void)
{
    return "builtin";
}
