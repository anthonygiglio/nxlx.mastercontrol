// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: GPL-3.0-or-later
//
// pvj-render (project-log/DECISIONS.md, D95 and D98): the box's own renderer behind the socket the one mpv had.
//
//   two video layers  two libmpv cores (render API), each drawing its clip, with its brightness, zoom, pan,
//                     rotation, flips and its user shaders (effects, generators), into a texture at the internal
//                     size. One is on the screen (the front: the socket speaks to it); the other is idle and costs
//                     nothing, except while a crossfade runs: then it holds the outgoing clip (phase 2, D98). These
//                     two are source 1
//   sources 2 to N    one libmpv core each (D101), playing a clip of its own, muted, into a texture of its own at
//                     the internal size; idle until something is loaded into it, and then it costs nothing. The
//                     mapping's final pass reads them by name (SOURCEk); without a mapping only source 1 is seen
//   the compositor    while a crossfade runs, mixes the two textures by real opacity at the internal size, once a
//                     vblank, and hands the mix to the final pass; otherwise there is no such pass
//   the final pass    scales the picture to the screen; with a mapping it is the mapping's own OUTPUT shader
//   the text layer    a second libmpv that plays nothing draws overlays and show-text at the screen's size, laid
//                     over the final picture while something is on it
//
// Usage: pvj-render --socket=PATH [--drm-device=/dev/dri/cardN] [--drm-connector=NAME] [--drm-mode=WxH@R|N]
//                   [--detail=LINES|output] [--hwdec=NAME] [--audio-device=NAME] [--fast] [--every-vblank]
//                   [--sources=N] [--headless=WxH] [--frames=N] [--verbose]
#define _GNU_SOURCE
#include "pvj.h"
#include "json.h"

#include <errno.h>
#include <fcntl.h>
#include <math.h>
#include <signal.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include <EGL/egl.h>
#include <mpv/render_gl.h>

App app = { .lock = PTHREAD_MUTEX_INITIALIZER, .cond = PTHREAD_COND_INITIALIZER, .wake_fd = { -1, -1 } };
const char *const BLEND_NAMES[BLENDS] = { "normal" };
static volatile sig_atomic_t g_signal;

double now_s(void)
{
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (double)t.tv_sec + t.tv_nsec * 1e-9;
}

void logf_(const char *fmt, ...)
{
    char line[600];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(line, sizeof line, fmt, ap);
    va_end(ap);
    fprintf(stderr, "pvj-render: %s\n", line);
}

void wake_render(void)
{
    if (app.wake_fd[1] >= 0) {
        char c = 1;
        ssize_t r = write(app.wake_fd[1], &c, 1);
        (void)r;
    }
}

static void on_signal(int s)
{
    g_signal = s;
    wake_render();
}

/* ---- options ------------------------------------------------------------------------------------------------------- */
static int opt(const char *a, const char *name, const char **val)
{
    size_t n = strlen(name);
    if (strncmp(a, name, n) || a[n] != '=') return 0;
    *val = a + n + 1;
    return 1;
}

static int parse_args(int argc, char **argv, Cfg *c)
{
    *c = (Cfg){ .hwdec = "no", .detail = 0, .sources = 1 };
    for (int i = 1; i < argc; i++) {
        const char *a = argv[i], *v;
        if (opt(a, "--socket", &v)) c->socket_path = v;
        else if (opt(a, "--drm-device", &v)) c->drm_device = v;
        else if (opt(a, "--drm-connector", &v)) c->drm_connector = v;
        else if (opt(a, "--drm-mode", &v)) c->drm_mode = v;
        else if (opt(a, "--detail", &v)) {
            if (!strcmp(v, "output")) c->detail = 0;
            else {
                char *e;
                long d = strtol(v, &e, 10);
                if (*e || d < 16 || d > 4320) { LOG("--detail is a number of lines from 16 to 4320, or output"); return -1; }
                c->detail = (int)d;
            }
        } else if (opt(a, "--hwdec", &v)) {
            for (const char *p = v; *p; p++)
                if (!((*p >= 'a' && *p <= 'z') || (*p >= '0' && *p <= '9') || *p == '-' || *p == ',')) { LOG("--hwdec: not a name"); return -1; }
            c->hwdec = v;
        } else if (opt(a, "--audio-device", &v)) c->audio_device = v;
        else if (opt(a, "--headless", &v)) {
            if (sscanf(v, "%dx%d", &c->headless_w, &c->headless_h) != 2 || c->headless_w < 16 || c->headless_h < 16
                || c->headless_w > 7680 || c->headless_h > 4320) { LOG("--headless=WxH"); return -1; }
        } else if (opt(a, "--sources", &v)) {
            char *e;
            long k = strtol(v, &e, 10);
            if (*e || k < 1 || k > MAX_SOURCES) { LOG("--sources is a number from 1 to %d", MAX_SOURCES); return -1; }
            c->sources = (int)k;
        } else if (opt(a, "--frames", &v)) c->frames = atoi(v);
        else if (!strcmp(a, "--fast")) c->fast = 1;
        else if (!strcmp(a, "--every-vblank")) c->every_vblank = 1;
        else if (!strcmp(a, "--verbose")) c->verbose = 1;
        else if (!strcmp(a, "--version")) { printf("pvj-render %s\n", PVJ_VERSION); exit(0); }
        else { LOG("unknown argument: %s", a); return -1; }
    }
    if (!c->socket_path) { LOG("--socket=PATH is needed"); return -1; }
    return 0;
}

/* ---- the libmpv instances ------------------------------------------------------------------------------------------- */
typedef struct {
    mpv_handle *h;
    mpv_render_context *rc;
    GLuint tex, fbo;
    int w, h_;
    int dirty;
    pthread_t events;
    int have_events;
    int index;                  // 0 or 1 for source 1's layers, k for source k (D101), -1 for the text layer
    int changed, seen;          // its end of a file and change of its output (event thread), and the count drawn for
} Layer;

