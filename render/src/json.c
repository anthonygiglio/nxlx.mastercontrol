// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: GPL-3.0-or-later
//
// A small JSON reader and writer for mpv's IPC protocol, into and out of mpv_node.
#include "json.h"

#include <errno.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

void buf_init(Buf *b) { b->p = NULL; b->len = b->cap = 0; b->failed = 0; }
void buf_free(Buf *b) { free(b->p); buf_init(b); }

void buf_add(Buf *b, const char *s, size_t n)
{
    if (b->failed)
        return;
    if (b->len + n + 1 > b->cap) {
        size_t cap = b->cap ? b->cap : 256;
        while (cap < b->len + n + 1)
            cap *= 2;
        char *p = realloc(b->p, cap);
        if (!p) {
            b->failed = 1;
            return;
        }
        b->p = p;
        b->cap = cap;
    }
    memcpy(b->p + b->len, s, n);
    b->len += n;
    b->p[b->len] = 0;
}

void buf_str(Buf *b, const char *s) { buf_add(b, s, strlen(s)); }

/* ---- reading ---------------------------------------------------------------------------------------------------- */
typedef struct {
    const char *s, *end;
} Rd;

static void skip_ws(Rd *r)
{
    while (r->s < r->end && (*r->s == ' ' || *r->s == '\t' || *r->s == '\n' || *r->s == '\r'))
        r->s++;
}

static int hex4(const char *s, unsigned *out)
{
    unsigned v = 0;
    for (int i = 0; i < 4; i++) {
        char c = s[i];
        v <<= 4;
        if (c >= '0' && c <= '9') v |= (unsigned)(c - '0');
        else if (c >= 'a' && c <= 'f') v |= (unsigned)(c - 'a' + 10);
        else if (c >= 'A' && c <= 'F') v |= (unsigned)(c - 'A' + 10);
        else return -1;
    }
    *out = v;
    return 0;
}

static void put_utf8(Buf *b, unsigned cp)
{
    char t[4];
    if (cp < 0x80) { t[0] = (char)cp; buf_add(b, t, 1); }
    else if (cp < 0x800) { t[0] = (char)(0xC0 | (cp >> 6)); t[1] = (char)(0x80 | (cp & 0x3F)); buf_add(b, t, 2); }
    else if (cp < 0x10000) {
        t[0] = (char)(0xE0 | (cp >> 12)); t[1] = (char)(0x80 | ((cp >> 6) & 0x3F)); t[2] = (char)(0x80 | (cp & 0x3F));
        buf_add(b, t, 3);
    } else {
        t[0] = (char)(0xF0 | (cp >> 18)); t[1] = (char)(0x80 | ((cp >> 12) & 0x3F));
        t[2] = (char)(0x80 | ((cp >> 6) & 0x3F)); t[3] = (char)(0x80 | (cp & 0x3F));
        buf_add(b, t, 4);
    }
}

// A string, the reader just past its opening quote. A NUL inside (\u0000) is refused: mpv's strings are C strings.
static char *read_string(Rd *r)
{
    Buf b;
    buf_init(&b);
    buf_add(&b, "", 0);
    while (r->s < r->end) {
        unsigned char c = (unsigned char)*r->s++;
        if (c == '"') {
            if (b.failed) break;
            return b.p;
        }
        if (c < 0x20) break;
        if (c != '\\') {
            buf_add(&b, (const char *)&c, 1);
            continue;
        }
        if (r->s >= r->end) break;
        char e = *r->s++;
        switch (e) {
        case '"': buf_add(&b, "\"", 1); break;
        case '\\': buf_add(&b, "\\", 1); break;
        case '/': buf_add(&b, "/", 1); break;
        case 'b': buf_add(&b, "\b", 1); break;
        case 'f': buf_add(&b, "\f", 1); break;
        case 'n': buf_add(&b, "\n", 1); break;
        case 'r': buf_add(&b, "\r", 1); break;
        case 't': buf_add(&b, "\t", 1); break;
        case 'u': {
            unsigned cp, lo;
            if (r->end - r->s < 4 || hex4(r->s, &cp)) goto fail;
            r->s += 4;
            if (cp >= 0xD800 && cp <= 0xDBFF) {
                if (r->end - r->s < 6 || r->s[0] != '\\' || r->s[1] != 'u' || hex4(r->s + 2, &lo) || lo < 0xDC00 || lo > 0xDFFF)
                    goto fail;
                r->s += 6;
                cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00);
            } else if (cp >= 0xDC00 && cp <= 0xDFFF) {
                goto fail;
            }
            if (cp == 0) goto fail;
            put_utf8(&b, cp);
            break;
        }
        default:
            goto fail;
        }
    }
fail:
    buf_free(&b);
    return NULL;
}

static int read_value(Rd *r, mpv_node *out, int depth);

