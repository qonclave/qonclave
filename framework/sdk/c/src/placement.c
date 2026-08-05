/*
 * placement.c — the placement mechanism in C.
 *
 * Deliberately implemented for real rather than stubbed, for two reasons.
 *
 * First, it is pure logic: no I/O, no crypto, no allocation, so there is nothing to defer. Second
 * and more importantly, having it in both bindings is what lets the two be compared. A placement
 * decision that differs between the C sensor and the Python hub is a bug that no amount of schema
 * validation would catch.
 *
 * Mirrors sdk/python/src/qonclave/placement/ladder.py. Keep them in step.
 */

#include "qonclave/placement.h"

#include <stddef.h>

static bool tier_in(const qc_tier_t *list, uint8_t count, qc_tier_t tier)
{
    for (uint8_t i = 0; i < count; i++) {
        if (list[i] == tier) {
            return true;
        }
    }
    return false;
}

/*
 * Denials the framework applies regardless of what the callback returned.
 *
 * NO_EGRESS denies only *shared* compute. A single-tenant node is not an egress risk, and
 * blanket-denying it would push work back to the hub for no privacy gain — the kind of
 * over-strict rule that gets disabled in production and takes the real protection with it.
 */
static uint8_t framework_denials(const qc_task_t *task,
                                 const qc_tier_set_t *tiers,
                                 qc_tier_t *out)
{
    uint8_t n = 0;

    if (task->privacy == QC_PRIVACY_LOCAL_ONLY) {
        out[n++] = QC_TIER_HUB;
        out[n++] = QC_TIER_COMPUTE;
        return n;
    }

    if (task->privacy == QC_PRIVACY_NO_EGRESS) {
        for (uint8_t i = 0; i < tiers->count; i++) {
            const qc_tier_state_t *c = &tiers->candidates[i];
            if (c->tier == QC_TIER_COMPUTE && c->multi_tenant) {
                out[n++] = QC_TIER_COMPUTE;
                break;
            }
        }
    }
    return n;
}

/* Least-loaded reachable candidate at a tier. Home hubs win ties against peers: the grant was
 * already verified, so this is about blast radius and about the home hub holding our state. */
static const qc_tier_state_t *pick(const qc_tier_set_t *tiers, qc_tier_t tier)
{
    const qc_tier_state_t *best = NULL;

    for (uint8_t i = 0; i < tiers->count; i++) {
        const qc_tier_state_t *c = &tiers->candidates[i];
        if (c->tier != tier || !c->reachable) {
            continue;
        }
        if (best == NULL) {
            best = c;
            continue;
        }
        if (best->is_peer != c->is_peer) {
            best = c->is_peer ? best : c;
        } else if (c->cpu_percent < best->cpu_percent) {
            best = c;
        }
    }
    return best;
}

static const qc_tier_state_t *local_of(const qc_tier_set_t *tiers)
{
    for (uint8_t i = 0; i < tiers->count; i++) {
        if (tiers->candidates[i].is_local) {
            return &tiers->candidates[i];
        }
    }
    return NULL;
}

const qc_tier_state_t *qc_placement_resolve(const qc_task_t *task,
                                            const qc_tier_set_t *tiers,
                                            qc_placement_fn decide,
                                            void *user_data,
                                            qc_on_miss_t *out_on_miss)
{
    qc_placement_t p = decide ? decide(task, tiers, user_data)
                              : qc_placement_default(task, tiers, user_data);

    if (out_on_miss) {
        *out_on_miss = p.on_miss;
    }

    /* Union of what the policy denied and what the framework denies. A callback selecting
     * QC_TIER_COMPUTE for a NO_EGRESS task is CORRECTED, not obeyed — the isolation guarantee
     * must not depend on every firmware author remembering it. */
    qc_tier_t denied[6];
    uint8_t   denied_n = 0;

    for (uint8_t i = 0; i < p.deny_count && denied_n < 6; i++) {
        denied[denied_n++] = p.deny[i];
    }
    qc_tier_t fw[3];
    uint8_t   fw_n = framework_denials(task, tiers, fw);
    for (uint8_t i = 0; i < fw_n && denied_n < 6; i++) {
        denied[denied_n++] = fw[i];
    }

    qc_tier_t chain[4];
    uint8_t   chain_n = 0;
    chain[chain_n++] = p.tier;
    for (uint8_t i = 0; i < p.fallback_count && chain_n < 4; i++) {
        chain[chain_n++] = p.fallback[i];
    }

    for (uint8_t i = 0; i < chain_n; i++) {
        if (tier_in(denied, denied_n, chain[i])) {
            continue;
        }
        const qc_tier_state_t *node = pick(tiers, chain[i]);
        if (node == NULL) {
            continue;
        }
        if (node->max_complexity < task->complexity) {
            continue;
        }
        return node;
    }

    if (p.on_miss == QC_ON_MISS_DEGRADE) {
        const qc_tier_state_t *local = local_of(tiers);
        if (local && local->reachable && !tier_in(denied, denied_n, local->tier)) {
            return local;
        }
    }

    return NULL;
}

qc_placement_t qc_placement_default(const qc_task_t *task,
                                    const qc_tier_set_t *tiers,
                                    void *user_data)
{
    (void)tiers;
    (void)user_data;

    qc_placement_t p = {
        .tier           = QC_TIER_EDGE,
        .fallback_count = 0,
        .deny_count     = 0,
        .on_miss        = QC_ON_MISS_FAIL,
        .reason         = "default: triage locally",
    };

    if (task->privacy == QC_PRIVACY_LOCAL_ONLY) {
        p.on_miss = QC_ON_MISS_DEGRADE;
        p.reason  = "privacy=local_only";
        return p;
    }

    /* Below this, a round trip is a material fraction of the budget. */
    if (task->has_deadline && task->deadline_ms < 50) {
        p.on_miss = QC_ON_MISS_DEGRADE;
        p.reason  = "deadline leaves no room for a hop";
        return p;
    }

    if (task->complexity >= QC_COMPLEXITY_VLM_REASON) {
        p.tier             = QC_TIER_COMPUTE;
        p.fallback[0]      = QC_TIER_HUB;
        p.fallback[1]      = QC_TIER_EDGE;
        p.fallback_count   = 2;
        p.reason           = "complexity wants a real accelerator";
        return p;
    }

    /* Anything the sensor cannot answer itself goes up. On a duty-cycled device this is nearly
     * the whole of the decision, and that is fine — the value is that it lives in one place with
     * the same signature as on the hub. */
    p.fallback[0]    = QC_TIER_HUB;
    p.fallback_count = 1;
    return p;
}