static Layer video[MAX_LAYERS], text;

// libmpv's GL fences. mpv 0.40 and 0.41 make a fence at the end of every mpv_render_context_render
// (ra_gl_ctx_submit_frame, video/out/opengl/context.c) for a swap that never comes with the render API, so nothing
// ever deletes it (mpv's own fix: f74adc4, "require swap_buffers param for FenceSync", after 0.41). On the Pi's V3D
// every GL fence holds a sync file: about 1000 frames in, the process had no file descriptor left, every fence after
// failed with Mesa's "Export failed" (v3d_pipe_flush, src/gallium/drivers/v3d/v3d_context.c), and the socket could
// take no more clients (2026-10-18). mpv asks this program for its GL functions, so glFenceSync and glDeleteSync are
// passed through two counting wrappers, and after each render the fence mpv made last in it is deleted here: that
// is the frame's fence, since done_frame is the last thing mpv does in a render. mpv's other fences (a texture upload
// from a mapped buffer, video/out/opengl/ra_gl.c) are always made right after a glDeleteSync of the one before and
// are mpv's to delete; a fence made right after a delete is never taken, so an mpv that no longer leaks loses
// nothing either. Render thread only.
typedef GLsync (GL_APIENTRYP FenceSyncFn)(GLenum, GLbitfield);
typedef void (GL_APIENTRYP DeleteSyncFn)(GLsync);
static FenceSyncFn real_fence_sync;
static DeleteSyncFn real_delete_sync;
static GLsync render_last_fence;        // the last fence made during the render in progress
static int render_last_after_delete;    // ... and whether it came right after a glDeleteSync
static int last_call_delete;

static GLsync GL_APIENTRY count_fence_sync(GLenum cond, GLbitfield flags)
{
    GLsync s = real_fence_sync(cond, flags);
    if (s) {
        app.fences++;
        render_last_fence = s;
        render_last_after_delete = last_call_delete;
    }
    last_call_delete = 0;
    return s;
}

static void GL_APIENTRY count_delete_sync(GLsync s)
{
    if (s) app.fences--;
    if (s && s == render_last_fence) render_last_fence = NULL;
    last_call_delete = 1;
    real_delete_sync(s);
}

// libmpv's GL objects, counted the same way (phase 2, D98): a crossfade loads a clip into a core that was idle and
// stops the other one, again and again in a show, and whatever libmpv makes for a clip and does not give back would
// pile up. `gl-objects` in the pvj-render property is the number it holds now; a test runs many fades and looks.
typedef void (GL_APIENTRYP GenFn)(GLsizei, GLuint *);
typedef void (GL_APIENTRYP DelFn)(GLsizei, const GLuint *);
static GenFn real_gen[4];
static DelFn real_del[4];
static const char *const GEN_NAMES[4] = { "glGenTextures", "glGenFramebuffers", "glGenRenderbuffers", "glGenBuffers" };
static const char *const DEL_NAMES[4] = { "glDeleteTextures", "glDeleteFramebuffers", "glDeleteRenderbuffers", "glDeleteBuffers" };

static void count_gen(int k, GLsizei n, GLuint *names)
{
    real_gen[k](n, names);
    long made = 0;
    for (GLsizei i = 0; i < n; i++) made += names[i] != 0;
    __atomic_add_fetch(&app.gl_objects, made, __ATOMIC_RELAXED);
}
static void count_del(int k, GLsizei n, const GLuint *names)
{
    long gone = 0;
    for (GLsizei i = 0; names && i < n; i++) gone += names[i] != 0;
    __atomic_sub_fetch(&app.gl_objects, gone, __ATOMIC_RELAXED);
    real_del[k](n, names);
}
static void GL_APIENTRY gen0(GLsizei n, GLuint *p) { count_gen(0, n, p); }
static void GL_APIENTRY gen1(GLsizei n, GLuint *p) { count_gen(1, n, p); }
static void GL_APIENTRY gen2(GLsizei n, GLuint *p) { count_gen(2, n, p); }
static void GL_APIENTRY gen3(GLsizei n, GLuint *p) { count_gen(3, n, p); }
static void GL_APIENTRY del0(GLsizei n, const GLuint *p) { count_del(0, n, p); }
static void GL_APIENTRY del1(GLsizei n, const GLuint *p) { count_del(1, n, p); }
static void GL_APIENTRY del2(GLsizei n, const GLuint *p) { count_del(2, n, p); }
static void GL_APIENTRY del3(GLsizei n, const GLuint *p) { count_del(3, n, p); }
static const GenFn GEN_WRAP[4] = { gen0, gen1, gen2, gen3 };
static const DelFn DEL_WRAP[4] = { del0, del1, del2, del3 };

static void *get_proc(void *ctx, const char *name)
{
    (void)ctx;
    void *p = (void *)eglGetProcAddress(name);
    if (p && !strcmp(name, "glFenceSync")) { real_fence_sync = (FenceSyncFn)p; return (void *)count_fence_sync; }
    if (p && !strcmp(name, "glDeleteSync")) { real_delete_sync = (DeleteSyncFn)p; return (void *)count_delete_sync; }
    for (int k = 0; p && k < 4; k++) {
        if (!strcmp(name, GEN_NAMES[k])) { real_gen[k] = (GenFn)p; return (void *)GEN_WRAP[k]; }
        if (!strcmp(name, DEL_NAMES[k])) { real_del[k] = (DelFn)p; return (void *)DEL_WRAP[k]; }
    }
    return p;
}
static void on_update(void *ctx) { (void)ctx; wake_render(); }

static volatile int events_stop;

