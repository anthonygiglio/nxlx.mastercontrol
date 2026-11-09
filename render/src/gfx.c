// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: GPL-3.0-or-later
//
// The graphics stage (D129, slice 3 of design/text-and-logo-sources_2026-11-08.md): text and logos drawn by the GPU
// over the final picture, one textured quad each, with a fade, a typewriter, a scroll and a crawl worked out every frame.
//
// Where it sits in the frame. main.c's final pass draws the picture (the mapping's shader or the plain scale) and then,
// when an overlay or a text is on, the text layer (libmpv's on-screen layer: the masks, the pairing codes, the OSD).
// The graphics are drawn between the two (final_pass calls gfx_draw after the picture, before the text layer): after the
// mapping, so a surface never warps a graphic and it stays where it was put, and under the text layer, so the masks
// (overlay 30) and the pairing codes (40 to 43) stay on top of them, as on classic mpv. The screenshots and the
// captures use the same final pass, so they show the graphics too.
//
// What the panel sends (ipc.c, "pvj-graphic"): a picture as a premultiplied B G R A file of the rasteriser's, loaded
// into a slot; the slot's place (pixels of the screen), motion and times; show, hide, replay and clear. Everything the
// frame needs to move is worked out here from the monotonic clock and the slot's times, so a motion costs the socket
// nothing and the panel sends nothing while it runs.
//
// Threads. A socket thread (one per client, any number of them) reads the file and works out the lines' ink before it
// takes app.lock; under it, it only swaps pointers and numbers. The render thread takes the lock for one short look a frame
// (gfx_frame), and once more for a picture that was loaded. GL is the render thread's alone. The lock order of ipc.c
// (fx_lock, then layer_lock, then app.lock) is kept: nothing here takes another lock while it holds app.lock.
//
// Bounds. Eight slots; a picture's side is at most GFX_SIDE_MAX and the GPU's own limit; all pictures together (loaded and
// waiting) at most GFX_BYTES_MAX; at most two uploads a frame; a slot that is not on and not fading out is not drawn.
#define _GNU_SOURCE
#include "pvj.h"

#include <errno.h>
#include <fcntl.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#define UPLOADS_PER_FRAME 2
#define INK_ALPHA 12                    // a pixel with at least this alpha is ink when the lines' extents are measured
#define EDGE_SOFT 0.03f                 // the typewriter's soft edge, as a part of the picture's width (at least 6 pixels)

const char *const GFX_MOTIONS[GM_N] = { "none", "fade", "typewriter", "scroll", "crawl" };

long gfx_draws_total, gfx_uploads_total, gfx_gl_objects;

static GLuint prog;
static GLint u_rect, u_alpha, u_nl, u_reveal, u_soft, u_tex;
static GLuint texture[GFX_SLOTS];
static int side_max = 2048;

static const char *VS =
    "#version 300 es\n"
    "in vec2 pvj_vert;\n"
    "out highp vec2 pvj_uv;\n"
    "uniform vec4 rect;\n"                                        /* left, top, right, bottom in clip space */
    "void main() {\n"
    "  vec2 s = pvj_vert * 0.5 + 0.5;\n"
    "  pvj_uv = vec2(s.x, 1.0 - s.y);\n"                          /* the file's first row is the top */
    "  gl_Position = vec4(mix(rect.x, rect.z, s.x), mix(rect.w, rect.y, s.y), 0.0, 1.0);\n"
    "}\n";
// The picture is premultiplied. `reveal[i]` is how far along line i the typewriter has come, as a part of the picture's
// width (below 0: not begun; 2 and over: all of it); a picture with no typewriter has one line, shown all.
static const char *FS =
    "#version 300 es\n"
    "precision highp float;\n"
    "in vec2 pvj_uv;\n"
    "out vec4 pvj_out;\n"
    "uniform sampler2D tex;\n"
    "uniform float alpha;\n"
    "uniform int nl;\n"
    "uniform float reveal[6];\n"
    "uniform float soft;\n"
    "void main() {\n"
    "  int li = clamp(int(pvj_uv.y * float(nl)), 0, nl - 1);\n"
    "  float k = clamp((reveal[li] - pvj_uv.x) / soft, 0.0, 1.0);\n"
    "  pvj_out = texture(tex, pvj_uv) * (alpha * k);\n"
    "}\n";

