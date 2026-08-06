/*
 * command.h — an instruction from a hub, as a C struct.
 *
 * Mirrors spec/v1/json-schema/command.schema.json.
 *
 * `expires_at` is not optional in practice on this profile. A device that sleeps for a day and
 * wakes to a stale actuation command must discard it, so the decoder drops expired commands
 * before the application ever sees them.
 */

#ifndef QONCLAVE_COMMAND_H
#define QONCLAVE_COMMAND_H

#include <stdbool.h>
#include <stdint.h>

#include "qonclave/event.h" /* QC_MAX_ID_LEN */

#ifdef __cplusplus
extern "C" {
#endif

#ifndef QC_MAX_ACTION_LEN
#define QC_MAX_ACTION_LEN 33
#endif

#ifndef QC_MAX_COMMAND_PARAMS
#define QC_MAX_COMMAND_PARAMS 4
#endif

typedef struct {
    char command_id[QC_MAX_ID_LEN];
    char issuer_id[QC_MAX_ID_LEN];
    char action[QC_MAX_ACTION_LEN];

    struct {
        const char *key;
        const char *value;
    } params[QC_MAX_COMMAND_PARAMS];
    uint8_t param_count;

    int64_t issued_at_unix;
    int64_t expires_at_unix;
    bool    has_expiry;
} qc_command_t;

/* True when `now_unix` is past the command's expiry. Commands with no expiry never expire. */
bool qc_command_expired(const qc_command_t *cmd, int64_t now_unix);

/* Look up a parameter by key. Returns NULL if absent. */
const char *qc_command_param(const qc_command_t *cmd, const char *key);

#ifdef __cplusplus
}
#endif

#endif /* QONCLAVE_COMMAND_H */
