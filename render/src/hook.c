// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: GPL-3.0-or-later
//
// The final pass: an mpv user shader on the OUTPUT stage, run by pvj-render at the output's pixels.
//
// The projection mapping (pvj/mapper.py: warp_shader for the show, shader while editing) is written as an mpv user
// shader file: //!TEXTURE blocks with their data as hex, then one //!HOOK OUTPUT block of GLSL that defines
// `vec4 hook()`, reading the picture with HOOKED_tex(pos) at HOOKED_pos and knowing the screen's size as HOOKED_size.
// mpv runs it on the final picture at the screen's size. Here the same text is compiled into the pass that scales the
// video layer (drawn at the internal size) to the screen: HOOKED_size is the output's size, as on mpv's OUTPUT stage,
// and HOOKED_tex reads the layer, so the mapping's screen pixels mean what they meant. Mapping and scaling are one pass.
//
// Only what the mapping uses is taken: HOOK OUTPUT (once), BIND HOOKED and BIND of a texture of the same file, DESC;
// TEXTURE with SIZE, FORMAT (rgba16f, rgba32f, rgba8), FILTER and BORDER. A file with a hook on any other stage, or
// with any other directive, is not this pass: it is handed to libmpv, which runs it on the video layer (effects and
// generators). A file that looks like ours and is damaged is refused.
#define _GNU_SOURCE
#include "pvj.h"
#include "json.h"

#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#define MAX_FILE (16u << 20)
#define MAX_TEX 8
#define MAX_TEX_SIDE 4096

typedef struct {
    char name[64];
    int w, h;
    GLenum ifmt, type;
    int bpp;            // bytes a texel in the data
    int linear, repeat, mirror;
    unsigned char *data;
} HookTex;

struct Hook {
    char *body;
    int ntex;
    HookTex tex[MAX_TEX];
    int nbind;
    char bind[MAX_TEX][64];
};

void hook_free(Hook *h)
{
    if (!h) return;
    free(h->body);
    for (int i = 0; i < h->ntex; i++) free(h->tex[i].data);
    free(h);
}

static char *read_file(const char *path, size_t *len, char *err, size_t n)
{
    int fd = open(path, O_RDONLY | O_CLOEXEC | O_NONBLOCK);
    if (fd < 0) { snprintf(err, n, "cannot open the shader file (%s)", strerror(errno)); return NULL; }
    struct stat st;
    if (fstat(fd, &st) || !S_ISREG(st.st_mode)) { close(fd); snprintf(err, n, "the shader is not a file"); return NULL; }
    if ((unsigned long long)st.st_size > MAX_FILE) { close(fd); snprintf(err, n, "the shader file is larger than 16 MiB"); return NULL; }
    char *buf = malloc((size_t)st.st_size + 1);
    if (!buf) { close(fd); snprintf(err, n, "out of memory"); return NULL; }
    size_t got = 0;
    while (got < (size_t)st.st_size) {
        ssize_t r = read(fd, buf + got, (size_t)st.st_size - got);
        if (r < 0 && errno == EINTR) continue;
        if (r <= 0) break;
        got += (size_t)r;
    }
    close(fd);
    buf[got] = 0;
    if (memchr(buf, 0, got)) { free(buf); snprintf(err, n, "the shader file holds a NUL byte"); return NULL; }
    *len = got;
    return buf;
}

static int valid_name(const char *s)
{
    size_t n = strlen(s);
    if (n == 0 || n > 60 || !(isalpha((unsigned char)s[0]) || s[0] == '_')) return 0;
    for (size_t i = 0; i < n; i++)
        if (!(isalnum((unsigned char)s[i]) || s[i] == '_')) return 0;
    if (!strncmp(s, "gl_", 3) || !strncmp(s, "pvj_", 4) || !strncmp(s, "HOOKED", 6)) return 0;
    return 1;
}

