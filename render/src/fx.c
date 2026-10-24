// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: GPL-3.0-or-later
//
// The renderer's own shader programs (project-log/DECISIONS.md, D106): ISF generators and effects drawn by
// pvj-render itself, with their inputs as uniforms set every frame, so a value that moves is never compiled again.
//
// The panel (pvj/shaders.py and pvj/effects.py, translate_native) is the only reader of an ISF file: it checks the
// file, renames what ISF names differently, and writes a "program file" into the player's runtime folder:
//
//   //!PVJ 1
//   //!KIND generator | filter
//   //!SIZE W H                       a generator's drawing size (Picture detail, D93); a filter draws at the inside size
//   //!CLOCK 0 | 1                    a filter reads TIME (a generator always does)
//   //!LINE N                        the ISF file's line the code starts on, for what the compiler says
//   //!INPUT float|bool|long|color|point2D NAME VALUE...
//   //!CONTROL speed|hue|gain|amount|offset VALUE
//   //!CODE
//   ... the code, with exactly one void pvj_main() ...
//
// This reader takes nothing else: every line before //!CODE is one of these, every name is a plain identifier that is
// not the language's or this wrapper's, every value a finite number, and the code holds no "//!", no NUL and no
// backslash. The file is read as hook.c reads the mapping's (a regular file, never through a link, at most 64 KiB).
//
// What runs. Source 1 has two slots: 0, a generator, which replaces source 1's picture; 1, an effect, which filters
// the picture (the generator's, or the clip's after the crossfade's mix, so an effect stays on through a crossfade as
// D74 wants). A generator runs at most `--fx-fps` times a second (30 by default, the carrier's rate on classic), at
// its own size, into a texture of its own; an effect runs at the inside size when its picture is new, its values
// moved, or (one that reads the clock) when its time is due; on every other vblank the last result is used again.
//
// Values. The socket's threads write a slot's values under app.lock; the render thread copies them once a frame and
// sets them as uniforms. TIME is the renderer's own clock: offset + the sum of each frame's seconds times speed, so a
// change of speed never makes it jump. A program whose code cannot take its inputs as uniforms (a uniform in a
// constant expression, an array sized by one) is built with its values written in as constants instead, as classic
// does; then a change of a value is a new build, at most five a second (state "constants").
#define _GNU_SOURCE
#include "pvj.h"
#include "json.h"

#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <math.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#define FX_MAX_FILE (64u << 10)
#define FX_REBUILD_GAP 0.2              // a program built with constants is built again at most this often
#define FX_DT_MAX 1.0                   // a frame counts for at most this many seconds of TIME (after a stall)

#define FX_TARGETS 4                    // buffers a program may draw into (ISF PASSES with TARGET), D106 step c
#define FX_PASSES 4
#define FX_BUFFER_BYTES (64u << 20)     // all of one program's buffers together (each is two textures)

typedef struct {
    char name[FX_NAME];
    int persistent, isfloat;
    char wk, hk;                        // 'p': pixels; 'w' or 'h': a factor of the drawing's own width or height
    float wv, hv;
} FxTarget;

struct FxProg {
    char path[1024];
    int kind, w, h, clock, line;
    int ntargets, npasses;
    FxTarget tgt[FX_TARGETS];
    int pass[FX_PASSES];                // the target each pass draws into; -1: the picture itself (the last pass)
    int ninputs;
    FxValue in[FX_MAX_INPUTS];
    float ctl[FXC_N];
    char *code;
};

static const char *const CONTROL_NAMES[FXC_N] = { "speed", "hue", "gain", "amount", "offset" };
static const char *const TYPE_NAMES[FXT_N] = { "float", "bool", "long", "color", "point2D" };
static const int TYPE_COUNT[FXT_N] = { 1, 1, 1, 4, 2 };

// The names the wrapper itself defines, or GLSL's that an input may never take. The panel refuses these before;
// this list is the second line.
static const char *const TAKEN[] = {
    "TIME", "TIMEDELTA", "FRAMEINDEX", "DATE", "RENDERSIZE", "PASSINDEX", "isf_FragNormCoord", "vv_FragNormCoord",
    "main", "hook", "texture",
    // GLSL ES 3.00's keywords and the words it reserves (its specification, 3.6)
    "attribute", "const", "uniform", "varying", "layout", "centroid", "flat", "smooth", "break", "continue", "do", "for",
    "while", "switch", "case", "default", "if", "else", "in", "out", "inout", "float", "int", "void", "bool", "true",
    "false", "invariant", "discard", "return", "mat2", "mat3", "mat4", "mat2x2", "mat2x3", "mat2x4", "mat3x2", "mat3x3",
    "mat3x4", "mat4x2", "mat4x3", "mat4x4", "vec2", "vec3", "vec4", "ivec2", "ivec3", "ivec4", "bvec2", "bvec3", "bvec4",
    "uint", "uvec2", "uvec3", "uvec4", "lowp", "mediump", "highp", "precision", "sampler2D", "sampler3D", "samplerCube",
    "sampler2DShadow", "samplerCubeShadow", "sampler2DArray", "sampler2DArrayShadow", "isampler2D", "isampler3D",
    "isamplerCube", "isampler2DArray", "usampler2D", "usampler3D", "usamplerCube", "usampler2DArray", "struct",
    "coherent", "volatile", "restrict", "readonly", "writeonly", "resource", "atomic_uint", "noperspective", "patch",
    "sample", "subroutine", "common", "partition", "active", "asm", "class", "union", "enum", "typedef", "template",
    "this", "goto", "inline", "noinline", "public", "static", "extern", "external", "interface", "long", "short",
    "double", "half", "fixed", "unsigned", "superp", "input", "output", "hvec2", "hvec3", "hvec4", "dvec2", "dvec3",
    "dvec4", "fvec2", "fvec3", "fvec4", "sampler3DRect", "filter", "image1D", "image2D", "image3D", "imageCube",
    "sizeof", "cast", "namespace", "using", NULL };

void fx_prog_free(FxProg *p)
{
    if (!p) return;
    free(p->code);
    free(p);
}

int fx_prog_kind(const FxProg *p) { return p->kind; }
const char *fx_prog_path(const FxProg *p) { return p->path; }

// A buffer's name: it never reaches GLSL (the code reads pvj_t<i>), so a plain identifier is enough (the review of #170).
static int fx_plain_ident(const char *s)
{
    size_t n = strlen(s);
    if (n == 0 || n > FX_NAME - 1 || !isalpha((unsigned char)s[0])) return 0;
    for (size_t i = 0; i < n; i++)
        if (!(isalnum((unsigned char)s[i]) || s[i] == '_')) return 0;
    return 1;
}

static int fx_name_ok(const char *s)
{
    size_t n = strlen(s);
    if (n == 0 || n > FX_NAME - 1 || !(isalpha((unsigned char)s[0]))) return 0;
    for (size_t i = 0; i < n; i++)
        if (!(isalnum((unsigned char)s[i]) || s[i] == '_')) return 0;
    if (strstr(s, "__") || !strncmp(s, "gl_", 3) || !strncmp(s, "isf_", 4)) return 0;
    if (!strncmp(s, "pvj_", 4) && strncmp(s, "pvj_in_", 7)) return 0;   // pvj_in_: the panel's renamed inputs
    for (int i = 0; TAKEN[i]; i++)
        if (!strcmp(TAKEN[i], s)) return 0;
    return 1;
}

