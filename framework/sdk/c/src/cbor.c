/*
 * cbor.c — canonical CBOR primitives.
 *
 * See cbor.h for the contract. The two subtle parts are the shortest-float selection and the
 * RFC 3339 parser; everything else is mechanical.
 */

#include "cbor.h"

#include <string.h>

/* --------------------------------------------------------------------------- writer */

void qc_cbor_w_init(qc_cbor_writer_t *w, uint8_t *buf, size_t cap)
{
    w->buf      = buf;
    w->cap      = cap;
    w->len      = 0;
    w->overflow = false;
}

static void w_byte(qc_cbor_writer_t *w, uint8_t b)
{
    if (w->len < w->cap) {
        w->buf[w->len] = b;
    } else {
        w->overflow = true;
    }
    /* Keep counting past the end so a caller can size a buffer by encoding into a zero-length
     * one. Without this, measuring would need a second code path that could drift. */
    w->len++;
}

static void w_raw(qc_cbor_writer_t *w, const uint8_t *data, size_t n)
{
    for (size_t i = 0; i < n; i++) {
        w_byte(w, data[i]);
    }
}

/* Head = major type in the top 3 bits, plus the shortest encoding of `value`. Emitting a longer
 * form than necessary is valid CBOR but not deterministic, so it would break signatures. */
static void w_head(qc_cbor_writer_t *w, uint8_t major, uint64_t value)
{
    const uint8_t mt = (uint8_t)(major << 5);

    if (value < 24u) {
        w_byte(w, (uint8_t)(mt | (uint8_t)value));
    } else if (value <= 0xffu) {
        w_byte(w, (uint8_t)(mt | 24u));
        w_byte(w, (uint8_t)value);
    } else if (value <= 0xffffu) {
        w_byte(w, (uint8_t)(mt | 25u));
        w_byte(w, (uint8_t)(value >> 8));
        w_byte(w, (uint8_t)value);
    } else if (value <= 0xffffffffu) {
        w_byte(w, (uint8_t)(mt | 26u));
        for (int i = 24; i >= 0; i -= 8) {
            w_byte(w, (uint8_t)(value >> i));
        }
    } else {
        w_byte(w, (uint8_t)(mt | 27u));
        for (int i = 56; i >= 0; i -= 8) {
            w_byte(w, (uint8_t)(value >> i));
        }
    }
}

void qc_cbor_w_uint(qc_cbor_writer_t *w, uint64_t v) { w_head(w, QC_CBOR_UINT, v); }

void qc_cbor_w_int(qc_cbor_writer_t *w, int64_t v)
{
    if (v < 0) {
        /* CBOR encodes -1-n, so -1 becomes 0. Computing on the unsigned side avoids UB at
         * INT64_MIN, where negation would overflow. */
        uint64_t n = (uint64_t)(-(v + 1));
        w_head(w, QC_CBOR_NINT, n);
    } else {
        w_head(w, QC_CBOR_UINT, (uint64_t)v);
    }
}

void qc_cbor_w_bytes(qc_cbor_writer_t *w, const uint8_t *data, size_t len)
{
    w_head(w, QC_CBOR_BYTES, (uint64_t)len);
    w_raw(w, data, len);
}

void qc_cbor_w_text_n(qc_cbor_writer_t *w, const char *s, size_t len)
{
    w_head(w, QC_CBOR_TEXT, (uint64_t)len);
    w_raw(w, (const uint8_t *)s, len);
}

void qc_cbor_w_text(qc_cbor_writer_t *w, const char *s)
{
    qc_cbor_w_text_n(w, s, s ? strlen(s) : 0u);
}

void qc_cbor_w_array(qc_cbor_writer_t *w, size_t count) { w_head(w, QC_CBOR_ARRAY, (uint64_t)count); }
void qc_cbor_w_map(qc_cbor_writer_t *w, size_t count)   { w_head(w, QC_CBOR_MAP, (uint64_t)count); }

