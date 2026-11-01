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
//                     vblank, and hands the mix to the final pass; a wipe or a slide is the same stage with the
//                     picture cut along an edge instead of mixed (D111); otherwise there is no such pass
//   the final pass    scales the picture to the screen; with a mapping it is the mapping's own OUTPUT shader. It may
//                     draw at 720 lines and leave the rest to the display's scaler (D108); the screen's size stays
//   the text layer    a second libmpv that plays nothing draws overlays and show-text at the screen's size, laid
//                     over the final picture while something is on it
//
// Usage: pvj-render --socket=PATH [--drm-device=/dev/dri/cardN] [--drm-connector=NAME] [--drm-mode=WxH@R|N]
//                   [--detail=LINES|output] [--hwdec=NAME] [--audio-device=NAME] [--fast] [--every-vblank]
//                   [--sources=N] [--fx-fps=N] [--final=auto|720] [--headless=WxH] [--frames=N] [--verbose]
#define _GNU_SOURCE
#include "pvj.h"
#include "json.h"

#include <errno.h>
#include <fcntl.h>
#include <math.h>
#include <sched.h>
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
const Move MOVE_LIST[MOVES] = {
    { "wipe-from-left", 0, 1, 0 }, { "wipe-from-right", 0, 0, 0 }, { "wipe-from-top", 1, 1, 0 }, { "wipe-from-bottom", 1, 0, 0 },
    { "slide-left", 0, 0, 1 }, { "slide-right", 0, 1, 1 }, { "slide-up", 1, 0, 1 }, { "slide-down", 1, 1, 1 } };
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
    *c = (Cfg){ .hwdec = "no", .detail = 0, .sources = 1, .fx_fps = 30 };
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
        } else if (opt(a, "--final", &v)) {
            if (!strcmp(v, "auto")) c->final_mode = FINAL_AUTO;
            else if (!strcmp(v, "720")) c->final_mode = FINAL_720;
            else { LOG("--final is auto or 720"); return -1; }
        } else if (opt(a, "--fx-fps", &v)) {
            char *e;
            long k = strtol(v, &e, 10);
            if (*e || k < 1 || k > 240) { LOG("--fx-fps is a number from 1 to 240"); return -1; }
            c->fx_fps = (int)k;
        } else if (opt(a, "--frames", &v)) c->frames = atoi(v);
        else if (!strcmp(a, "--fast")) c->fast = 1;
        else if (!strcmp(a, "--every-vblank")) c->every_vblank = 1;
        else if (!strcmp(a, "--verbose")) c->verbose = 1;
#ifdef PVJ_RENDER_TEST_HOOKS
        else if (!strcmp(a, "--version")) { printf("pvj-render %s (with test hooks)\n", PVJ_VERSION); exit(0); }
#else
        else if (!strcmp(a, "--version")) { printf("pvj-render %s\n", PVJ_VERSION); exit(0); }
#endif
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
#define SMALL_CACHE "32MiB"
#define SMALL_BACK "16MiB"

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

// A loop that cannot go on (D118): the file of a generated list is gone at a join, so mpv's loop-file seeks to the start,
// reaches the end at once and seeks again, for ever, with nothing drawn (measured on a Pi 4, plain mpv: 7000 turns a
// second, two cores busy, never idle). Nothing else restarts this fast with no frame between: a clip is at least a frame
// long, and a person or a controller seeks far slower. The loop is taken off and the file ends as any other does, on its
// own layer and no other.
#define SPIN_GAP 0.02                   // seconds between two restarts that count as one turn of such a loop
#define SPIN_TURNS 20

// That seek is not only wasted: after a failed open at a join, FFmpeg's concat demuxer can seek through a context it no
// longer has (a null pointer in avformat_seek_file, seen with FFmpeg 7.1 and 8.0 on this program in CI and on a desk), and
// the whole renderer falls. The demuxer reads ahead of the picture, so its complaint comes while frames are still to
// show: the loop is taken off then, the buffered frames play out, and the file ends with no seek at all. Only for a file
// that has played: an open that a newer load cuts short says the same words, and the new file's loop is not to be touched.
static int file_is_lost(const mpv_event_log_message *m)
{
    return (!strcmp(m->prefix, "ffmpeg/demuxer") && strstr(m->text, "concat: Impossible to open"))
           || (!strcmp(m->prefix, "lavf") && strstr(m->text, "treating it as fatal error"));
}