int gfx_side_max(void) { return side_max; }

// What a slot is before anything is set on it, and again after a clear: still, opaque, a crawl at an eighth of the screen a second.
static void defaults(GfxSlot *s)
{
    s->motion = GM_NONE;
    s->loop = 0;
    s->on = 0;
    s->x = s->y = 0.0f;
    s->opacity = 1.0f;
    s->seconds = 1.0f;
    s->out = 0.0f;
    s->speed = 12.0f;
    s->shown_at = s->hidden_at = 0.0;
}

void gfx_init(void)
{
    GLint m = 0;
    glGetIntegerv(GL_MAX_TEXTURE_SIZE, &m);
    side_max = m < 1024 ? 1024 : m > GFX_SIDE_MAX ? GFX_SIDE_MAX : m;
#ifdef PVJ_RENDER_TEST_HOOKS
    const char *t = getenv("PVJ_RENDER_TEST_MAX_TEXTURE");     // tests: a small limit, to see a long crawl refused
    if (t && atoi(t) >= 64 && atoi(t) < side_max) side_max = atoi(t);
#endif
    for (int i = 0; i < GFX_SLOTS; i++) defaults(&app.gfx[i]);
    char err[300] = "";
    prog = gl_program(VS, FS, err, sizeof err);
    if (!prog) { LOG("graphics: %s (no graphics will be drawn)", err); return; }
    glUseProgram(prog);
    u_tex = glGetUniformLocation(prog, "tex");
    glUniform1i(u_tex, 0);
    u_rect = glGetUniformLocation(prog, "rect");
    u_alpha = glGetUniformLocation(prog, "alpha");
    u_nl = glGetUniformLocation(prog, "nl");
    u_reveal = glGetUniformLocation(prog, "reveal");
    u_soft = glGetUniformLocation(prog, "soft");
    glUseProgram(0);
}

/* ---- the socket's side ------------------------------------------------------------------------------------------ */
// The ink of each of `lines` equal bands of the picture, from its leftmost to its rightmost pixel with some alpha, as
// parts of the width (the typewriter wipes along it). A band with no ink gets 0, 0.
static void measure(const uint8_t *px, int w, int h, int lines, float ext[GFX_LINES][2])
{
    for (int i = 0; i < GFX_LINES; i++) ext[i][0] = ext[i][1] = 0.0f;
    for (int li = 0; li < lines; li++) {
        int y0 = (int)((long)h * li / lines), y1 = (int)((long)h * (li + 1) / lines), lo = w, hi = -1;
        for (int y = y0; y < y1; y++) {
            const uint8_t *row = px + (size_t)y * w * 4;
            for (int x = 0; x < lo; x++)
                if (row[x * 4 + 3] >= INK_ALPHA) { lo = x; break; }
            for (int x = w - 1; x > hi; x--)
                if (row[x * 4 + 3] >= INK_ALPHA) { hi = x; break; }
        }
        if (hi >= lo) { ext[li][0] = (float)lo / (float)w; ext[li][1] = (float)(hi + 1) / (float)w; }
    }
}

long gfx_slot_bytes(const GfxSlot *s)
{
    long want = (long)s->want_w * s->want_h * 4, held = s->loaded ? (long)s->w * s->h * 4 : 0;
    return want > held ? want : held;       // while a new picture waits the old texture is still on the GPU
}

static long gfx_bytes(void)       // under app.lock
{
    long total = 0;
    for (int i = 0; i < GFX_SLOTS; i++) total += gfx_slot_bytes(&app.gfx[i]);
    return total;
}

