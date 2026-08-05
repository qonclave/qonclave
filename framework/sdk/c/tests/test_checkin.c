/*
 * test_checkin.c — the whole exchange, against a stand-in hub.
 *
 * This is the test that says whether the `minimal` profile is actually implemented: one round
 * trip, signed both ways, expired commands dropped, and a transport failure handled without
 * burning the battery on retries.
 */

#include <stdio.h>
#include <string.h>

#include "qonclave/checkin.h"
#include "qonclave/codec.h"
#include "qonclave/event.h"
#include "qonclave/psk.h"

#include "../ports/posix/qc_port_posix.h"
#include "../src/cbor.h"

static int failures;

#define CHECK(cond, msg)                                         \
    do {                                                         \
        if (!(cond)) {                                           \
            printf("FAIL %s:%d  %s\n", __FILE__, __LINE__, msg); \
            failures++;                                          \
        } else {                                                 \
            printf("ok   %s\n", msg);                            \
        }                                                        \
    } while (0)

static const uint8_t PSK[16] = { 0xde, 0xad, 0xbe, 0xef, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12 };

/* What the stand-in hub should do this time. */
static struct {
    bool    verify_uplink;      /* assert the uplink MAC is valid */
    bool    uplink_was_valid;
    bool    send_expired_cmd;
    bool    corrupt_signature;
    bool    fail_transport;
    int64_t command_expiry;
} s_hub;

static int s_calls;

static int fake_hub(const uint8_t *req, size_t req_len,
                    uint8_t *resp, size_t resp_cap, void *ud)
{
    (void)ud;

    s_calls++;

    if (s_hub.fail_transport) {
        return -1;
    }

    if (s_hub.verify_uplink && req_len > QC_PSK_SIG_LEN) {
        const size_t doc = req_len - QC_PSK_SIG_LEN;
        s_hub.uplink_was_valid =
            (qc_psk_verify(PSK, sizeof PSK, req, doc, QC_PSK_UPLINK, req + doc) == 0);
    }

    uint8_t          body[256];
    qc_cbor_writer_t w;
    qc_cbor_w_init(&w, body, sizeof body);

    qc_cbor_w_map(&w, 3);
    qc_cbor_w_uint(&w, 1); qc_cbor_w_text(&w, "1.0");
    qc_cbor_w_uint(&w, 2); qc_cbor_w_epoch(&w, 1785868860LL);
    qc_cbor_w_uint(&w, 4);
    qc_cbor_w_array(&w, 1);
    qc_cbor_w_map(&w, 4);
    qc_cbor_w_uint(&w, 2); qc_cbor_w_text(&w, "cmd-1");
    qc_cbor_w_uint(&w, 3); qc_cbor_w_text(&w, "hub-a");
    qc_cbor_w_uint(&w, 6); qc_cbor_w_text(&w, "set_led");
    qc_cbor_w_uint(&w, 9); qc_cbor_w_epoch(&w, s_hub.command_expiry);

    if (w.overflow || w.len + QC_PSK_SIG_LEN > resp_cap) {
        return -1;
    }

    memcpy(resp, body, w.len);
    qc_psk_sign(PSK, sizeof PSK, resp, w.len, QC_PSK_DOWNLINK, resp + w.len);

    if (s_hub.corrupt_signature) {
        resp[w.len] ^= 0x01u;
    }
    return (int)(w.len + QC_PSK_SIG_LEN);
}

static void build_request(qc_checkin_request_t *req, qc_event_t *ev)
{
    qc_event_init(ev, "e9", "s7", "threshold_crossed");
    qc_event_set_relative_time(ev, 412, 180);
    qc_event_add_metadata(ev, "m", "11");

    memset(req, 0, sizeof *req);
    req->node_id           = "s7";
    req->wake_counter      = 412;
    req->events[0]         = *ev;
    req->event_count       = 1;
    req->has_power         = true;
    req->power.battery_pct = 62.0f;
    req->power.has_battery = true;
    req->power.on_mains    = false;
}

static qc_checkin_config_t config(void)
{
    qc_checkin_config_t cfg;
    memset(&cfg, 0, sizeof cfg);
    cfg.psk           = PSK;
    cfg.psk_len       = sizeof PSK;
    cfg.use_int_keys  = true;
    cfg.omit_identity = true;
    cfg.timeout_ms    = 5000;
    return cfg;
}

