/*
 * port.h — the entire platform surface.
 *
 * Three functions. Keeping it this small is what makes bringing up a new platform a day of work
 * rather than a fork, and it is why `ports/posix` can exist purely so the library is testable
 * without hardware.
 */

#ifndef QONCLAVE_PORT_H
#define QONCLAVE_PORT_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/*
 * One network round trip. Blocking, bounded by timeout_ms.
 *
 * Returns bytes written to `resp`, or negative on failure. The abstraction is a round trip rather
 * than send/recv because that is the only shape the minimal profile ever needs, and offering more
 * would invite implementations that use it.
 */
int qc_port_request(const uint8_t *req, size_t req_len,
                    uint8_t *resp, size_t resp_cap,
                    uint32_t timeout_ms);

/*
 * Non-volatile storage for the spool and the wake counter.
 *
 * Both MUST survive a power cycle, not merely deep sleep. The hub treats the wake counter as a
 * replay guard and will reject a device whose count restarted, and a spool that does not outlive
 * the sleep cycle is not a spool.
 */
int qc_port_nvs_read(const char *key, uint8_t *out, size_t out_cap);
int qc_port_nvs_write(const char *key, const uint8_t *data, size_t len);

/* Monotonic milliseconds. Need not relate to wall-clock time — this device may not know it. */
uint32_t qc_port_now_ms(void);

#ifdef __cplusplus
}
#endif

#endif /* QONCLAVE_PORT_H */