static int pending_flag;                // atomics: something is waiting for the render thread (a picture or a clear)
static int loaded_count;                // atomics: slots with a texture, so a frame with none does no work

// Read `path` (a regular file, never through a link) as w x h of B G R A. Answers NULL, or the words why not. The file is read
// and measured before the lock is taken; under it only the slot's pointer changes.
const char *gfx_load(int slot, const char *path, int w, int h, int lines)
{
    if (slot < 0 || slot >= GFX_SLOTS || !path || path[0] != '/' || strlen(path) > 4000 || lines < 1 || lines > GFX_LINES) return "invalid parameter";
    if (w < 1 || h < 1 || w > side_max || h > side_max)
        return "this picture is larger than this node can hold (its limit is on the renderer's graphics property)";
    size_t bytes = (size_t)w * (size_t)h * 4;
    if (bytes > GFX_BYTES_MAX) return "this picture needs more memory than all the graphics may use together";
    // a cheap look at the budget before any memory is taken or file read; asked again below, after the read
    pthread_mutex_lock(&app.lock);
    long room = (long)GFX_BYTES_MAX - (gfx_bytes() - gfx_slot_bytes(&app.gfx[slot]));
    pthread_mutex_unlock(&app.lock);
    if ((long)bytes > room) return "all the graphics together would use more memory than this node allows";
    int fd = open(path, O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC);
    if (fd < 0) return "invalid parameter";
    struct stat sb;
    if (fstat(fd, &sb) || !S_ISREG(sb.st_mode) || (double)sb.st_size < (double)bytes) { close(fd); return "invalid parameter"; }
    uint8_t *px = malloc(bytes);
    if (!px) { close(fd); return "the renderer is out of memory"; }
    size_t got = 0;
    while (got < bytes) {
        ssize_t r = read(fd, px + got, bytes - got);
        if (r < 0 && errno == EINTR) continue;
        if (r <= 0) break;
        got += (size_t)r;
    }
    close(fd);
    if (got != bytes) { free(px); return "invalid parameter"; }
    float ext[GFX_LINES][2];
    measure(px, w, h, lines, ext);
    pthread_mutex_lock(&app.lock);
    GfxSlot *s = &app.gfx[slot];
    long others = gfx_bytes() - gfx_slot_bytes(s);
    if (others + (long)bytes > (long)GFX_BYTES_MAX) {
        pthread_mutex_unlock(&app.lock);
        free(px);
        return "all the graphics together would use more memory than this node allows";
    }
    free(s->pending);                   // an older picture that was never taken
    s->pending = px;
    s->pw = w;
    s->ph = h;
    s->plines = lines;
    memcpy(s->pext, ext, sizeof ext);
    s->want_w = w;
    s->want_h = h;
    s->loads++;
    __atomic_store_n(&pending_flag, 1, __ATOMIC_RELEASE);
    pthread_mutex_unlock(&app.lock);
    wake_render();
    return NULL;
}

static double unit_(double v) { return v < 0 ? 0 : v > 1 ? 1 : v; }

void gfx_set(int slot, const GfxSet *g)
{
    if (slot < 0 || slot >= GFX_SLOTS) return;
    pthread_mutex_lock(&app.lock);
    GfxSlot *s = &app.gfx[slot];
    if (g->mask & GS_MOTION) {
        // a scroll or a crawl chosen on a graphic that is on starts now: measured from the old motion's clock it would be past its end
        if (g->motion != s->motion && (g->motion == GM_SCROLL || g->motion == GM_CRAWL) && s->on) s->shown_at = now_s();
        s->motion = g->motion;
    }
    if (g->mask & GS_LOOP) s->loop = g->loop;
    if (g->mask & GS_X) s->x = (float)g->x;
    if (g->mask & GS_Y) s->y = (float)g->y;
    if (g->mask & GS_OPACITY) s->opacity = (float)g->opacity;
    if (g->mask & GS_SECONDS) s->seconds = (float)g->seconds;
    if (g->mask & GS_OUT) s->out = (float)g->out;
    if (g->mask & GS_SPEED) s->speed = (float)g->speed;
    pthread_mutex_unlock(&app.lock);
    wake_render();
}

