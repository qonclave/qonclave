/*
 * placement.h — the placement decision, as a callback.
 *
 * Same shape as the Python `PlacementPolicy` ABC: the framework measures the facts, the developer
 * decides, the framework enforces. Dropping the rule DSL from the design is what makes this
 * header possible at all — there is no evaluator, no parser, and no config file on the device.
 *
 * On a duty-cycled sensor this is usually a constant ("triage here, escalate the rest"), and that
 * is fine. The value of having it at all is that the decision sits in one auditable place with
 * the same signature as on the hub, rather than as thresholds scattered through application code.
 *
 * Docs: framework/docs/PLACEMENT.md
 */

#ifndef QONCLAVE_PLACEMENT_H
#define QONCLAVE_PLACEMENT_H

#include <stdbool.h>
#include <stdint.h>

#include "qonclave/event.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Ordered by distance from the sensor, which is also increasing latency, increasing energy per
 * task, and increasing blast radius if the node is compromised. */
typedef enum {
    QC_TIER_EDGE    = 0,
    QC_TIER_HUB     = 1,
    QC_TIER_COMPUTE = 2
} qc_tier_t;

#ifndef QC_MAX_TIER_CANDIDATES
#define QC_MAX_TIER_CANDIDATES 4
#endif

/* MEASURED state of one candidate. Nothing the application declares appears here — that lives in
 * qc_task_t on the event. Keeping the two apart is what makes a surprising placement debuggable:
 * either the measurement was wrong or the decision was, and they are different bugs. */
typedef struct {
    qc_tier_t   tier;
    const char *node_id;
    bool        reachable;
    bool        is_local;
    bool        is_peer;        /* a hub other than the one that commissioned us */
    bool        multi_tenant;   /* disqualified from QC_PRIVACY_NO_EGRESS work */

    uint32_t        rtt_ms;
    uint8_t         cpu_percent;
    qc_complexity_t max_complexity;
    qc_power_t      power;
} qc_tier_state_t;

typedef struct {
    qc_tier_state_t candidates[QC_MAX_TIER_CANDIDATES];
    uint8_t         count;
} qc_tier_set_t;

typedef enum {
    QC_ON_MISS_FAIL    = 0,  /* give up; the caller decides */
    QC_ON_MISS_DEGRADE = 1,  /* run locally with a smaller model */
    QC_ON_MISS_DEFER   = 2   /* spool and retry — possibly tomorrow */
} qc_on_miss_t;

typedef struct {
    qc_tier_t    tier;
    qc_tier_t    fallback[3];
    uint8_t      fallback_count;
    qc_tier_t    deny[3];
    uint8_t      deny_count;
    qc_on_miss_t on_miss;
    const char  *reason;      /* surfaced by the host tooling; worth setting */
} qc_placement_t;

/*
 * The developer's decision function.
 *
 * `tiers` has already been pruned of candidates that cannot serve `task->complexity`, so an
 * implementation never has to defend against being handed an impossible tier.
 *
 * Do not signal "cannot run here" by returning an error — return a tier with a fallback chain and
 * let qc_placement_resolve walk it.
 */
typedef qc_placement_t (*qc_placement_fn)(const qc_task_t *task,
                                          const qc_tier_set_t *tiers,
                                          void *user_data);

/*
 * Resolve a decision to a concrete node.
 *
 * Applies the framework's own denials on top of whatever the callback returned. A callback that
 * selects QC_TIER_COMPUTE for a QC_PRIVACY_NO_EGRESS task is CORRECTED, not obeyed — the
 * isolation guarantee must not depend on every firmware author remembering it.
 *
 * Returns NULL when no permitted tier can serve the task; `out_on_miss` then says what the
 * caller should do about it.
 */
const qc_tier_state_t *qc_placement_resolve(const qc_task_t *task,
                                            const qc_tier_set_t *tiers,
                                            qc_placement_fn decide,
                                            void *user_data,
                                            qc_on_miss_t *out_on_miss);

/*
 * The default: triage locally, escalate anything heavier.
 *
 * Correct for the overwhelming majority of duty-cycled sensors, and the reason most firmware
 * never needs to write a callback at all.
 */
qc_placement_t qc_placement_default(const qc_task_t *task,
                                    const qc_tier_set_t *tiers,
                                    void *user_data);

#ifdef __cplusplus
}
#endif

#endif /* QONCLAVE_PLACEMENT_H */