static void *event_loop(void *arg)
{
    Layer *l = arg;
    mpv_handle *h = l->h;
    int i = l->index;
    while (!events_stop) {
        mpv_event *ev = mpv_wait_event(h, 0.5);
        if (ev->event_id == MPV_EVENT_SHUTDOWN) break;
        if (i >= 0) {
            if (ev->event_id == MPV_EVENT_VIDEO_RECONFIG || ev->event_id == MPV_EVENT_END_FILE || ev->event_id == MPV_EVENT_IDLE) {
                // libmpv asks for no new frame when its video ends (a Stop): the layer is drawn once more, which gives
                // its empty picture. Before, the last picture stayed unless a late redraw of mpv's happened to come after.
                __atomic_add_fetch(&l->changed, 1, __ATOMIC_RELEASE);
                wake_render();
            }
            if (ev->event_id == MPV_EVENT_START_FILE) __atomic_add_fetch(&app.starts[i], 1, __ATOMIC_RELEASE);
            if (ev->event_id == MPV_EVENT_PROPERTY_CHANGE) {
                mpv_event_property *p = ev->data;
                if (p && p->format == MPV_FORMAT_FLAG && p->data && !strcmp(p->name, "idle-active"))
                    __atomic_store_n(&app.idle[i], *(int *)p->data, __ATOMIC_RELEASE);
            }
            if (ev->event_id == MPV_EVENT_PLAYBACK_RESTART) {
                __atomic_store_n(&app.restart_of[i], __atomic_load_n(&app.starts[i], __ATOMIC_ACQUIRE), __ATOMIC_RELEASE);
                wake_render();
            }
            if (ev->event_id == MPV_EVENT_END_FILE && ev->data
                && ((mpv_event_end_file *)ev->data)->reason == MPV_END_FILE_REASON_ERROR) {
                __atomic_store_n(&app.failed_of[i], __atomic_load_n(&app.starts[i], __ATOMIC_ACQUIRE), __ATOMIC_RELEASE);
                wake_render();
            }
        }
        if (ev->event_id == MPV_EVENT_LOG_MESSAGE) {
            mpv_event_log_message *m = ev->data;
            size_t n = strlen(m->text);
            char who[24];
            if (i < 0) snprintf(who, sizeof who, "text");
            else if (i < LAYERS) snprintf(who, sizeof who, "video %d", i);
            else snprintf(who, sizeof who, "source %d", i);
            LOG("%s %s: %.*s", who, m->prefix, (int)(n && m->text[n - 1] == '\n' ? n - 1 : n), m->text);
        }
    }
    return NULL;
}

static int set(mpv_handle *h, const char *k, const char *v)
{
    int r = mpv_set_option_string(h, k, v);
    if (r < 0) LOG("mpv option %s=%s: %s", k, v, mpv_error_string(r));
    return r;
}

// A hardening option (nothing a file can point elsewhere) that mpv rejects is fatal at start: running without it
// would be running with the media able to name other files, scripts or programs.
static int hard_failed;
static void hard(mpv_handle *h, const char *k, const char *v)
{
    if (set(h, k, v) < 0) { hard_failed = 1; LOG("fatal: the option %s is needed and mpv does not take it", k); }
}

static int target(Layer *l, int w, int h)
{
    l->w = w;
    l->h_ = h;
    glGenTextures(1, &l->tex);
    glBindTexture(GL_TEXTURE_2D, l->tex);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA8, w, h, 0, GL_RGBA, GL_UNSIGNED_BYTE, NULL);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    glGenFramebuffers(1, &l->fbo);
    glBindFramebuffer(GL_FRAMEBUFFER, l->fbo);
    glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, l->tex, 0);
    if (glCheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE) return -1;
    glClearColor(0, 0, 0, 0);
    glClear(GL_COLOR_BUFFER_BIT);
    return 0;
}

// For the tests only: PVJ_RENDER_TEST_SLOW_START=<ms> waits that long after the text core's mpv_initialize, as a
// loaded machine may, so that the core is surely idle before anything else is done to it (tests/test_pvj_render.py).
static void slow_start(void)
{
    const char *v = getenv("PVJ_RENDER_TEST_SLOW_START");
    long ms = v ? strtol(v, NULL, 10) : 0;
    if (ms <= 0 || ms > 5000) return;
    struct timespec t = { ms / 1000, (ms % 1000) * 1000000L };
    nanosleep(&t, NULL);
}

static int layer_render_context(Layer *l)
{
    int adv = 0;
    mpv_opengl_init_params gl = { .get_proc_address = get_proc };
    mpv_render_param params[] = {
        { MPV_RENDER_PARAM_API_TYPE, (void *)MPV_RENDER_API_TYPE_OPENGL },
        { MPV_RENDER_PARAM_OPENGL_INIT_PARAMS, &gl },
        { MPV_RENDER_PARAM_ADVANCED_CONTROL, &adv },
        { 0 } };
    if (mpv_render_context_create(&l->rc, l->h, params) < 0) return -1;
    mpv_render_context_set_update_callback(l->rc, on_update, NULL);
    return 0;
}