void gfx_show(int slot, int still)
{
    if (slot < 0 || slot >= GFX_SLOTS) return;
    pthread_mutex_lock(&app.lock);
    GfxSlot *s = &app.gfx[slot];
    if (!s->on) {
        double now = now_s(), level = 0.0;
        if (s->motion == GM_FADE && s->out > 0.0f && s->hidden_at > 0.0 && s->seconds > 0.0f) {
            // shown again while it fades out: the fade in begins at the level the fade out had reached
            double at_hide = unit_((s->hidden_at - s->shown_at) / s->seconds), k = 1.0 - (now - s->hidden_at) / s->out;
            level = k > 0.0 ? at_hide * k : 0.0;
        }
        s->on = 1;
        s->shown_at = still ? now - 2.0 * GFX_SECONDS_MAX : now - level * s->seconds;   // (still: long enough ago that a fade or typing is done)
        s->hidden_at = 0;
    }
    pthread_mutex_unlock(&app.lock);
    wake_render();
}

void gfx_replay(int slot)
{
    if (slot < 0 || slot >= GFX_SLOTS) return;
    pthread_mutex_lock(&app.lock);
    GfxSlot *s = &app.gfx[slot];
    s->on = 1;
    s->shown_at = now_s();
    s->hidden_at = 0;
    pthread_mutex_unlock(&app.lock);
    wake_render();
}

void gfx_hide(int slot)
{
    if (slot < 0 || slot >= GFX_SLOTS) return;
    pthread_mutex_lock(&app.lock);
    GfxSlot *s = &app.gfx[slot];
    if (s->on) {
        s->on = 0;
        s->hidden_at = now_s();
    }
    pthread_mutex_unlock(&app.lock);
    wake_render();
}

void gfx_clear(int slot)
{
    if (slot < 0 || slot >= GFX_SLOTS) return;
    pthread_mutex_lock(&app.lock);
    GfxSlot *s = &app.gfx[slot];
    free(s->pending);
    s->pending = NULL;
    s->want_w = s->want_h = 0;
    s->clear = 1;
    __atomic_store_n(&pending_flag, 1, __ATOMIC_RELEASE);
    defaults(s);
    pthread_mutex_unlock(&app.lock);
    wake_render();
}

/* ---- the render thread ------------------------------------------------------------------------------------------ */
typedef struct { int draw; float rect[4]; float alpha, soft; int nl; float reveal[GFX_LINES]; } Quad;
static Quad frame_quad[GFX_SLOTS];
static int frame_any;

static void free_texture(int i)
{
    if (texture[i]) {
        glDeleteTextures(1, &texture[i]);
        texture[i] = 0;
        __atomic_sub_fetch(&gfx_gl_objects, 1, __ATOMIC_RELAXED);
    }
}

