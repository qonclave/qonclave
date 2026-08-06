/*
 * command.c — command helpers.
 *
 * Real, not stubbed. The expiry check in particular is a correctness rule rather than plumbing:
 * a device that wakes to a day-old actuation command and performs it is a security failure, not
 * a late delivery.
 */

#include "qonclave/command.h"

#include <string.h>

bool qc_command_expired(const qc_command_t *cmd, int64_t now_unix)
{
    if (!cmd->has_expiry) {
        return false;
    }
    return now_unix >= cmd->expires_at_unix;
}

const char *qc_command_param(const qc_command_t *cmd, const char *key)
{
    for (uint8_t i = 0; i < cmd->param_count; i++) {
        if (cmd->params[i].key && strcmp(cmd->params[i].key, key) == 0) {
            return cmd->params[i].value;
        }
    }
    return NULL;
}
