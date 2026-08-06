/*
 * test_codec.c — canonical CBOR encoding and check-in decoding.
 *
 * The size assertions are the ones that matter most. spec/v1/profiles/minimal.md commits to a
 * no-media check-in fitting a LoRaWAN frame, and that single number decides whether the profile
 * is real or merely described. Everything else here protects the determinism the signature
 * scheme depends on.
 */

#include <stdio.h>
#include <string.h>

#include "qonclave/codec.h"
#include "qonclave/event.h"

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

/* Mirrors conformance/cases/checkin/minimal-lora-sized/input.json. */
static void build_minimal(qc_checkin_request_t *req, qc_event_t *ev)
{
    qc_event_init(ev, "e9", "s7", "threshold_crossed");
    qc_event_set_relative_time(ev, 412, 180);
    qc_event_add_metadata(ev, "m", "11");

    memset(req, 0, sizeof *req);
    req->node_id            = "s7";
    req->wake_counter       = 412;
    req->events[0]          = *ev;
    req->event_count        = 1;
    req->has_power          = true;
    req->power.battery_pct  = 62.0f;
    req->power.has_battery  = true;
    req->power.on_mains     = false;
}

static void test_head_is_shortest_form(void)
{
    uint8_t          buf[16];
    qc_cbor_writer_t w;

    qc_cbor_w_init(&w, buf, sizeof buf);
    qc_cbor_w_uint(&w, 5);
    CHECK(w.len == 1 && buf[0] == 0x05, "small uint uses a 1-byte head");

    qc_cbor_w_init(&w, buf, sizeof buf);
    qc_cbor_w_uint(&w, 200);
    CHECK(w.len == 2 && buf[0] == 0x18, "uint 200 uses the 1-byte-argument form");

    qc_cbor_w_init(&w, buf, sizeof buf);
    qc_cbor_w_uint(&w, 300);
    CHECK(w.len == 3 && buf[0] == 0x19, "uint 300 uses the 2-byte-argument form");

    /* A longer-than-necessary head is legal CBOR but not deterministic, so it would silently
     * break every signature. */
    qc_cbor_w_init(&w, buf, sizeof buf);
    qc_cbor_w_int(&w, -1);
    CHECK(w.len == 1 && buf[0] == 0x20, "negative -1 encodes as major 1, argument 0");
}

static void test_float_shortest(void)
{
    uint8_t          buf[16];
    qc_cbor_writer_t w;

    qc_cbor_w_init(&w, buf, sizeof buf);
    qc_cbor_w_float(&w, 62.0);
    CHECK(w.len == 3 && buf[0] == 0xf9, "62.0 fits half precision (3 bytes)");

    qc_cbor_w_init(&w, buf, sizeof buf);
    qc_cbor_w_float(&w, 0.1);
    CHECK(w.len == 9 && buf[0] == 0xfb, "0.1 needs double precision (9 bytes)");

    /* Round trip through the reader, since a shortest-form encoder is only useful if the value
     * survives it. */
    qc_cbor_w_init(&w, buf, sizeof buf);
    qc_cbor_w_float(&w, 62.5);
    qc_cbor_reader_t r;
    qc_cbor_r_init(&r, buf, w.len);
    double back = 0.0;
    CHECK(qc_cbor_r_float(&r, &back) && back == 62.5, "half-precision float round-trips exactly");
}