void gfx_take(void)
{
    if (!__atomic_load_n(&pending_flag, __ATOMIC_ACQUIRE)) return;      // the usual frame: nothing waits, no lock taken
    int uploads = 0;
    for (int i = 0; i < GFX_SLOTS; i++) {
        uint8_t *px = NULL;
        int w = 0, h = 0, lines = 1, do_clear = 0;
        float ext[GFX_LINES][2];
        pthread_mutex_lock(&app.lock);
        GfxSlot *s = &app.gfx[i];
        if (s->clear) {                 // (a load that came after the clear is taken next frame)
            do_clear = 1;
            s->clear = 0;
        } else if (s->pending && uploads < UPLOADS_PER_FRAME) {
            px = s->pending;
            w = s->pw;
            h = s->ph;
            lines = s->plines;
            memcpy(ext, s->pext, sizeof ext);
            s->pending = NULL;
        }
        pthread_mutex_unlock(&app.lock);
        if (do_clear) {
            free_texture(i);
            pthread_mutex_lock(&app.lock);
            if (s->loaded) __atomic_sub_fetch(&loaded_count, 1, __ATOMIC_RELAXED);
            s->loaded = 0;
            s->alpha = 0.0f;
            pthread_mutex_unlock(&app.lock);
        }
        if (!px) continue;
        uploads++;
        if (!texture[i]) {
            glGenTextures(1, &texture[i]);
            __atomic_add_fetch(&gfx_gl_objects, 1, __ATOMIC_RELAXED);
        }
        glActiveTexture(GL_TEXTURE0);
        glBindTexture(GL_TEXTURE_2D, texture[i]);
        glPixelStorei(GL_UNPACK_ALIGNMENT, 4);
        // the file is B G R A; the swizzle reads it as R G B A, so nothing is turned about on the CPU
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_SWIZZLE_R, GL_BLUE);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_SWIZZLE_B, GL_RED);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
        while (glGetError() != GL_NO_ERROR) {}
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA8, w, h, 0, GL_RGBA, GL_UNSIGNED_BYTE, px);
        int bad = glGetError() != GL_NO_ERROR;
        free(px);
        glBindTexture(GL_TEXTURE_2D, 0);
        pthread_mutex_lock(&app.lock);
        if (bad) {
            LOG("graphics: the GPU did not take a %dx%d picture", w, h);
            if (s->loaded) __atomic_sub_fetch(&loaded_count, 1, __ATOMIC_RELAXED);
            s->loaded = 0;
            if (!s->pending) s->want_w = s->want_h = 0;     // nothing newer waits: the refused picture holds no memory
        } else {
            if (!s->loaded) __atomic_add_fetch(&loaded_count, 1, __ATOMIC_RELAXED);
            s->loaded = 1;
            s->w = w;
            s->h = h;
            s->lines = lines;
            memcpy(s->ext, ext, sizeof ext);
            __atomic_add_fetch(&gfx_uploads_total, 1, __ATOMIC_RELAXED);
        }
        pthread_mutex_unlock(&app.lock);
        if (bad) free_texture(i);
    }
    int more = 0;
    pthread_mutex_lock(&app.lock);
    for (int i = 0; i < GFX_SLOTS; i++) more |= app.gfx[i].pending != NULL || app.gfx[i].clear;
    __atomic_store_n(&pending_flag, more, __ATOMIC_RELEASE);        // (under the lock a load sets it under: none is lost)
    pthread_mutex_unlock(&app.lock);
}

static double unit(double v) { return v < 0 ? 0 : v > 1 ? 1 : v; }

