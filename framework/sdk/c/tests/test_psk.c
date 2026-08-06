/*
 * test_psk.c — known-answer tests for SHA-256, HMAC, and the PSK signing scheme.
 *
 * Reading a hash implementation for correctness does not work; the only review that means
 * anything is a published vector. These are the FIPS 180-4 SHA-256 examples and the RFC 4231
 * HMAC-SHA256 vectors, plus the properties our own scheme adds on top.
 */

#include <stdio.h>
#include <string.h>

#include "qonclave/psk.h"

#include "../src/sha256.h"

static int failures;

#define CHECK(cond, msg)                                         \
    do {                                                         \
        if (!(cond)) {                                           \
            printf("FAIL %s:%d  %s\n", __FILE__, __LINE__, msg); \
            failures++;                                          \
        } else {                                                 \
            printf("ok   %s\n", msg);                            \
        }                                                        \
    } while (0)

static int hex_eq(const uint8_t *digest, const char *expect_hex)
{
    char got[65];
    for (int i = 0; i < 32; i++) {
        sprintf(got + i * 2, "%02x", digest[i]);
    }
    got[64] = '\0';
    return strcmp(got, expect_hex) == 0;
}

static void test_sha256_vectors(void)
{
    uint8_t d[32];

    /* FIPS 180-4 one-block message. */
    qc_sha256((const uint8_t *)"abc", 3, d);
    CHECK(hex_eq(d, "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"),
          "SHA-256(\"abc\") matches FIPS 180-4");

    /* Empty input — the padding-only path, which is where naive implementations break. */
    qc_sha256((const uint8_t *)"", 0, d);
    CHECK(hex_eq(d, "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"),
          "SHA-256(\"\") matches");

    /* FIPS 180-4 two-block message: 56 bytes, so the length does not fit the first block and a
     * second padding block is required. */
    const char *m = "abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq";
    qc_sha256((const uint8_t *)m, strlen(m), d);
    CHECK(hex_eq(d, "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1"),
          "SHA-256 two-block message matches");

    /* Exactly one block: the boundary where an off-by-one in the padding shows up. */
    uint8_t block[64];
    memset(block, 'a', sizeof block);
    qc_sha256(block, sizeof block, d);
    CHECK(hex_eq(d, "ffe054fe7ae0cb6dc65c3af9b61d5209f439851db43d0ba5997337df154668eb"),
          "SHA-256 of exactly 64 bytes matches");
}

static void test_sha256_streaming(void)
{
    uint8_t one[32], many[32];

    qc_sha256((const uint8_t *)"abcdefghijklmnopqrstuvwxyz", 26, one);

    qc_sha256_t ctx;
    qc_sha256_init(&ctx);
    qc_sha256_update(&ctx, (const uint8_t *)"abcdefghij", 10);
    qc_sha256_update(&ctx, (const uint8_t *)"klmnopqrst", 10);
    qc_sha256_update(&ctx, (const uint8_t *)"uvwxyz", 6);
    qc_sha256_final(&ctx, many);

    CHECK(memcmp(one, many, 32) == 0, "streaming update matches one-shot");
}

static void test_hmac_vectors(void)
{
    uint8_t d[32];

    /* RFC 4231 test case 1. */
    uint8_t key1[20];
    memset(key1, 0x0b, sizeof key1);
    qc_hmac_sha256(key1, sizeof key1, (const uint8_t *)"Hi There", 8, d);
    CHECK(hex_eq(d, "b0344c61d8db38535ca8afceaf0bf12b881dc200c9833da726e9376c2e32cff7"),
          "HMAC-SHA256 RFC 4231 case 1 matches");

    /* RFC 4231 test case 2 — short key. */
    qc_hmac_sha256((const uint8_t *)"Jefe", 4,
                   (const uint8_t *)"what do ya want for nothing?", 28, d);
    CHECK(hex_eq(d, "5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843"),
          "HMAC-SHA256 RFC 4231 case 2 matches");

    /* RFC 4231 test case 6 — key longer than the block, so it must be hashed first rather than
     * truncated. Getting this wrong produces a working-looking implementation that disagrees with
     * every other one in the world. */
    uint8_t key6[131];
    memset(key6, 0xaa, sizeof key6);
    qc_hmac_sha256(key6, sizeof key6,
                   (const uint8_t *)"Test Using Larger Than Block-Size Key - Hash Key First", 54, d);
    CHECK(hex_eq(d, "60e431591ee0b67f0d8a26aacbf5b77f8e0bc6213728c5140546040f0ee37f54"),
          "HMAC-SHA256 RFC 4231 case 6 (oversized key) matches");
}