void qc_cbor_w_bool(qc_cbor_writer_t *w, bool v)
{
    w_byte(w, (uint8_t)((QC_CBOR_SIMPLE << 5) | (v ? 21u : 20u)));
}

void qc_cbor_w_null(qc_cbor_writer_t *w)
{
    w_byte(w, (uint8_t)((QC_CBOR_SIMPLE << 5) | 22u));
}

/*
 * Try to represent a float exactly in IEEE half precision.
 *
 * Returns false for anything that would lose precision, is subnormal, or is inf/nan — the caller
 * then falls back to single, then double. Refusing subnormals is deliberate: they are the one
 * range where the round-trip check is easy to get subtly wrong, and no sensor reading needs them.
 */
static bool f32_to_half(float f, uint16_t *out)
{
    uint32_t bits;
    memcpy(&bits, &f, sizeof bits);

    const uint32_t sign = (bits >> 16) & 0x8000u;
    const int32_t  exp  = (int32_t)((bits >> 23) & 0xffu);
    const uint32_t mant = bits & 0x7fffffu;

    if (exp == 0 && mant == 0) {           /* +/-0 */
        *out = (uint16_t)sign;
        return true;
    }
    if (exp == 0 || exp == 0xff) {         /* subnormal, inf, nan */
        return false;
    }
    if (mant & 0x1fffu) {                  /* mantissa needs more than 10 bits */
        return false;
    }

    const int32_t half_exp = exp - 127 + 15;
    if (half_exp <= 0 || half_exp >= 31) { /* out of half's normal range */
        return false;
    }

    *out = (uint16_t)(sign | ((uint32_t)half_exp << 10) | (mant >> 13));
    return true;
}

void qc_cbor_w_float(qc_cbor_writer_t *w, double v)
{
    const float f = (float)v;

    if ((double)f == v) {
        uint16_t h;
        if (f32_to_half(f, &h)) {
            w_byte(w, (uint8_t)((QC_CBOR_SIMPLE << 5) | 25u));
            w_byte(w, (uint8_t)(h >> 8));
            w_byte(w, (uint8_t)h);
            return;
        }
        uint32_t bits;
        memcpy(&bits, &f, sizeof bits);
        w_byte(w, (uint8_t)((QC_CBOR_SIMPLE << 5) | 26u));
        for (int i = 24; i >= 0; i -= 8) {
            w_byte(w, (uint8_t)(bits >> i));
        }
        return;
    }

    uint64_t bits;
    memcpy(&bits, &v, sizeof bits);
    w_byte(w, (uint8_t)((QC_CBOR_SIMPLE << 5) | 27u));
    for (int i = 56; i >= 0; i -= 8) {
        w_byte(w, (uint8_t)(bits >> i));
    }
}

void qc_cbor_w_epoch(qc_cbor_writer_t *w, int64_t unix_seconds)
{
    w_head(w, QC_CBOR_TAG, QC_CBOR_TAG_EPOCH);
    qc_cbor_w_int(w, unix_seconds);
}

/* --------------------------------------------------------------------------- reader */

void qc_cbor_r_init(qc_cbor_reader_t *r, const uint8_t *buf, size_t len)
{
    r->buf   = buf;
    r->len   = len;
    r->pos   = 0;
    r->error = false;
}

static bool r_take(qc_cbor_reader_t *r, size_t n, const uint8_t **out)
{
    if (r->error || r->pos + n > r->len) {
        r->error = true;
        return false;
    }
    *out = r->buf + r->pos;
    r->pos += n;
    return true;
}

uint8_t qc_cbor_r_peek_major(const qc_cbor_reader_t *r)
{
    if (r->error || r->pos >= r->len) {
        return 0xffu;
    }
    return (uint8_t)(r->buf[r->pos] >> 5);
}