static int layer_open(Layer *l, int index, const Cfg *c, int w, int h)
{
    int is_text = index < 0;
    l->index = index;
    l->h = mpv_create();
    if (!l->h) return -1;
    // as classic mpv is started (pvj/player.py, mpv_command): media only, nothing a file can point elsewhere
    hard(l->h, "config", "no");
    set(l->h, "vo", "libmpv");
    set(l->h, "idle", "yes");
    // A video core's force-window is turned on only once its render context exists (below): set before, the core
    // tried to make its window before there was one ("No render context set") and raced the render context's
    // creation. The text core is different (below).
    set(l->h, "force-window", "no");
    set(l->h, "keep-open", "no");
    set(l->h, "osd-level", "0");
    set(l->h, "terminal", "no");
    hard(l->h, "input-default-bindings", "no");
    hard(l->h, "input-vo-keyboard", "no");
    hard(l->h, "load-unsafe-playlists", "no");
    hard(l->h, "access-references", "no");
    hard(l->h, "sub-auto", "no");
    hard(l->h, "audio-file-auto", "no");
    hard(l->h, "ytdl", "no");
    hard(l->h, "load-scripts", "no");
    hard(l->h, "load-auto-profiles", "no");
    if (hard_failed) return -1;
    if (is_text) {
        // plays nothing: only its on-screen layer, over a clear background
        set(l->h, "ao", "null");
        set(l->h, "aid", "no");
        // a clear background: mpv 0.38 on calls the colour background-color; 0.37 (Ubuntu 24.04) calls it background
        if (mpv_set_option_string(l->h, "background-color", "#00000000") < 0) set(l->h, "background", "#00000000");
        set(l->h, "osd-bar", "no");
        // The text core plays nothing, so its window (and with it the on-screen layer) is made only when the core
        // first goes idle, a moment after mpv_initialize; mpv before 0.41 (the Pi's 0.40, Ubuntu 24.04's 0.37) never
        // acts on a force-window turned on later while idle. Turned on after the render context, the text layer
        // worked only when the core happened to go idle after that: in about 1 start in 30 it had no window at all
        // and drew no text and no overlay for the life of the process (CI, 2026-10-21). So here the render context
        // is made first and the window is wanted from the start.
        set(l->h, "force-window", "yes");
        if (layer_render_context(l)) return -1;
    } else {
        set(l->h, "hwdec", c->hwdec);
        if (c->fast) set(l->h, "profile", "fast");
        if (index >= LAYERS) {
            // sources 2 to N have no sound (D101): no track is chosen, so no sound output is ever opened, and the
            // socket may not set aid on them
            set(l->h, "aid", "no");
            set(l->h, "ao", "null");
        } else if (c->audio_device) {
            set(l->h, "audio-device", c->audio_device);
        }
    }
    if (mpv_initialize(l->h) < 0) return -1;
    if (is_text) slow_start();
    mpv_request_log_messages(l->h, c->verbose ? "info" : "warn");
    if (!is_text) {
        // the layer's idleness, kept by its event thread, so the renderer's own state never waits on the core
        __atomic_store_n(&app.idle[index], 1, __ATOMIC_RELEASE);
        mpv_observe_property(l->h, 0, "idle-active", MPV_FORMAT_FLAG);
    }
    if (!is_text) {
        if (layer_render_context(l)) return -1;
        mpv_set_property_string(l->h, "force-window", "yes");
    }
    if (target(l, w, h)) return -1;
    l->dirty = 1;
    if (pthread_create(&l->events, NULL, event_loop, l) == 0) l->have_events = 1;
    return 0;
}

static void layer_render(Layer *l)
{
    int flip = 0, block = 0;
    mpv_opengl_fbo fbo = { .fbo = (int)l->fbo, .w = l->w, .h = l->h_, .internal_format = GL_RGBA8 };
    mpv_render_param rp[] = { { MPV_RENDER_PARAM_OPENGL_FBO, &fbo }, { MPV_RENDER_PARAM_FLIP_Y, &flip },
                              { MPV_RENDER_PARAM_BLOCK_FOR_TARGET_TIME, &block }, { 0 } };
    render_last_fence = NULL;
    last_call_delete = 0;
    int r = mpv_render_context_render(l->rc, rp);
    // the frame's fence mpv will never delete (see count_fence_sync)
    if (r >= 0 && render_last_fence && !render_last_after_delete && real_delete_sync) {
        real_delete_sync(render_last_fence);
        app.fences--;
        app.fences_freed++;
    }
    render_last_fence = NULL;
}

static void layer_close(Layer *l)
{
    // the event thread lets go of the handle first: it must never wait on one that is gone
    events_stop = 1;
    if (l->have_events) {
        mpv_wakeup(l->h);
        pthread_join(l->events, NULL);
        l->have_events = 0;
    }
    if (l->rc) mpv_render_context_free(l->rc);
    l->rc = NULL;
    if (l->h) mpv_terminate_destroy(l->h);
    l->h = NULL;
    if (l->fbo) glDeleteFramebuffers(1, &l->fbo);
    if (l->tex) glDeleteTextures(1, &l->tex);
}

/* ---- the passes ----------------------------------------------------------------------------------------------------- */
static const char *VS_PLAIN =
    "#version 300 es\n"
    "in vec2 pvj_vert;\n"
    "out vec2 pvj_pos;\n"
    "void main() { pvj_pos = vec2(pvj_vert.x * 0.5 + 0.5, 0.5 - pvj_vert.y * 0.5); gl_Position = vec4(pvj_vert, 0.0, 1.0); }\n";
static const char *FS_SCALE =
    "#version 300 es\n"
    "precision mediump float;\n"
    "in vec2 pvj_pos;\n"
    "out vec4 pvj_out;\n"
    "uniform sampler2D tex;\n"
    "void main() { pvj_out = vec4(texture(tex, pvj_pos).rgb, 1.0); }\n";
// the text layer is premultiplied (mpv draws its on-screen layer that way): laid over with ONE, ONE_MINUS_SRC_ALPHA
static const char *FS_OVER =
    "#version 300 es\n"
    "precision mediump float;\n"
    "in vec2 pvj_pos;\n"
    "out vec4 pvj_out;\n"
    "uniform sampler2D tex;\n"
    "void main() { pvj_out = texture(tex, pvj_pos); }\n";