static char *fx_read_file(const char *path, size_t *len, char *err, size_t n)
{
    int fd = open(path, O_RDONLY | O_CLOEXEC | O_NONBLOCK | O_NOFOLLOW);
    if (fd < 0) { snprintf(err, n, "cannot open the program file (%s)", strerror(errno)); return NULL; }
    struct stat st;
    if (fstat(fd, &st) || !S_ISREG(st.st_mode)) { close(fd); snprintf(err, n, "the program is not a file"); return NULL; }
    if ((unsigned long long)st.st_size > FX_MAX_FILE) { close(fd); snprintf(err, n, "the program file is larger than 64 KiB"); return NULL; }
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
    if (memchr(buf, 0, got)) { free(buf); snprintf(err, n, "the program file holds a NUL byte"); return NULL; }
    *len = got;
    return buf;
}

// Numbers from one line: exactly `want` finite numbers in `s`, else -1.
static int numbers(const char *s, float *out, int want)
{
    int got = 0;
    while (*s) {
        while (*s == ' ') s++;
        if (!*s) break;
        if (got == want) return -1;
        char *e;
        errno = 0;
        double v = strtod(s, &e);
        if (e == s || errno || !isfinite(v) || fabs(v) > 1e9 || (*e && *e != ' ')) return -1;
        out[got++] = (float)v;
        s = e;
    }
    return got == want ? 0 : -1;
}

int fx_read(const char *path, FxProg **out, char *err, size_t n)
{
    *out = NULL;
    size_t len = 0;
    char *text = fx_read_file(path, &len, err, n);
    if (!text) return -1;
    FxProg *p = calloc(1, sizeof *p);
    if (!p) { free(text); snprintf(err, n, "out of memory"); return -1; }
    p->kind = -1;
    p->clock = 1;
    snprintf(p->path, sizeof p->path, "%s", path);
    p->ctl[FXC_SPEED] = 1.0f;
    p->ctl[FXC_GAIN] = 1.0f;
    p->ctl[FXC_AMOUNT] = 1.0f;
    int first = 1, have_code = 0;
    char *line = text, *end = text + len;
    while (line < end) {
        char *eol = memchr(line, '\n', (size_t)(end - line));
        if (!eol) eol = end;
        *eol = 0;
        if (first && strcmp(line, "//!PVJ 1")) { snprintf(err, n, "not a program file (no //!PVJ 1 at the top)"); goto bad; }
        if (!first) {
            if (strncmp(line, "//!", 3)) { snprintf(err, n, "a line before //!CODE is not a directive"); goto bad; }
            const char *d = line + 3;
            if (!strcmp(d, "CODE")) { line = eol < end ? eol + 1 : end; have_code = 1; break; }
            if (!strcmp(d, "KIND generator")) p->kind = FX_GENERATOR;
            else if (!strcmp(d, "KIND filter")) p->kind = FX_FILTER;
            else if (!strncmp(d, "SIZE ", 5)) {
                float wh[2];
                if (numbers(d + 5, wh, 2) || wh[0] != floorf(wh[0]) || wh[1] != floorf(wh[1]) || wh[0] < 16 || wh[1] < 16
                    || wh[0] > 4096 || wh[1] > 4096) { snprintf(err, n, "SIZE is two whole numbers from 16 to 4096"); goto bad; }
                p->w = (int)wh[0];
                p->h = (int)wh[1];
            } else if (!strcmp(d, "CLOCK 0") || !strcmp(d, "CLOCK 1")) p->clock = d[6] == '1';
            else if (!strncmp(d, "LINE ", 5)) {
                char *e;
                long v = strtol(d + 5, &e, 10);
                if (!d[5] || *e || v < 0 || v > 100000) { snprintf(err, n, "LINE is a whole number"); goto bad; }
                p->line = (int)v;
            } else if (!strncmp(d, "CONTROL ", 8)) {
                char name[16];
                int used = 0;
                if (sscanf(d + 8, "%15s %n", name, &used) != 1 || !used) { snprintf(err, n, "a CONTROL line is not read"); goto bad; }
                int k = -1;
                for (int i = 0; i < FXC_N; i++) if (!strcmp(CONTROL_NAMES[i], name)) k = i;
                float v;
                if (k < 0 || numbers(d + 8 + used, &v, 1)) { snprintf(err, n, "a CONTROL is not one of speed, hue, gain, amount, offset with a number"); goto bad; }
                p->ctl[k] = v;
            } else if (!strncmp(d, "TARGET ", 7)) {
                // //!TARGET name persistent float wspec hspec (D106 step c)
                if (p->ntargets == FX_TARGETS) { snprintf(err, n, "more than %d buffers", FX_TARGETS); goto bad; }
                FxTarget *t = &p->tgt[p->ntargets];
                char name[FX_NAME], ws[24], hs[24];
                int pers = -1, fl = -1, used = 0;
                if (sscanf(d + 7, "%40s %d %d %23s %23s %n", name, &pers, &fl, ws, hs, &used) != 5 || d[7 + used]
                    || (pers != 0 && pers != 1) || (fl != 0 && fl != 1) || !fx_plain_ident(name)) {
                    snprintf(err, n, "a TARGET line is not a name, two switches and two sizes");
                    goto bad;
                }
                for (int i = 0; i < p->ntargets; i++)
                    if (!strcmp(p->tgt[i].name, name)) { snprintf(err, n, "two buffers of one name"); goto bad; }
                const char *specs[2] = { ws, hs };
                for (int a = 0; a < 2; a++) {
                    char k = specs[a][0], *e;
                    errno = 0;
                    double v = strtod(specs[a] + 1, &e);
                    int ok = (k == 'p' || k == 'w' || k == 'h') && e != specs[a] + 1 && !*e && !errno && isfinite(v)
                             && (k == 'p' ? (v >= 1 && v <= 4096 && v == floor(v)) : (v >= 1.0 / 16 - 1e-6 && v <= 4.0 + 1e-6));
                    if (!ok) { snprintf(err, n, "a buffer's size is not p<pixels>, w<factor> or h<factor>"); goto bad; }
                    if (a == 0) { t->wk = k; t->wv = (float)v; } else { t->hk = k; t->hv = (float)v; }
                }
                snprintf(t->name, sizeof t->name, "%s", name);
                t->persistent = pers;
                t->isfloat = fl;
                p->ntargets++;
            } else if (!strncmp(d, "PASS ", 5)) {
                if (p->npasses == FX_PASSES) { snprintf(err, n, "more than %d passes", FX_PASSES); goto bad; }
                if (!strcmp(d + 5, "-")) p->pass[p->npasses++] = -1;
                else if (d[5] >= '0' && d[5] < '0' + FX_TARGETS && !d[6]) p->pass[p->npasses++] = d[5] - '0';
                else { snprintf(err, n, "a PASS is a buffer's number or -"); goto bad; }
            } else if (!strncmp(d, "INPUT ", 6)) {
                char type[16], name[FX_NAME];
                int used = 0;
                if (sscanf(d + 6, "%15s %40s %n", type, name, &used) != 2 || !used) { snprintf(err, n, "an INPUT line is not read"); goto bad; }
                int t = -1;
                for (int i = 0; i < FXT_N; i++) if (!strcmp(TYPE_NAMES[i], type)) t = i;
                if (t < 0) { snprintf(err, n, "an INPUT has a type that is not float, bool, long, color or point2D"); goto bad; }
                if (!fx_name_ok(name)) { snprintf(err, n, "an INPUT's name is not allowed"); goto bad; }
                if (p->ninputs == FX_MAX_INPUTS) { snprintf(err, n, "more than %d inputs", FX_MAX_INPUTS); goto bad; }
                for (int i = 0; i < p->ninputs; i++)
                    if (!strcmp(p->in[i].name, name)) { snprintf(err, n, "two inputs of one name"); goto bad; }
                FxValue *v = &p->in[p->ninputs];
                memset(v, 0, sizeof *v);
                snprintf(v->name, sizeof v->name, "%s", name);
                v->type = t;
                if (numbers(d + 6 + used, v->v, TYPE_COUNT[t])) { snprintf(err, n, "the INPUT %s has no value of its type", name); goto bad; }
                if ((t == FXT_BOOL && v->v[0] != 0 && v->v[0] != 1) || (t == FXT_LONG && v->v[0] != floorf(v->v[0]))) {
                    snprintf(err, n, "the INPUT %s has no value of its type", name);
                    goto bad;
                }
                p->ninputs++;
            } else { snprintf(err, n, "a directive that is not known"); goto bad; }
        }
        first = 0;
        line = eol < end ? eol + 1 : end;
    }
    if (!have_code) { snprintf(err, n, "the program has no //!CODE"); goto bad; }
    if (p->kind < 0) { snprintf(err, n, "the program has no KIND"); goto bad; }
    if (p->kind == FX_GENERATOR && !p->w) { snprintf(err, n, "a generator needs its SIZE"); goto bad; }
    if (!p->npasses) p->pass[p->npasses++] = -1;            // one pass: the picture itself
    for (int i = 0; i < p->npasses; i++) {
        int last = i == p->npasses - 1;
        if ((last && p->pass[i] != -1) || (!last && (p->pass[i] < 0 || p->pass[i] >= p->ntargets))) {
            snprintf(err, n, "every pass but the last draws into a buffer of the file, and the last draws the picture");
            goto bad;
        }
    }
    // the code: what is left of the file (the line ends cut above are put back)
    for (char *c = line; c < end; c++) if (!*c) *c = '\n';
    size_t clen = (size_t)(end - line);
    if (memmem(line, clen, "//!", 3)) { snprintf(err, n, "the code holds //!"); goto bad; }
    if (memchr(line, '\\', clen)) { snprintf(err, n, "the code holds a backslash"); goto bad; }
    if (!memmem(line, clen, "pvj_main", 8)) { snprintf(err, n, "the code has no pvj_main()"); goto bad; }
    p->code = malloc(clen + 1);
    if (!p->code) { snprintf(err, n, "out of memory"); goto bad; }
    memcpy(p->code, line, clen);
    p->code[clen] = 0;
    free(text);
    *out = p;
    return 0;
bad:
    free(text);
    fx_prog_free(p);
    return -1;
}