bool qc_cbor_r_head(qc_cbor_reader_t *r, uint8_t *major, uint64_t *value)
{
    const uint8_t *p;
    if (!r_take(r, 1, &p)) {
        return false;
    }

    const uint8_t ib    = p[0];
    const uint8_t minor = ib & 0x1fu;
    *major = (uint8_t)(ib >> 5);

    if (minor < 24u) {
        *value = minor;
        return true;
    }

    size_t n;
    switch (minor) {
        case 24: n = 1; break;
        case 25: n = 2; break;
        case 26: n = 4; break;
        case 27: n = 8; break;
        default:
            /* 28-30 are reserved; 31 is indefinite length, which deterministic CBOR forbids. */
            r->error = true;
            return false;
    }

    if (!r_take(r, n, &p)) {
        return false;
    }
    uint64_t v = 0;
    for (size_t i = 0; i < n; i++) {
        v = (v << 8) | p[i];
    }
    *value = v;
    return true;
}

bool qc_cbor_r_uint(qc_cbor_reader_t *r, uint64_t *out)
{
    uint8_t  major;
    uint64_t v;
    if (!qc_cbor_r_head(r, &major, &v) || major != QC_CBOR_UINT) {
        r->error = true;
        return false;
    }
    *out = v;
    return true;
}

bool qc_cbor_r_int(qc_cbor_reader_t *r, int64_t *out)
{
    uint8_t  major;
    uint64_t v;
    if (!qc_cbor_r_head(r, &major, &v)) {
        return false;
    }
    if (major == QC_CBOR_UINT) {
        *out = (int64_t)v;
        return true;
    }
    if (major == QC_CBOR_NINT) {
        *out = -1 - (int64_t)v;
        return true;
    }
    r->error = true;
    return false;
}

bool qc_cbor_r_bool(qc_cbor_reader_t *r, bool *out)
{
    uint8_t  major;
    uint64_t v;
    if (!qc_cbor_r_head(r, &major, &v) || major != QC_CBOR_SIMPLE) {
        r->error = true;
        return false;
    }
    if (v == 20u || v == 21u) {
        *out = (v == 21u);
        return true;
    }
    r->error = true;
    return false;
}

static double half_to_double(uint16_t h)
{
    const uint32_t sign = (uint32_t)(h & 0x8000u) << 16;
    const int32_t  exp  = (h >> 10) & 0x1f;
    const uint32_t mant = h & 0x3ffu;

    uint32_t bits;
    if (exp == 0) {
        if (mant == 0) {
            bits = sign;
        } else {
            /* Subnormal: renormalize into single precision. */
            int32_t  e = -1;
            uint32_t m = mant;
            do {
                e++;
                m <<= 1;
            } while ((m & 0x400u) == 0);
            bits = sign | ((uint32_t)(127 - 15 - e) << 23) | ((m & 0x3ffu) << 13);
        }
    } else if (exp == 0x1f) {
        bits = sign | 0x7f800000u | (mant << 13);
    } else {
        bits = sign | ((uint32_t)(exp - 15 + 127) << 23) | (mant << 13);
    }

    float f;
    memcpy(&f, &bits, sizeof f);
    return (double)f;
}

bool qc_cbor_r_float(qc_cbor_reader_t *r, double *out)
{
    const uint8_t *p;
    if (!r_take(r, 1, &p)) {
        return false;
    }
    const uint8_t ib = p[0];
    if ((ib >> 5) != QC_CBOR_SIMPLE) {
        r->error = true;
        return false;
    }

    switch (ib & 0x1fu) {
        case 25: {
            if (!r_take(r, 2, &p)) return false;
            *out = half_to_double((uint16_t)((p[0] << 8) | p[1]));
            return true;
        }
        case 26: {
            if (!r_take(r, 4, &p)) return false;
            uint32_t bits = 0;
            for (int i = 0; i < 4; i++) bits = (bits << 8) | p[i];
            float f;
            memcpy(&f, &bits, sizeof f);
            *out = (double)f;
            return true;
        }
        case 27: {
            if (!r_take(r, 8, &p)) return false;
            uint64_t bits = 0;
            for (int i = 0; i < 8; i++) bits = (bits << 8) | p[i];
            memcpy(out, &bits, sizeof *out);
            return true;
        }
        default:
            r->error = true;
            return false;
    }
}