// The compositor (D98): the two video layers mixed by real opacity, texel for texel at the internal size, into a
// texture laid out as a layer's is, so the final pass reads it as it reads a layer.
static const char *VS_SAME =
    "#version 300 es\n"
    "in vec2 pvj_vert;\n"
    "out vec2 pvj_pos;\n"
    "void main() { pvj_pos = pvj_vert * 0.5 + 0.5; gl_Position = vec4(pvj_vert, 0.0, 1.0); }\n";
// The mixing stage (D101): a layer laid over a base by the layer's opacity and blend. The crossfade of D98 is this
// stage with the incoming layer's opacity moved by the clock (0 to 1 over the fade's seconds) over the outgoing one;
// a manual A/B crossfader is the same stage with the opacity set from outside. Blend 0 is normal (an alpha mix);
// the others are a line each here when they come.
static const char *FS_MIX =
    "#version 300 es\n"
    "precision mediump float;\n"
    "in vec2 pvj_pos;\n"
    "out vec4 pvj_out;\n"
    "uniform sampler2D outgoing;\n"
    "uniform sampler2D incoming;\n"
    "uniform float share;\n"
    "uniform int blend;\n"
    "void main() {\n"
    "  vec3 base = texture(outgoing, pvj_pos).rgb, layer = texture(incoming, pvj_pos).rgb;\n"
    "  vec3 c = layer;\n"                  /* blend 0, normal; e.g. add: if (blend == 1) c = min(base + layer, 1.0) */
    "  pvj_out = vec4(mix(base, c, share), 1.0);\n"
    "}\n";

static GLuint vbo, vao, p_scale, p_over, p_mix;
static GLint loc_share, loc_blend;
static GLuint mix_tex, mix_fbo;
static HookGL hook_gl;
static int have_hook;

static void quad(void)
{
    glBindVertexArray(vao);
    glDrawArrays(GL_TRIANGLE_STRIP, 0, 4);
    glBindVertexArray(0);
}

static void reset_gl(void)
{
    glDisable(GL_BLEND);
    glDisable(GL_SCISSOR_TEST);
    glDisable(GL_DEPTH_TEST);
    glDisable(GL_STENCIL_TEST);
    glDisable(GL_CULL_FACE);
    glColorMask(1, 1, 1, 1);
    glPixelStorei(GL_UNPACK_ALIGNMENT, 4);
    glPixelStorei(GL_PACK_ALIGNMENT, 4);
    glUseProgram(0);
    glActiveTexture(GL_TEXTURE0);
}

static void take_hook(void)
{
    pthread_mutex_lock(&app.lock);
    if (!app.hook_pending) { pthread_mutex_unlock(&app.lock); return; }
    Hook *h = app.hook_next;
    app.hook_next = NULL;
    pthread_mutex_unlock(&app.lock);
    char state[sizeof app.hook_state] = "off";
    if (have_hook) hookgl_free(&hook_gl);
    have_hook = 0;
    if (h) {
        char err[150] = "";
        if (hookgl_build(h, &hook_gl, err, sizeof err) == 0) {
            have_hook = 1;
            snprintf(state, sizeof state, "on");
        } else {
            snprintf(state, sizeof state, "failed: %s", err);
            LOG("the mapping is not drawn: %s", err);
        }
        hook_free(h);
    }
    pthread_mutex_lock(&app.lock);
    memcpy(app.hook_state, state, sizeof state);
    app.hook_pending = 0;
    pthread_cond_broadcast(&app.cond);
    pthread_mutex_unlock(&app.lock);
}

// One mix of the outgoing and the incoming layer at `share` (0: all outgoing, 1: all incoming), at the inside size.
static void mix_pass(const Layer *outgoing, const Layer *incoming, float share, int blend)
{
    reset_gl();
    glBindFramebuffer(GL_FRAMEBUFFER, mix_fbo);
    glViewport(0, 0, app.in_w, app.in_h);
    glActiveTexture(GL_TEXTURE0);
    glBindTexture(GL_TEXTURE_2D, outgoing->tex);
    glActiveTexture(GL_TEXTURE1);
    glBindTexture(GL_TEXTURE_2D, incoming->tex);
    glUseProgram(p_mix);
    glUniform1f(loc_share, share);
    glUniform1i(loc_blend, blend);
    quad();
    glActiveTexture(GL_TEXTURE1);
    glBindTexture(GL_TEXTURE_2D, 0);
    glActiveTexture(GL_TEXTURE0);
}

static float frame_opacity[MAX_LAYERS];
static int idle_drawn[MAX_LAYERS];         // render thread: source k's empty picture is drawn since it went idle     // each source's opacity for this frame (one look under app.lock)

static void final_pass(GLuint picture, int text_on)
{
    reset_gl();
    glBindFramebuffer(GL_FRAMEBUFFER, out_target());
    glViewport(0, 0, app.out_w, app.out_h);
    glActiveTexture(GL_TEXTURE0);
    glBindTexture(GL_TEXTURE_2D, picture);
    if (have_hook) {
        glUseProgram(hook_gl.prog);
        glUniform1i(hook_gl.loc_raw, 0);
        glUniform2f(hook_gl.loc_size, (float)app.out_w, (float)app.out_h);
        glUniform2f(hook_gl.loc_pt, 1.0f / app.out_w, 1.0f / app.out_h);
        glUniform2f(hook_gl.loc_target, (float)app.out_w, (float)app.out_h);
        glUniform2f(hook_gl.loc_input, (float)app.in_w, (float)app.in_h);
        for (int i = 0; i < hook_gl.ntex; i++) {
            glActiveTexture(GL_TEXTURE1 + i);
            glBindTexture(GL_TEXTURE_2D, hook_gl.tex[i]);
            glUniform1i(hook_gl.tex_loc[i], 1 + i);
            glUniform2f(hook_gl.tex_size_loc[i], (float)hook_gl.tex_w[i], (float)hook_gl.tex_h[i]);
        }
        // the other sources (D101), after the file's textures: at most 1 + 8 + 7 units, GLES 3's 16. A source this
        // run does not have is read as source 1's picture (the panel never sends one; this keeps the pass whole).
        for (int i = 0; i < hook_gl.nsrc; i++) {
            int k = hook_gl.src[i];
            glActiveTexture(GL_TEXTURE1 + hook_gl.ntex + i);
            glBindTexture(GL_TEXTURE_2D, k <= app.nsources ? video[k].tex : picture);
            glUniform1i(hook_gl.src_loc[i], 1 + hook_gl.ntex + i);
            glUniform1f(hook_gl.src_opacity_loc[i], k <= app.nsources ? frame_opacity[k] : 1.0f);
        }
    } else {
        glUseProgram(p_scale);
    }
    quad();
    if (text_on) {
        glActiveTexture(GL_TEXTURE0);
        glBindTexture(GL_TEXTURE_2D, text.tex);
        glUseProgram(p_over);
        glEnable(GL_BLEND);
        glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA);
        quad();
        glDisable(GL_BLEND);
    }
}

