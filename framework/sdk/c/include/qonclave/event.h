/*
 * event.h — an observation, as a C struct.
 *
 * Mirrors spec/v1/json-schema/edge-event.schema.json. Fixed-size and heapless: an event is
 * assembled on the stack, encoded, and discarded.
 *
 * The notable difference from the JSON view is time. A duty-cycled device does not carry an
 * RFC 3339 string around — after a day of deep sleep its RTC has drifted or was never set — so it
 * reports a wake counter and lets the hub stamp authoritative time on receipt.
 */

#ifndef QONCLAVE_EVENT_H
#define QONCLAVE_EVENT_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#ifndef QC_MAX_ID_LEN
#define QC_MAX_ID_LEN 33
#endif

#ifndef QC_MAX_TRIGGER_LEN
#define QC_MAX_TRIGGER_LEN 33
#endif

/* Ordered. A node advertising max_complexity = QC_COMPLEXITY_DETECT cannot serve a VLM task, and
 * placement prunes on this before any policy is consulted. Values match the Python IntEnum and
 * the protobuf enum minus its UNSPECIFIED offset. */
typedef enum {
    QC_COMPLEXITY_HEURISTIC  = 0,
    QC_COMPLEXITY_DETECT     = 1,
    QC_COMPLEXITY_CLASSIFY   = 2,
    QC_COMPLEXITY_EMBED      = 3,
    QC_COMPLEXITY_VLM_REASON = 4,
    QC_COMPLEXITY_LLM_REASON = 5
} qc_complexity_t;

typedef enum {
    QC_URGENCY_BACKGROUND = 0,
    QC_URGENCY_NORMAL     = 1,
    QC_URGENCY_HIGH       = 2,
    QC_URGENCY_CRITICAL   = 3
} qc_urgency_t;

typedef enum {
    QC_PRIVACY_UNRESTRICTED = 0,
    QC_PRIVACY_NO_EGRESS    = 1,
    QC_PRIVACY_LOCAL_ONLY   = 2
} qc_privacy_t;

typedef struct {
    float   battery_pct;
    bool    has_battery;
    bool    on_mains;
    float   thermal_headroom_c;
    bool    has_thermal;
    uint32_t duty_cycle_s;   /* 86400 for a daily sensor; 0 if always on */
} qc_power_t;

typedef struct {
    uint32_t wake_counter;
    uint32_t ms_since_wake;
    uint32_t uncertainty_s;
    bool     has_uncertainty;
} qc_relative_time_t;

typedef struct {
    qc_complexity_t complexity;
    qc_urgency_t    urgency;
    qc_privacy_t    privacy;
    uint32_t        deadline_ms;
    uint32_t        remaining_ms;   /* MUST be decremented before escalating */
    bool            has_deadline;
} qc_task_t;

typedef struct {
    char event_id[QC_MAX_ID_LEN];
    char source_node_id[QC_MAX_ID_LEN];
    char trigger[QC_MAX_TRIGGER_LEN];

    /* Exactly one of these carries the time. A minimal-profile device uses relative. */
    int64_t            timestamp_unix;
    bool               has_timestamp;
    qc_relative_time_t relative;
    bool               has_relative;

    float confidence;
    bool  has_confidence;

    qc_task_t task;
    bool      has_task;

    /* Optional media. Normally absent on constrained links — a temperature reading belongs in
     * the metadata pairs below, and a LoRa frame could not carry a frame anyway. */
    const uint8_t *payload;
    size_t         payload_len;
    const char    *payload_media_type;

    /* Flat key/value metadata. Deliberately not a nested document: arbitrary nesting needs a
     * parser this profile cannot afford. */
    struct {
        const char *key;
        const char *value;
    } metadata[8];
    uint8_t metadata_count;
} qc_event_t;

/* Zero-initialize and set the required fields. Always use this rather than assigning members on
 * an uninitialized struct — the `has_*` flags decide what gets encoded, and a stale flag emits a
 * field full of stack garbage. */
void qc_event_init(qc_event_t *ev, const char *event_id, const char *node_id, const char *trigger);

/* Attach device-relative time. Use when the device has no trustworthy clock. */
void qc_event_set_relative_time(qc_event_t *ev, uint32_t wake_counter, uint32_t ms_since_wake);

/* Append a metadata pair. Returns false if the fixed table is full. */
bool qc_event_add_metadata(qc_event_t *ev, const char *key, const char *value);

#ifdef __cplusplus
}
#endif

#endif /* QONCLAVE_EVENT_H */