static void test_rfc3339(void)
{
    int64_t t = 0;

    /* 1785868860 = 2026-08-04T18:41:00Z, cross-checked against Python's datetime. */
    CHECK(qc_time_parse_rfc3339("2026-08-04T18:41:00Z", 20, &t) && t == 1785868860LL,
          "RFC 3339 with Z parses to the right epoch");

    CHECK(qc_time_parse_rfc3339("2026-08-04T13:41:00-05:00", 25, &t) && t == 1785868860LL,
          "a -05:00 offset yields the same instant as the Z form");

    CHECK(qc_time_parse_rfc3339("2026-08-04T18:41:00.123Z", 24, &t) && t == 1785868860LL,
          "fractional seconds are accepted and discarded");

    /* Leap-year and century boundaries are where a hand-rolled civil-date conversion usually
     * goes wrong, so pin them rather than trusting the algorithm by inspection. */
    CHECK(qc_time_parse_rfc3339("2024-02-29T00:00:00Z", 20, &t) && t == 1709164800LL,
          "a leap day converts correctly");
    CHECK(qc_time_parse_rfc3339("2000-03-01T00:00:00Z", 20, &t) && t == 951868800LL,
          "the day after the 2000 leap day converts correctly");
    CHECK(qc_time_parse_rfc3339("1970-01-01T00:00:00Z", 20, &t) && t == 0LL,
          "the epoch itself is zero");

    CHECK(!qc_time_parse_rfc3339("not-a-time", 10, &t), "garbage is rejected");
    CHECK(!qc_time_parse_rfc3339("2026-08-04", 10, &t), "a date with no time is rejected");
}

static void test_lora_size_bound(void)
{
    qc_checkin_request_t req;
    qc_event_t           ev;
    build_minimal(&req, &ev);

    uint8_t     buf[QC_CHECKIN_MAX_UPLINK];
    size_t      len = 0;
    qc_status_t rc;

    rc = qc_cbor_encode_checkin(&req, true, true, buf, sizeof buf, &len);
    printf("     minimal uplink: %zu bytes (int keys, identity omitted)\n", len);
    CHECK(rc == QC_OK && len <= 242, "minimal check-in fits a 242-byte LoRaWAN frame");

    size_t with_identity = 0;
    rc = qc_cbor_encode_checkin(&req, true, false, buf, sizeof buf, &with_identity);
    printf("     with identity : %zu bytes\n", with_identity);
    CHECK(rc == QC_OK && with_identity > len,
          "omitting identity on the minimal profile actually saves bytes");

    size_t string_keys = 0;
    rc = qc_cbor_encode_checkin(&req, false, false, buf, sizeof buf, &string_keys);
    printf("     string keys   : %zu bytes\n", string_keys);
    CHECK(rc == QC_OK && string_keys > with_identity, "integer keys are smaller than string keys");
}

static void test_measure_then_encode(void)
{
    qc_checkin_request_t req;
    qc_event_t           ev;
    build_minimal(&req, &ev);

    size_t needed = 0;
    /* Measuring must go through the same code path that encodes; a separate estimator is a second
     * implementation that will eventually disagree with the first. */
    qc_status_t rc = qc_cbor_encode_checkin(&req, true, true, NULL, 0, &needed);
    CHECK(rc == QC_ERR_BUFFER_TOO_SMALL && needed > 0, "measuring into a null buffer reports size");

    uint8_t buf[QC_CHECKIN_MAX_UPLINK];
    size_t  actual = 0;
    rc = qc_cbor_encode_checkin(&req, true, true, buf, sizeof buf, &actual);
    CHECK(rc == QC_OK && actual == needed, "the measured size matches the encoded size");

    uint8_t tiny[4];
    size_t  ignored = 0;
    rc = qc_cbor_encode_checkin(&req, true, true, tiny, sizeof tiny, &ignored);
    CHECK(rc == QC_ERR_BUFFER_TOO_SMALL, "a short buffer errors rather than truncating");
}