// The final picture as a PPM file (P6, top row first), never through a link.
static int write_capture(const char *path)
{
    size_t row = (size_t)app.out_w * 4;
    unsigned char *px = malloc(row * (size_t)app.out_h);
    if (!px) return -1;
    glBindFramebuffer(GL_FRAMEBUFFER, out_target());
    glPixelStorei(GL_PACK_ALIGNMENT, 4);
    glReadPixels(0, 0, app.out_w, app.out_h, GL_RGBA, GL_UNSIGNED_BYTE, px);
    int fd = open(path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0640);
    if (fd < 0) { LOG("capture: %s: %s", path, strerror(errno)); free(px); return -1; }
    FILE *f = fdopen(fd, "wb");
    if (!f) { close(fd); free(px); return -1; }
    fprintf(f, "P6\n%d %d\n255\n", app.out_w, app.out_h);
    unsigned char *rgb = malloc((size_t)app.out_w * 3);
    int ok = rgb != NULL;
    for (int y = app.out_h - 1; ok && y >= 0; y--) {
        const unsigned char *s = px + row * (size_t)y;
        for (int x = 0; x < app.out_w; x++) {
            rgb[x * 3] = s[x * 4];
            rgb[x * 3 + 1] = s[x * 4 + 1];
            rgb[x * 3 + 2] = s[x * 4 + 2];
        }
        ok = fwrite(rgb, 3, (size_t)app.out_w, f) == (size_t)app.out_w;
    }
    free(rgb);
    free(px);
    if (fclose(f)) ok = 0;
    return ok ? 0 : -1;
}

