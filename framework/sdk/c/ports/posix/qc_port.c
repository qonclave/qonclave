/*
 * qc_port.c — POSIX port.
 *
 * Exists so the library is testable without hardware. The clock is real; the network is a
 * settable handler (see qc_port_posix.h) so a test can stand in for a hub; NVS is an in-memory
 * table, which is enough to exercise the spool and wake-counter logic but is NOT persistence —
 * a real port must use flash that survives a power cycle.
 */

#include "qonclave/port.h"

#include <string.h>
#include <time.h>

#include "qc_port_posix.h"

static qc_port_handler_fn s_handler;
static void              *s_handler_ud;
static size_t             s_last_request_len;

void qc_port_posix_set_handler(qc_port_handler_fn fn, void *user_data)
{
    s_handler    = fn;
    s_handler_ud = user_data;
}

size_t qc_port_posix_last_request_len(void)
{
    return s_last_request_len;
}

int qc_port_request(const uint8_t *req, size_t req_len,
                    uint8_t *resp, size_t resp_cap, uint32_t timeout_ms)
{
    (void)timeout_ms;

    s_last_request_len = req_len;

    if (s_handler == NULL) {
        /* No hub configured. Failing rather than blocking matches what a device should see on a
         * dead link, and is what the spool-and-retry path is written against. */
        return -1;
    }
    return s_handler(req, req_len, resp, resp_cap, s_handler_ud);
}

/* --- NVS ------------------------------------------------------------------------------- */

#ifndef QC_POSIX_NVS_SLOTS
#define QC_POSIX_NVS_SLOTS 8
#endif
#ifndef QC_POSIX_NVS_VALUE_MAX
#define QC_POSIX_NVS_VALUE_MAX 256
#endif

static struct {
    char    key[32];
    uint8_t value[QC_POSIX_NVS_VALUE_MAX];
    size_t  len;
    bool    used;
} s_nvs[QC_POSIX_NVS_SLOTS];

int qc_port_nvs_read(const char *key, uint8_t *out, size_t out_cap)
{
    for (size_t i = 0; i < QC_POSIX_NVS_SLOTS; i++) {
        if (s_nvs[i].used && strcmp(s_nvs[i].key, key) == 0) {
            const size_t n = s_nvs[i].len < out_cap ? s_nvs[i].len : out_cap;
            memcpy(out, s_nvs[i].value, n);
            return (int)n;
        }
    }
    return -1;
}

int qc_port_nvs_write(const char *key, const uint8_t *data, size_t len)
{
    if (len > QC_POSIX_NVS_VALUE_MAX || strlen(key) >= sizeof s_nvs[0].key) {
        return -1;
    }

    int slot = -1;
    for (size_t i = 0; i < QC_POSIX_NVS_SLOTS; i++) {
        if (s_nvs[i].used && strcmp(s_nvs[i].key, key) == 0) {
            slot = (int)i;
            break;
        }
        if (!s_nvs[i].used && slot < 0) {
            slot = (int)i;
        }
    }
    if (slot < 0) {
        return -1;
    }

    strcpy(s_nvs[slot].key, key);
    memcpy(s_nvs[slot].value, data, len);
    s_nvs[slot].len  = len;
    s_nvs[slot].used = true;
    return (int)len;
}

/* --- clock ----------------------------------------------------------------------------- */

uint32_t qc_port_now_ms(void)
{
#if defined(CLOCK_MONOTONIC)
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint32_t)((uint64_t)ts.tv_sec * 1000u + (uint64_t)ts.tv_nsec / 1000000u);
#else
    return (uint32_t)((uint64_t)clock() * 1000u / CLOCKS_PER_SEC);
#endif
}