/* ---- the slots as the socket sees them ---------------------------------------------------------------------------- */
// A value from the socket for an input of the slot's program (under app.lock): the right count of numbers for its type,
// a bool true or false (or 0 and 1), a whole number for a long. 0 when it was taken.
int fx_set_value(FxSlot *s, const char *name, const mpv_node *v)
{
    if (name[0] == '@') {
        for (int k = 0; k < FXC_N; k++) {
            if (strcmp(name + 1, CONTROL_NAMES[k])) continue;
            if (v->format != MPV_FORMAT_INT64 && v->format != MPV_FORMAT_DOUBLE) return -1;
            double x = v->format == MPV_FORMAT_INT64 ? (double)v->u.int64 : v->u.double_;      // bounded below, as every value
            if (!isfinite(x) || fabs(x) > 1e9) return -1;
            s->ctl[k] = (float)x;
            if (k == FXC_OFFSET) s->reset_time = 1;
            return 0;
        }
        return -1;
    }
    for (int i = 0; i < s->ninputs; i++) {
        FxValue *in = &s->val[i];
        if (strcmp(in->name, name)) continue;
        float got[4];
        int want = TYPE_COUNT[in->type];
        if (want == 1) {
            if (v->format == MPV_FORMAT_FLAG && in->type == FXT_BOOL) got[0] = v->u.flag ? 1.0f : 0.0f;
            else if (v->format == MPV_FORMAT_INT64 && v->u.int64 >= -1000000000LL && v->u.int64 <= 1000000000LL) got[0] = (float)v->u.int64;
            else if (v->format == MPV_FORMAT_DOUBLE && isfinite(v->u.double_) && fabs(v->u.double_) <= 1e9) got[0] = (float)v->u.double_;
            else return -1;
            if (in->type == FXT_BOOL && got[0] != 0 && got[0] != 1) return -1;
            if (in->type == FXT_LONG && got[0] != floorf(got[0])) return -1;
        } else {
            if (v->format != MPV_FORMAT_NODE_ARRAY || v->u.list->num != want) return -1;
            for (int k = 0; k < want; k++) {
                const mpv_node *e = &v->u.list->values[k];
                double x = e->format == MPV_FORMAT_INT64 ? (double)e->u.int64 : e->format == MPV_FORMAT_DOUBLE ? e->u.double_ : NAN;   // bounded below
                if (!isfinite(x) || fabs(x) > 1e9) return -1;
                got[k] = (float)x;
            }
        }
        memcpy(in->v, got, sizeof(float) * (size_t)want);
        return 0;
    }
    return -1;
}

/* ---- GL --------------------------------------------------------------------------------------------------------- */
typedef struct {
    int on, kind, clock, w, h, constants;
    GLuint prog, tex, fbo;
    GLint u_time, u_delta, u_frame, u_date, u_size, u_hue, u_gain, u_src, u_amount;
    GLint u_in[FX_MAX_INPUTS];
    FxProg *src;                        // kept: a program with constants is built again from it
    double time, last_run, last_frame, last_build;
    long frames, seen_values;
    int dirty;                          // values moved since the last run
    int rebuild;                        // a program with constants: its values moved since it was built
    FxValue val[FX_MAX_INPUTS];         // this frame's copy
    float ctl[FXC_N];
    GLuint out;                         // the texture the last run left (an effect at amount 0: its picture)
    GLuint last_in;                     // the texture its last run read: another one (the chain changed before it) runs it again
    // the buffers (D106 step c): two textures each, read from one (`cur`, last frame's for a persistent buffer) and
    // drawn into the other, which is then the one read
    int ntargets;
    GLuint ttex[FX_TARGETS][2], tfbo[FX_TARGETS][2];
    int tcur[FX_TARGETS], tw[FX_TARGETS], th[FX_TARGETS];
    GLint u_t[FX_TARGETS], u_tsz[FX_TARGETS], u_pass, u_final, u_srcsz;
    double tbytes;                      // what its buffers take (counted in fx_buffer_total)
    int float_low;                      // a FLOAT buffer is 8-bit here: this GPU cannot draw into half floats
} FxGL;

