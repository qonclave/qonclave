/*
 * psk.h — symmetric authentication for the constrained profiles.
 *
 * The key is established out-of-band at commissioning (an operator scans a QR code on the
 * enclosure) and never traverses the network. See SECURITY.md section 3.
 *
 * Uplink and downlink are domain-separated. Both directions are authenticated with the same
 * symmetric key, so without separation a downlink could be replayed back as an uplink whenever
 * the two happen to encode identically.
 */

#ifndef QONCLAVE_PSK_H
#define QONCLAVE_PSK_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define QC_PSK_SIG_LEN 32

typedef enum {
    QC_PSK_UPLINK   = 0,  /* device -> hub */
    QC_PSK_DOWNLINK = 1   /* hub -> device */
} qc_psk_dir_t;

/* Returns 0 on success, -1 on bad arguments. */
int qc_psk_sign(const uint8_t *psk, size_t psk_len,
                const uint8_t *payload, size_t payload_len,
                qc_psk_dir_t dir,
                uint8_t out[QC_PSK_SIG_LEN]);

/*
 * Returns 0 if the signature is valid, -1 otherwise.
 *
 * Comparison is constant time. Do not replace it with memcmp: the timing difference tells an
 * attacker how many leading bytes they got right, which turns forgery into a per-byte search.
 */
int qc_psk_verify(const uint8_t *psk, size_t psk_len,
                  const uint8_t *payload, size_t payload_len,
                  qc_psk_dir_t dir,
                  const uint8_t sig[QC_PSK_SIG_LEN]);

#ifdef __cplusplus
}
#endif

#endif /* QONCLAVE_PSK_H */