static void test_decode_downlink(void)
{
    /* Hand-built downlink: {1:"1.0", 2:tag(1)1785782460, 4:[{2:"cmd-1",3:"hub-a",6:"set_led",
     * 9:tag(1)1785786000}], 6:3600} */
    uint8_t          buf[128];
    qc_cbor_writer_t w;
    qc_cbor_w_init(&w, buf, sizeof buf);

    qc_cbor_w_map(&w, 4);
    qc_cbor_w_uint(&w, 1); qc_cbor_w_text(&w, "1.0");
    qc_cbor_w_uint(&w, 2); qc_cbor_w_epoch(&w, 1785782460LL);
    qc_cbor_w_uint(&w, 4);
    qc_cbor_w_array(&w, 1);
    qc_cbor_w_map(&w, 4);
    qc_cbor_w_uint(&w, 2); qc_cbor_w_text(&w, "cmd-1");
    qc_cbor_w_uint(&w, 3); qc_cbor_w_text(&w, "hub-a");
    qc_cbor_w_uint(&w, 6); qc_cbor_w_text(&w, "set_led");
    qc_cbor_w_uint(&w, 9); qc_cbor_w_epoch(&w, 1785786000LL);
    qc_cbor_w_uint(&w, 6); qc_cbor_w_uint(&w, 3600);

    qc_checkin_response_t resp;

    qc_status_t rc = qc_cbor_decode_checkin(buf, w.len, 1785782500LL, &resp);
    CHECK(rc == QC_OK, "downlink decodes");
    CHECK(resp.server_time_unix == 1785782460LL, "server_time is read from the epoch tag");
    CHECK(resp.has_next_checkin && resp.next_checkin_s == 3600, "next_checkin_s is read");
    CHECK(resp.command_count == 1 && strcmp(resp.commands[0].action, "set_led") == 0,
          "a live command is delivered");

    /* Same document, but the device wakes after the command expired. */
    rc = qc_cbor_decode_checkin(buf, w.len, 1785790000LL, &resp);
    CHECK(rc == QC_OK && resp.command_count == 0,
          "an expired command is dropped by the decoder, not handed to the application");
}

static void test_unknown_fields_survive(void)
{
    /* A v1.7 hub sends a field this v1.0 decoder has never heard of. */
    uint8_t          buf[64];
    qc_cbor_writer_t w;
    qc_cbor_w_init(&w, buf, sizeof buf);

    qc_cbor_w_map(&w, 3);
    qc_cbor_w_uint(&w, 2); qc_cbor_w_epoch(&w, 1785782460LL);
    qc_cbor_w_uint(&w, 99); qc_cbor_w_array(&w, 2);
    qc_cbor_w_text(&w, "future"); qc_cbor_w_uint(&w, 7);
    qc_cbor_w_uint(&w, 6); qc_cbor_w_uint(&w, 60);

    qc_checkin_response_t resp;
    qc_status_t rc = qc_cbor_decode_checkin(buf, w.len, 0, &resp);

    CHECK(rc == QC_OK, "an unknown field does not fail the document");
    CHECK(resp.has_next_checkin && resp.next_checkin_s == 60,
          "fields after an unknown one are still read");
}

static void test_rejects_bad_input(void)
{
    qc_checkin_response_t resp;
    const uint8_t truncated[] = { 0xa4, 0x01 };   /* map(4) then nothing */
    CHECK(qc_cbor_decode_checkin(truncated, sizeof truncated, 0, &resp) != QC_OK,
          "a truncated document is rejected");

    const uint8_t not_a_map[] = { 0x63, 'a', 'b', 'c' };
    CHECK(qc_cbor_decode_checkin(not_a_map, sizeof not_a_map, 0, &resp) != QC_OK,
          "a non-map top level is rejected");

    /* Indefinite-length items are legal CBOR but forbidden by deterministic encoding, and
     * accepting them would let a peer produce bytes we cannot reproduce for signing. */
    const uint8_t indefinite[] = { 0xbf, 0x01, 0x01, 0xff };
    CHECK(qc_cbor_decode_checkin(indefinite, sizeof indefinite, 0, &resp) != QC_OK,
          "indefinite-length encoding is rejected");
}

int main(void)
{
    test_head_is_shortest_form();
    test_float_shortest();
    test_rfc3339();
    test_lora_size_bound();
    test_measure_then_encode();
    test_decode_downlink();
    test_unknown_fields_survive();
    test_rejects_bad_input();

    printf("\n%s\n", failures ? "FAILURES" : "all codec checks passed");
    return failures ? 1 : 0;
}