static int float_targets = -1;          // the GPU can draw into half-float textures (EXT_color_buffer_(half_)float)

static FxGL gl[MAX_SOURCES][FX_SLOTS];      // by source index (0 is source 1) and slot
static GLuint fx_vao, fx_vbo;

static const char *FX_VS =
    "#version 300 es\n"
    "in vec2 pvj_vert;\n"
    "out vec2 pvj_pos;\n"
    "void main() { pvj_pos = pvj_vert * 0.5 + 0.5; gl_Position = vec4(pvj_vert, 0.0, 1.0); }\n";

static void count_gl(long d) { __atomic_add_fetch(&app.fx_gl, d, __ATOMIC_RELAXED); }

void fx_init(void)
{
    static const float q[] = { -1, -1, 1, -1, -1, 1, 1, 1 };
    glGenVertexArrays(1, &fx_vao);
    glBindVertexArray(fx_vao);
    glGenBuffers(1, &fx_vbo);
    glBindBuffer(GL_ARRAY_BUFFER, fx_vbo);
    glBufferData(GL_ARRAY_BUFFER, sizeof q, q, GL_STATIC_DRAW);
    glEnableVertexAttribArray(0);
    glVertexAttribPointer(0, 2, GL_FLOAT, GL_FALSE, 0, 0);
    glBindVertexArray(0);
}

static void fx_quad(void)
{
    glBindVertexArray(fx_vao);
    glDrawArrays(GL_TRIANGLE_STRIP, 0, 4);
    glBindVertexArray(0);
}

static void g(Buf *b, const char *s) { buf_str(b, s); }

static void gf(Buf *b, const char *fmt, ...) __attribute__((format(printf, 2, 3)));
static void gf(Buf *b, const char *fmt, ...)
{
    char line[512];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(line, sizeof line, fmt, ap);
    va_end(ap);
    buf_str(b, line);
}

static void glsl_float(char *out, size_t n, float v)
{
    snprintf(out, n, "%.9g", (double)v);
    if (!strpbrk(out, ".eE")) strncat(out, ".0", n - strlen(out) - 1);
}

static void input_decl(Buf *b, const FxValue *in, int constants)
{
    static const char *const GLSL[FXT_N] = { "float", "bool", "int", "vec4", "vec2" };
    if (!constants) { gf(b, "uniform %s %s;\n", GLSL[in->type], in->name); return; }
    char a[4][40];
    for (int k = 0; k < TYPE_COUNT[in->type]; k++) glsl_float(a[k], sizeof a[k], in->v[k]);
    switch (in->type) {
    case FXT_FLOAT: gf(b, "const float %s = %s;\n", in->name, a[0]); break;
    case FXT_BOOL: gf(b, "const bool %s = %s;\n", in->name, in->v[0] != 0 ? "true" : "false"); break;
    case FXT_LONG: gf(b, "const int %s = %d;\n", in->name, (int)in->v[0]); break;
    case FXT_COLOR: gf(b, "const vec4 %s = vec4(%s, %s, %s, %s);\n", in->name, a[0], a[1], a[2], a[3]); break;
    default: gf(b, "const vec2 %s = vec2(%s, %s);\n", in->name, a[0], a[1]); break;
    }
}

static GLuint fx_compile(GLenum type, const char *src, char *err, size_t n)
{
    GLuint s = glCreateShader(type);
    glShaderSource(s, 1, &src, NULL);
    glCompileShader(s);
    GLint ok = 0;
    glGetShaderiv(s, GL_COMPILE_STATUS, &ok);
    if (!ok) {
        char log[1024] = "";
        glGetShaderInfoLog(s, sizeof log, NULL, log);
        for (char *c = log; *c; c++) if (*c == '\n' || *c == '\r' || *c == '\t' || (unsigned char)*c < 32 || (unsigned char)*c > 126) *c = ' ';
        snprintf(err, n, "the GPU refused the shader: %.400s", log);
        glDeleteShader(s);
        return 0;
    }
    return s;
}

// The program for `p`, with its inputs as uniforms or (constants) written in with `vals`.
static GLuint fx_program(const FxProg *p, const FxValue *vals, int constants, char *err, size_t n)
{
    Buf b;
    buf_init(&b);
    g(&b, "#version 300 es\nprecision highp float;\nprecision highp int;\nprecision highp sampler2D;\n"
          "in vec2 pvj_pos;\nout vec4 pvj_out;\n"
          "uniform float TIME;\nuniform float TIMEDELTA;\nuniform int FRAMEINDEX;\nuniform vec4 DATE;\nuniform vec2 RENDERSIZE;\n"
          "uniform int PASSINDEX;\nuniform int pvj_final;\n"
          "#define isf_FragNormCoord pvj_norm\n#define vv_FragNormCoord pvj_norm\n#define PVJ_HP highp\n"
          "vec2 pvj_norm;\nvec4 pvj_coord;\nvec4 pvj_color;\n");
    // the buffers, read as ISF's IMG_ calls on them (the panel writes them as pvj_t<i>_...); stored top row first,
    // as every texture here, and read at a place counted from the bottom left as ISF counts
    for (int i = 0; i < p->ntargets; i++)
        gf(&b, "uniform sampler2D pvj_t%d;\nuniform vec2 pvj_t%d_sz;\n"
               "vec4 pvj_t%d_this() { return texture(pvj_t%d, pvj_pos); }\n"
               "vec4 pvj_t%d_norm(vec2 q) { return texture(pvj_t%d, vec2(q.x, 1.0 - q.y)); }\n"
               "vec4 pvj_t%d_px(vec2 q) { return texture(pvj_t%d, vec2(q.x, pvj_t%d_sz.y - q.y) / pvj_t%d_sz); }\n"
               "vec2 pvj_t%d_size() { return pvj_t%d_sz; }\n", i, i, i, i, i, i, i, i, i, i, i, i);
    if (p->kind == FX_GENERATOR) {
        g(&b, "uniform mat3 pvj_hue;\nuniform float pvj_gain;\n");
    } else {
        // the picture, read at a place counted from the top left (0 to 1), as on the player's NATIVE stage; outside
        // it, its edge. The same five calls the panel's translation names (effects.py, _block).
        g(&b, "uniform sampler2D pvj_src;\nuniform float pvj_amount;\n"
              "vec4 pvj_at(vec2 p) { return vec4(texture(pvj_src, clamp(p, vec2(0.0), vec2(1.0))).rgb, 1.0); }\n"
              "vec4 pvj_img_this() { return pvj_at(pvj_pos); }\n"
              "vec4 pvj_img_norm(vec2 q) { return pvj_at(vec2(q.x, 1.0 - q.y)); }\n"
              // the picture's own size, not the pass's (a pass into a smaller buffer has its own RENDERSIZE): the
              // review of #170
              "uniform vec2 pvj_src_sz;\n"
              "vec4 pvj_img_px(vec2 q) { return pvj_at(vec2(q.x, pvj_src_sz.y - q.y) / pvj_src_sz); }\n"
              "vec2 pvj_img_size() { return pvj_src_sz; }\n");
    }
    for (int i = 0; i < p->ninputs; i++) input_decl(&b, &vals[i], constants);
    gf(&b, "#line %d\n", p->line);
    g(&b, p->code);
    g(&b, "\nvoid main() {\n"
          "    pvj_norm = vec2(pvj_pos.x, 1.0 - pvj_pos.y);\n"
          "    pvj_coord = vec4(pvj_norm * RENDERSIZE, 0.0, 1.0);\n"
          "    pvj_color = vec4(0.0, 0.0, 0.0, 1.0);\n"
          "    pvj_main();\n"
          // a pass into a buffer keeps what the code wrote, alpha and (in a float buffer) values outside 0 to 1
          "    if (pvj_final == 0) { pvj_out = pvj_color; return; }\n"
          "    vec3 c = clamp(pvj_color.rgb, 0.0, 1.0) * clamp(pvj_color.a, 0.0, 1.0);\n");
    if (p->kind == FX_GENERATOR)
        g(&b, "    c = clamp(pvj_hue * c, 0.0, 1.0);\n"
              "    c = clamp(c * pvj_gain, 0.0, 1.0);\n"
              // half a step of noise against bands in slow gradients, as the panel's translation has it
              "    c += (fract(pvj_coord.x * 0.6113 + pvj_coord.y * 0.3791) - 0.5) / 255.0;\n");
    else
        g(&b, "    c = mix(texture(pvj_src, pvj_pos).rgb, c, pvj_amount);\n");
    g(&b, "    pvj_out = vec4(c, 1.0);\n}\n");
    if (b.failed) { buf_free(&b); snprintf(err, n, "out of memory"); return 0; }
    GLuint vs = fx_compile(GL_VERTEX_SHADER, FX_VS, err, n);
    GLuint fs = vs ? fx_compile(GL_FRAGMENT_SHADER, b.p, err, n) : 0;
    buf_free(&b);
    if (!fs) { if (vs) glDeleteShader(vs); return 0; }
    GLuint prog = glCreateProgram();
    glAttachShader(prog, vs);
    glAttachShader(prog, fs);
    glBindAttribLocation(prog, 0, "pvj_vert");
    glLinkProgram(prog);
    glDeleteShader(vs);
    glDeleteShader(fs);
    GLint ok = 0;
    glGetProgramiv(prog, GL_LINK_STATUS, &ok);
    if (!ok) {
        char log[512] = "";
        glGetProgramInfoLog(prog, sizeof log, NULL, log);
        for (char *c = log; *c; c++) if ((unsigned char)*c < 32 || (unsigned char)*c > 126) *c = ' ';
        snprintf(err, n, "the GPU refused the shader: %.400s", log);
        glDeleteProgram(prog);
        return 0;
    }
    count_gl(1);
    return prog;
}

