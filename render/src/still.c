// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: GPL-3.0-or-later
//
// Pictures to files, for the screenshots taken while one of the renderer's own programs is on (D106): libmpv's
// screenshot is of its layer, which then holds only the generator's blank carrier, so the renderer writes its own.
//
//   PNG   8-bit RGB with no row filter and stored (not compressed) deflate blocks: what Player.still asks of mpv
//         (screenshot-png-compression 0, screenshot-png-filter 0) and what pvj/transitions.py read_still takes.
//   JPEG  baseline, 4:4:4, the tables of the JPEG standard's annex K, scaled by quality as mpv's libjpeg scales them:
//         the panel's preview (api.preview_jpeg).
//
// No library: the program links only what D95 names. Written from the standards (ISO/IEC 10918-1 annex K, RFC 1950,
// RFC 1951, the PNG specification), not copied from any implementation.
#define _GNU_SOURCE
#include "pvj.h"

#include <errno.h>
#include <math.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

typedef struct {
    unsigned char *p;
    size_t len, cap;
    int failed;
} Out;

static void put(Out *o, const void *data, size_t n)
{
    if (o->failed) return;
    if (o->len + n > o->cap) {
        size_t cap = o->cap ? o->cap : 65536;
        while (cap < o->len + n) cap *= 2;
        unsigned char *p = realloc(o->p, cap);
        if (!p) { o->failed = 1; return; }
        o->p = p;
        o->cap = cap;
    }
    memcpy(o->p + o->len, data, n);
    o->len += n;
}

static void put8(Out *o, unsigned v) { unsigned char c = (unsigned char)v; put(o, &c, 1); }
static void put16(Out *o, unsigned v) { put8(o, v >> 8); put8(o, v & 255); }
static void put32(Out *o, uint32_t v) { put16(o, v >> 16); put16(o, v & 0xffff); }

static int write_all(int fd, const Out *o)
{
    if (o->failed) return -1;
    size_t off = 0;
    while (off < o->len) {
        ssize_t w = write(fd, o->p + off, o->len - off);
        if (w < 0 && errno == EINTR) continue;
        if (w <= 0) return -1;
        off += (size_t)w;
    }
    return 0;
}

/* ---- PNG --------------------------------------------------------------------------------------------------------- */
static uint32_t crc_table[256];

static uint32_t crc32_of(uint32_t c, const unsigned char *p, size_t n)
{
    if (!crc_table[1])
        for (uint32_t i = 0; i < 256; i++) {
            uint32_t v = i;
            for (int k = 0; k < 8; k++) v = v & 1 ? 0xedb88320u ^ (v >> 1) : v >> 1;
            crc_table[i] = v;
        }
    c = ~c;
    for (size_t i = 0; i < n; i++) c = crc_table[(c ^ p[i]) & 255] ^ (c >> 8);
    return ~c;
}

static void chunk(Out *o, const char *type, const unsigned char *data, size_t n)
{
    put32(o, (uint32_t)n);
    size_t at = o->len;
    put(o, type, 4);
    put(o, data, n);
    if (o->failed) return;
    put32(o, crc32_of(0, o->p + at, n + 4));
}

