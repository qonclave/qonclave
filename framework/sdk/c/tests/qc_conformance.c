/*
 * qc_conformance.c — prove this binding agrees with the Python one.
 *
 * The fixtures are JSON, and this device has no JSON parser and should not grow one. So the
 * cross-check runs the other way: C builds the documented fixture from a struct, encodes it
 * canonically, and writes the bytes out. A Python test then decodes those bytes and asserts they
 * mean the same thing as conformance/cases/checkin/minimal-lora-sized/input.json.
 *
 * That direction is also the one that matters operationally — device to hub is the traffic that
 * exists, and it is the direction where a disagreement would silently drop real sensor data.
 *
 * Usage: qc_conformance <cases-dir> [emit-dir]
 */

#include <stdio.h>
#include <string.h>

#include "qonclave/checkin.h"
#include "qonclave/codec.h"
#include "qonclave/command.h"
#include "qonclave/event.h"
#include "qonclave/psk.h"

static int failures;

#define CHECK(cond, msg)                            \
    do {                                            \
        if (!(cond)) {                              \
            printf("FAIL %s\n", msg);               \
            failures++;                             \
        } else {                                    \
            printf("ok   %s\n", msg);               \
        }                                           \
    } while (0)

/*
 * Mirrors conformance/cases/checkin/minimal-lora-sized/input.json field for field.
 *
 * Hardcoded rather than parsed, deliberately: a JSON parser is exactly the kind of dependency the
 * minimal profile exists to avoid, and the Python side already proves the fixture parses.
 */
static void build_fixture(qc_checkin_request_t *req, qc_event_t *ev)
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

static void test_size_bound(const uint8_t *enc, size_t len)
{
    printf("     minimal uplink: %zu bytes\n", len);
    (void)enc;

    /* The number spec/v1/profiles/minimal.md commits to. It is the single fact that decides
     * whether the profile is real or merely described. */
    CHECK(len <= 242, "no-media check-in fits a 242-byte LoRaWAN frame");
}

static void test_determinism(const qc_checkin_request_t *req)
{
    uint8_t a[QC_CHECKIN_MAX_UPLINK];
    uint8_t b[QC_CHECKIN_MAX_UPLINK];
    size_t  la = 0, lb = 0;

    qc_cbor_encode_checkin(req, true, true, a, sizeof a, &la);
    qc_cbor_encode_checkin(req, true, true, b, sizeof b, &lb);

    /* Encoding must be a pure function of the document. If it is not, the MAC computed at send
     * time will not match the one a peer computes over the same logical content, and the failure
     * looks like a key problem rather than an encoder problem. */
    CHECK(la == lb && memcmp(a, b, la) == 0, "encoding the same document twice yields the same bytes");
}

static void test_key_map_applied(const qc_checkin_request_t *req)
{
    uint8_t buf[QC_CHECKIN_MAX_UPLINK];
    size_t  len = 0;
    qc_cbor_encode_checkin(req, true, true, buf, sizeof buf, &len);

    /* A map head for <24 entries is 0xa0|n, and the first key must be the integer 2 (node_id)
     * once schema_version is omitted. If string keys leaked in, byte 1 would be a text head
     * (0x60|len) instead — which is precisely the bug that cost the Python side 87 bytes. */
    CHECK(len > 2 && (buf[0] & 0xe0u) == 0xa0u, "top level is a definite-length map");
    CHECK(buf[1] == 0x02u, "first key is the integer 2 (node_id), not a string");
}

static void test_expiry_rule(void)
{
    qc_command_t cmd;
    memset(&cmd, 0, sizeof cmd);
    cmd.has_expiry      = true;
    cmd.expires_at_unix = 1000;

    CHECK(qc_command_expired(&cmd, 2000), "a command past its expiry is expired");
    CHECK(!qc_command_expired(&cmd, 500), "a command before its expiry is not");

    cmd.has_expiry = false;
    CHECK(!qc_command_expired(&cmd, 999999), "a command with no expiry never expires");
}

static void test_relative_time_excludes_absolute(void)
{
    qc_event_t ev;
    qc_event_init(&ev, "e1", "n1", "heartbeat");
    qc_event_set_relative_time(&ev, 1, 2);

    CHECK(ev.has_relative && !ev.has_timestamp,
          "relative time and absolute timestamp are mutually exclusive");
}

static int emit(const char *dir, const char *name, const uint8_t *data, size_t len)
{
    char path[512];
    snprintf(path, sizeof path, "%s/%s", dir, name);

    FILE *f = fopen(path, "wb");
    if (f == NULL) {
        printf("     (could not write %s)\n", path);
        return -1;
    }
    fwrite(data, 1, len, f);
    fclose(f);
    printf("     wrote %s (%zu bytes)\n", path, len);
    return 0;
}

int main(int argc, char **argv)
{
    const char *cases    = (argc > 1) ? argv[1] : "../../conformance/cases";
    const char *emit_dir = (argc > 2) ? argv[2] : NULL;

    printf("qonclave C conformance\n");
    printf("cases: %s\n\n", cases);

    qc_checkin_request_t req;
    qc_event_t           ev;
    build_fixture(&req, &ev);

    uint8_t buf[QC_CHECKIN_MAX_UPLINK];
    size_t  len = 0;

    const qc_status_t rc = qc_cbor_encode_checkin(&req, true, true, buf, sizeof buf, &len);
    CHECK(rc == QC_OK, "the minimal fixture encodes");

    test_size_bound(buf, len);
    test_determinism(&req);
    test_key_map_applied(&req);
    test_expiry_rule();
    test_relative_time_excludes_absolute();

    if (emit_dir != NULL) {
        printf("\n");
        /* Python's test_interop.py decodes this and compares it against the JSON fixture. That
         * comparison is the actual interop assertion; everything above is this binding checking
         * itself. */
        emit(emit_dir, "c-uplink-minimal.cbor", buf, len);
    }

    printf("\n%s\n", failures ? "FAILURES" : "all conformance checks passed");
    return failures ? 1 : 0;
}