static void fx_locations(FxGL *f, const FxProg *p)
{
    f->u_time = glGetUniformLocation(f->prog, "TIME");
    f->u_delta = glGetUniformLocation(f->prog, "TIMEDELTA");
    f->u_frame = glGetUniformLocation(f->prog, "FRAMEINDEX");
    f->u_date = glGetUniformLocation(f->prog, "DATE");
    f->u_size = glGetUniformLocation(f->prog, "RENDERSIZE");
    f->u_hue = glGetUniformLocation(f->prog, "pvj_hue");
    f->u_gain = glGetUniformLocation(f->prog, "pvj_gain");
    f->u_src = glGetUniformLocation(f->prog, "pvj_src");
    f->u_amount = glGetUniformLocation(f->prog, "pvj_amount");
    f->u_pass = glGetUniformLocation(f->prog, "PASSINDEX");
    f->u_final = glGetUniformLocation(f->prog, "pvj_final");
    f->u_srcsz = glGetUniformLocation(f->prog, "pvj_src_sz");
    for (int i = 0; i < p->ntargets; i++) {
        char name[32];
        snprintf(name, sizeof name, "pvj_t%d", i);
        f->u_t[i] = glGetUniformLocation(f->prog, name);
        snprintf(name, sizeof name, "pvj_t%d_sz", i);
        f->u_tsz[i] = glGetUniformLocation(f->prog, name);
    }
    for (int i = 0; i < p->ninputs; i++) f->u_in[i] = f->constants ? -1 : glGetUniformLocation(f->prog, p->in[i].name);
}

static void fx_target(FxGL *f, int w, int h)
{
    if (f->tex && f->w == w && f->h == h) return;
    if (f->fbo) { glDeleteFramebuffers(1, &f->fbo); count_gl(-1); }
    if (f->tex) { glDeleteTextures(1, &f->tex); count_gl(-1); }
    f->w = w;
    f->h = h;
    glGenTextures(1, &f->tex);
    glBindTexture(GL_TEXTURE_2D, f->tex);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA8, w, h, 0, GL_RGBA, GL_UNSIGNED_BYTE, NULL);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    glGenFramebuffers(1, &f->fbo);
    glBindFramebuffer(GL_FRAMEBUFFER, f->fbo);
    glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, f->tex, 0);
    glClearColor(0, 0, 0, 1);
    glClear(GL_COLOR_BUFFER_BIT);
    count_gl(2);
}

// A program's buffers: freed, or made (cleared) at the sizes its file names, relative to the drawing's (base) size.
#define FX_ALL_BUFFER_BYTES (192.0 * 1048576)     // every program's buffers together, on the whole box
#define FX_RUN_AREA 4.0                              // a run's passes together: at most this many times the drawing's area
static double fx_buffer_total;          // bytes of every program's buffers now (render thread)

static void fx_targets_free(FxGL *f)
{
    for (int i = 0; i < f->ntargets; i++)
        for (int j = 0; j < 2; j++) {
            if (f->tfbo[i][j]) { glDeleteFramebuffers(1, &f->tfbo[i][j]); count_gl(-1); }
            if (f->ttex[i][j]) { glDeleteTextures(1, &f->ttex[i][j]); count_gl(-1); }
            f->tfbo[i][j] = f->ttex[i][j] = 0;
        }
    f->ntargets = 0;
    fx_buffer_total -= f->tbytes;
    f->tbytes = 0;
}

static int target_side(char k, float v, int bw, int bh)
{
    double s = k == 'p' ? v : (k == 'w' ? bw : bh) * (double)v;
    int n = (int)lround(s);
    return n < 1 ? 1 : n > 4096 ? 4096 : n;
}

// Two textures of one buffer, in half floats or 8 bits; 0 when the GPU can draw into them.
static int target_pair(FxGL *f, int i, int w, int h, int fl)
{
    for (int j = 0; j < 2; j++) {
        glGenTextures(1, &f->ttex[i][j]);
        glBindTexture(GL_TEXTURE_2D, f->ttex[i][j]);
        glTexImage2D(GL_TEXTURE_2D, 0, fl ? GL_RGBA16F : GL_RGBA8, w, h, 0, GL_RGBA, fl ? GL_HALF_FLOAT : GL_UNSIGNED_BYTE, NULL);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
        glGenFramebuffers(1, &f->tfbo[i][j]);
        glBindFramebuffer(GL_FRAMEBUFFER, f->tfbo[i][j]);
        glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, f->ttex[i][j], 0);
        count_gl(2);
        if (glCheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE) return -1;
        glClearColor(0, 0, 0, 0);
        glClear(GL_COLOR_BUFFER_BIT);
    }
    return 0;
}

