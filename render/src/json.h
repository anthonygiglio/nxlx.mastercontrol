// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: GPL-3.0-or-later
//
// JSON <-> mpv_node, for the socket. Bounded on purpose: the text comes from another process.
#ifndef PVJ_JSON_H
#define PVJ_JSON_H

#include <stddef.h>
#include <mpv/client.h>

#define JSON_MAX_DEPTH 16

// A growable byte buffer.
typedef struct {
    char *p;
    size_t len, cap;
    int failed;         // an allocation failed: the buffer must not be used
} Buf;

void buf_init(Buf *b);
void buf_free(Buf *b);
void buf_add(Buf *b, const char *s, size_t n);
void buf_str(Buf *b, const char *s);

// Parse one JSON value from text[0..len). Returns 0 and fills *out (free it with json_free), or -1.
// Only the value and white space may be in the text.
int json_parse(const char *text, size_t len, mpv_node *out);
// Free a node made by json_parse (never one that libmpv made: that is mpv_free_node_contents).
void json_free(mpv_node *n);
// Append a node as JSON. Strings that are not valid UTF-8 have the bad bytes replaced by U+FFFD; numbers that are
// not finite are written as null.
void json_write(Buf *b, const mpv_node *n);
void json_write_string(Buf *b, const char *s);

// Helpers for building replies.
const mpv_node *node_map_get(const mpv_node *map, const char *key);

#endif
