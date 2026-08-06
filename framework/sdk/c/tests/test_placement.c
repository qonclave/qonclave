/*
 * test_placement.c — C placement must agree with Python placement.
 *
 * These mirror sdk/python/tests/test_placement.py case for case. A placement decision that
 * differs between the C sensor and the Python hub is a bug no amount of schema validation would
 * catch, so the two suites are deliberately parallel. If you change one, change the other.
 */

#include <stdio.h>
#include <string.h>

#include "qonclave/placement.h"

static int failures;

#define CHECK(cond, msg)                                        \
    do {                                                        \
        if (!(cond)) {                                          \
            printf("FAIL %s:%d  %s\n", __FILE__, __LINE__, msg); \
            failures++;                                         \
        } else {                                                \
            printf("ok   %s\n", msg);                           \
        }                                                       \
    } while (0)

static qc_tier_set_t full_mesh(bool multi_tenant_compute)
{
    qc_tier_set_t t;
    memset(&t, 0, sizeof t);

    t.candidates[0].tier           = QC_TIER_EDGE;
    t.candidates[0].node_id        = "edge-1";
    t.candidates[0].reachable      = true;
    t.candidates[0].is_local       = true;
    t.candidates[0].max_complexity = QC_COMPLEXITY_DETECT;

    t.candidates[1].tier           = QC_TIER_HUB;
    t.candidates[1].node_id        = "hub-alpha";
    t.candidates[1].reachable      = true;
    t.candidates[1].max_complexity = QC_COMPLEXITY_VLM_REASON;

    t.candidates[2].tier           = QC_TIER_COMPUTE;
    t.candidates[2].node_id        = "npu-1";
    t.candidates[2].reachable      = true;
    t.candidates[2].multi_tenant   = multi_tenant_compute;
    t.candidates[2].max_complexity = QC_COMPLEXITY_LLM_REASON;

    t.count = 3;
    return t;
}

/* A policy that always asks for one tier, so the tests exercise the mechanism not the heuristic. */
static qc_tier_t   g_want;
static qc_on_miss_t g_on_miss;
static qc_tier_t   g_deny[2];
static uint8_t     g_deny_n;

static qc_placement_t fixed(const qc_task_t *task, const qc_tier_set_t *tiers, void *ud)
{
    (void)task;
    (void)tiers;
    (void)ud;

    qc_placement_t p;
    memset(&p, 0, sizeof p);
    p.tier    = g_want;
    p.on_miss = g_on_miss;
    for (uint8_t i = 0; i < g_deny_n; i++) {
        p.deny[i] = g_deny[i];
    }
    p.deny_count = g_deny_n;
    return p;
}

static void reset_policy(qc_tier_t want)
{
    g_want    = want;
    g_on_miss = QC_ON_MISS_FAIL;
    g_deny_n  = 0;
}

int main(void)
{
    qc_task_t task;
    memset(&task, 0, sizeof task);

    /* --- no_egress must not reach a shared compute node, even when the policy asks --------- */
    {
        qc_tier_set_t tiers = full_mesh(true);
        task.privacy    = QC_PRIVACY_NO_EGRESS;
        task.complexity = QC_COMPLEXITY_CLASSIFY;
        reset_policy(QC_TIER_COMPUTE);

        const qc_tier_state_t *got = qc_placement_resolve(&task, &tiers, fixed, NULL, NULL);
        CHECK(got == NULL || got->tier != QC_TIER_COMPUTE,
              "no_egress task denied a shared multi-tenant compute node");
    }

    /* --- but a single-tenant compute node is fine ------------------------------------------ */
    {
        qc_tier_set_t tiers = full_mesh(false);
        task.privacy    = QC_PRIVACY_NO_EGRESS;
        task.complexity = QC_COMPLEXITY_CLASSIFY;
        reset_policy(QC_TIER_COMPUTE);

        const qc_tier_state_t *got = qc_placement_resolve(&task, &tiers, fixed, NULL, NULL);
        CHECK(got != NULL && got->tier == QC_TIER_COMPUTE,
              "no_egress permits a dedicated single-tenant compute node");
    }

    /* --- local_only never leaves the device ------------------------------------------------ */
    {
        qc_tier_set_t tiers = full_mesh(true);
        task.privacy    = QC_PRIVACY_LOCAL_ONLY;
        task.complexity = QC_COMPLEXITY_DETECT;
        reset_policy(QC_TIER_COMPUTE);
        g_on_miss = QC_ON_MISS_DEGRADE;

        const qc_tier_state_t *got = qc_placement_resolve(&task, &tiers, fixed, NULL, NULL);
        CHECK(got != NULL && got->is_local, "local_only degraded to the local node");
    }

    /* --- capability pruning ----------------------------------------------------------------- */
    {
        qc_tier_set_t tiers = full_mesh(true);
        task.privacy    = QC_PRIVACY_UNRESTRICTED;
        task.complexity = QC_COMPLEXITY_VLM_REASON;
        reset_policy(QC_TIER_EDGE);

        /* edge maxes out at DETECT, so it must be skipped rather than handed impossible work */
        const qc_tier_state_t *got = qc_placement_resolve(&task, &tiers, fixed, NULL, NULL);
        CHECK(got == NULL, "a tier that cannot serve the complexity is not selected");
    }

    /* --- default: tight deadline stays local ----------------------------------------------- */
    {
        qc_tier_set_t tiers = full_mesh(true);
        memset(&task, 0, sizeof task);
        task.complexity   = QC_COMPLEXITY_DETECT;
        task.has_deadline = true;
        task.deadline_ms  = 30;

        const qc_tier_state_t *got = qc_placement_resolve(&task, &tiers, NULL, NULL, NULL);
        CHECK(got != NULL && got->tier == QC_TIER_EDGE,
              "default keeps a 30ms deadline on the local node");
    }

    /* --- default: VLM work goes to compute, falls back when absent -------------------------- */
    {
        qc_tier_set_t tiers = full_mesh(true);
        memset(&task, 0, sizeof task);
        task.complexity = QC_COMPLEXITY_VLM_REASON;

        const qc_tier_state_t *got = qc_placement_resolve(&task, &tiers, NULL, NULL, NULL);
        CHECK(got != NULL && got->tier == QC_TIER_COMPUTE, "default sends VLM work to compute");

        tiers.count = 2; /* drop the compute node — the monolith case */
        got = qc_placement_resolve(&task, &tiers, NULL, NULL, NULL);
        CHECK(got != NULL && got->tier == QC_TIER_HUB,
              "default falls back to the hub with no compute node present");
    }

    /* --- home hub preferred over a peer ----------------------------------------------------- */
    {
        qc_tier_set_t tiers = full_mesh(true);
        tiers.candidates[3].tier           = QC_TIER_HUB;
        tiers.candidates[3].node_id        = "hub-beta";
        tiers.candidates[3].reachable      = true;
        tiers.candidates[3].is_peer        = true;
        tiers.candidates[3].max_complexity = QC_COMPLEXITY_VLM_REASON;
        tiers.count                        = 4;

        memset(&task, 0, sizeof task);
        task.complexity = QC_COMPLEXITY_CLASSIFY;
        reset_policy(QC_TIER_HUB);

        const qc_tier_state_t *got = qc_placement_resolve(&task, &tiers, fixed, NULL, NULL);
        CHECK(got != NULL && strcmp(got->node_id, "hub-alpha") == 0,
              "home hub wins over an authorized peer when otherwise equal");
    }

    printf("\n%s\n", failures ? "FAILURES" : "all placement checks passed");
    return failures ? 1 : 0;
}