static void target_pair_free(FxGL *f, int i)
{
    for (int j = 0; j < 2; j++) {
        if (f->tfbo[i][j]) { glDeleteFramebuffers(1, &f->tfbo[i][j]); count_gl(-1); }
        if (f->ttex[i][j]) { glDeleteTextures(1, &f->ttex[i][j]); count_gl(-1); }
        f->tfbo[i][j] = f->ttex[i][j] = 0;
    }
}

// `replacing`: the bytes of the buffers this program takes the place of (freed once it is built).
static int fx_targets_make(FxGL *f, const FxProg *p, int bw, int bh, double replacing, char *err, size_t n)
{
    if (float_targets < 0) {
        const char *ext = (const char *)glGetString(GL_EXTENSIONS);
        float_targets = ext && (strstr(ext, "GL_EXT_color_buffer_float") || strstr(ext, "GL_EXT_color_buffer_half_float"));
    }
    double bytes = 0, area = (double)bw * bh;
    for (int i = 0; i < p->ntargets; i++)
        bytes += 2.0 * target_side(p->tgt[i].wk, p->tgt[i].wv, bw, bh) * target_side(p->tgt[i].hk, p->tgt[i].hv, bw, bh)
                 * (p->tgt[i].isfloat && float_targets ? 8 : 4);
    if (bytes > FX_BUFFER_BYTES) { snprintf(err, n, "its buffers would take %.0f MB (at most %u)", bytes / 1048576.0, FX_BUFFER_BYTES >> 20); return -1; }
    // the box's whole budget (the review of #170): every slot's programs together, this one in place of its old one
    if (fx_buffer_total - replacing + bytes > FX_ALL_BUFFER_BYTES) {
        snprintf(err, n, "the buffers of every program on the box would take %.0f MB together (at most %.0f): take an effect or a shader off first",
                 (fx_buffer_total - replacing + bytes) / 1048576.0, FX_ALL_BUFFER_BYTES / 1048576.0);
        return -1;
    }
    // what one run draws: every pass's area together (a buffer of twice the width is four of the picture's)
    double run = 0;
    for (int k = 0; k < p->npasses; k++) {
        int t = p->pass[k];
        run += t < 0 ? area : (double)target_side(p->tgt[t].wk, p->tgt[t].wv, bw, bh) * target_side(p->tgt[t].hk, p->tgt[t].hv, bw, bh);
    }
    if (run > FX_RUN_AREA * area + 0.5) {
        snprintf(err, n, "its passes would draw %.1f times the picture's area every run (at most %.0f)", run / area, FX_RUN_AREA);
        return -1;
    }
    f->float_low = 0;
    for (int i = 0; i < p->ntargets; i++) {
        int w = target_side(p->tgt[i].wk, p->tgt[i].wv, bw, bh), h = target_side(p->tgt[i].hk, p->tgt[i].hv, bw, bh);
        int fl = p->tgt[i].isfloat && float_targets;
        if (p->tgt[i].isfloat && !float_targets) f->float_low = 1;
        f->tw[i] = w;
        f->th[i] = h;
        f->tcur[i] = 0;
        f->ntargets = i + 1;
        if (target_pair(f, i, w, h, fl)) {
            target_pair_free(f, i);
            // a half-float buffer this GPU cannot draw into after all: once more in 8 bits, and the state says so
            if (!fl || target_pair(f, i, w, h, 0)) {
                snprintf(err, n, "the GPU cannot draw into the buffer %s", p->tgt[i].name);
                fx_targets_free(f);
                return -1;
            }
            f->float_low = 1;
        }
    }
    f->tbytes = bytes;
    fx_buffer_total += bytes;
    return 0;
}

static void fx_off(FxGL *f)
{
    fx_targets_free(f);
    if (f->prog) { glDeleteProgram(f->prog); count_gl(-1); }
    if (f->fbo) { glDeleteFramebuffers(1, &f->fbo); count_gl(-1); }
    if (f->tex) { glDeleteTextures(1, &f->tex); count_gl(-1); }
    fx_prog_free(f->src);
    memset(f, 0, sizeof *f);
}

#ifdef PVJ_RENDER_TEST_HOOKS
// For the tests only, in a build made with `make TEST_HOOKS=1`: PVJ_RENDER_TEST_SLOW_BUILD=<ms> makes every build
// of a program take that much longer, as a slow GPU's compiler may (tests/test_native_shaders.py, SlowBuildTest).
static void slow_build(void)
{
    const char *v = getenv("PVJ_RENDER_TEST_SLOW_BUILD");
    long ms = v ? strtol(v, NULL, 10) : 0;
    if (ms <= 0 || ms > 10000) return;
    struct timespec t = { ms / 1000, (ms % 1000) * 1000000L };
    nanosleep(&t, NULL);
}
#endif

// Build `p` into slot `k` of source index `s` (render thread). On success the slot runs it from its own start (TIME
// from its offset); on failure the program that ran before runs on, untouched.
static int fx_build(int s, int k, FxProg *p, char *err, size_t n)
{
#ifdef PVJ_RENDER_TEST_HOOKS
    slow_build();
#endif
    char err2[sizeof ((FxSlot *)0)->answer] = "";
    int constants = 0;
    GLuint prog = fx_program(p, p->in, 0, err2, sizeof err2);
    if (!prog) {
        // The code cannot take its inputs as uniforms (a uniform in a constant expression, an array sized by one):
        // its values written in, as classic does. Tried only when the compiler said so, so a program with a plain
        // mistake is compiled once (the review of #167).
        if (!strstr(err2, "constant") && !strstr(err2, "array size")) { snprintf(err, n, "%s", err2); return -1; }
        prog = fx_program(p, p->in, 1, err, n);
        if (!prog) return -1;
        constants = 1;
        LOG("program in source %d slot %d: built with its values as constants (%s)", s + 1, k, err2);
    }
    FxGL *f = &gl[s][k];
    // the new program's buffers first, so that a refusal (too large, a format the GPU cannot draw into) leaves the
    // program before it running with its own
    FxGL fresh = { 0 };
    int bw = p->kind == FX_GENERATOR ? p->w : app.in_w, bh = p->kind == FX_GENERATOR ? p->h : app.in_h;
    if (fx_targets_make(&fresh, p, bw, bh, f->tbytes, err, n)) { glDeleteProgram(prog); count_gl(-1); return -1; }
    fx_targets_free(f);
    GLuint keep_tex = f->tex, keep_fbo = f->fbo;
    int keep_w = f->w, keep_h = f->h;
    if (f->prog) { glDeleteProgram(f->prog); count_gl(-1); }
    fx_prog_free(f->src);
    memset(f, 0, sizeof *f);
    f->tex = keep_tex;
    f->fbo = keep_fbo;
    f->w = keep_w;
    f->h = keep_h;
    f->ntargets = fresh.ntargets;
    f->float_low = fresh.float_low;
    f->tbytes = fresh.tbytes;
    memcpy(f->ttex, fresh.ttex, sizeof f->ttex);
    memcpy(f->tfbo, fresh.tfbo, sizeof f->tfbo);
    memcpy(f->tcur, fresh.tcur, sizeof f->tcur);
    memcpy(f->tw, fresh.tw, sizeof f->tw);
    memcpy(f->th, fresh.th, sizeof f->th);
    f->on = 1;
    f->kind = p->kind;
    f->clock = p->kind == FX_GENERATOR || p->clock;
    f->constants = constants;
    f->prog = prog;
    f->src = p;
    fx_locations(f, p);
    fx_target(f, p->kind == FX_GENERATOR ? p->w : app.in_w, p->kind == FX_GENERATOR ? p->h : app.in_h);
    f->time = p->ctl[FXC_OFFSET];
    f->last_build = now_s();
    f->dirty = 1;
    memcpy(f->val, p->in, sizeof f->val);
    memcpy(f->ctl, p->ctl, sizeof f->ctl);
    return 0;
}