static void test_constant_time_compare(void)
{
    const uint8_t a[4] = { 1, 2, 3, 4 };
    const uint8_t b[4] = { 1, 2, 3, 4 };
    const uint8_t c[4] = { 1, 2, 3, 5 };  /* differs only in the last byte */
    const uint8_t e[4] = { 9, 2, 3, 4 };  /* differs only in the first */

    CHECK(qc_ct_equal(a, b, 4), "equal buffers compare equal");
    CHECK(!qc_ct_equal(a, c, 4), "a trailing difference is detected");
    CHECK(!qc_ct_equal(a, e, 4), "a leading difference is detected");
}

static void test_psk_sign_verify(void)
{
    const uint8_t psk[16] = { 0 };
    const uint8_t msg[]   = { 0xa2, 0x01, 0x63, 'x', 'y', 'z' };
    uint8_t       sig[QC_PSK_SIG_LEN];

    CHECK(qc_psk_sign(psk, sizeof psk, msg, sizeof msg, QC_PSK_UPLINK, sig) == 0,
          "signing succeeds");
    CHECK(qc_psk_verify(psk, sizeof psk, msg, sizeof msg, QC_PSK_UPLINK, sig) == 0,
          "a good signature verifies");

    /* Domain separation: both directions share one symmetric key, so without it a downlink could
     * be replayed as an uplink whenever the two encode identically. */
    CHECK(qc_psk_verify(psk, sizeof psk, msg, sizeof msg, QC_PSK_DOWNLINK, sig) != 0,
          "an uplink signature does not verify as a downlink");

    uint8_t tampered[sizeof msg];
    memcpy(tampered, msg, sizeof msg);
    tampered[3] ^= 0x01u;
    CHECK(qc_psk_verify(psk, sizeof psk, tampered, sizeof tampered, QC_PSK_UPLINK, sig) != 0,
          "a modified message fails verification");

    const uint8_t other_psk[16] = { 1 };
    CHECK(qc_psk_verify(other_psk, sizeof other_psk, msg, sizeof msg, QC_PSK_UPLINK, sig) != 0,
          "the wrong key fails verification");

    uint8_t bad_sig[QC_PSK_SIG_LEN];
    memcpy(bad_sig, sig, sizeof bad_sig);
    bad_sig[QC_PSK_SIG_LEN - 1] ^= 0x01u;
    CHECK(qc_psk_verify(psk, sizeof psk, msg, sizeof msg, QC_PSK_UPLINK, bad_sig) != 0,
          "a one-bit signature change fails verification");

    CHECK(qc_psk_sign(NULL, 0, msg, sizeof msg, QC_PSK_UPLINK, sig) != 0,
          "a null key is rejected rather than crashing");
}

static void test_backend_is_reported(void)
{
    const char *name = qc_sha256_backend();
    printf("     sha256 backend: %s\n", name);

    /* Whichever backend is compiled in, every vector above had to pass. That is the property that
     * makes the backend swappable: the digests are identical, so a device using its hardware
     * accelerator and a hub using the bundled software implementation still agree on every MAC. */
    CHECK(name != NULL && name[0] != '\0', "the compiled-in backend identifies itself");
}

int main(void)
{
    test_sha256_vectors();
    test_sha256_streaming();
    test_hmac_vectors();
    test_constant_time_compare();
    test_psk_sign_verify();
    test_backend_is_reported();

    printf("\n%s\n", failures ? "FAILURES" : "all psk checks passed");
    return failures ? 1 : 0;
}