static int read_number(Rd *r, mpv_node *out)
{
    const char *start = r->s;
    int is_int = 1;
    if (r->s < r->end && *r->s == '-') r->s++;
    if (r->s >= r->end || !(*r->s >= '0' && *r->s <= '9')) return -1;
    if (*r->s == '0') r->s++;
    else while (r->s < r->end && *r->s >= '0' && *r->s <= '9') r->s++;
    if (r->s < r->end && *r->s == '.') {
        is_int = 0;
        r->s++;
        if (r->s >= r->end || !(*r->s >= '0' && *r->s <= '9')) return -1;
        while (r->s < r->end && *r->s >= '0' && *r->s <= '9') r->s++;
    }
    if (r->s < r->end && (*r->s == 'e' || *r->s == 'E')) {
        is_int = 0;
        r->s++;
        if (r->s < r->end && (*r->s == '+' || *r->s == '-')) r->s++;
        if (r->s >= r->end || !(*r->s >= '0' && *r->s <= '9')) return -1;
        while (r->s < r->end && *r->s >= '0' && *r->s <= '9') r->s++;
    }
    size_t n = (size_t)(r->s - start);
    char tmp[64];
    if (n >= sizeof tmp) return -1;
    memcpy(tmp, start, n);
    tmp[n] = 0;
    if (is_int) {
        errno = 0;
        char *e;
        long long v = strtoll(tmp, &e, 10);
        if (errno == 0 && *e == 0) {
            out->format = MPV_FORMAT_INT64;
            out->u.int64 = (int64_t)v;
            return 0;
        }
    }
    char *e;
    double d = strtod(tmp, &e);
    if (*e != 0 || !isfinite(d)) return -1;
    out->format = MPV_FORMAT_DOUBLE;
    out->u.double_ = d;
    return 0;
}

static int read_list(Rd *r, mpv_node *out, int depth, int is_map)
{
    mpv_node_list *l = calloc(1, sizeof *l);
    if (!l) return -1;
    out->format = is_map ? MPV_FORMAT_NODE_MAP : MPV_FORMAT_NODE_ARRAY;
    out->u.list = l;
    char close = is_map ? '}' : ']';
    skip_ws(r);
    if (r->s < r->end && *r->s == close) {
        r->s++;
        return 0;
    }
    for (;;) {
        if (l->num >= 4096) return -1;          // no request needs more
        mpv_node *v = realloc(l->values, sizeof *v * (size_t)(l->num + 1));
        if (!v) return -1;
        l->values = v;
        if (is_map) {
            char **k = realloc(l->keys, sizeof *k * (size_t)(l->num + 1));
            if (!k) return -1;
            l->keys = k;
            skip_ws(r);
            if (r->s >= r->end || *r->s != '"') return -1;
            r->s++;
            char *key = read_string(r);
            if (!key) return -1;
            l->keys[l->num] = key;
            skip_ws(r);
            if (r->s >= r->end || *r->s != ':') {
                l->values[l->num].format = MPV_FORMAT_NONE;
                l->num++;
                return -1;
            }
            r->s++;
        }
        l->values[l->num].format = MPV_FORMAT_NONE;
        l->num++;
        if (read_value(r, &l->values[l->num - 1], depth + 1)) return -1;
        skip_ws(r);
        if (r->s >= r->end) return -1;
        if (*r->s == ',') { r->s++; continue; }
        if (*r->s == close) { r->s++; return 0; }
        return -1;
    }
}

static int lit(Rd *r, const char *w)
{
    size_t n = strlen(w);
    if ((size_t)(r->end - r->s) < n || memcmp(r->s, w, n)) return -1;
    r->s += n;
    return 0;
}

static int read_value(Rd *r, mpv_node *out, int depth)
{
    out->format = MPV_FORMAT_NONE;
    if (depth > JSON_MAX_DEPTH) return -1;
    skip_ws(r);
    if (r->s >= r->end) return -1;
    char c = *r->s;
    if (c == '{' || c == '[') {
        r->s++;
        return read_list(r, out, depth, c == '{');
    }
    if (c == '"') {
        r->s++;
        char *s = read_string(r);
        if (!s) return -1;
        out->format = MPV_FORMAT_STRING;
        out->u.string = s;
        return 0;
    }
    if (c == 't') { if (lit(r, "true")) return -1; out->format = MPV_FORMAT_FLAG; out->u.flag = 1; return 0; }
    if (c == 'f') { if (lit(r, "false")) return -1; out->format = MPV_FORMAT_FLAG; out->u.flag = 0; return 0; }
    if (c == 'n') { if (lit(r, "null")) return -1; out->format = MPV_FORMAT_NONE; return 0; }
    return read_number(r, out);
}

int json_parse(const char *text, size_t len, mpv_node *out)
{
    Rd r = { text, text + len };
    if (read_value(&r, out, 0)) {
        json_free(out);
        return -1;
    }
    skip_ws(&r);
    if (r.s != r.end) {
        json_free(out);
        return -1;
    }
    return 0;
}