static int source_on(int s)
{
    for (int k = 0; k < FX_SLOTS; k++)
        if (gl[s][k].on) return 1;
    return 0;
}

// The pending loads and clears of every slot, built now (render thread, once a frame).
void fx_take(void)
{
    for (int s = 0; s < app.nsources; s++)
        for (int k = 0; k < FX_SLOTS; k++) {
            pthread_mutex_lock(&app.lock);
            FxSlot *sl = &app.fx[s][k];
            FxProg *p = sl->pending;
            int clear = sl->pending_clear;
            long asked = sl->asked;
            int any = p || clear;
            sl->pending = NULL;
            sl->pending_clear = 0;
            pthread_mutex_unlock(&app.lock);
            if (!any) continue;
            char err[sizeof sl->answer] = "";
            int ok = 1;
            FxGL *f = &gl[s][k];
            if (clear) fx_off(f);
            else if (fx_build(s, k, p, err, sizeof err)) {
                ok = 0;
                fx_prog_free(p);
                LOG("program in source %d slot %d refused: %s", s + 1, k, err);
            }
            pthread_mutex_lock(&app.lock);
            if (ok) {
                sl->on = f->on;
                sl->constants = f->constants;
                if (sl->on) {
                    sl->kind = f->kind;
                    sl->ninputs = f->src->ninputs;
                    memcpy(sl->val, f->src->in, sizeof sl->val);
                    memcpy(sl->ctl, f->src->ctl, sizeof sl->ctl);
                    sl->values_serial++;
                    f->seen_values = sl->values_serial;
                    sl->reset_time = 0;
                    sl->runs = 0;
                    sl->drops = 0;
                    sl->rebuilds = 0;
                    sl->time = f->time;
                } else {
                    sl->ninputs = 0;
                }
                snprintf(sl->state, sizeof sl->state, "%s%s", sl->on ? (sl->constants ? "constants" : "on") : "off",
                         sl->on && f->float_low ? "; its FLOAT buffers are 8-bit on this GPU" : "");
                sl->answer[0] = 0;
            } else {
                snprintf(sl->answer, sizeof sl->answer, "%s", err);
            }
            sl->answered = asked;
            int was = app.native[s];
            app.native[s] = source_on(s);
            if (was != app.native[s]) app.level_resync = 1;     // whoever asks next brings the layers' level along
            sl->w = f->w;
            sl->h = f->h;
            if (ok) snprintf(sl->path, sizeof sl->path, "%s", f->on ? fx_prog_path(f->src) : "");
            pthread_cond_broadcast(&app.cond);
            pthread_mutex_unlock(&app.lock);
        }
}

static void hue_matrix(float degrees, float m[9])
{
    double a = degrees * M_PI / 180.0, c = cos(a), s = sin(a);
    double k = (1.0 - c) / 3.0, r = s / sqrt(3.0);
    // the panel's hue_matrix, row-major; GLSL takes columns, so it is passed transposed below
    double rows[9] = { c + k, k - r, k + r, k + r, c + k, k - r, k - r, k + r, c + k };
    for (int i = 0; i < 9; i++) m[i] = (float)rows[i];
}

static void fx_uniforms(FxGL *f, double dt)
{
    glUniform1f(f->u_time, (float)f->time);
    glUniform1f(f->u_delta, (float)dt);
    int frame = f->kind == FX_GENERATOR ? (int)(f->frames & 0x7fffffff) : (int)(f->time * app.fx_fps + 0.5);
    glUniform1i(f->u_frame, frame);
    time_t t = time(NULL);
    struct tm tm;
    localtime_r(&t, &tm);
    glUniform4f(f->u_date, (float)(tm.tm_year + 1900), (float)(tm.tm_mon + 1), (float)tm.tm_mday,
                (float)(tm.tm_hour * 3600 + tm.tm_min * 60 + tm.tm_sec));
    glUniform2f(f->u_size, (float)f->w, (float)f->h);
    if (f->kind == FX_GENERATOR) {
        float m[9];
        hue_matrix(f->ctl[FXC_HUE], m);
        glUniformMatrix3fv(f->u_hue, 1, GL_TRUE, m);
        glUniform1f(f->u_gain, f->ctl[FXC_GAIN]);
    } else {
        glUniform1i(f->u_src, 0);
        glUniform1f(f->u_amount, f->ctl[FXC_AMOUNT]);
        glUniform2f(f->u_srcsz, (float)f->w, (float)f->h);      // the picture it filters is at its own output size
    }
    for (int i = 0; !f->constants && i < f->src->ninputs; i++) {
        const FxValue *v = &f->val[i];
        GLint loc = f->u_in[i];
        if (loc < 0) continue;                  // an input the code never reads (the compiler took it out)
        switch (v->type) {
        case FXT_FLOAT: glUniform1f(loc, v->v[0]); break;
        case FXT_BOOL: case FXT_LONG: glUniform1i(loc, (int)v->v[0]); break;
        case FXT_COLOR: glUniform4f(loc, v->v[0], v->v[1], v->v[2], v->v[3]); break;
        default: glUniform2f(loc, v->v[0], v->v[1]); break;
        }
    }
}

// One run: every pass in order (D106 step c). A pass into a buffer draws into its other texture, which is then the
// one read; a buffer that is not persistent starts each run black.
static void fx_draw(FxGL *f, GLuint src, double dt)
{
    gl_reset();
    for (int i = 0; i < f->ntargets; i++)
        if (!f->src->tgt[i].persistent) {
            glBindFramebuffer(GL_FRAMEBUFFER, f->tfbo[i][f->tcur[i]]);
            glClearColor(0, 0, 0, 0);
            glClear(GL_COLOR_BUFFER_BIT);
        }
    glUseProgram(f->prog);
    fx_uniforms(f, dt);
    for (int k = 0; k < f->src->npasses; k++) {
        int t = f->src->pass[k];
        int w = t < 0 ? f->w : f->tw[t], h = t < 0 ? f->h : f->th[t];
        glBindFramebuffer(GL_FRAMEBUFFER, t < 0 ? f->fbo : f->tfbo[t][1 - f->tcur[t]]);
        glViewport(0, 0, w, h);
        if (src) {
            glActiveTexture(GL_TEXTURE0);
            glBindTexture(GL_TEXTURE_2D, src);
        }
        for (int i = 0; i < f->ntargets; i++) {
            glActiveTexture(GL_TEXTURE1 + i);
            glBindTexture(GL_TEXTURE_2D, f->ttex[i][f->tcur[i]]);
            glUniform1i(f->u_t[i], 1 + i);
            glUniform2f(f->u_tsz[i], (float)f->tw[i], (float)f->th[i]);
        }
        glUniform2f(f->u_size, (float)w, (float)h);
        glUniform1i(f->u_pass, k);
        glUniform1i(f->u_final, t < 0);
        fx_quad();
        if (t >= 0) f->tcur[t] = 1 - f->tcur[t];
    }
    for (int i = 0; i < f->ntargets; i++) {
        glActiveTexture(GL_TEXTURE1 + i);
        glBindTexture(GL_TEXTURE_2D, 0);
    }
    glActiveTexture(GL_TEXTURE0);
    glUseProgram(0);
    f->frames++;
    __atomic_add_fetch(&app.fx_passes, f->src->npasses, __ATOMIC_RELAXED);    // passes drawn (the property gives them a second)
}