bool qc_cbor_r_text(qc_cbor_reader_t *r, const char **out, size_t *out_len)
{
    uint8_t  major;
    uint64_t n;
    if (!qc_cbor_r_head(r, &major, &n) || major != QC_CBOR_TEXT) {
        r->error = true;
        return false;
    }
    const uint8_t *p;
    if (!r_take(r, (size_t)n, &p)) {
        return false;
    }
    *out     = (const char *)p;
    *out_len = (size_t)n;
    return true;
}

bool qc_cbor_r_bytes(qc_cbor_reader_t *r, const uint8_t **out, size_t *out_len)
{
    uint8_t  major;
    uint64_t n;
    if (!qc_cbor_r_head(r, &major, &n) || major != QC_CBOR_BYTES) {
        r->error = true;
        return false;
    }
    const uint8_t *p;
    if (!r_take(r, (size_t)n, &p)) {
        return false;
    }
    *out     = p;
    *out_len = (size_t)n;
    return true;
}

bool qc_cbor_r_array(qc_cbor_reader_t *r, size_t *count)
{
    uint8_t  major;
    uint64_t n;
    if (!qc_cbor_r_head(r, &major, &n) || major != QC_CBOR_ARRAY) {
        r->error = true;
        return false;
    }
    *count = (size_t)n;
    return true;
}

bool qc_cbor_r_map(qc_cbor_reader_t *r, size_t *count)
{
    uint8_t  major;
    uint64_t n;
    if (!qc_cbor_r_head(r, &major, &n) || major != QC_CBOR_MAP) {
        r->error = true;
        return false;
    }
    *count = (size_t)n;
    return true;
}

bool qc_cbor_r_text_copy(qc_cbor_reader_t *r, char *dst, size_t cap)
{
    const char *s;
    size_t      n;
    if (!qc_cbor_r_text(r, &s, &n)) {
        return false;
    }
    if (n >= cap) {
        n = cap - 1;
    }
    memcpy(dst, s, n);
    dst[n] = '\0';
    return true;
}

bool qc_cbor_r_skip(qc_cbor_reader_t *r)
{
    uint8_t  major;
    uint64_t value;

    /* Floats share major type 7 with bool/null and carry payload bytes the head reader does not
     * consume, so they need handling before the generic path. */
    const uint8_t peek_major = qc_cbor_r_peek_major(r);
    if (peek_major == QC_CBOR_SIMPLE && r->pos < r->len) {
        const uint8_t minor = r->buf[r->pos] & 0x1fu;
        if (minor >= 25u && minor <= 27u) {
            double ignored;
            return qc_cbor_r_float(r, &ignored);
        }
    }

    if (!qc_cbor_r_head(r, &major, &value)) {
        return false;
    }

    switch (major) {
        case QC_CBOR_UINT:
        case QC_CBOR_NINT:
        case QC_CBOR_SIMPLE:
            return true;

        case QC_CBOR_BYTES:
        case QC_CBOR_TEXT: {
            const uint8_t *p;
            return r_take(r, (size_t)value, &p);
        }

        case QC_CBOR_ARRAY:
            for (uint64_t i = 0; i < value; i++) {
                if (!qc_cbor_r_skip(r)) return false;
            }
            return true;

        case QC_CBOR_MAP:
            for (uint64_t i = 0; i < value; i++) {
                if (!qc_cbor_r_skip(r)) return false;  /* key */
                if (!qc_cbor_r_skip(r)) return false;  /* value */
            }
            return true;

        case QC_CBOR_TAG:
            return qc_cbor_r_skip(r);

        default:
            r->error = true;
            return false;
    }
}

/* --------------------------------------------------------------------------- time */