static void *event_loop(void *arg)
{
    Layer *l = arg;
    mpv_handle *h = l->h;
    int i = l->index;
    double restart_at = -1e9;
    long restart_drawn = -1;
    int turns = 0;
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
                if (p && p->format == MPV_FORMAT_FLAG && p->data && !strcmp(p->name, "pause"))
                    __atomic_store_n(&app.paused[i], *(int *)p->data, __ATOMIC_RELEASE);
            }
            if (ev->event_id == MPV_EVENT_PLAYBACK_RESTART) {
                __atomic_store_n(&app.restart_of[i], __atomic_load_n(&app.starts[i], __ATOMIC_ACQUIRE), __ATOMIC_RELEASE);
                wake_render();
                double t = now_s();
                long drawn = __atomic_load_n(&app.layer_drawn[i], __ATOMIC_RELAXED);
                turns = t - restart_at < SPIN_GAP && drawn == restart_drawn ? turns + 1 : 0;
                restart_at = t;
                restart_drawn = drawn;
                if (turns == SPIN_TURNS) {
                    LOG("layer %d: its file restarts without a frame; the loop is taken off and it ends", i);
                    mpv_set_property_string(h, "loop-file", "no");
                }
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
            if (i >= 0 && file_is_lost(m) && __atomic_load_n(&app.restart_of[i], __ATOMIC_ACQUIRE) == __atomic_load_n(&app.starts[i], __ATOMIC_ACQUIRE)) {
                LOG("layer %d: its file cannot be read any more; the loop is taken off and it ends", i);
                mpv_set_property_string(h, "loop-file", "no");
            }
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

#ifdef PVJ_RENDER_TEST_HOOKS
// For the tests only, and only in a build made with `make TEST_HOOKS=1` (CI; the box's own build has none):
// PVJ_RENDER_TEST_SLOW_START=<ms> waits that long after the text core's mpv_initialize, as a loaded machine may, so
// that the core is surely idle before anything else is done to it (tests/test_pvj_render.py).
static void slow_start(void)
{
    const char *v = getenv("PVJ_RENDER_TEST_SLOW_START");
    long ms = v ? strtol(v, NULL, 10) : 0;
    if (ms <= 0 || ms > 5000) return;
    struct timespec t = { ms / 1000, (ms % 1000) * 1000000L };
    nanosleep(&t, NULL);
}
#endif

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
    // first, so that what goes wrong while the core starts (the text core's window is made then) reaches the journal
    mpv_request_log_messages(l->h, c->verbose ? "info" : "warn");
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
    // No compiled shaders on disk, on every core: config=no does not stop mpv's shader cache everywhere. On 0.37 the
    // "cache" folder is not one that config=no clears (options/path.c), so every core wrote $XDG_CACHE_HOME/mpv; on
    // 0.40 and 0.41 it is cleared only by mpv_initialize, and the text core's render context is made before that.
    // The option (a flag) is in 0.37, 0.40 and 0.41 alike (the review of #162, and its CI on 0.37).
    hard(l->h, "gpu-shader-cache", "no");
    if (hard_failed) return -1;
    // A small demuxer cache on the cores that play only local clips or nothing (the review of the 2-hour soak, D106):
    // mpv's default holds about 150 MiB ahead and 50 MiB behind per core, and with 4 sources at 720p looping the
    // process grew from 340 to about 875 MB in its first hour on the Pi. Sources 2 to N play clips from the media
    // folder only (D101: never a stream or a live input's pipe), and the text core plays nothing. Source 1's two
    // layers keep mpv's own: they play streams and NDI, and the player sets their cache itself (Player.PIPE_DEFAULTS).
    if (is_text || index >= LAYERS) {
        set(l->h, "demuxer-max-bytes", SMALL_CACHE);
        set(l->h, "demuxer-max-back-bytes", SMALL_BACK);
    }
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
        // is made first and the window is wanted from the start (gpu-shader-cache=no above keeps it off the disk).
        set(l->h, "force-window", "yes");
        if (layer_render_context(l)) return -1;
    } else {
        set(l->h, "hwdec", c->hwdec);
        if (c->fast) {
            set(l->h, "profile", "fast");           // a Pi: mpv's cheap scalers, bilinear (no table of weights)
        } else {
            // mpv up to 0.41 leaves the padding of its scaler table uninitialised when a kernel has 6 taps (rows of 6
            // weights padded to 8; video/out/gpu/video.c, reinit_scaler), which the default lanczos of radius 3 has
            // when it scales up. A GPU that filters the table arithmetically (llvmpipe) then mixes that garbage into
            // the weights, and the picture is black or dim for the life of the process (D101's addendum). Fixed
            // upstream after 0.41 (mpv 72d43dc9c, issue #18540). Until the box's mpv is 0.42: every scaler is a
            // lanczos of radius 4, 8 taps up and 8 or more down, never 6 (mpv's sizes are 2, 4, 6, then multiples of
            // 4). Explicit, so it holds on 0.37 too, whose defaults differ; the socket cannot change them.
            set(l->h, "scale", "lanczos");
            set(l->h, "scale-radius", "4");
            set(l->h, "cscale", "lanczos");
            set(l->h, "cscale-radius", "4");
            set(l->h, "dscale", "lanczos");
            set(l->h, "dscale-radius", "4");
        }
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
#ifdef PVJ_RENDER_TEST_HOOKS
    if (is_text) slow_start();
#endif
    if (!is_text) {
        // the layer's idleness, kept by its event thread, so the renderer's own state never waits on the core
        __atomic_store_n(&app.idle[index], 1, __ATOMIC_RELEASE);
        mpv_observe_property(l->h, 0, "idle-active", MPV_FORMAT_FLAG);
        mpv_observe_property(l->h, 0, "pause", MPV_FORMAT_FLAG);     // an effect's clock (D106)
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
    "uniform float pvj_level;\n"         /* source 1's level while a program of the renderer's is on (D106), else 0 */
    "void main() { pvj_out = vec4(clamp(texture(tex, pvj_pos).rgb + pvj_level, 0.0, 1.0), 1.0); }\n";
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
// A wipe or a slide (D111): one layer's part of the picture, read where it lies now (`shift`, in parts of the picture).
static const char *VS_MOVE =
    "#version 300 es\n"
    "in vec2 pvj_vert;\n"
    "out vec2 pvj_pos;\n"
    "uniform vec2 shift;\n"
    "void main() { pvj_pos = pvj_vert * 0.5 + 0.5 + shift; gl_Position = vec4(pvj_vert, 0.0, 1.0); }\n";
static const char *FS_MOVE =
    "#version 300 es\n"
    "precision mediump float;\n"
    "in highp vec2 pvj_pos;\n"            /* highp: a moving picture must land on its texels, whatever the GPU's mediump is */
    "out vec4 pvj_out;\n"
    "uniform sampler2D tex;\n"
    "void main() { pvj_out = vec4(texture(tex, pvj_pos).rgb, 1.0); }\n";

static GLuint vbo, vao, p_scale, p_over, p_mix, p_move;
static GLint loc_share, loc_blend, loc_scale_level, loc_shift;
static GLuint mix_tex, mix_fbo;
static HookGL hook_gl;
static int have_hook;

static void quad(void)
{
    glBindVertexArray(vao);
    glDrawArrays(GL_TRIANGLE_STRIP, 0, 4);
    glBindVertexArray(0);
}

static void reset_gl(void);
void gl_reset(void) { reset_gl(); }

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

// One moment of a wipe or a slide at `share` (0: all outgoing, 1: all incoming), at the inside size. The edge lies
// where the panel's still had it (transitions.Move.gone): that share of the columns or rows, rounded, no softness. Two
// scissored draws, one from each layer, so a pixel reads one texture where the mix reads two; the texture's last
// picture need not be read in first, so that is said (the same saving as the final pass's, D106 and D108).
static void move_pass(const Move *m, const Layer *outgoing, const Layer *incoming, float share)
{
    static const GLenum colour = GL_COLOR_ATTACHMENT0;
    int size = m->axis ? app.in_h : app.in_w;
    int n = (int)lround(share * size), edge = m->low ? n : size - n;
    float shift = m->shift ? (float)(m->low ? -n : n) / (float)size : 0.0f;
    reset_gl();
    glBindFramebuffer(GL_FRAMEBUFFER, mix_fbo);
    glInvalidateFramebuffer(GL_FRAMEBUFFER, 1, &colour);
    glViewport(0, 0, app.in_w, app.in_h);
    glEnable(GL_SCISSOR_TEST);
    glUseProgram(p_move);
    for (int side = 0; side < 2; side++) {                  // 0: from column or row 0 to the edge; 1: the rest
        int from = side ? edge : 0, to = side ? size : edge, in = side == (m->low ? 0 : 1);
        if (to <= from) continue;
        if (m->axis) glScissor(0, from, app.in_w, to - from);
        else glScissor(from, 0, to - from, app.in_h);
        glBindTexture(GL_TEXTURE_2D, in ? incoming->tex : outgoing->tex);
        glUniform2f(loc_shift, in || m->axis ? 0.0f : shift, in || !m->axis ? 0.0f : shift);
        quad();
    }
    glDisable(GL_SCISSOR_TEST);
}

static float frame_opacity[MAX_LAYERS];
static float frame_level[MAX_SOURCES];     // each source's level while it has a program on (D106), else 0
static GLuint source_tex[MAX_LAYERS];      // each source k > 1's picture after its programs (D106), this frame
static int idle_drawn[MAX_LAYERS];         // render thread: source k's empty picture is drawn since it went idle     // each source's opacity for this frame (one look under app.lock)

// The final pass into `target`, at w x h in its top left corner (GL's origin is the bottom left, so the corner starts at
// row `target_h` - h). The size is only the viewport: every uniform below keeps the output's real size, because HOOKED_size
// is the mapping's screen, and HOOKED_pos goes from 0 to 1 over the viewport at any size (D108).
static void final_pass(GLuint target, int target_h, int w, int h, GLuint picture, int text_on, float level)
{
    reset_gl();
    glBindFramebuffer(GL_FRAMEBUFFER, target);
    glViewport(0, target_h - h, w, h);
    // Both final passes (the plain scale and the mapping's shader) draw the whole target, so the clear changes no pixel;
    // it tells the GPU the old frame need not be loaded into the tiles first (measured on the Pi 4, 4 sources: 40.7 to 44.9 flips a second).
    // In a corner of the buffer (D108) a clear would fill the part the plane does not show: the same work is asked by
    // invalidating the buffer, and the pass still draws every pixel that goes out. Measured on the Pi 4, 4 sources and the
    // mapping, GPU busy: whole clear 69%, scissored clear 83%, invalidate 62%.
    glClearColor(0, 0, 0, 1);
    if (h == target_h) {
        glClear(GL_COLOR_BUFFER_BIT);
    } else {
        const GLenum what = target ? GL_COLOR_ATTACHMENT0 : GL_COLOR;
        glInvalidateFramebuffer(GL_FRAMEBUFFER, 1, &what);
    }
    glActiveTexture(GL_TEXTURE0);
    glBindTexture(GL_TEXTURE_2D, picture);
    if (have_hook) {
        glUseProgram(hook_gl.prog);
        glUniform1i(hook_gl.loc_raw, 0);
        glUniform1f(hook_gl.loc_level, level);
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
            glBindTexture(GL_TEXTURE_2D, k <= app.nsources ? source_tex[k] : picture);
            glUniform1i(hook_gl.src_loc[i], 1 + hook_gl.ntex + i);
            glUniform1f(hook_gl.src_opacity_loc[i], k <= app.nsources ? frame_opacity[k] : 1.0f);
            glUniform1f(hook_gl.src_level_loc[i], k <= app.nsources ? frame_level[k - 1] : level);
        }
    } else {
        glUseProgram(p_scale);
        glUniform1f(loc_scale_level, level);
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

// A framebuffer of the screen's size of its own, for a picture that must be the screen's whichever way the final pass draws.
static int screen_target(GLuint *tex, GLuint *fbo)
{
    glGenTextures(1, tex);
    glBindTexture(GL_TEXTURE_2D, *tex);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA8, app.out_w, app.out_h, 0, GL_RGBA, GL_UNSIGNED_BYTE, NULL);
    glGenFramebuffers(1, fbo);
    glBindFramebuffer(GL_FRAMEBUFFER, *fbo);
    glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, *tex, 0);
    return glCheckFramebufferStatus(GL_FRAMEBUFFER) == GL_FRAMEBUFFER_COMPLETE ? 0 : -1;
}

// The final picture as a PPM file (P6, top row first), never through a link. Always at the screen's size: while the
// final pass draws smaller (D108) the frame on its way out is a corner of the buffer, so the picture is drawn once more
// at the screen's size for the file.
static int write_capture(const char *path, int small, GLuint picture, int text_on, float level)
{
    size_t row = (size_t)app.out_w * 4;
    unsigned char *px = malloc(row * (size_t)app.out_h);
    if (!px) return -1;
    GLuint tex = 0, fbo = 0;
    if (small && screen_target(&tex, &fbo)) { free(px); return -1; }
    if (small) final_pass(fbo, app.out_h, app.out_w, app.out_h, picture, text_on, level);
    glBindFramebuffer(GL_FRAMEBUFFER, small ? fbo : out_target());
    glPixelStorei(GL_PACK_ALIGNMENT, 4);
    glReadPixels(0, 0, app.out_w, app.out_h, GL_RGBA, GL_UNSIGNED_BYTE, px);
    if (fbo) glDeleteFramebuffers(1, &fbo);
    if (tex) glDeleteTextures(1, &tex);
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

// A screenshot while a program of the renderer's is on (D106): libmpv's layer then holds only the generator's blank
// carrier, or a clip without its effect, so the picture is the renderer's own: source 1 as the programs left it, with
// the level, scaled to the screen's size with the text over it ("window", as mpv's window screenshot has the OSD) or
// at the inside size ("video"). This thread draws it and starts reading it into a pixel buffer; the next frame maps
// the buffer (by then the GPU has written it) and hands the pixels to the client's thread, which encodes them
// (ipc.c, native_screenshot). Nothing here waits for an encoder or a file (the review of #167).
// Kind 2 is the whole final picture at the screen's size, the mapping and the text in it, drawn once more into the
// readback's own framebuffer: a transition's still of the screen (pvj/transitions.py), whatever the final pass's size.
static struct { int serial, w, h; GLuint tex, fbo, pbo; } shot_read;      // a readback on its way (serial > 0)

static void shot_hand(int serial, unsigned char *rgba, int w, int h)
{
    pthread_mutex_lock(&app.lock);
    if (app.shot.serial == serial) {
        free(app.shot.rgba);
        app.shot.rgba = rgba;
        app.shot.w = w;
        app.shot.h = h;
        app.shot.done_serial = serial;
        rgba = NULL;
    }
    pthread_cond_broadcast(&app.cond);
    pthread_mutex_unlock(&app.lock);
    free(rgba);                         // asked for again meanwhile: this one is nobody's
}

static void shot_free(void)
{
    if (shot_read.pbo) glDeleteBuffers(1, &shot_read.pbo);
    if (shot_read.fbo) glDeleteFramebuffers(1, &shot_read.fbo);
    if (shot_read.tex) glDeleteTextures(1, &shot_read.tex);
    memset(&shot_read, 0, sizeof shot_read);
}

// The readback started a frame before: its pixels, mapped now.
static void shot_finish(void)
{
    if (!shot_read.serial) return;
    size_t n = (size_t)shot_read.w * (size_t)shot_read.h * 4;
    unsigned char *rgba = malloc(n);
    glBindBuffer(GL_PIXEL_PACK_BUFFER, shot_read.pbo);
    void *mapped = rgba ? glMapBufferRange(GL_PIXEL_PACK_BUFFER, 0, (GLsizeiptr)n, GL_MAP_READ_BIT) : NULL;
    if (mapped) {
        memcpy(rgba, mapped, n);
        glUnmapBuffer(GL_PIXEL_PACK_BUFFER);
    } else {
        free(rgba);
        rgba = NULL;
    }
    glBindBuffer(GL_PIXEL_PACK_BUFFER, 0);
    int serial = shot_read.serial, w = shot_read.w, h = shot_read.h;
    shot_free();
    shot_hand(serial, rgba, w, h);
}

static void native_shot(GLuint picture, int text_on, float level)
{
    pthread_mutex_lock(&app.lock);
    int kind = app.shot.kind, serial = app.shot.serial;
    pthread_mutex_unlock(&app.lock);
    if (shot_read.serial == serial || app.shot.done_serial == serial) return;      // on its way, or handed over
    shot_free();
    int w = kind ? app.out_w : app.in_w, h = kind ? app.out_h : app.in_h;
    GLuint tex = 0, fbo = 0;
    if (kind == 2) {
        if (screen_target(&tex, &fbo)) goto done;
        final_pass(fbo, h, w, h, picture, text_on, level);
        goto read;
    }
    glGenTextures(1, &tex);
    glBindTexture(GL_TEXTURE_2D, tex);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA8, w, h, 0, GL_RGBA, GL_UNSIGNED_BYTE, NULL);
    glGenFramebuffers(1, &fbo);
    glBindFramebuffer(GL_FRAMEBUFFER, fbo);
    glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, tex, 0);
    if (glCheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE) goto done;
    reset_gl();
    glBindFramebuffer(GL_FRAMEBUFFER, fbo);
    glViewport(0, 0, w, h);
    glActiveTexture(GL_TEXTURE0);
    glBindTexture(GL_TEXTURE_2D, picture);
    glUseProgram(p_scale);
    glUniform1f(loc_scale_level, level);
    quad();
    if (kind && text_on) {
        glBindTexture(GL_TEXTURE_2D, text.tex);
        glUseProgram(p_over);
        glEnable(GL_BLEND);
        glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA);
        quad();
        glDisable(GL_BLEND);
    }
