/*
 * cbor.h — minimal canonical CBOR writer and reader. Internal to the SDK.
 *
 * RFC 8949, restricted to what the Qonclave schemas actually use: unsigned and negative
 * integers, byte and text strings, arrays, maps, booleans, null, floats, and the epoch-time tag.
 * No indefinite-length anything, no bignums, no decimal fractions.
 *
 * Everything is CORE DETERMINISTIC (RFC 8949 section 4.2.1): shortest-form head for every
 * integer, definite lengths only, shortest float that round-trips, and map keys emitted in
 * ascending encoded-byte order. This is not a preference. Signatures are computed over these
 * exact bytes, so a non-deterministic encoder produces signatures the hub rejects with no useful
 * diagnostic on either side.
 *
 * The writer never fails mid-call. It records an overflow flag and keeps counting, so a caller
 * can encode into a zero-length buffer purely to measure the size, and can check success once at
 * the end rather than after every field.
 */

#ifndef QONCLAVE_CBOR_H
#define QONCLAVE_CBOR_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* CBOR major types, in the top 3 bits of the initial byte. */
#define QC_CBOR_UINT   0
#define QC_CBOR_NINT   1
#define QC_CBOR_BYTES  2
#define QC_CBOR_TEXT   3
#define QC_CBOR_ARRAY  4
#define QC_CBOR_MAP    5
#define QC_CBOR_TAG    6
#define QC_CBOR_SIMPLE 7

#define QC_CBOR_TAG_EPOCH 1

typedef struct {
    uint8_t *buf;
    size_t   cap;
    size_t   len;      /* bytes that would be written, even past cap */
    bool     overflow;
} qc_cbor_writer_t;

typedef struct {
    const uint8_t *buf;
    size_t         len;
    size_t         pos;
    bool           error;
} qc_cbor_reader_t;

/* --- writer ---------------------------------------------------------------------------- */

void qc_cbor_w_init(qc_cbor_writer_t *w, uint8_t *buf, size_t cap);

void qc_cbor_w_uint(qc_cbor_writer_t *w, uint64_t v);
void qc_cbor_w_int(qc_cbor_writer_t *w, int64_t v);
void qc_cbor_w_bytes(qc_cbor_writer_t *w, const uint8_t *data, size_t len);
void qc_cbor_w_text(qc_cbor_writer_t *w, const char *s);
void qc_cbor_w_text_n(qc_cbor_writer_t *w, const char *s, size_t len);
void qc_cbor_w_array(qc_cbor_writer_t *w, size_t count);
void qc_cbor_w_map(qc_cbor_writer_t *w, size_t count);
void qc_cbor_w_bool(qc_cbor_writer_t *w, bool v);
void qc_cbor_w_null(qc_cbor_writer_t *w);

/*
 * Shortest float that preserves the value exactly: half, then single, then double.
 *
 * Half-precision matters more here than it looks. Every float in a check-in is a sensor reading
 * with a couple of significant digits, and half costs 3 bytes against double's 9 — on a link
 * where the whole message must fit in 242 bytes, that is not a micro-optimization.
 */
void qc_cbor_w_float(qc_cbor_writer_t *w, double v);

/* Epoch seconds as tag(1) + integer. ~18 bytes cheaper than an RFC 3339 string. */
void qc_cbor_w_epoch(qc_cbor_writer_t *w, int64_t unix_seconds);

/* True if everything fit. Check once at the end. */
static inline bool qc_cbor_w_ok(const qc_cbor_writer_t *w) { return !w->overflow; }

/* --- reader ---------------------------------------------------------------------------- */

void qc_cbor_r_init(qc_cbor_reader_t *r, const uint8_t *buf, size_t len);

/* Read one head: major type and its argument. For strings/arrays/maps the argument is a length
 * or count; for floats `value` is unspecified and qc_cbor_r_float should be used instead. */
bool qc_cbor_r_head(qc_cbor_reader_t *r, uint8_t *major, uint64_t *value);

/* Peek the next major type without consuming. Returns 0xff at end of input. */
uint8_t qc_cbor_r_peek_major(const qc_cbor_reader_t *r);

bool qc_cbor_r_uint(qc_cbor_reader_t *r, uint64_t *out);
bool qc_cbor_r_int(qc_cbor_reader_t *r, int64_t *out);
bool qc_cbor_r_bool(qc_cbor_reader_t *r, bool *out);
bool qc_cbor_r_float(qc_cbor_reader_t *r, double *out);

/* Text is returned as a pointer INTO the input buffer plus a length — not copied, not
 * NUL-terminated. The device has no heap, and the caller already owns the buffer. */
bool qc_cbor_r_text(qc_cbor_reader_t *r, const char **out, size_t *out_len);
bool qc_cbor_r_bytes(qc_cbor_reader_t *r, const uint8_t **out, size_t *out_len);

bool qc_cbor_r_array(qc_cbor_reader_t *r, size_t *count);
bool qc_cbor_r_map(qc_cbor_reader_t *r, size_t *count);

/* Accepts tag(1)+int, a bare integer, or an RFC 3339 text timestamp. The spec requires decoders
 * to accept all three: a constrained device emits the tag, a gateway transcoding from JSON emits
 * the string, and neither should be able to talk past the other. */
bool qc_cbor_r_time(qc_cbor_reader_t *r, int64_t *out_unix);

/* Skip one complete item, including all nested content. Needed for forward compatibility: a v1.0
 * decoder meeting a field added in v1.7 must step over it, not give up on the document. */
bool qc_cbor_r_skip(qc_cbor_reader_t *r);

/* Copy a text item into a fixed buffer, NUL-terminated and truncated to fit. */
bool qc_cbor_r_text_copy(qc_cbor_reader_t *r, char *dst, size_t cap);

/* --- helpers --------------------------------------------------------------------------- */

/*
 * Parse an RFC 3339 timestamp to Unix seconds.
 *
 * Handles "YYYY-MM-DDTHH:MM:SS" with an optional fractional part and an optional "Z" or
 * "+HH:MM"/"-HH:MM" offset. Exposed because the reader needs it and tests want it directly.
 */
bool qc_time_parse_rfc3339(const char *s, size_t len, int64_t *out_unix);

#endif /* QONCLAVE_CBOR_H */