int still_png(int fd, const unsigned char *rgb, int w, int h)
{
    size_t row = (size_t)w * 3 + 1, raw_len = row * (size_t)h;
    // the zlib stream: a header, stored blocks of at most 65535 bytes of the rows (each with its filter byte 0), Adler-32
    Out z = { 0 };
    put8(&z, 0x78);
    put8(&z, 0x01);
    uint32_t a = 1, b = 0;
    size_t left = raw_len, y = 0, in_row = 0;
    unsigned char *block = malloc(65535);
    if (!block) return -1;
    while (left > 0 && !z.failed) {
        size_t n = left > 65535 ? 65535 : left;
        for (size_t i = 0; i < n; i++) {
            unsigned char c = in_row == 0 ? 0 : rgb[y * (size_t)w * 3 + in_row - 1];
            block[i] = c;
            a = (a + c) % 65521;
            b = (b + a) % 65521;
            if (++in_row == row) { in_row = 0; y++; }
        }
        left -= n;
        put8(&z, left == 0 ? 1 : 0);
        put8(&z, (unsigned)(n & 255));
        put8(&z, (unsigned)(n >> 8));
        put8(&z, (unsigned)(~n & 255));
        put8(&z, (unsigned)((~n >> 8) & 255));
        put(&z, block, n);
    }
    free(block);
    put32(&z, (b << 16) | a);
    Out o = { 0 };
    static const unsigned char sig[8] = { 0x89, 'P', 'N', 'G', '\r', '\n', 0x1a, '\n' };
    put(&o, sig, 8);
    unsigned char ihdr[13] = { (unsigned char)(w >> 24), (unsigned char)(w >> 16), (unsigned char)(w >> 8), (unsigned char)w,
                               (unsigned char)(h >> 24), (unsigned char)(h >> 16), (unsigned char)(h >> 8), (unsigned char)h,
                               8, 2, 0, 0, 0 };
    chunk(&o, "IHDR", ihdr, sizeof ihdr);
    if (z.failed) o.failed = 1;
    else chunk(&o, "IDAT", z.p, z.len);
    chunk(&o, "IEND", NULL, 0);
    int r = write_all(fd, &o);
    free(z.p);
    free(o.p);
    return r;
}

/* ---- JPEG -------------------------------------------------------------------------------------------------------- */
static const unsigned char ZIGZAG[64] = {
    0, 1, 8, 16, 9, 2, 3, 10, 17, 24, 32, 25, 18, 11, 4, 5, 12, 19, 26, 33, 40, 48, 41, 34, 27, 20, 13, 6, 7, 14, 21, 28,
    35, 42, 49, 56, 57, 50, 43, 36, 29, 22, 15, 23, 30, 37, 44, 51, 58, 59, 52, 45, 38, 31, 39, 46, 53, 60, 61, 54, 47, 55, 62, 63 };
// annex K.1, in natural order
static const unsigned char Q_LUMA[64] = {
    16, 11, 10, 16, 24, 40, 51, 61, 12, 12, 14, 19, 26, 58, 60, 55, 14, 13, 16, 24, 40, 57, 69, 56, 14, 17, 22, 29, 51, 87, 80, 62,
    18, 22, 37, 56, 68, 109, 103, 77, 24, 35, 55, 64, 81, 104, 113, 92, 49, 64, 78, 87, 103, 121, 120, 101, 72, 92, 95, 98, 112, 100, 103, 99 };
static const unsigned char Q_CHROMA[64] = {
    17, 18, 24, 47, 99, 99, 99, 99, 18, 21, 26, 66, 99, 99, 99, 99, 24, 26, 56, 99, 99, 99, 99, 99, 47, 66, 99, 99, 99, 99, 99, 99,
    99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99 };