read:
    // the read goes into a pixel buffer and returns at once; the next frame maps it (shot_finish)
    glBindFramebuffer(GL_FRAMEBUFFER, fbo);
    GLuint pbo = 0;
    glGenBuffers(1, &pbo);
    glBindBuffer(GL_PIXEL_PACK_BUFFER, pbo);
    glBufferData(GL_PIXEL_PACK_BUFFER, (GLsizeiptr)w * h * 4, NULL, GL_STREAM_READ);
    glPixelStorei(GL_PACK_ALIGNMENT, 4);
    glReadPixels(0, 0, w, h, GL_RGBA, GL_UNSIGNED_BYTE, 0);
    glBindBuffer(GL_PIXEL_PACK_BUFFER, 0);
    shot_read.serial = serial;
    shot_read.w = w;
    shot_read.h = h;
    shot_read.tex = tex;
    shot_read.fbo = fbo;
    shot_read.pbo = pbo;
    return;
done:
    if (fbo) glDeleteFramebuffers(1, &fbo);
    if (tex) glDeleteTextures(1, &tex);
    shot_hand(serial, NULL, w, h);
}

// The render thread (the one that calls this) asks for SCHED_RR at priority 1, once, before its loop. With four decoders
// busy it waited for a core and the turn's worst stage took 82 to 91 ms; at real-time priority no flip was longer than
// 2 vblanks (a scratch build on the Pi 4, 20 s windows). Only this thread: on Linux pid 0 is the calling thread, and
// RESET_ON_FORK keeps any thread or child it starts later at normal priority. It is allowed by the unit's
// LimitRTPRIO=1 (install/pvj-player.service), no capability. Without that (a desktop, CI, an older unit) it is said
// once and the program runs as it always did; this never stops it from starting.
static void realtime_priority(void)
{
    struct sched_param sp = { .sched_priority = 1 };
    if (sched_setscheduler(0, SCHED_RR | SCHED_RESET_ON_FORK, &sp))
        LOG("the render thread stays at normal priority: %s", strerror(errno));
    else
        LOG("the render thread runs at real-time priority 1 (SCHED_RR)");
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
    if (cfg.final_mode == FINAL_720) app.final_want = FINAL_LINES;   // the size from the frame the scaler is known to work on (D108)

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
    p_move = gl_program(VS_MOVE, FS_MOVE, err, sizeof err);
    if (!p_scale || !p_over || !p_mix || !p_move) { LOG("%s", err); out_close(); return 2; }
    glUseProgram(p_scale);
    glUniform1i(glGetUniformLocation(p_scale, "tex"), 0);
    loc_scale_level = glGetUniformLocation(p_scale, "pvj_level");
    glUseProgram(p_over);
    glUniform1i(glGetUniformLocation(p_over, "tex"), 0);
    glUseProgram(p_mix);
    glUniform1i(glGetUniformLocation(p_mix, "outgoing"), 0);
    glUniform1i(glGetUniformLocation(p_mix, "incoming"), 1);
    loc_share = glGetUniformLocation(p_mix, "share");
    loc_blend = glGetUniformLocation(p_mix, "blend");
    glUseProgram(p_move);
    glUniform1i(glGetUniformLocation(p_move, "tex"), 0);
    loc_shift = glGetUniformLocation(p_move, "shift");
    Layer mixer = { 0 };
    if (target(&mixer, app.in_w, app.in_h)) { LOG("no texture for the crossfade"); out_close(); return 2; }
    mix_tex = mixer.tex;
    mix_fbo = mixer.fbo;
    app.fx_fps = cfg.fx_fps;
    fx_init();

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
    // A wipe or a slide (D111) is all of that with one difference: the picture of a turn is cut along an edge where
    // the share lies, not mixed.
    realtime_priority();
    int failed = 0, text_was = 0, was_front = 0;
    long presented = 0;
    while (!g_signal) {
        pthread_mutex_lock(&app.lock);
        int quit = app.quit, cap = app.capture_serial != app.capture_done_serial;
        int text_on = app.overlays != 0 || now_s() < app.text_until;
        // one look at the whole fade: the layer, the phase and its clock are never from two different crossfades
        int fi = app.front, phase = app.fade.phase, base = app.fade.base_starts;
        double asked = app.fade.asked, t0 = app.fade.t0, len = app.fade.seconds;
        const Move *move = app.fade.move;
        memcpy(frame_opacity, app.opacity, sizeof frame_opacity);     // the sources' mix, read once for this frame
        // each source's level: while it has a program on, or while its layers still have brightness 0 (ipc.c, level_sync)
        for (int s = 0; s < MAX_SOURCES; s++)
            frame_level[s] = app.native[s] || app.level_native[s] ? (float)(app.level[s] / 100.0) : 0.0f;
        float level = frame_level[0];
        int shot = app.shot.serial != app.shot.done_serial;
        pthread_mutex_unlock(&app.lock);
        if (quit) break;
        if (app.hook_pending) take_hook();
        fx_take();
        Layer *front = &video[fi], *back = &video[1 - fi];
        int fresh = 0;                                      // source 1's picture is new this frame (D106)
        if (fi != was_front) { front->dirty = 1; was_front = fi; fresh = 1; }
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
            fresh = 1;
            app.drawn++;
            __atomic_add_fetch(&app.layer_drawn[fi], 1, __ATOMIC_RELAXED);
        }
        if (phase != FADE_OFF && back->dirty) {
            reset_gl();
            layer_render(back);
            back->dirty = 0;
            fresh = 1;
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
            int idle = __atomic_load_n(&app.idle[i], __ATOMIC_ACQUIRE), drew = 0;
            if (!idle) idle_drawn[i] = 0;
            if (video[i].dirty && !(idle && idle_drawn[i])) {
                reset_gl();
                layer_render(&video[i]);
                video[i].dirty = 0;
                if (idle) idle_drawn[i] = 1;
                __atomic_add_fetch(&app.layer_drawn[i], 1, __ATOMIC_RELAXED);
                drew = 1;
            }
            // the source's own programs (D106): its effects over its clip. An idle source shows its black and runs
            // none of them (an invert over black would light its surface white: the review of #168)
            // A generator in slot 0 is the source's picture (D106 step d): its clip layer is idle under it
            source_tex[i] = idle && !fx_has_generator(i - 1) ? video[i].tex
                                 : fx_source(i - 1, video[i].tex, drew, __atomic_load_n(&app.paused[i], __ATOMIC_ACQUIRE), now_s());
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
                if (same) {
                    app.fade.progress = share;
                    if (share > 0.0f) app.fade.steps++;
                }
                pthread_mutex_unlock(&app.lock);
                if (!same) {
                    picture = front->tex;
                } else if (share <= 0.0f) {
                    picture = back->tex;        // waiting for the incoming clip: the outgoing picture, with no mix
                } else {
                    if (move) move_pass(move, back, front, share);  // the edge where the clock says
                    else mix_pass(back, front, share, BLEND_NORMAL);    // the incoming layer at the clock's opacity
                    picture = mix_tex;
                    fresh = 1;
                }
            }
        }
        // the renderer's own programs on source 1 (D106): a generator in place of the picture, an effect over it
        // source 1 with nothing to show (its clip ended, a Stop) runs no effect over black either, unless a
        // generator is its picture (the carrier plays under it)
        if (!(__atomic_load_n(&app.idle[fi], __ATOMIC_ACQUIRE) && phase == FADE_OFF && !fx_has_generator(0)))
            picture = fx_source(0, picture, fresh, __atomic_load_n(&app.paused[fi], __ATOMIC_ACQUIRE), now);
        int draw_w, draw_h;
        out_draw_size(&draw_w, &draw_h);                    // this frame's size, whole: it goes out with the plane's for it (D108)
        final_pass(out_target(), app.out_h, draw_w, draw_h, picture, text_on, level);
        shot_finish();                                      // a readback started a frame ago (D106)
        if (shot) native_shot(picture, text_on, level);
        if (cap) {
            pthread_mutex_lock(&app.lock);
            char *path = app.capture_path ? strdup(app.capture_path) : NULL;
            int serial = app.capture_serial, raw = app.capture_buffer;
            app.capture_buffer = 0;
            pthread_mutex_unlock(&app.lock);
            int r = path ? write_capture(path, draw_h != app.out_h && !raw, picture, text_on, level) : -1;
            free(path);
            pthread_mutex_lock(&app.lock);
            app.capture_result = r;
            app.capture_done_serial = serial;
            pthread_cond_broadcast(&app.cond);
            pthread_mutex_unlock(&app.lock);
        }
        {   // the programs' passes a second, over about a second (D106: for the panel and the guard of step e)
            static double rate_t0;
            static long rate_n0;
            double t = now_s();
            if (t - rate_t0 >= 1.0) {
                long nn = __atomic_load_n(&app.fx_passes, __ATOMIC_RELAXED);
                pthread_mutex_lock(&app.lock);
                app.fx_passes_rate = rate_t0 > 0 ? (nn - rate_n0) / (t - rate_t0) : 0;
                pthread_mutex_unlock(&app.lock);
                rate_t0 = t;
                rate_n0 = nn;
            }
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
    fx_free_all();
    shot_free();
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