void json_free(mpv_node *n)
{
    if (n->format == MPV_FORMAT_STRING) {
        free(n->u.string);
    } else if (n->format == MPV_FORMAT_NODE_ARRAY || n->format == MPV_FORMAT_NODE_MAP) {
        mpv_node_list *l = n->u.list;
        if (l) {
            for (int i = 0; i < l->num; i++) {
                json_free(&l->values[i]);
                if (l->keys) free(l->keys[i]);
            }
            free(l->values);
            free(l->keys);
            free(l);
        }
    }
    n->format = MPV_FORMAT_NONE;
}

/* ---- writing ---------------------------------------------------------------------------------------------------- */
// Length of a valid UTF-8 sequence at s (at most `left` bytes), or 0.
static int utf8_len(const unsigned char *s, size_t left)
{
    unsigned c = s[0];
    int n;
    unsigned cp;
    if (c < 0x80) return 1;
    else if ((c & 0xE0) == 0xC0) { n = 2; cp = c & 0x1F; }
    else if ((c & 0xF0) == 0xE0) { n = 3; cp = c & 0x0F; }
    else if ((c & 0xF8) == 0xF0) { n = 4; cp = c & 0x07; }
    else return 0;
    if ((size_t)n > left) return 0;
    for (int i = 1; i < n; i++) {
        if ((s[i] & 0xC0) != 0x80) return 0;
        cp = (cp << 6) | (s[i] & 0x3F);
    }
    if ((n == 2 && cp < 0x80) || (n == 3 && cp < 0x800) || (n == 4 && cp < 0x10000) || cp > 0x10FFFF
        || (cp >= 0xD800 && cp <= 0xDFFF))
        return 0;
    return n;
}

void json_write_string(Buf *b, const char *s)
{
    const unsigned char *p = (const unsigned char *)s;
    size_t left = strlen(s);
    buf_add(b, "\"", 1);
    while (left) {
        unsigned char c = *p;
        if (c == '"') buf_add(b, "\\\"", 2);
        else if (c == '\\') buf_add(b, "\\\\", 2);
        else if (c == '\n') buf_add(b, "\\n", 2);
        else if (c == '\r') buf_add(b, "\\r", 2);
        else if (c == '\t') buf_add(b, "\\t", 2);
        else if (c < 0x20 || c == 0x7F) {
            char t[8];
            snprintf(t, sizeof t, "\\u%04x", c);
            buf_add(b, t, 6);
        } else if (c < 0x80) {
            buf_add(b, (const char *)p, 1);
        } else {
            int n = utf8_len(p, left);
            if (n == 0) {
                buf_add(b, "\xEF\xBF\xBD", 3);
                n = 1;
            } else {
                buf_add(b, (const char *)p, (size_t)n);
            }
            p += n;
            left -= (size_t)n;
            continue;
        }
        p++;
        left--;
    }
    buf_add(b, "\"", 1);
}

void json_write(Buf *b, const mpv_node *n)
{
    char t[64];
    switch (n->format) {
    case MPV_FORMAT_STRING:
    case MPV_FORMAT_OSD_STRING:
        json_write_string(b, n->u.string ? n->u.string : "");
        break;
    case MPV_FORMAT_FLAG:
        buf_str(b, n->u.flag ? "true" : "false");
        break;
    case MPV_FORMAT_INT64:
        snprintf(t, sizeof t, "%lld", (long long)n->u.int64);
        buf_str(b, t);
        break;
    case MPV_FORMAT_DOUBLE:
        if (!isfinite(n->u.double_)) {
            buf_str(b, "null");
        } else {
            snprintf(t, sizeof t, "%.17g", n->u.double_);
            // a whole double stays a double for the reader (mpv writes "0.000000" for 0.0)
            if (!strpbrk(t, ".eEn")) strcat(t, ".0");
            buf_str(b, t);
        }
        break;
    case MPV_FORMAT_NODE_ARRAY:
    case MPV_FORMAT_NODE_MAP: {
        int map = n->format == MPV_FORMAT_NODE_MAP;
        mpv_node_list *l = n->u.list;
        buf_str(b, map ? "{" : "[");
        for (int i = 0; l && i < l->num; i++) {
            if (i) buf_str(b, ",");
            if (map) {
                json_write_string(b, l->keys[i]);
                buf_str(b, ":");
            }
            json_write(b, &l->values[i]);
        }
        buf_str(b, map ? "}" : "]");
        break;
    }
    default:
        buf_str(b, "null");
    }
}

const mpv_node *node_map_get(const mpv_node *map, const char *key)
{
    if (!map || map->format != MPV_FORMAT_NODE_MAP || !map->u.list) return NULL;
    for (int i = 0; i < map->u.list->num; i++)
        if (!strcmp(map->u.list->keys[i], key)) return &map->u.list->values[i];
    return NULL;
}