/* ---- main ---------------------------------------------------------------------------------------------------------- */
int main(int argc, char **argv)
{
    Cfg cfg;
    if (parse_args(argc, argv, &cfg)) return 64;
    app.cfg = &cfg;
    app.pid = getpid();
    snprintf(app.hook_state, sizeof app.hook_state, "off");
    if (pipe2(app.wake_fd, O_CLOEXEC | O_NONBLOCK)) { LOG("pipe: %s", strerror(errno)); return 1; }
    struct sigaction sa = { .sa_handler = on_signal };
    sigaction(SIGTERM, &sa, NULL);
    sigaction(SIGINT, &sa, NULL);
    signal(SIGPIPE, SIG_IGN);

    char err[200] = "";
    if (out_open(&cfg, err, sizeof err)) { LOG("no output: %s", err); out_close(); return 2; }
    // the internal size: `detail` lines at the output's shape (even numbers), never more than the output
    if (cfg.detail <= 0 || cfg.detail >= app.out_h) {
        app.in_w = app.out_w;
        app.in_h = app.out_h;
    } else {
        app.in_h = cfg.detail & ~1;
        app.in_w = ((int)lround((double)app.out_w * app.in_h / app.out_h)) & ~1;
    }
    if (cfg.headless_w > 0) app.screen_open = 1;

    static const float q[] = { -1, -1, 1, -1, -1, 1, 1, 1 };
    glGenVertexArrays(1, &vao);
    glBindVertexArray(vao);
    glGenBuffers(1, &vbo);
    glBindBuffer(GL_ARRAY_BUFFER, vbo);
    glBufferData(GL_ARRAY_BUFFER, sizeof q, q, GL_STATIC_DRAW);
    glEnableVertexAttribArray(0);
    glVertexAttribPointer(0, 2, GL_FLOAT, GL_FALSE, 0, 0);
    glBindVertexArray(0);
    p_scale = gl_program(VS_PLAIN, FS_SCALE, err, sizeof err);
    p_over = gl_program(VS_PLAIN, FS_OVER, err, sizeof err);
    p_mix = gl_program(VS_SAME, FS_MIX, err, sizeof err);
    if (!p_scale || !p_over || !p_mix) { LOG("%s", err); out_close(); return 2; }
    glUseProgram(p_scale);
    glUniform1i(glGetUniformLocation(p_scale, "tex"), 0);
    glUseProgram(p_over);
    glUniform1i(glGetUniformLocation(p_over, "tex"), 0);
    glUseProgram(p_mix);
    glUniform1i(glGetUniformLocation(p_mix, "outgoing"), 0);
    glUniform1i(glGetUniformLocation(p_mix, "incoming"), 1);
    loc_share = glGetUniformLocation(p_mix, "share");
    loc_blend = glGetUniformLocation(p_mix, "blend");
    Layer mixer = { 0 };
    if (target(&mixer, app.in_w, app.in_h)) { LOG("no texture for the crossfade"); out_close(); return 2; }
    mix_tex = mixer.tex;
    mix_fbo = mixer.fbo;

    // Both video layers are started now; the second plays nothing until a crossfade loads it (an idle libmpv core
    // decodes nothing and is not drawn: its cost is its threads asleep and a few megabytes).
    // Sources 2 to N (D101) are started now too, each idle until something is loaded into it.
    app.nsources = cfg.sources;
    for (int i = 0; i < MAX_LAYERS; i++) { app.opacity[i] = 1.0f; app.blend[i] = BLEND_NORMAL; }
    int nlayers = LAYERS + app.nsources - 1, opened = 0;
    int bad = 0;
    for (int i = 0; i < nlayers && !bad; i++, opened++) bad = layer_open(&video[i], i, &cfg, app.in_w, app.in_h) != 0;
    if (bad || layer_open(&text, -1, &cfg, app.out_w, app.out_h)) {
        LOG("libmpv could not be started");
        layer_close(&text);
        for (int i = opened - 1; i >= 0; i--) layer_close(&video[i]);
        out_close();
        return 2;
    }
    for (int i = 0; i < nlayers; i++) app.layer[i] = video[i].h;
    app.front = 0;
    app.osd = text.h;
    if (ipc_open(cfg.socket_path, err, sizeof err)) {
        LOG("%s", err);
        layer_close(&text);
        for (int i = nlayers - 1; i >= 0; i--) layer_close(&video[i]);
        out_close();
        return 2;
    }
    pthread_t ipc;
    if (pthread_create(&ipc, NULL, ipc_thread, NULL)) { LOG("no socket thread"); return 2; }
    LOG("drawing %dx%d inside, %dx%d at %.2f Hz out, %d source%s, hwdec %s, socket %s", app.in_w, app.in_h, app.out_w, app.out_h,
        app.out_hz, app.nsources, app.nsources == 1 ? "" : "s", cfg.hwdec, cfg.socket_path);

    // A picture goes out at every vblank, whether or not the clip has a new frame: the compositor runs at the output's
    // rate (shaders, crossfades and overlays move between a clip's frames). out_present queues this flip and returns
    // once the one before it is done, so the next picture is drawn while this flip is pending, and the loop turns
    // once a vblank. In a turn the video layer converts at most one clip frame; with none new, its last one is used.
    //
    // A crossfade (D98) is drawn here, once a vblank: the socket's thread has loaded the incoming clip into the idle
    // layer and made it the front; this loop waits for that clip's first frame (FADE_WAITING, the outgoing picture
    // alone), then mixes the two by the clock (FADE_RUNNING: the incoming share goes from 0 to 1 in a straight line
    // over the fade's seconds, as D71's still was made more transparent in a straight line), and at the end stops
    // the outgoing layer, which is idle and costs nothing again. Each layer converts at most one clip frame a turn.
    int failed = 0, text_was = 0, was_front = 0;
    long presented = 0;
    while (!g_signal) {
        pthread_mutex_lock(&app.lock);
        int quit = app.quit, cap = app.capture_serial != app.capture_done_serial;
        int text_on = app.overlays != 0 || now_s() < app.text_until;
        // one look at the whole fade: the layer, the phase and its clock are never from two different crossfades
        int fi = app.front, phase = app.fade.phase, base = app.fade.base_starts;
        double asked = app.fade.asked, t0 = app.fade.t0, len = app.fade.seconds;
        memcpy(frame_opacity, app.opacity, sizeof frame_opacity);     // the sources' mix, read once for this frame
        pthread_mutex_unlock(&app.lock);
        if (quit) break;
        if (app.hook_pending) take_hook();
        Layer *front = &video[fi], *back = &video[1 - fi];
        if (fi != was_front) { front->dirty = 1; was_front = fi; }
        for (int i = 0; i < nlayers; i++) {
            // every layer's update is taken at every turn (libmpv asks for that), whether or not it is drawn
            uint64_t uv = mpv_render_context_update(video[i].rc);
            if (uv & MPV_RENDER_UPDATE_FRAME) video[i].dirty = 1;
            int vc = __atomic_load_n(&video[i].changed, __ATOMIC_ACQUIRE);
            if (vc != video[i].seen) { video[i].seen = vc; video[i].dirty = 1; }
        }
        uint64_t ut = mpv_render_context_update(text.rc);
        if (ut & MPV_RENDER_UPDATE_FRAME) text.dirty = 1;
        if (text_on && !text_was) text.dirty = 1;          // never a stale text for a frame
        double now = now_s();
        if (phase == FADE_WAITING) {
            int restarted = __atomic_load_n(&app.restart_of[fi], __ATOMIC_ACQUIRE) > base;
            int failed_load = __atomic_load_n(&app.failed_of[fi], __ATOMIC_ACQUIRE) > base;
            if (restarted || failed_load || now - asked > FADE_FIRST_FRAME) {
                // the incoming clip's first frame is ready (it is drawn below, in this turn), its load failed (the
                // fade goes on to its empty picture, as a cut to it would), or it never came in time (as D71 did)
                pthread_mutex_lock(&app.lock);
                if (app.fade.phase == FADE_WAITING && app.front == fi) {
                    app.fade.phase = phase = FADE_RUNNING;
                    app.fade.t0 = t0 = now;
                }
                pthread_mutex_unlock(&app.lock);
                front->dirty = 1;
            }
        }
        if (front->dirty) {
            reset_gl();
            layer_render(front);
            front->dirty = 0;
            app.drawn++;
            __atomic_add_fetch(&app.layer_drawn[fi], 1, __ATOMIC_RELAXED);
        }
        if (phase != FADE_OFF && back->dirty) {
            reset_gl();
            layer_render(back);
            back->dirty = 0;
            app.back_drawn++;
            __atomic_add_fetch(&app.layer_drawn[1 - fi], 1, __ATOMIC_RELAXED);
        }
        // Sources 2 to N (D101): each converts at most one clip frame a turn, and only a new one. An idle source
        // has none (it is drawn once more as it goes idle, which gives its empty picture), so it costs nothing.
        // An idle source is drawn once (its empty picture) and then not again until it plays: a core's start and
        // stop send more than one event that asks for a new picture (CI's llvmpipe drew an idle source three times,
        // the review of #153). What asked stays asked (dirty), so a clip's first frame that comes before its
        // idle-active is seen to fall is drawn as soon as it is.
        for (int i = LAYERS; i < nlayers; i++) {
            int idle = __atomic_load_n(&app.idle[i], __ATOMIC_ACQUIRE);
            if (!idle) idle_drawn[i] = 0;
            if (!video[i].dirty || (idle && idle_drawn[i])) continue;
            reset_gl();
            layer_render(&video[i]);
            video[i].dirty = 0;
            if (idle) idle_drawn[i] = 1;
            __atomic_add_fetch(&app.layer_drawn[i], 1, __ATOMIC_RELAXED);
        }
        if (text_on && text.dirty) {
            reset_gl();
            layer_render(&text);
            text.dirty = 0;
        }
        text_was = text_on;
        GLuint picture = front->tex;
        if (phase != FADE_OFF) {
            float share = 0.0f;
            if (phase == FADE_RUNNING) {
                double p = len > 0 ? (now - t0) / len : 1.0;
                share = (float)(p < 0 ? 0 : p > 1 ? 1 : p);
            }
            if (share >= 1.0f) {
                // the end: the incoming layer alone from this picture on; the outgoing one is stopped (async: this
                // thread never waits on libmpv, which may be waiting on it). Under the lock, so the socket's thread,
                // which loads the next crossfade into this layer only once the fade is off, queues its load after
                // this stop in the same core.
                pthread_mutex_lock(&app.lock);
                if (app.fade.phase != FADE_OFF && app.front == fi) {
                    app.fade.phase = FADE_OFF;
                    app.fade.progress = 1.0;
                    app.fades_done++;
                    const char *stop[] = { "stop", NULL };
                    mpv_command_async(back->h, 0, stop);
                }
                pthread_mutex_unlock(&app.lock);
            } else {
                // A new crossfade may have begun since the look above (the socket's thread made the other layer the
                // front): then this turn's front is that fade's outgoing layer, which is what its first picture
                // shows, and nothing of the old fade is mixed or written.
                pthread_mutex_lock(&app.lock);
                int same = app.front == fi && app.fade.phase != FADE_OFF;
                if (same) app.fade.progress = share;
                pthread_mutex_unlock(&app.lock);
                if (!same) {
                    picture = front->tex;
                } else if (share <= 0.0f) {
                    picture = back->tex;        // waiting for the incoming clip: the outgoing picture, with no mix
                } else {
                    mix_pass(back, front, share, BLEND_NORMAL);     // the incoming layer at the clock's opacity
                    picture = mix_tex;
                }
            }
        }
        final_pass(picture, text_on);
        if (cap) {
            pthread_mutex_lock(&app.lock);
            char *path = app.capture_path ? strdup(app.capture_path) : NULL;
            int serial = app.capture_serial;
            pthread_mutex_unlock(&app.lock);
            int r = path ? write_capture(path) : -1;
            free(path);
            pthread_mutex_lock(&app.lock);
            app.capture_result = r;
            app.capture_done_serial = serial;
            pthread_cond_broadcast(&app.cond);
            pthread_mutex_unlock(&app.lock);
        }
        if (out_present()) { failed = 1; break; }
        mpv_render_context_report_swap(front->rc);
        if (phase != FADE_OFF) mpv_render_context_report_swap(back->rc);
        for (int i = LAYERS; i < nlayers; i++)
            if (!__atomic_load_n(&app.idle[i], __ATOMIC_ACQUIRE)) mpv_render_context_report_swap(video[i].rc);
        if (text_on) mpv_render_context_report_swap(text.rc);
        presented++;
        if (cfg.frames > 0 && presented >= cfg.frames) break;
        out_wait(0);                                        // the wake pipe and the screen's events; never a sleep
    }
    pthread_mutex_lock(&app.lock);
    app.quit = 1;
    pthread_cond_broadcast(&app.cond);
    pthread_mutex_unlock(&app.lock);
    // The socket's threads end at once, but one may be inside libmpv waiting for this thread (a screenshot of the
    // window is done on the render thread): libmpv's queue for it is still served until they have all ended.
    double until = now_s() + 5.0;
    while (!ipc_finished() && now_s() < until) {
        for (int i = 0; i < nlayers; i++) mpv_render_context_update(video[i].rc);
        mpv_render_context_update(text.rc);
        struct timespec ts = { 0, 10 * 1000 * 1000 };
        nanosleep(&ts, NULL);
    }
    if (!ipc_finished()) {
        // never free libmpv under a thread that is still in it: the screen is given back and the process ends
        LOG("the socket's threads did not end within 5 seconds; ending at once");
        out_close();
        _exit(failed ? 3 : 4);
    }
    pthread_join(ipc, NULL);
    ipc_close();
    if (have_hook) hookgl_free(&hook_gl);
    layer_close(&text);
    for (int i = nlayers - 1; i >= 0; i--) layer_close(&video[i]);
    glDeleteFramebuffers(1, &mix_fbo);
    glDeleteTextures(1, &mix_tex);
    out_close();
    LOG("ended: %ld flips, %ld video frames drawn (%ld on the second layer), %ld vblanks missed, %ld crossfades (%ld cut short), "
        "%ld of libmpv's fences deleted%s", app.flips, app.drawn, app.back_drawn, app.missed, app.fades, app.fades_cut,
        app.fences_freed, failed ? ", the screen failed" : "");
    return failed ? 3 : app.exit_code;
}