// annex K.3: the code lengths' counts (1 to 16) and the symbols
static const unsigned char DC_BITS[2][16] = { { 0, 1, 5, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0 }, { 0, 3, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0 } };
static const unsigned char DC_VALS[12] = { 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11 };
static const unsigned char AC_BITS[2][16] = { { 0, 2, 1, 3, 3, 2, 4, 3, 5, 5, 4, 4, 0, 0, 1, 0x7d }, { 0, 2, 1, 2, 4, 4, 3, 4, 7, 5, 4, 4, 0, 1, 2, 0x77 } };
static const unsigned char AC_VALS[2][162] = {
    { 0x01, 0x02, 0x03, 0x00, 0x04, 0x11, 0x05, 0x12, 0x21, 0x31, 0x41, 0x06, 0x13, 0x51, 0x61, 0x07, 0x22, 0x71, 0x14, 0x32, 0x81, 0x91,
      0xa1, 0x08, 0x23, 0x42, 0xb1, 0xc1, 0x15, 0x52, 0xd1, 0xf0, 0x24, 0x33, 0x62, 0x72, 0x82, 0x09, 0x0a, 0x16, 0x17, 0x18, 0x19, 0x1a,
      0x25, 0x26, 0x27, 0x28, 0x29, 0x2a, 0x34, 0x35, 0x36, 0x37, 0x38, 0x39, 0x3a, 0x43, 0x44, 0x45, 0x46, 0x47, 0x48, 0x49, 0x4a, 0x53,
      0x54, 0x55, 0x56, 0x57, 0x58, 0x59, 0x5a, 0x63, 0x64, 0x65, 0x66, 0x67, 0x68, 0x69, 0x6a, 0x73, 0x74, 0x75, 0x76, 0x77, 0x78, 0x79,
      0x7a, 0x83, 0x84, 0x85, 0x86, 0x87, 0x88, 0x89, 0x8a, 0x92, 0x93, 0x94, 0x95, 0x96, 0x97, 0x98, 0x99, 0x9a, 0xa2, 0xa3, 0xa4, 0xa5,
      0xa6, 0xa7, 0xa8, 0xa9, 0xaa, 0xb2, 0xb3, 0xb4, 0xb5, 0xb6, 0xb7, 0xb8, 0xb9, 0xba, 0xc2, 0xc3, 0xc4, 0xc5, 0xc6, 0xc7, 0xc8, 0xc9,
      0xca, 0xd2, 0xd3, 0xd4, 0xd5, 0xd6, 0xd7, 0xd8, 0xd9, 0xda, 0xe1, 0xe2, 0xe3, 0xe4, 0xe5, 0xe6, 0xe7, 0xe8, 0xe9, 0xea, 0xf1, 0xf2,
      0xf3, 0xf4, 0xf5, 0xf6, 0xf7, 0xf8, 0xf9, 0xfa },
    { 0x00, 0x01, 0x02, 0x03, 0x11, 0x04, 0x05, 0x21, 0x31, 0x06, 0x12, 0x41, 0x51, 0x07, 0x61, 0x71, 0x13, 0x22, 0x32, 0x81, 0x08, 0x14,
      0x42, 0x91, 0xa1, 0xb1, 0xc1, 0x09, 0x23, 0x33, 0x52, 0xf0, 0x15, 0x62, 0x72, 0xd1, 0x0a, 0x16, 0x24, 0x34, 0xe1, 0x25, 0xf1, 0x17,
      0x18, 0x19, 0x1a, 0x26, 0x27, 0x28, 0x29, 0x2a, 0x35, 0x36, 0x37, 0x38, 0x39, 0x3a, 0x43, 0x44, 0x45, 0x46, 0x47, 0x48, 0x49, 0x4a,
      0x53, 0x54, 0x55, 0x56, 0x57, 0x58, 0x59, 0x5a, 0x63, 0x64, 0x65, 0x66, 0x67, 0x68, 0x69, 0x6a, 0x73, 0x74, 0x75, 0x76, 0x77, 0x78,
      0x79, 0x7a, 0x82, 0x83, 0x84, 0x85, 0x86, 0x87, 0x88, 0x89, 0x8a, 0x92, 0x93, 0x94, 0x95, 0x96, 0x97, 0x98, 0x99, 0x9a, 0xa2, 0xa3,
      0xa4, 0xa5, 0xa6, 0xa7, 0xa8, 0xa9, 0xaa, 0xb2, 0xb3, 0xb4, 0xb5, 0xb6, 0xb7, 0xb8, 0xb9, 0xba, 0xc2, 0xc3, 0xc4, 0xc5, 0xc6, 0xc7,
      0xc8, 0xc9, 0xca, 0xd2, 0xd3, 0xd4, 0xd5, 0xd6, 0xd7, 0xd8, 0xd9, 0xda, 0xe2, 0xe3, 0xe4, 0xe5, 0xe6, 0xe7, 0xe8, 0xe9, 0xea, 0xf2,
      0xf3, 0xf4, 0xf5, 0xf6, 0xf7, 0xf8, 0xf9, 0xfa } };