int main(void)
{
    qc_port_posix_set_handler(fake_hub, NULL);

    qc_checkin_request_t  req;
    qc_event_t            ev;
    qc_checkin_response_t resp;
    qc_checkin_config_t   cfg = config();

    /* --- the happy path ----------------------------------------------------------------- */
    memset(&s_hub, 0, sizeof s_hub);
    s_hub.verify_uplink  = true;
    s_hub.command_expiry = 1785900000LL;   /* comfortably in the future */
    build_request(&req, &ev);

    qc_status_t rc = qc_checkin_perform(&req, &cfg, 1785868900LL, &resp);

    CHECK(rc == QC_OK, "a complete check-in succeeds");
    CHECK(s_hub.uplink_was_valid, "the hub can verify the uplink signature we produced");
    CHECK(resp.server_time_unix == 1785868860LL, "authoritative time comes back from the hub");
    CHECK(resp.command_count == 1 && strcmp(resp.commands[0].action, "set_led") == 0,
          "a mailbox command is delivered");

    printf("     uplink frame: %zu bytes (document + 32-byte MAC)\n",
           qc_port_posix_last_request_len());
    CHECK(qc_port_posix_last_request_len() <= 242,
          "the signed uplink still fits a 242-byte LoRaWAN frame");

    /* --- exactly one round trip ---------------------------------------------------------- */
    /* The whole profile rests on this number. If perform() ever makes a second call — to fetch
     * time, or to retry — a daily sensor's radio budget doubles and nothing else in the design
     * compensates. */
    memset(&s_hub, 0, sizeof s_hub);
    s_hub.command_expiry = 1785900000LL;
    s_calls = 0;
    build_request(&req, &ev);

    rc = qc_checkin_perform(&req, &cfg, 1785868900LL, &resp);
    CHECK(rc == QC_OK && s_calls == 1, "a check-in costs exactly one round trip");

    /* --- an expired command must not reach the application ------------------------------- */
    memset(&s_hub, 0, sizeof s_hub);
    s_hub.command_expiry = 1785868800LL;    /* already past when the device wakes */
    build_request(&req, &ev);

    rc = qc_checkin_perform(&req, &cfg, 1785868900LL, &resp);
    CHECK(rc == QC_OK && resp.command_count == 0,
          "a command that expired while the device slept is dropped");

    /* --- a spoofed hub is rejected before parsing ---------------------------------------- */
    memset(&s_hub, 0, sizeof s_hub);
    s_hub.corrupt_signature = true;
    s_hub.command_expiry    = 1785900000LL;
    build_request(&req, &ev);

    rc = qc_checkin_perform(&req, &cfg, 1785868900LL, &resp);
    CHECK(rc == QC_ERR_SIGNATURE, "a downlink with a bad MAC is rejected");

    /* --- the wrong key is rejected -------------------------------------------------------- */
    memset(&s_hub, 0, sizeof s_hub);
    s_hub.command_expiry = 1785900000LL;
    build_request(&req, &ev);

    const uint8_t     other[16] = { 0 };
    qc_checkin_config_t bad_cfg = cfg;
    bad_cfg.psk = other;

    rc = qc_checkin_perform(&req, &bad_cfg, 1785868900LL, &resp);
    CHECK(rc == QC_ERR_SIGNATURE, "a downlink signed with a different key is rejected");

    /* --- a dead link fails cleanly -------------------------------------------------------- */
    memset(&s_hub, 0, sizeof s_hub);
    s_hub.fail_transport = true;
    build_request(&req, &ev);

    rc = qc_checkin_perform(&req, &cfg, 1785868900LL, &resp);
    CHECK(rc == QC_ERR_TRANSPORT,
          "an unreachable hub reports a transport error rather than blocking or retrying");

    /* --- no handler installed at all ------------------------------------------------------ */
    qc_port_posix_set_handler(NULL, NULL);
    build_request(&req, &ev);
    rc = qc_checkin_perform(&req, &cfg, 1785868900LL, &resp);
    CHECK(rc == QC_ERR_TRANSPORT, "no hub configured is a transport error, not a crash");

    printf("\n%s\n", failures ? "FAILURES" : "all checkin checks passed");
    return failures ? 1 : 0;
}
