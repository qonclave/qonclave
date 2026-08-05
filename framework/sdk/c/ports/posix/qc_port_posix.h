/*
 * qc_port_posix.h — test hooks for the POSIX port.
 *
 * The POSIX port exists so the library is testable without hardware, which is only useful if a
 * test can stand in for the network. Installing a handler lets tests drive a whole check-in
 * exchange — encode, sign, verify, decode — with no radio and no hub.
 *
 * Not part of the public API and not present in the esp32 or zephyr ports.
 */

#ifndef QONCLAVE_PORT_POSIX_H
#define QONCLAVE_PORT_POSIX_H

#include <stddef.h>
#include <stdint.h>

/*
 * Handle one request. Return the number of bytes written to `resp`, or negative to simulate a
 * transport failure — which is the case worth testing, since a device that mishandles an
 * unreachable hub burns the battery it was supposed to be conserving.
 */
typedef int (*qc_port_handler_fn)(const uint8_t *req, size_t req_len,
                                  uint8_t *resp, size_t resp_cap,
                                  void *user_data);

void qc_port_posix_set_handler(qc_port_handler_fn fn, void *user_data);

/* Bytes of the most recent request, for assertions about what actually went on the wire. */
size_t qc_port_posix_last_request_len(void);

#endif /* QONCLAVE_PORT_POSIX_H */