typedef struct { unsigned short code[256]; unsigned char size[256]; } Huff;

static void huff_make(Huff *t, const unsigned char bits[16], const unsigned char *vals)
{
    memset(t, 0, sizeof *t);
    unsigned code = 0;
    int k = 0;
    for (int len = 1; len <= 16; len++) {
        for (int i = 0; i < bits[len - 1]; i++, k++) {
            t->code[vals[k]] = (unsigned short)code++;
            t->size[vals[k]] = (unsigned char)len;
        }
        code <<= 1;
    }
}

typedef struct { Out *o; uint32_t acc; int n; } Bits;

static void bits_put(Bits *b, unsigned code, int size)
{
    b->acc = (b->acc << size) | (code & ((1u << size) - 1));
    b->n += size;
    while (b->n >= 8) {
        unsigned c = (b->acc >> (b->n - 8)) & 255;
        put8(b->o, c);
        if (c == 0xff) put8(b->o, 0);           // a 0xFF in the data is followed by a 0
        b->n -= 8;
    }
}

static int magnitude(int v) { int n = 0; v = v < 0 ? -v : v; while (v) { n++; v >>= 1; } return n; }

static double cos_table[8][8];       // cos((2x + 1) u pi / 16)

static void block_code(Bits *b, const float in[64], const float q[64], int *prev_dc, const Huff *dc, const Huff *ac)
{
    float tmp[64], coef[64];
    // the DCT, rows then columns
    for (int y = 0; y < 8; y++)
        for (int u = 0; u < 8; u++) {
            double s = 0;
            for (int x = 0; x < 8; x++) s += in[y * 8 + x] * cos_table[x][u];
            tmp[y * 8 + u] = (float)(s * (u == 0 ? M_SQRT1_2 : 1.0) * 0.5);
        }
    for (int u = 0; u < 8; u++)
        for (int v = 0; v < 8; v++) {
            double s = 0;
            for (int y = 0; y < 8; y++) s += tmp[y * 8 + u] * cos_table[y][v];
            coef[v * 8 + u] = (float)(s * (v == 0 ? M_SQRT1_2 : 1.0) * 0.5);
        }
    int z[64];
    for (int i = 0; i < 64; i++) {
        z[i] = (int)lroundf(coef[ZIGZAG[i]] / q[ZIGZAG[i]]);
        if (i && z[i] > 1023) z[i] = 1023;            // a baseline AC value has at most 10 bits
        if (i && z[i] < -1023) z[i] = -1023;
    }
    int diff = z[0] - *prev_dc, m = magnitude(diff);
    *prev_dc = z[0];
    bits_put(b, dc->code[m], dc->size[m]);
    if (m) bits_put(b, (unsigned)(diff < 0 ? diff - 1 : diff), m);
    int run = 0;
    for (int i = 1; i < 64; i++) {
        if (!z[i]) { run++; continue; }
        while (run >= 16) { bits_put(b, ac->code[0xf0], ac->size[0xf0]); run -= 16; }
        int mm = magnitude(z[i]);
        int sym = (run << 4) | mm;
        bits_put(b, ac->code[sym], ac->size[sym]);
        bits_put(b, (unsigned)(z[i] < 0 ? z[i] - 1 : z[i]), mm);
        run = 0;
    }
    if (run) bits_put(b, ac->code[0], ac->size[0]);
}