static bool digits(const char *s, size_t n, int *out)
{
    int v = 0;
    for (size_t i = 0; i < n; i++) {
        if (s[i] < '0' || s[i] > '9') {
            return false;
        }
        v = v * 10 + (s[i] - '0');
    }
    *out = v;
    return true;
}

/* Days from 1970-01-01 to y-m-d. Howard Hinnant's civil_from_days inverse: shifts the year to
 * start in March so the leap day lands at the end and the month-length table disappears. */
static int64_t days_from_civil(int y, int m, int d)
{
    y -= (m <= 2);
    const int64_t era = (y >= 0 ? y : y - 399) / 400;
    const int64_t yoe = y - era * 400;                                   /* [0, 399] */
    const int64_t doy = (153 * (m + (m > 2 ? -3 : 9)) + 2) / 5 + d - 1;  /* [0, 365] */
    const int64_t doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;           /* [0, 146096] */
    return era * 146097 + doe - 719468;
}

bool qc_time_parse_rfc3339(const char *s, size_t len, int64_t *out_unix)
{
    /* Shortest acceptable form is YYYY-MM-DDTHH:MM:SS = 19 chars. */
    if (s == NULL || len < 19u) {
        return false;
    }

    int year, mon, day, hour, min, sec;
    if (!digits(s, 4, &year) || s[4] != '-' ||
        !digits(s + 5, 2, &mon) || s[7] != '-' ||
        !digits(s + 8, 2, &day)) {
        return false;
    }
    if (s[10] != 'T' && s[10] != 't' && s[10] != ' ') {
        return false;
    }
    if (!digits(s + 11, 2, &hour) || s[13] != ':' ||
        !digits(s + 14, 2, &min) || s[16] != ':' ||
        !digits(s + 17, 2, &sec)) {
        return false;
    }
    if (mon < 1 || mon > 12 || day < 1 || day > 31 || hour > 23 || min > 59 || sec > 60) {
        return false;
    }

    size_t i = 19;
    if (i < len && s[i] == '.') {           /* fractional seconds: parsed and discarded */
        i++;
        while (i < len && s[i] >= '0' && s[i] <= '9') {
            i++;
        }
    }

    int64_t offset = 0;
    if (i < len) {
        const char z = s[i];
        if (z == 'Z' || z == 'z') {
            i++;
        } else if (z == '+' || z == '-') {
            int oh, om;
            if (i + 6 > len || !digits(s + i + 1, 2, &oh) || s[i + 3] != ':' ||
                !digits(s + i + 4, 2, &om)) {
                return false;
            }
            offset = (int64_t)oh * 3600 + (int64_t)om * 60;
            if (z == '-') {
                offset = -offset;
            }
            i += 6;
        }
    }

    const int64_t days = days_from_civil(year, mon, day);
    *out_unix = days * 86400 + (int64_t)hour * 3600 + (int64_t)min * 60 + sec - offset;
    return true;
}

bool qc_cbor_r_time(qc_cbor_reader_t *r, int64_t *out_unix)
{
    const uint8_t major = qc_cbor_r_peek_major(r);

    if (major == QC_CBOR_TAG) {
        uint8_t  m;
        uint64_t tag;
        if (!qc_cbor_r_head(r, &m, &tag) || tag != QC_CBOR_TAG_EPOCH) {
            r->error = true;
            return false;
        }
        return qc_cbor_r_int(r, out_unix);
    }

    if (major == QC_CBOR_UINT || major == QC_CBOR_NINT) {
        return qc_cbor_r_int(r, out_unix);
    }

    if (major == QC_CBOR_TEXT) {
        const char *s;
        size_t      n;
        if (!qc_cbor_r_text(r, &s, &n)) {
            return false;
        }
        if (!qc_time_parse_rfc3339(s, n, out_unix)) {
            r->error = true;
            return false;
        }
        return true;
    }

    r->error = true;
    return false;
}
