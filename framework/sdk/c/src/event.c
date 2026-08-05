/*
 * event.c — struct helpers.
 *
 * Real, not stubbed: pure struct manipulation with no I/O, so there is nothing to defer.
 */

#include "qonclave/event.h"

#include <string.h>

static void copy_bounded(char *dst, size_t cap, const char *src)
{
    if (!src) {
        dst[0] = '\0';
        return;
    }
    size_t n = strlen(src);
    if (n >= cap) {
        n = cap - 1;
    }
    memcpy(dst, src, n);
    dst[n] = '\0';
}

void qc_event_init(qc_event_t *ev, const char *event_id, const char *node_id, const char *trigger)
{
    /* Zeroing matters more than it looks: the has_* flags decide what gets encoded, so a stale
     * flag on a reused stack slot emits a field full of garbage. */
    memset(ev, 0, sizeof *ev);
    copy_bounded(ev->event_id, sizeof ev->event_id, event_id);
    copy_bounded(ev->source_node_id, sizeof ev->source_node_id, node_id);
    copy_bounded(ev->trigger, sizeof ev->trigger, trigger);
}

void qc_event_set_relative_time(qc_event_t *ev, uint32_t wake_counter, uint32_t ms_since_wake)
{
    ev->relative.wake_counter  = wake_counter;
    ev->relative.ms_since_wake = ms_since_wake;
    ev->has_relative           = true;

    /* Mutually exclusive with an absolute timestamp. Sending both from a device with no
     * trustworthy clock would assert something it cannot know. */
    ev->has_timestamp = false;
}

bool qc_event_add_metadata(qc_event_t *ev, const char *key, const char *value)
{
    const uint8_t cap = (uint8_t)(sizeof ev->metadata / sizeof ev->metadata[0]);
    if (ev->metadata_count >= cap) {
        return false;
    }
    ev->metadata[ev->metadata_count].key   = key;
    ev->metadata[ev->metadata_count].value = value;
    ev->metadata_count++;
    return true;
}