static int hexval(int c)
{
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

// One line: the text after "//!" split into a word and the rest.
static void directive(const char *line, size_t len, char *word, size_t wn, char *rest, size_t rn)
{
    size_t i = 3, j = 0;
    while (i < len && isspace((unsigned char)line[i])) i++;
    while (i < len && !isspace((unsigned char)line[i]) && j + 1 < wn) word[j++] = line[i++];
    word[j] = 0;
    while (i < len && isspace((unsigned char)line[i])) i++;
    size_t k = 0;
    while (i < len && k + 1 < rn) rest[k++] = line[i++];
    while (k > 0 && isspace((unsigned char)rest[k - 1])) k--;
    rest[k] = 0;
}

typedef struct {
    const char *head;       // first header line
    const char *body;       // after the header lines
    const char *end;
} Block;

int hook_load(const char *path, Hook **out, char *err, size_t n)
{
    *out = NULL;
    size_t len = 0;
    char *text = read_file(path, &len, err, n);
    if (!text) return -1;
    // Blocks: a run of "//!" lines, then everything up to the next "//!" line.
    Block blocks[2 * MAX_TEX + 4];
    int nb = 0, in_head = 0;
    const char *p = text, *end = text + len;
    while (p < end) {
        const char *eol = memchr(p, '\n', (size_t)(end - p));
        if (!eol) eol = end;
        int is_dir = (eol - p) >= 3 && !strncmp(p, "//!", 3);
        if (is_dir && !in_head) {
            if (nb == (int)(sizeof blocks / sizeof blocks[0])) { free(text); return 0; }   // more than ours ever has
            if (nb) blocks[nb - 1].end = p;
            blocks[nb].head = p;
            blocks[nb].body = NULL;
            blocks[nb].end = end;
            nb++;
            in_head = 1;
        } else if (!is_dir && in_head) {
            blocks[nb - 1].body = p;
            in_head = 0;
        }
        p = eol < end ? eol + 1 : end;
    }
    if (nb && !blocks[nb - 1].body) blocks[nb - 1].body = end;

    // First pass: is every hook on the OUTPUT stage, and is every directive one we take?
    int hooks = 0, ours = 1;
    for (int b = 0; b < nb; b++) {
        int is_tex = 0, is_hook = 0;
        for (const char *l = blocks[b].head; l < blocks[b].body;) {
            const char *eol = memchr(l, '\n', (size_t)(blocks[b].body - l));
            if (!eol) eol = blocks[b].body;
            char word[32], rest[128];
            directive(l, (size_t)(eol - l), word, sizeof word, rest, sizeof rest);
            if (!strcmp(word, "HOOK")) { is_hook = 1; if (strcmp(rest, "OUTPUT")) { free(text); return 0; } }
            else if (!strcmp(word, "TEXTURE")) is_tex = 1;
            else if (!strcmp(word, "BIND") || !strcmp(word, "DESC") || !strcmp(word, "SIZE") || !strcmp(word, "FORMAT")
                     || !strcmp(word, "FILTER") || !strcmp(word, "BORDER")) {}
            else ours = 0;
            l = eol < blocks[b].body ? eol + 1 : blocks[b].body;
        }
        if (is_hook && is_tex) ours = 0;
        if (is_hook) hooks++;
    }
    if (hooks != 1 || !ours) { free(text); return 0; }       // not one OUTPUT pass we can run: libmpv's

    Hook *h = calloc(1, sizeof *h);
    if (!h) { free(text); snprintf(err, n, "out of memory"); return -1; }
    for (int b = 0; b < nb; b++) {
        HookTex t = { .ifmt = 0 };
        int is_tex = 0, is_hook = 0;
        for (const char *l = blocks[b].head; l < blocks[b].body;) {
            const char *eol = memchr(l, '\n', (size_t)(blocks[b].body - l));
            if (!eol) eol = blocks[b].body;
            char word[32], rest[128];
            directive(l, (size_t)(eol - l), word, sizeof word, rest, sizeof rest);
            if (!strcmp(word, "HOOK")) is_hook = 1;
            else if (!strcmp(word, "TEXTURE")) {
                is_tex = 1;
                if (!valid_name(rest)) { snprintf(err, n, "a texture's name is not allowed"); goto bad; }
                snprintf(t.name, sizeof t.name, "%.60s", rest);
            } else if (!strcmp(word, "SIZE")) {
                char extra;
                if (sscanf(rest, "%d %d %c", &t.w, &t.h, &extra) != 2 || t.w < 1 || t.h < 1 || t.w > MAX_TEX_SIDE || t.h > MAX_TEX_SIDE) {
                    snprintf(err, n, "a texture's SIZE is not two numbers from 1 to %d", MAX_TEX_SIDE);
                    goto bad;
                }
            } else if (!strcmp(word, "FORMAT")) {
                if (!strcmp(rest, "rgba16f")) { t.ifmt = GL_RGBA16F; t.type = GL_FLOAT; t.bpp = 16; }
                else if (!strcmp(rest, "rgba32f")) { t.ifmt = GL_RGBA32F; t.type = GL_FLOAT; t.bpp = 16; }
                else if (!strcmp(rest, "rgba8")) { t.ifmt = GL_RGBA8; t.type = GL_UNSIGNED_BYTE; t.bpp = 4; }
                else { snprintf(err, n, "a texture's FORMAT is not rgba16f, rgba32f or rgba8"); goto bad; }
            } else if (!strcmp(word, "FILTER")) {
                if (!strcmp(rest, "LINEAR")) t.linear = 1;
                else if (strcmp(rest, "NEAREST")) { snprintf(err, n, "FILTER is LINEAR or NEAREST"); goto bad; }
            } else if (!strcmp(word, "BORDER")) {
                if (!strcmp(rest, "REPEAT")) t.repeat = 1;
                else if (!strcmp(rest, "MIRROR")) t.mirror = 1;
                else if (strcmp(rest, "CLAMP")) { snprintf(err, n, "BORDER is CLAMP, REPEAT or MIRROR"); goto bad; }
            } else if (!strcmp(word, "BIND")) {
                if (!strcmp(rest, "HOOKED")) {}
                else if (!valid_name(rest) || h->nbind == MAX_TEX) { snprintf(err, n, "a BIND names no texture of the file"); goto bad; }
                else snprintf(h->bind[h->nbind++], sizeof h->bind[0], "%.60s", rest);
            }
            l = eol < blocks[b].body ? eol + 1 : blocks[b].body;
        }
        if (is_hook) {
            size_t blen = (size_t)(blocks[b].end - blocks[b].body);
            h->body = malloc(blen + 1);
            if (!h->body) { snprintf(err, n, "out of memory"); goto bad; }
            memcpy(h->body, blocks[b].body, blen);
            h->body[blen] = 0;
        } else if (is_tex) {
            if (!t.w || !t.ifmt) { snprintf(err, n, "a texture needs SIZE and FORMAT"); goto bad; }
            if (h->ntex == MAX_TEX) { snprintf(err, n, "more than %d textures", MAX_TEX); goto bad; }
            for (int i = 0; i < h->ntex; i++)
                if (!strcmp(h->tex[i].name, t.name)) { snprintf(err, n, "two textures of one name"); goto bad; }
            if (t.w > MAX_TEX_SIDE || t.h > MAX_TEX_SIDE || (size_t)(blocks[b].end - blocks[b].body) < (size_t)t.w * (size_t)t.h * (size_t)t.bpp * 2) {
                snprintf(err, n, "a texture's data is not as long as its SIZE says"); goto bad;
            }
            size_t need = (size_t)t.w * (size_t)t.h * (size_t)t.bpp;
            t.data = malloc(need);
            if (!t.data) { snprintf(err, n, "out of memory"); goto bad; }
            size_t got = 0;
            int hi = -1;
            for (const char *c = blocks[b].body; c < blocks[b].end; c++) {
                if (isspace((unsigned char)*c)) continue;
                int v = hexval(*c);
                if (v < 0 || got == need) { free(t.data); snprintf(err, n, "a texture's data is not %zu bytes of hex", need); goto bad; }
                if (hi < 0) hi = v;
                else { t.data[got++] = (unsigned char)(hi << 4 | v); hi = -1; }
            }
            if (got != need || hi >= 0) { free(t.data); snprintf(err, n, "a texture's data is not %zu bytes of hex", need); goto bad; }
            h->tex[h->ntex++] = t;
        }
    }
    for (int i = 0; i < h->nbind; i++) {
        int found = 0;
        for (int j = 0; j < h->ntex; j++) found |= !strcmp(h->bind[i], h->tex[j].name);
        if (!found) { snprintf(err, n, "BIND %s names no texture of the file", h->bind[i]); goto bad; }
    }
    if (!h->body || !strstr(h->body, "hook")) { snprintf(err, n, "the OUTPUT pass has no hook()"); goto bad; }
    free(text);
    *out = h;
    return 1;
bad:
    free(text);
    hook_free(h);
    return -1;
}

/* ---- GL ---------------------------------------------------------------------------------------------------------- */
static GLuint compile(GLenum type, const char *src, char *err, size_t n)
{
    GLuint s = glCreateShader(type);
    glShaderSource(s, 1, &src, NULL);
    glCompileShader(s);
    GLint ok = 0;
    glGetShaderiv(s, GL_COMPILE_STATUS, &ok);
    if (!ok) {
        char log[512] = "";
        glGetShaderInfoLog(s, sizeof log, NULL, log);
        for (char *c = log; *c; c++) if (*c == '\n' || *c == '\r') *c = ' ';
        snprintf(err, n, "the shader did not compile: %.120s", log);
        glDeleteShader(s);
        return 0;
    }
    return s;
}

GLuint gl_program(const char *vs, const char *fs, char *err, size_t n)
{
    GLuint v = compile(GL_VERTEX_SHADER, vs, err, n);
    if (!v) return 0;
    GLuint f = compile(GL_FRAGMENT_SHADER, fs, err, n);
    if (!f) { glDeleteShader(v); return 0; }
    GLuint p = glCreateProgram();
    glAttachShader(p, v);
    glAttachShader(p, f);
    glBindAttribLocation(p, 0, "pvj_vert");
    glLinkProgram(p);
    glDeleteShader(v);
    glDeleteShader(f);
    GLint ok = 0;
    glGetProgramiv(p, GL_LINK_STATUS, &ok);
    if (!ok) {
        char log[512] = "";
        glGetProgramInfoLog(p, sizeof log, NULL, log);
        snprintf(err, n, "the shader did not link: %.120s", log);
        glDeleteProgram(p);
        return 0;
    }
    return p;
}

// pvj_pos is mpv's HOOKED_pos: (0, 0) at the top left of the screen, as the mapping's screen pixels are measured.
static const char *VS =
    "#version 300 es\n"
    "in vec2 pvj_vert;\n"
    "out vec2 pvj_pos;\n"
    "void main() { pvj_pos = vec2(pvj_vert.x * 0.5 + 0.5, 0.5 - pvj_vert.y * 0.5); gl_Position = vec4(pvj_vert, 0.0, 1.0); }\n";

int hookgl_build(const Hook *h, HookGL *g, char *err, size_t n)
{
    memset(g, 0, sizeof *g);
    Buf fs;
    buf_init(&fs);
    buf_str(&fs,
            "#version 300 es\n"
            "precision highp float;\n"
            "precision highp int;\n"
            "precision highp sampler2D;\n"
            "in vec2 pvj_pos;\n"
            "out vec4 pvj_out;\n"
            "uniform sampler2D HOOKED_raw;\n"
            "uniform vec2 HOOKED_size;\n"
            "uniform vec2 HOOKED_pt;\n"
            "uniform vec2 target_size;\n"
            "uniform vec2 input_size;\n"
            "#define HOOKED_pos pvj_pos\n"
            "#define HOOKED_mul 1.0\n"
            "#define HOOKED_tex(pos) texture(HOOKED_raw, pos)\n"
            "#define HOOKED_texOff(off) texture(HOOKED_raw, pvj_pos + HOOKED_pt * vec2(off))\n");
    for (int i = 0; i < h->ntex; i++) {
        char line[200];
        snprintf(line, sizeof line, "uniform sampler2D %s;\nuniform vec2 %s_size;\n", h->tex[i].name, h->tex[i].name);
        buf_str(&fs, line);
    }
    buf_str(&fs, "#line 1\n");
    buf_str(&fs, h->body);
    buf_str(&fs, "\nvoid main() { pvj_out = hook(); }\n");
    if (fs.failed) { buf_free(&fs); snprintf(err, n, "out of memory"); return -1; }
    g->prog = gl_program(VS, fs.p, err, n);
    buf_free(&fs);
    if (!g->prog) return -1;
    g->loc_raw = glGetUniformLocation(g->prog, "HOOKED_raw");
    g->loc_size = glGetUniformLocation(g->prog, "HOOKED_size");
    g->loc_pt = glGetUniformLocation(g->prog, "HOOKED_pt");
    g->loc_target = glGetUniformLocation(g->prog, "target_size");
    g->loc_input = glGetUniformLocation(g->prog, "input_size");
    int float_linear = 0;
    const char *ext = (const char *)glGetString(GL_EXTENSIONS);
    if (ext && strstr(ext, "GL_OES_texture_float_linear")) float_linear = 1;
    g->ntex = h->ntex;
    for (int i = 0; i < h->ntex; i++) {
        const HookTex *t = &h->tex[i];
        glGenTextures(1, &g->tex[i]);
        glBindTexture(GL_TEXTURE_2D, g->tex[i]);
        glPixelStorei(GL_UNPACK_ALIGNMENT, 4);
        glTexImage2D(GL_TEXTURE_2D, 0, (GLint)t->ifmt, t->w, t->h, 0, GL_RGBA, t->type, t->data);
        if (glGetError() != GL_NO_ERROR) { snprintf(err, n, "the texture %s was not taken by the GPU", t->name); hookgl_free(g); return -1; }
        // 32-bit float textures are filtered only with OES_texture_float_linear: else the nearest texel
        GLint f = t->linear && (t->ifmt != GL_RGBA32F || float_linear) ? GL_LINEAR : GL_NEAREST;
        GLint wrap = t->repeat ? GL_REPEAT : t->mirror ? GL_MIRRORED_REPEAT : GL_CLAMP_TO_EDGE;
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, f);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, f);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, wrap);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, wrap);
        g->tex_loc[i] = glGetUniformLocation(g->prog, t->name);
        char sz[80];
        snprintf(sz, sizeof sz, "%s_size", t->name);
        g->tex_size_loc[i] = glGetUniformLocation(g->prog, sz);
        g->tex_w[i] = t->w;
        g->tex_h[i] = t->h;
    }
    return 0;
}

void hookgl_free(HookGL *g)
{
    if (g->prog) glDeleteProgram(g->prog);
    for (int i = 0; i < g->ntex; i++)
        if (g->tex[i]) glDeleteTextures(1, &g->tex[i]);
    memset(g, 0, sizeof *g);
}