int still_jpeg(int fd, const unsigned char *rgb, int w, int h, int quality)
{
    if (quality < 1) quality = 1;
    if (quality > 100) quality = 100;
    int scale = quality < 50 ? 5000 / quality : 200 - 2 * quality;
    unsigned char qt[2][64];
    float qf[2][64];
    for (int i = 0; i < 64; i++) {
        int a = (Q_LUMA[i] * scale + 50) / 100, c = (Q_CHROMA[i] * scale + 50) / 100;
        qt[0][i] = (unsigned char)(a < 1 ? 1 : a > 255 ? 255 : a);
        qt[1][i] = (unsigned char)(c < 1 ? 1 : c > 255 ? 255 : c);
        qf[0][i] = qt[0][i];
        qf[1][i] = qt[1][i];
    }
    for (int x = 0; x < 8; x++)
        for (int u = 0; u < 8; u++) cos_table[x][u] = cos((2 * x + 1) * u * M_PI / 16.0);
    Huff dc[2], ac[2];
    for (int t = 0; t < 2; t++) { huff_make(&dc[t], DC_BITS[t], DC_VALS); huff_make(&ac[t], AC_BITS[t], AC_VALS[t]); }

    Out o = { 0 };
    put16(&o, 0xffd8);
    static const unsigned char jfif[] = { 'J', 'F', 'I', 'F', 0, 1, 1, 0, 0, 1, 0, 1, 0, 0 };
    put16(&o, 0xffe0);
    put16(&o, 2 + sizeof jfif);
    put(&o, jfif, sizeof jfif);
    put16(&o, 0xffdb);
    put16(&o, 2 + 2 * 65);
    for (int t = 0; t < 2; t++) {
        put8(&o, (unsigned)t);
        for (int i = 0; i < 64; i++) put8(&o, qt[t][ZIGZAG[i]]);
    }
    put16(&o, 0xffc0);
    put16(&o, 17);
    put8(&o, 8);
    put16(&o, (unsigned)h);
    put16(&o, (unsigned)w);
    put8(&o, 3);
    for (int c = 0; c < 3; c++) { put8(&o, (unsigned)c + 1); put8(&o, 0x11); put8(&o, c ? 1 : 0); }
    for (int t = 0; t < 2; t++) {
        int nac = 0;
        for (int i = 0; i < 16; i++) nac += AC_BITS[t][i];
        put16(&o, 0xffc4);
        put16(&o, (unsigned)(2 + 17 + 12 + 17 + nac));
        put8(&o, (unsigned)t);
        put(&o, DC_BITS[t], 16);
        put(&o, DC_VALS, 12);
        put8(&o, 0x10 | (unsigned)t);
        put(&o, AC_BITS[t], 16);
        put(&o, AC_VALS[t], (size_t)nac);
    }
    put16(&o, 0xffda);
    put16(&o, 12);
    put8(&o, 3);
    for (int c = 0; c < 3; c++) { put8(&o, (unsigned)c + 1); put8(&o, c ? 0x11 : 0x00); }
    put8(&o, 0);
    put8(&o, 63);
    put8(&o, 0);
    Bits b = { .o = &o };
    int prev[3] = { 0, 0, 0 };
    for (int by = 0; by < h; by += 8)
        for (int bx = 0; bx < w; bx += 8) {
            float ycc[3][64];
            for (int y = 0; y < 8; y++)
                for (int x = 0; x < 8; x++) {
                    int sx = bx + x < w ? bx + x : w - 1, sy = by + y < h ? by + y : h - 1;   // the edge, repeated
                    const unsigned char *p = rgb + ((size_t)sy * (size_t)w + (size_t)sx) * 3;
                    float r = p[0], g = p[1], bl = p[2];
                    ycc[0][y * 8 + x] = 0.299f * r + 0.587f * g + 0.114f * bl - 128.0f;
                    ycc[1][y * 8 + x] = -0.168736f * r - 0.331264f * g + 0.5f * bl;
                    ycc[2][y * 8 + x] = 0.5f * r - 0.418688f * g - 0.081312f * bl;
                }
            for (int c = 0; c < 3; c++) block_code(&b, ycc[c], qf[c ? 1 : 0], &prev[c], &dc[c ? 1 : 0], &ac[c ? 1 : 0]);
        }
    if (b.n) bits_put(&b, 0x7f, 8 - b.n);          // the last byte filled with ones
    put16(&o, 0xffd9);
    int r = write_all(fd, &o);
    free(o.p);
    return r;
}