// A program with constants whose values moved: built again with them (at most every FX_REBUILD_GAP).
static void fx_rebuild(int s, int k, double now)
{
    FxGL *f = &gl[s][k];
    if (now - f->last_build < FX_REBUILD_GAP) return;
    char err[300] = "";
    GLuint prog = fx_program(f->src, f->val, 1, err, sizeof err);
    f->last_build = now;
    if (!prog) {
        LOG("program in source %d slot %d: the new values were refused: %s", s + 1, k, err);
        pthread_mutex_lock(&app.lock);
        snprintf(app.fx[s][k].state, sizeof app.fx[s][k].state, "constants; the last values were refused");
        pthread_mutex_unlock(&app.lock);
        f->rebuild = 0;
        return;
    }
    glDeleteProgram(f->prog);
    count_gl(-1);
    f->prog = prog;
    f->rebuild = 0;
    f->dirty = 1;
    fx_locations(f, f->src);
    pthread_mutex_lock(&app.lock);
    app.fx[s][k].rebuilds++;
    snprintf(app.fx[s][k].state, sizeof app.fx[s][k].state, "constants%s", f->float_low ? "; its FLOAT buffers are 8-bit on this GPU" : "");
    pthread_mutex_unlock(&app.lock);
}

// One source's picture after its programs (render thread, once a frame): `s` is the source's index (0 is source 1),
// `picture` what its layers made (source 1: the front layer, or the crossfade's mix), `fresh` whether it is new this
// frame, `paused` whether its clip stands still. The generator (slot 0) replaces the picture; the effects (slots 1 to
// FX_SLOTS - 1) run in their order, each over what the one before left. Returns the texture to show.
GLuint fx_source(int s, GLuint picture, int fresh, int paused, double now)
{
    if (!source_on(s)) return picture;
    FxGL *slots = gl[s];
    for (int k = 0; k < FX_SLOTS; k++) {        // this frame's values, in one look per slot
        FxGL *f = &slots[k];
        if (!f->on) continue;
        pthread_mutex_lock(&app.lock);
        FxSlot *sl = &app.fx[s][k];
        long serial = sl->values_serial;
        int reset = sl->reset_time;
        sl->reset_time = 0;
        if (serial != f->seen_values) {
            memcpy(f->val, sl->val, sizeof f->val);
            memcpy(f->ctl, sl->ctl, sizeof f->ctl);
        }
        pthread_mutex_unlock(&app.lock);
        if (serial != f->seen_values) {
            f->seen_values = serial;
            f->dirty = 1;
            f->rebuild = f->constants;
        }
        if (reset) f->time = f->ctl[FXC_OFFSET];
        if (f->rebuild) fx_rebuild(s, k, now);
    }
    double period = 1.0 / app.fx_fps, slack = app.out_hz > 0 ? 0.25 / app.out_hz : 0.004;
    GLuint pic = picture;
    int new_pic = fresh;
    FxGL *gen = &slots[0];
    if (gen->on) {
        double dt = gen->last_frame > 0 ? now - gen->last_frame : 0;
        if (dt > FX_DT_MAX) dt = FX_DT_MAX;
        gen->last_frame = now;
        gen->time += dt * gen->ctl[FXC_SPEED];
        double since = now - gen->last_run;
        // a value that moved waits for the next run (the review of #167: at 48 sets a second it ran 48.5 times)
        if (gen->last_run <= 0 || since >= period - slack) {
            if (gen->last_run > 0 && since > 1.5 * period) {
                long lost = (long)(since / period + 0.5) - 1;
                pthread_mutex_lock(&app.lock);
                app.fx[s][0].drops += lost;
                if (s == 0) app.fx_drops += lost;
                pthread_mutex_unlock(&app.lock);
            }
            fx_draw(gen, 0, gen->last_run > 0 ? since * gen->ctl[FXC_SPEED] : 0);
            gen->last_run = now;
            gen->dirty = 0;
            new_pic = 1;
            pthread_mutex_lock(&app.lock);
            app.fx[s][0].runs++;
            app.fx[s][0].time = gen->time;
            pthread_mutex_unlock(&app.lock);
        }
        pic = gen->tex;
    }
    // over a clip that stands still, an effect's TIME stands still too (as it did on classic, counted from the clip's
    // frames); over a generator it runs with the clock
    int still = paused && !gen->on;
    for (int k = 1; k < FX_SLOTS; k++) {
        FxGL *eff = &slots[k];
        if (!eff->on) continue;
        double dt = eff->last_frame > 0 ? now - eff->last_frame : 0;
        if (dt > FX_DT_MAX) dt = FX_DT_MAX;
        eff->last_frame = now;
        if (!still) eff->time += dt * eff->ctl[FXC_SPEED];
        if (eff->ctl[FXC_AMOUNT] <= 0.0f) {
            eff->out = pic;                     // amount 0: the picture as it is, and nothing drawn
        } else {
            double since = now - eff->last_run;
            int slot_due = eff->last_run <= 0 || since >= period - slack;
            int due = eff->clock && slot_due && !still;
            // a new picture runs it at once (or another picture before it in the chain); a value that moved waits for
            // the next run at the cap (coalesced with the picture's when one comes first)
            if (new_pic || (eff->dirty && slot_due) || due || eff->out != eff->tex || eff->last_run <= 0 || eff->last_in != pic) {
                fx_draw(eff, pic, eff->last_run > 0 ? since * eff->ctl[FXC_SPEED] : 0);
                eff->last_in = pic;
                eff->last_run = now;
                eff->dirty = 0;
                eff->out = eff->tex;
                new_pic = 1;                    // the next effect in the chain reads a new picture
                pthread_mutex_lock(&app.lock);
                app.fx[s][k].runs++;
                app.fx[s][k].time = eff->time;
                pthread_mutex_unlock(&app.lock);
            }
        }
        pic = eff->out;
    }
    return pic;
}

int fx_has_generator(int s) { return gl[s][0].on; }

void fx_free_all(void)
{
    for (int s = 0; s < MAX_SOURCES; s++)
        for (int k = 0; k < FX_SLOTS; k++) fx_off(&gl[s][k]);
    if (fx_vbo) glDeleteBuffers(1, &fx_vbo);
    if (fx_vao) glDeleteVertexArrays(1, &fx_vao);
    fx_vbo = fx_vao = 0;
}