// This frame's look at every slot, once, under one hold of the lock: the quads gfx_draw draws (it may be called more than
// once in a frame, for a capture or a screenshot, and draws the same picture).
void gfx_frame(double now)
{
    frame_any = 0;
    if (!__atomic_load_n(&loaded_count, __ATOMIC_ACQUIRE)) return;      // nothing is loaded: nothing to look at
    const float ow = (float)app.out_w, oh = (float)app.out_h;
    pthread_mutex_lock(&app.lock);
    for (int i = 0; i < GFX_SLOTS; i++) {
        GfxSlot *s = &app.gfx[i];
        Quad *q = &frame_quad[i];
        q->draw = 0;
        s->alpha = 0.0f;
        if (!s->loaded || !texture[i]) continue;
        double t = now - s->shown_at, a = 1.0;
        int fade = s->motion == GM_FADE;
        if (fade && s->seconds > 0.0f) a = unit(t / s->seconds);
        if (!s->on) {
            // off: only a fade out keeps it on the screen, from the level it had when it was hidden
            if (!(fade && s->out > 0.0f && s->hidden_at > 0.0)) continue;
            double at_hide = s->seconds > 0.0f ? unit((s->hidden_at - s->shown_at) / s->seconds) : 1.0;
            double k = 1.0 - (now - s->hidden_at) / s->out;
            if (k <= 0.0) continue;
            a = at_hide * k;
        }
        a *= s->opacity;
        if (a <= 0.0005) continue;
        const float w = (float)s->w, h = (float)s->h;
        float x = s->x, y = s->y;
        int moving = 0;
        if (s->motion == GM_SCROLL || s->motion == GM_CRAWL) {
            const int scroll = s->motion == GM_SCROLL;
            const double span = scroll ? oh : ow, v = (double)s->speed / 100.0 * span;     // pixels a second
            const double dist = span + (scroll ? h : w), dur = v > 0.0 ? dist / v : 1e9;
            double tt = t;
            if (tt < 0) tt = 0;
            if (tt >= dur) {
                if (!s->loop || !s->on) continue;       // it has run off the screen (and is done until it is replayed)
                tt = fmod(tt, dur);
            }
            if (scroll) y = (float)(span - tt * v);
            else x = (float)(span - tt * v);
            moving = 1;
        }
        if (!moving) { x = roundf(x); y = roundf(y); }
        if (x >= ow || y >= oh || x + w <= 0 || y + h <= 0) continue;
        q->rect[0] = 2.0f * x / ow - 1.0f;
        q->rect[1] = 1.0f - 2.0f * y / oh;
        q->rect[2] = 2.0f * (x + w) / ow - 1.0f;
        q->rect[3] = 1.0f - 2.0f * (y + h) / oh;
        q->alpha = (float)a;
        q->nl = 1;
        q->soft = EDGE_SOFT;
        q->reveal[0] = 2.0f;
        if (s->motion == GM_TYPE && s->seconds > 0.0f && s->lines >= 1) {
            // line after line, at one pace along the ink: the lines' widths share the time
            q->nl = s->lines;
            double total = 0.0;
            for (int li = 0; li < s->lines; li++) total += s->ext[li][1] - s->ext[li][0];
            double left = total > 0.0 ? unit(t / s->seconds) * total : 0.0;
            const float soft = q->soft = EDGE_SOFT > 6.0f / w ? EDGE_SOFT : 6.0f / w;
            for (int li = 0; li < s->lines; li++) {
                double wd = s->ext[li][1] - s->ext[li][0];
                if (wd <= 0.0) { q->reveal[li] = 2.0f; continue; }
                if (left >= wd) { q->reveal[li] = 2.0f; left -= wd; }
                else if (left > 0.0) { q->reveal[li] = (float)(s->ext[li][0] + left / wd * (wd + soft)); left = 0.0; }
                else q->reveal[li] = -1.0f;
            }
        }
        q->draw = 1;
        s->alpha = q->alpha;
        s->draws++;
        frame_any = 1;
    }
    pthread_mutex_unlock(&app.lock);
}

void gfx_draw(void)
{
    if (!frame_any || !prog) return;
    glActiveTexture(GL_TEXTURE0);
    glUseProgram(prog);
    glEnable(GL_BLEND);
    glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA);        // the picture is premultiplied
    for (int i = 0; i < GFX_SLOTS; i++) {
        const Quad *q = &frame_quad[i];
        if (!q->draw || !texture[i]) continue;
        glBindTexture(GL_TEXTURE_2D, texture[i]);
        glUniform4f(u_rect, q->rect[0], q->rect[1], q->rect[2], q->rect[3]);
        glUniform1f(u_alpha, q->alpha);
        glUniform1i(u_nl, q->nl);
        glUniform1fv(u_reveal, GFX_LINES, q->reveal);
        glUniform1f(u_soft, q->soft);
        gl_quad();
        __atomic_add_fetch(&gfx_draws_total, 1, __ATOMIC_RELAXED);
    }
    glDisable(GL_BLEND);
}

void gfx_free_all(void)
{
    for (int i = 0; i < GFX_SLOTS; i++) {
        free_texture(i);
        free(app.gfx[i].pending);
        app.gfx[i].pending = NULL;
    }
    if (prog) glDeleteProgram(prog);
    prog = 0;
}
